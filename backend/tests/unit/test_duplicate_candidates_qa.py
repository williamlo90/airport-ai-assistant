"""Independent authorization and response-projection checks without I/O."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from airport_assistant.api.case_lifecycle import DuplicateCandidatesView
from airport_assistant.modules.cases.capabilities import StaffCapabilities, AssigneeCursor
from airport_assistant.modules.cases.duplicate import DuplicateSearch
from airport_assistant.modules.cases.lifecycle import CaseLifecycle, StatusChange
from airport_assistant.modules.identity.auth import AuthError


class Fixture:
    def __init__(self, kind='staff'):
        self.id, self.member, self.target = uuid4(), uuid4(), uuid4()
        self.identity = SimpleNamespace(database_context=None, session=SimpleNamespace(kind=kind))
        self.events = []
        self.active = True
        self.case = dict(id=self.id, version=1, status='new')
        self.service = StaffCapabilities(self, AssigneeCursor(b'q' * 32))

    @asynccontextmanager
    async def transaction(self, context):
        self.events.append('transaction')
        yield self

    async def session_active(self):
        self.events.append('session')
        return self.active

    async def find_case(self, id, *, lock=False):
        assert id == self.id and lock
        self.events.append('case')
        return self.case

    async def active_chat(self, id, *, lock=False):
        self.events.append('chat')
        return None

    async def staff_workspace(self, id, member):
        self.events.append('scope')
        return dict(membership_id=self.member, membership_version=1)

    async def duplicate_candidate(self, *args):
        raise AssertionError('Unauthorized lookup must never reach the candidate query')

    def search(self):
        command = DuplicateSearch(membership_id=self.member, expected_version=1,
                                  public_reference='CASE-' + self.target.hex)
        return asyncio.run(self.service.duplicate_candidates(self.identity, self.id, command))


@pytest.mark.parametrize('kind', ['anonymous', 'customer'])
def test_nonstaff_cannot_open_a_transaction_or_probe_case_metadata(kind):
    f = Fixture(kind)
    with pytest.raises(AuthError) as error:
        f.search()
    assert error.value.status == 401
    assert f.events == []


def test_expired_session_cannot_probe_source_existence():
    f = Fixture()
    f.active = False
    with pytest.raises(AuthError) as error:
        f.search()
    assert error.value.status == 401
    assert f.events == ['transaction', 'session']


def test_missing_source_does_not_touch_chat_selected_scope_or_target_directory():
    f = Fixture()
    f.case = None
    with pytest.raises(AuthError) as error:
        f.search()
    assert error.value.status == 404
    assert f.events == ['transaction', 'session', 'case']


def response():
    source, member, target = uuid4(), uuid4(), uuid4()
    return dict(case_id=source, case_version=1, membership_id=member, membership_version=1,
                items=[dict(case_id=target, public_reference='CASE-' + target.hex)])


def test_http_response_projection_never_serializes_target_body_or_owner_metadata():
    value = response()
    value['items'][0].update(customer_report='Synthetic private report', owner_principal_id=uuid4(),
                             owner_contact_id=uuid4(), status='closed', organization_id=uuid4())
    projected = DuplicateCandidatesView.model_validate(value).model_dump(mode='json')
    assert set(projected) == {'case_id', 'case_version', 'membership_id', 'membership_version', 'items'}
    assert set(projected['items'][0]) == {'case_id', 'public_reference'}
    assert 'Synthetic private report' not in str(projected)


def test_response_cannot_turn_exact_lookup_into_a_multi_case_directory():
    value = response()
    value['items'].append(dict(value['items'][0]))
    with pytest.raises(ValidationError):
        DuplicateCandidatesView.model_validate(value)


def test_previously_found_candidate_cannot_authorize_close_after_target_owner_changes():
    f = Fixture()
    owner = uuid4()
    f.case.update(owner_principal_id=owner, owner_anonymous_session_id=None)
    related = dict(id=f.target, owner_principal_id=owner, owner_anonymous_session_id=None)

    async def find_case(id, *, lock=False):
        if id == f.id:
            assert lock
            return f.case
        assert id == f.target and not lock
        return related

    async def candidate(reference, source, membership):
        assert related['owner_principal_id'] == source['owner_principal_id']
        return dict(case_id=f.target, public_reference=reference)

    async def update_case(*args):
        raise AssertionError('Revoked owner relationship must prevent the write')

    f.find_case = find_case
    f.duplicate_candidate = candidate
    f.update_case = update_case

    async def scenario():
        result = await f.service.duplicate_candidates(f.identity, f.id, DuplicateSearch(
            membership_id=f.member, expected_version=1, public_reference='CASE-' + f.target.hex))
        assert result['items'][0]['case_id'] == f.target
        related['owner_principal_id'] = uuid4()
        command = StatusChange(expected_version=1, status='closed', next_action='Recorded duplicate.',
                               close_reason='duplicate', resolution='Synthetic resolution.',
                               related_case_id=result['items'][0]['case_id'])
        with pytest.raises(AuthError) as error:
            await CaseLifecycle(f).status(f.identity, f.id, command)
        assert error.value.status == 404
        assert f.case['version'] == 1 and f.case['status'] == 'new'

    asyncio.run(scenario())
