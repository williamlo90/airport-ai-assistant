"""Source evidence is data, never tool authority or a pre-approved factual answer."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError, field_validator
from airport_assistant.modules.drafts.models import DraftError
from .models import Model, aware
from .retrieval import Search


class Citation(Model):
    version_id: UUID = Field(strict=False)
    generation: UUID = Field(strict=False)
    ordinal: int = Field(ge=0, le=62)
    basis_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    document_id: UUID = Field(strict=False)
    document_revision: int = Field(ge=1)
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)


class Snapshot(Model):
    tenant_id: UUID = Field(strict=False)
    session_id: UUID = Field(strict=False)
    principal_id: UUID | None = Field(strict=False)
    actor_kind: Literal['anonymous','customer','staff']
    context_key: str = Field(pattern=r'^[a-f0-9]{64}$')
    scope_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    policy_id: str = Field(min_length=1, max_length=100)
    issued_at: datetime = Field(strict=False)
    citations: tuple[Citation, ...] = Field(min_length=1, max_length=10, strict=False)
    signature: str = Field(pattern=r'^[a-f0-9]{64}$')
    _aware = field_validator('issued_at')(aware)


@dataclass(frozen=True)
class EvidencePolicy:
    # Explicit server-calibrated policy, not a universal embedding threshold.
    minimum_score: float
    policy_id: str
    ttl_seconds: int = 120

    def __post_init__(self):
        if (type(self.minimum_score) not in (int,float) or not math.isfinite(self.minimum_score)
            or not -1 <= self.minimum_score <= 1 or not self.policy_id.strip() or len(self.policy_id)>100
            or type(self.ttl_seconds) is not int or not 1<=self.ttl_seconds<=300):
            raise ValueError('Invalid evidence policy.')


@dataclass(frozen=True)
class SourceChunk:
    citation: Citation
    body: str
    source_uri: str
    authority: str = 'untrusted_source_text'


@dataclass(frozen=True)
class EvidenceResult:
    status: Literal['sources_available','insufficient_sources','conflicting_sources','stale_sources','source_unavailable']
    reason: str
    chunks: tuple[SourceChunk,...] = ()
    snapshot: Snapshot | None = None

    @property
    def next_action(self):
        return 'review_and_ground_claims' if self.status=='sources_available' else 'clarify_or_escalate'


class KnowledgeEvidence:
    def __init__(self, retrieval, policy: EvidencePolicy, secret: bytes, *, clock=None):
        if not isinstance(secret,bytes) or len(secret)<32:
            raise ValueError('Evidence signing key required.')
        self.retrieval, self.policy = retrieval, policy
        self._key=hmac.digest(secret,b'airport-knowledge-evidence-v1','sha256')
        self.clock=clock or (lambda:datetime.now(timezone.utc))

    def context(self, c):
        # Bind query, trusted applicability and the complete scoring policy.
        value={'selection':c.model_dump(mode='json'),'policy_id':self.policy.policy_id,
               'minimum_score':self.policy.minimum_score,'ttl_seconds':self.policy.ttl_seconds}
        return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()

    def signature(self, snapshot):
        data=json.dumps(snapshot.model_dump(mode='json',exclude={'signature'}),sort_keys=True,ensure_ascii=False,allow_nan=False)
        return hmac.new(self._key,data.encode(),'sha256').hexdigest()

    @staticmethod
    def citation(row):
        return Citation.model_validate({key:row[key] for key in Citation.model_fields})

    async def collect(self, identity, selection):
        c=Search.model_validate(selection.model_dump() if isinstance(selection,Search) else selection)
        try:
            rows=await self.retrieval.search(identity,c)
        except DraftError as error:
            if error.status!=503:raise
            return EvidenceResult('source_unavailable','retrieval_unavailable')
        if any(row['ambiguous'] for row in rows):
            return EvidenceResult('conflicting_sources','ambiguous_versions')
        rows=[r for r in rows if math.isfinite(r['score']) and r['score']>=self.policy.minimum_score]
        if not rows:
            return EvidenceResult('insufficient_sources','no_relevant_sources')
        citations=tuple(self.citation(row) for row in rows)
        if len({(r.version_id,r.generation,r.ordinal) for r in citations})!=len(citations):
            return EvidenceResult('stale_sources','invalid_reference_set')
        session=identity.session
        snapshot=Snapshot(tenant_id=session.tenant_id,session_id=session.id,principal_id=session.principal_id,
            actor_kind=session.kind,context_key=self.context(c),scope_token=rows[0]['scope_token'],
            policy_id=self.policy.policy_id,issued_at=self.clock(),citations=citations,signature='0'*64)
        snapshot=snapshot.model_copy(update={'signature':self.signature(snapshot)})
        # Re-read authoritative data even on first use; never return cached row bodies.
        return await self.consume(identity,c,snapshot)

    async def consume(self, identity, selection, snapshot):
        c=Search.model_validate(selection.model_dump() if isinstance(selection,Search) else selection)
        try:
            s=Snapshot.model_validate(snapshot.model_dump() if isinstance(snapshot,Snapshot) else snapshot)
        except (ValidationError,TypeError,ValueError):
            return EvidenceResult('stale_sources','invalid_snapshot')
        actor=identity.session
        age=(self.clock()-s.issued_at).total_seconds()
        if (not hmac.compare_digest(s.signature,self.signature(s)) or not 0<=age<self.policy.ttl_seconds
            or (s.tenant_id,s.session_id,s.principal_id,s.actor_kind)!=(actor.tenant_id,actor.id,actor.principal_id,actor.kind)
            or s.context_key!=self.context(c) or s.policy_id!=self.policy.policy_id
            or len({(r.version_id,r.generation,r.ordinal) for r in s.citations})!=len(s.citations)):
            return EvidenceResult('stale_sources','snapshot_not_current')
        try:
            async with self.retrieval.lifecycle.transaction(identity) as tx:
                rows=await tx.evidence_chunks(c,s.citations)
        except DraftError as error:
            if error.status!=503:raise
            return EvidenceResult('source_unavailable','retrieval_unavailable')
        # Pool/lock/query waits may consume the remaining TTL. Check only after
        # transaction exit has also revalidated the live session.
        age=(self.clock()-s.issued_at).total_seconds()
        if not 0<=age<self.policy.ttl_seconds:
            return EvidenceResult('stale_sources','snapshot_not_current')
        if any(row['ambiguous'] for row in rows):
            return EvidenceResult('conflicting_sources','ambiguous_versions')
        if len(rows)!=len(s.citations) or any(r['scope_token']!=s.scope_token for r in rows):
            return EvidenceResult('stale_sources','sources_changed')
        by_ref={(r['version_id'],r['generation'],r['ordinal']):r for r in rows}
        chunks=[]
        for citation in s.citations:
            row=by_ref.get((citation.version_id,citation.generation,citation.ordinal))
            if row is None or row['basis_hash']!=citation.basis_hash or row['document_revision']!=citation.document_revision or row['document_id']!=citation.document_id:
                return EvidenceResult('stale_sources','sources_changed')
            chunks.append(SourceChunk(citation,row['body'],row['source_uri']))
        return EvidenceResult('sources_available','candidates_require_grounding',tuple(chunks),s)
