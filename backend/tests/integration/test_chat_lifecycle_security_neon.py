"""Independent CHAT-02 ledger-origin, rollback, session and adoption probes."""
from uuid import UUID, uuid4
import os
import pytest

from test_chat_handoff import (
    target, events_target, files_target, lifecycle_target, commit_target,
    adoption_target, draft_target, conversation_target, auth_target,
    chats, handoff, meta, accept, close, reassign, operator, login,
)
from test_staff_auth import run, row, change
from test_drafts import actor, payload
from test_anonymous_adoption import issue, verify
from test_conversations import create, send
from test_case_commit import confirm
from airport_assistant.infrastructure.database.case_lifecycle import LifecycleSQL
from airport_assistant.infrastructure.database.connections import DatabaseError

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in isolated CHAT-02 QA probes.')


def test_case_next_action_update_with_valid_history_still_requires_chat_pair(target):
    async def scenario():
        async with chats(target) as (h, sender):
            async with h.client() as customer, h.client() as staff:
                conversation, case = await handoff(h, sender, customer)
                person = operator(target, case); await login(h, staff, person)
                identity = await actor(h, staff)
                repository = h.app.state.services.chat_handoff._repository
                with pytest.raises(DatabaseError) as error:
                    async with repository.transaction(identity.database_context) as tx:
                        before = await tx.find_case(UUID(case), lock=True)
                        after = await tx.update_case(before, {'next_action': 'Raw change without paired conversation'})
                        await tx.history(before, after, 'status_changed')
                assert error.value.sqlstate == '23514'
                assert row(target, 'SELECT version FROM {s}.cases WHERE id=:id', id=UUID(case))[0] == 1
                assert row(target, 'SELECT mode,last_transition_id FROM {s}.conversations WHERE id=:id', id=UUID(conversation)) == ('waiting_staff', None)
    run(scenario())


def test_other_case_transition_cannot_authorize_unpaired_case_closure(target):
    async def scenario():
        async with chats(target) as (h, sender):
            async with h.client() as first_customer, h.client() as second_customer, h.client() as staff:
                first_conversation, first_case = await handoff(h, sender, first_customer)
                second_conversation, second_case = await handoff(h, sender, second_customer)
                person = operator(target, first_case); await login(h, staff, person)
                accepted = await accept(h, staff, first_conversation, await meta(staff, first_conversation))
                assert accepted.status_code == 200, accepted.text
                borrowed = row(target, 'SELECT last_transition_id FROM {s}.conversations WHERE id=:id', id=UUID(first_conversation))[0]
                identity = await actor(h, staff)
                repository = h.app.state.services.chat_handoff._repository
                with pytest.raises(DatabaseError) as error:
                    async with repository.transaction(identity.database_context) as tx:
                        before = await tx.find_case(UUID(second_case), lock=True)
                        after = await tx.update_case(before, {'status': 'closed', 'next_actor': 'none', 'next_action': 'Finished',
                                                            'close_reason': 'resolved', 'resolution': 'Borrowed transition must fail'})
                        await tx.history(before, after, 'status_changed', transition=borrowed)
                assert error.value.sqlstate == '23514'
                assert row(target, 'SELECT status,version FROM {s}.cases WHERE id=:id', id=UUID(second_case)) == ('new', 1)
                assert row(target, 'SELECT mode,last_transition_id FROM {s}.conversations WHERE id=:id', id=UUID(second_conversation)) == ('waiting_staff', None)
    run(scenario())


@pytest.mark.parametrize('kind,sqlstate', [('assignment', '42501'), ('status', '23514')])
def test_paired_cs_self_claim_cannot_bypass_supervisor_or_mislabel_kind(target, kind, sqlstate):
    async def scenario():
        async with chats(target) as (h, sender):
            async with h.client() as customer, h.client() as staff:
                conversation, case = await handoff(h, sender, customer)
                person = operator(target, case); await login(h, staff, person)
                identity = await actor(h, staff)
                repository = h.app.state.services.chat_handoff._repository
                with pytest.raises(DatabaseError) as error:
                    async with repository.transaction(identity.database_context) as tx:
                        before = await tx.find_case(UUID(case), lock=True)
                        old_chat = await tx.chat(UUID(conversation), lock=True)
                        transition = uuid4()
                        after = await tx.update_case(before, {'assigned_membership_id': person['case_membership']})
                        await tx.history(before, after, 'assigned', transition=transition)
                        current = await tx.move_chat(old_chat, 'waiting_staff', None, transition)
                        await tx.transition(transition, kind, before, after, old_chat, current)
                assert error.value.sqlstate == sqlstate
                assert row(target, 'SELECT assigned_membership_id,version FROM {s}.cases WHERE id=:id', id=UUID(case)) == (None, 1)
                assert row(target, 'SELECT mode,handler_membership_id FROM {s}.conversations WHERE id=:id', id=UUID(conversation)) == ('waiting_staff', None)
    run(scenario())


