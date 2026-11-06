"""Independent service-boundary regressions; synthetic data, no database or network."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from airport_assistant.modules.cases.capabilities import AssigneeCursor, StaffCapabilities
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError


class Fixture:
    def __init__(self, *, supervisor=True, closed=False):
        self.id, self.member = uuid4(), uuid4()
        self.identity = SimpleNamespace(
            database_context=None,
            session=SimpleNamespace(kind='staff', tenant_id=uuid4(), id=uuid4(), principal_id=uuid4()),
        )
        self.case = dict(id=self.id, status='closed' if closed else 'new', version=4,
                         assigned_membership_id=None)
        self.workspace = dict(membership_id=self.member, membership_version=2,
                              supervisor=supervisor, organization=dict(id=str(uuid4()), name='Org'),
                              queue=dict(id=str(uuid4()), name='Queue'), assignee=None)
        self.events = []
        self.rows = []
        self.active = True
        self.service = StaffCapabilities(self, AssigneeCursor(b'q' * 32))

    @asynccontextmanager
    async def transaction(self, context):
        yield self

    async def session_active(self):
        return self.active

    async def find_case(self, id, *, lock=False):
        assert id == self.id and lock
        self.events.append('case')
        return self.case

    async def active_chat(self, id, *, lock=False):
        assert id == self.id and lock
        self.events.append('chat')
        return None

    async def staff_workspace(self, id, member):
        assert id == self.id and member == self.member
        self.events.append('workspace')
        return self.workspace

    async def current_assignee(self, id):
        return None

    async def staff_assignees(self, id, member, position, limit):
        self.events.append(('directory', position, limit))
        return [row for row in self.rows if position is None or UUID(row['membership_id']) > position][:limit]


def test_selected_cs_service_uses_locked_case_chat_and_exact_workspace_before_claim():
    f = Fixture(supervisor=False)
    result = asyncio.run(f.service.get(f.identity, f.id, f.member))
    assert result['self_claim_membership_id'] == f.member
    assert not {'assign', 'unassign'}.intersection(result['allowed_actions'])
    assert result['allowed_actions'] == ['claim', 'close']
    assert f.events[:3] == ['case', 'chat', 'workspace']


def test_revoked_selected_scope_is_concealed_before_cursor_validation_or_directory_access():
    f = Fixture()
    f.workspace = None
    with pytest.raises(AuthError) as error:
        asyncio.run(f.service.assignees(f.identity, f.id, f.member, 'malformed'))
    assert error.value.status == 404
    assert f.events == ['case', 'chat', 'workspace']


def test_closed_case_still_validates_supplied_cursor_before_returning_empty_directory():
    f = Fixture(closed=True)
    with pytest.raises(DraftError) as error:
        asyncio.run(f.service.assignees(f.identity, f.id, f.member, 'malformed'))
    assert error.value.status == 400
    assert f.events == ['case', 'chat', 'workspace']
    result = asyncio.run(f.service.assignees(f.identity, f.id, f.member))
    assert result['items'] == [] and result['has_more'] is False and result['next_cursor'] is None


def test_live_directory_cursor_rejects_changed_case_basis_and_never_restarts_first_page():
    f = Fixture()
    f.rows = [dict(membership_id=str(UUID(int=i)), display_name=None) for i in (1, 2, 3)]
    first = asyncio.run(f.service.assignees(f.identity, f.id, f.member, limit=2))
    assert len(first['items']) == 2 and first['has_more'] is True
    token = first['next_cursor']
    second = asyncio.run(f.service.assignees(f.identity, f.id, f.member, token, 2))
    assert second['items'] == f.rows[2:] and second['next_cursor'] is None
    f.case['version'] += 1
    before = list(f.events)
    with pytest.raises(DraftError) as error:
        asyncio.run(f.service.assignees(f.identity, f.id, f.member, token, 2))
    assert error.value.status == 400
    assert f.events[len(before):] == ['case', 'chat', 'workspace']
