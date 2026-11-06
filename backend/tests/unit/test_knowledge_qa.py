"""Independent knowledge publication proof boundaries; Lead owns execution."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.knowledge.models import Transition
from airport_assistant.modules.knowledge.service import IndexProof, KnowledgeService


class PublicationFixture:
    def __init__(self):
        self.tenant, self.member, self.document_id, self.version_id = (
            uuid4(), uuid4(), uuid4(), uuid4()
        )
        self.identity = SimpleNamespace(
            database_context=None,
            session=SimpleNamespace(kind='staff', tenant_id=self.tenant),
        )
        self.document_row = dict(id=self.document_id, organization_id=uuid4())
        self.version_row = dict(revision=1, status='draft', basis_hash='a' * 64)
        self.transactions = 0
        self.open_transaction = False
        self.writes = []
        self.on_probe = lambda: None
        self.proof = IndexProof(
            self.tenant, self.version_id, 'a' * 64, uuid4(),
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        self.service = KnowledgeService(self, self)

    @asynccontextmanager
    async def transaction(self, context):
        self.transactions += 1
        self.open_transaction = True
        try:
            yield self
        finally:
            self.open_transaction = False

    async def session_active(self):
        return True

    async def admin(self, member, organization):
        assert member == self.member
        assert organization == self.document_row['organization_id']
        return self.document_row

    async def parent(self, version):
        assert version == self.version_id
        return self.document_id

    async def document(self, document, lock=False):
        assert document == self.document_id and lock
        return self.document_row

    async def version(self, version, lock=False):
        assert version == self.version_id and lock
        return self.version_row

    async def readiness(self, tenant, version, basis):
        assert not self.open_transaction
        assert (tenant, version, basis) == (self.tenant, self.version_id, 'a' * 64)
        self.on_probe()
        return self.proof

    async def published(self, document):
        return []

    async def publish(self, version, proof):
        self.writes.append((version, proof))
        return self.version_row

    def publish_command(self):
        return self.service.publish(
            self.identity, self.version_id,
            Transition(membership_id=self.member, expected_revision=1),
        )


@pytest.mark.parametrize('invalid', ['tenant', 'version', 'basis', 'generation', 'expired', 'naive'])
def test_wrong_or_expired_index_proof_cannot_reach_publish(invalid):
    f = PublicationFixture()
    values = vars(f.proof).copy()
    key, value = {
        'tenant': ('tenant_id', uuid4()),
        'version': ('version_id', uuid4()),
        'basis': ('basis_hash', 'b' * 64),
        'generation': ('generation', str(uuid4())),
        'expired': ('valid_until', datetime.now(timezone.utc) - timedelta(seconds=1)),
        'naive': ('valid_until', datetime(2100, 1, 1)),
    }[invalid]
    values[key] = value
    f.proof = IndexProof(**values)
    with pytest.raises(DraftError) as failure:
        asyncio.run(f.publish_command())
    assert (failure.value.status, failure.value.code) == (503, 'KNOWLEDGE_INDEX_UNAVAILABLE')
    assert f.writes == []
    assert f.transactions == 1


def test_basis_change_during_probe_rejected_even_if_revision_is_unchanged():
    f = PublicationFixture()
    f.on_probe = lambda: f.version_row.update(basis_hash='b' * 64)
    with pytest.raises(DraftError) as failure:
        asyncio.run(f.publish_command())
    assert (failure.value.status, failure.value.code) == (409, 'VERSION_CONFLICT')
    assert f.transactions == 2
    assert f.writes == []


def test_default_index_never_publishes_without_real_readiness_adapter():
    f = PublicationFixture()
    f.service = KnowledgeService(f)
    with pytest.raises(DraftError) as failure:
        asyncio.run(f.publish_command())
    assert (failure.value.status, failure.value.code) == (503, 'KNOWLEDGE_INDEX_UNAVAILABLE')
    assert f.transactions == 1
    assert f.writes == []