def test_raw_accept_cannot_leave_case_new_while_chat_is_human(target):
    async def scenario():
        async with chats(target) as (h, sender):
            async with h.client() as customer, h.client() as staff:
                conversation, case = await handoff(h, sender, customer)
                person = operator(target, case); await login(h, staff, person)
                identity = await actor(h, staff)
                repository = h.app.state.services.chat_handoff._repository
                with pytest.raises(DatabaseError) as error:
                    async with repository.transaction(identity.database_context) as tx:
                        before = await tx.find_case(UUID(case), lock=True)
                        old_chat = await tx.chat(UUID(conversation), lock=True)
                        transition = uuid4()
                        after = await tx.update_case(before, {'assigned_membership_id': person['case_membership']})
                        await tx.history(before, after, 'assigned', transition=transition)
                        current = await tx.move_chat(old_chat, 'human', person['case_membership'], transition)
                        await tx.transition(transition, 'accept', before, after, old_chat, current,
                                            key=str(uuid4()), digest='0' * 64)
                assert error.value.sqlstate == '23514'
                assert row(target, 'SELECT status,assigned_membership_id,version FROM {s}.cases WHERE id=:id', id=UUID(case)) == ('new', None, 1)
                assert row(target, 'SELECT mode,handler_membership_id FROM {s}.conversations WHERE id=:id', id=UUID(conversation)) == ('waiting_staff', None)
    run(scenario())


@pytest.mark.parametrize('forge_previous', [False, True])
def test_supervisor_cannot_take_over_preassignment_via_raw_accept_or_forged_origin(target, forge_previous):
    async def scenario():
        async with chats(target) as (h, sender):
            async with h.client() as customer, h.client() as supervisor:
                conversation, case = await handoff(h, sender, customer)
                assigned = operator(target, case)
                lead = operator(target, case, ('supervisor',)); await login(h, supervisor, lead)
                initial = await meta(supervisor, conversation)
                result = await reassign(h, supervisor, case, initial, assigned['case_membership'])
                assert result.status_code == 200, result.text
                identity = await actor(h, supervisor)
                repository = h.app.state.services.chat_handoff._repository
                with pytest.raises(DatabaseError) as error:
                    async with repository.transaction(identity.database_context) as tx:
                        before = await tx.find_case(UUID(case), lock=True)
                        old_chat = await tx.chat(UUID(conversation), lock=True)
                        transition = uuid4()
                        after = await tx.update_case(before, {'status': 'in_progress', 'assigned_membership_id': lead['case_membership'],
                                                            'next_actor': 'staff', 'next_action': 'Trying to bypass reassignment'})
                        await tx.history(before, after, 'assigned', transition=transition)
                        current = await tx.move_chat(old_chat, 'human', lead['case_membership'], transition)
                        origin = {**before, 'assigned_membership_id': None} if forge_previous else before
                        await tx.transition(transition, 'accept', origin, after, old_chat, current,
                                            key=str(uuid4()), digest='0' * 64)
                assert error.value.sqlstate == '23514'
                assert row(target, 'SELECT assigned_membership_id,status,version FROM {s}.cases WHERE id=:id', id=UUID(case)) == (assigned['case_membership'], 'new', 2)
                assert row(target, 'SELECT mode,handler_membership_id FROM {s}.conversations WHERE id=:id', id=UUID(conversation)) == ('waiting_staff', None)
    run(scenario())


