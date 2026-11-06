"""Independent deadline regression; Lead executes this file, QA does not."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from airport_assistant.modules.knowledge.evidence import (
    Citation, EvidencePolicy, KnowledgeEvidence, Snapshot,
)
from airport_assistant.modules.knowledge.retrieval import Search


@pytest.mark.parametrize('elapsed', [1, 2])
def test_snapshot_expiring_during_authoritative_read_never_releases_source(elapsed):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    identity = SimpleNamespace(session=SimpleNamespace(
        tenant_id=uuid4(), id=uuid4(), principal_id=None, kind='anonymous',
    ))
    selection = Search(query='Parking policy', service_type='parking', language='en', as_of=now)
    citation = Citation(
        version_id=uuid4(), generation=uuid4(), ordinal=0, basis_hash='a' * 64,
        document_id=uuid4(), document_revision=2, score=.8,
    )
    clock = [now]

    class Repository:
        @asynccontextmanager
        async def transaction(self, actor):
            yield self

        async def evidence_chunks(self, context, citations):
            # Model a DB/lock wait without sleeping or making a database request.
            clock[0] += timedelta(seconds=elapsed)
            return [dict(
                version_id=citation.version_id, generation=citation.generation, ordinal=0,
                basis_hash=citation.basis_hash, document_id=citation.document_id,
                document_revision=2, scope_token='b' * 64, ambiguous=False,
                body='Authoritative parking policy', source_uri='urn:airport:parking:v1',
            )]

    service = KnowledgeEvidence(
        SimpleNamespace(lifecycle=Repository()), EvidencePolicy(.3, 'deadline-qa-v1', 1),
        b'q' * 48, clock=lambda: clock[0],
    )
    snapshot = Snapshot(
        tenant_id=identity.session.tenant_id, session_id=identity.session.id,
        principal_id=None, actor_kind='anonymous', context_key=service.context(selection),
        scope_token='b' * 64, policy_id=service.policy.policy_id, issued_at=now,
        citations=(citation,), signature='0' * 64,
    )
    snapshot = snapshot.model_copy(update={'signature': service.signature(snapshot)})
    result = asyncio.run(service.consume(identity, selection, snapshot))
    assert result.status == 'stale_sources'
    assert result.chunks == () and result.snapshot is None
