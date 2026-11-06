"""Bounded indexing and retrieval. Identity and applicability come from the server."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import Field, field_validator
from airport_assistant.modules.drafts.models import DraftError
from .models import Model, Selection, Transition, text
from .service import IndexProof

MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536
CHUNKER = 'chars384-overlap64-v1'


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    start: int
    body: str


def chunk_text(body: str) -> tuple[Chunk, ...]:
    if not isinstance(body, str) or not body.strip() or '\0' in body or len(body) > 20000:
        raise DraftError(422, 'VALIDATION_ERROR')
    # Python/Postgres character offsets, not UTF-8 byte offsets. No normalization.
    chunks = []
    start = 0
    while start < len(body):
        chunks.append(Chunk(len(chunks), start, body[start:start+384]))
        if start+384 >= len(body):
            break
        start += 320
    return tuple(chunks)


def normalized(values):
    try:
        if not isinstance(values, (list, tuple)) or len(values) != DIMENSIONS:
            raise ValueError
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError
        norm = math.sqrt(sum(v*v for v in values))
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError
        return tuple(v/norm for v in values)
    except (TypeError, ValueError, OverflowError):
        raise DraftError(503, 'EMBEDDING_UNAVAILABLE') from None


class Embeddings(Protocol):
    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]: ...


class Search(Selection):
    # No raw tenant/actor/ACL parameters; trusted applicability inherited from Selection.
    query: str = Field(min_length=1, max_length=1000)
    document_key: str | None = Field(default=None, min_length=1, max_length=100, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    limit: int = Field(default=5, ge=1, le=10)
    _query = field_validator('query')(text)


class KnowledgeRetrieval:
    def __init__(self, lifecycle, embeddings: Embeddings):
        self.lifecycle, self.embeddings = lifecycle, embeddings

    async def prepare(self, identity, version_id, command):
        c = command if isinstance(command, Transition) else Transition.model_validate(command)
        async with self.lifecycle.transaction(identity) as tx:
            _, version = await self.lifecycle.version(tx, identity, version_id, c.membership_id)
            self.lifecycle.revision(version, c.expected_revision)
            self.lifecycle.draft(version)
            existing = await tx.index_proof(version_id, version['basis_hash'])
            if existing:
                return existing
            basis, body = version['basis_hash'], version['body']
        chunks = chunk_text(body)
        vectors = []
        # No database locks or connections held during provider calls.
        for start in range(0, len(chunks), 8):
            batch = chunks[start:start+8]
            result = await self.embeddings.embed([chunk.body for chunk in batch])
            if len(result) != len(batch):
                raise DraftError(503, 'EMBEDDING_UNAVAILABLE')
            vectors.extend(normalized(v) for v in result)
        generation = uuid4()
        async with self.lifecycle.transaction(identity) as tx:
            _, current = await self.lifecycle.version(tx, identity, version_id, c.membership_id)
            self.lifecycle.revision(current, c.expected_revision)
            self.lifecycle.draft(current)
            if current['basis_hash'] != basis:
                raise DraftError(409, 'VERSION_CONFLICT')
            existing = await tx.index_proof(version_id, basis)
            if existing:
                return existing
            await tx.store_chunks(version_id, basis, generation, chunks, vectors)
            proof = await tx.index_proof(version_id, basis)
            if proof is None:
                raise DraftError(503, 'KNOWLEDGE_INDEX_UNAVAILABLE')
            return proof

    async def readiness(self, identity, version_id, basis):
        async with self.lifecycle.transaction(identity) as tx:
            return await tx.index_proof(version_id, basis)

    async def search(self, identity, selection):
        c = selection if isinstance(selection, Search) else Search.model_validate(selection)
        async with self.lifecycle.transaction(identity):
            pass  # Check a live session before sending query text to the provider.
        result = await self.embeddings.embed([c.query])
        if len(result) != 1:
            raise DraftError(503, 'EMBEDDING_UNAVAILABLE')
        vector = normalized(result[0])
        async with self.lifecycle.transaction(identity) as tx:
            # Recheck session and current publication/ACL after the network round-trip.
            return await tx.search_chunks(c, vector)


def proof(tenant, version, basis, generation):
    return IndexProof(tenant, version, basis, generation, datetime.now(timezone.utc)+timedelta(seconds=60))