def test_fault_after_transition_insert_rolls_back_both_states_and_event(target, monkeypatch):
    original = LifecycleSQL.transition

    async def fail_after_insert(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise RuntimeError('QA injected failure after transition insert')

    async def scenario():
        async with chats(target) as (h, sender):
            async with h.client() as customer, h.client() as staff:
                conversation, case = await handoff(h, sender, customer)
                person = operator(target, case); await login(h, staff, person)
                before = await meta(staff, conversation)
                monkeypatch.setattr(LifecycleSQL, 'transition', fail_after_insert)
                result = await accept(h, staff, conversation, before)
                assert result.status_code == 500
                assert await meta(staff, conversation) == before
                assert row(target, 'SELECT count(*) FROM {s}.chat_transitions WHERE case_id=:id', id=UUID(case))[0] == 0
                assert row(target, 'SELECT count(*) FROM {s}.case_history WHERE case_id=:id', id=UUID(case))[0] == 1
                assert row(target, 'SELECT count(*) FROM {s}.conversation_events WHERE conversation_id=:id', id=UUID(conversation))[0] == 1
                monkeypatch.setattr(LifecycleSQL, 'transition', original)
                result = await accept(h, staff, conversation, before)
                assert result.status_code == 200, result.text
    run(scenario())


def test_accept_receipt_replay_does_not_bypass_membership_or_session_revocation(target):
    async def scenario():
        async with chats(target) as (h, sender):
            async with h.client() as customer, h.client() as staff:
                conversation, case = await handoff(h, sender, customer)
                person = operator(target, case); await login(h, staff, person)
                before = await meta(staff, conversation); key = str(uuid4())
                accepted = await accept(h, staff, conversation, before, key)
                assert accepted.status_code == 200, accepted.text
                identity = await actor(h, staff)
                change(target, 'UPDATE {s}.staff_memberships SET active=false WHERE id=:id', id=person['case_membership'])
                denied = await accept(h, staff, conversation, before, key)
                assert denied.status_code == 404, denied.text
                change(target, 'UPDATE {s}.sessions SET revoked_at=clock_timestamp() WHERE id=:id', id=identity.session.id)
                denied = await accept(h, staff, conversation, before, key)
                assert denied.status_code == 401, denied.text
                assert row(target, 'SELECT count(*) FROM {s}.chat_transitions WHERE case_id=:id', id=UUID(case))[0] == 1
    run(scenario())


def test_human_chat_adoption_close_reopen_and_fresh_handoff_preserve_old_receipt(target):
    async def scenario():
        async with chats(target) as (h, sender):
            async with h.client() as customer, h.client() as staff:
                conversation, case = await handoff(h, sender, customer)
                person = operator(target, case); await login(h, staff, person)
                start = await meta(staff, conversation); key = str(uuid4())
                accepted = await accept(h, staff, conversation, start, key)
                assert accepted.status_code == 200, accepted.text
                before = await meta(staff, conversation)
                challenge, code = await issue(h, sender, customer)
                result = await verify(h, customer, challenge, code)
                assert result.status_code == 200, result.text
                adopted = await meta(staff, conversation)
                assert adopted['mode'] == 'human' and adopted['handler_membership_id'] == before['handler_membership_id']
                assert adopted['case_version'] == before['case_version'] + 1
                assert adopted['version'] == before['version'] + 1 and adopted['generation_epoch'] == before['generation_epoch'] + 1
                result = await close(h, staff, case, adopted)
                assert result.status_code == 200, result.text
                ended = await meta(staff, conversation)
                reopened = await customer.post('/api/v1/cases/' + case + '/continue', headers=h.headers(customer),
                                               json={'expected_version': ended['case_version'], 'intent': 'same_issue',
                                                     'message': 'The same issue remains', 'client_request_id': str(uuid4())})
                assert reopened.status_code == 200, reopened.text
                fresh = await create(h, customer)
                draft = await customer.post('/api/v1/action-drafts', headers=h.headers(customer), json={
                    'conversation_id': fresh, 'action_type': 'request_handoff', 'case_id': case,
                    'payload': payload(category='information', service_type='general', booking_reference=None, contact_email=None)})
                assert draft.status_code == 201, draft.text
                result = await confirm(h, customer, draft.json())
                assert result.status_code == 201, result.text
                assert (await meta(staff, fresh))['mode'] == 'waiting_staff'
                old = await meta(staff, conversation)
                assert old['mode'] == 'ended' and old['handler_membership_id'] is None
                assert old['version'] == ended['version']
                assert (await send(h, customer, conversation, body='No silent restart')).status_code == 409
                replay = await accept(h, staff, conversation, start, key)
                assert replay.status_code == 200 and replay.json() == accepted.json(), replay.text
                assert row(target, "SELECT count(*) FROM {s}.conversations WHERE case_id=:id AND mode IN('waiting_staff','human')", id=UUID(case))[0] == 1
    run(scenario())
