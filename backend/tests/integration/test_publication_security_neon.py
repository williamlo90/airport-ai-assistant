"""Independent CHAT-03 capability, context-race and transaction probes."""
import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import os
from uuid import UUID, uuid4

import pytest
from test_chat_publication import (
    target, handoff_target, events_target, files_target, lifecycle_target,
    commit_target, adoption_target, draft_target, conversation_target, auth_target,
    publications, active, command, reply,
)
from test_staff_auth import run, row, change, insert
from test_drafts import actor
from test_conversations import create, send
from test_case_commit import prepared, confirm
from test_chat_handoff import meta, operator, login, reassign
from test_anonymous_adoption import issue, verify
from airport_assistant.modules.conversations.publication import GenerationTicket
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.infrastructure.database.publication import PublicationSQL
from airport_assistant.infrastructure.database.case_repository import CaseSQL
from airport_assistant.infrastructure.database.conversation_repository import ConversationSQL
from airport_assistant.infrastructure.database.connections import DatabaseError

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in isolated publication QA probes.')


def test_generation_ticket_nonce_session_and_adoption_never_grant_publication(target):
    async def scenario():
        async with publications(target) as (h, sender, publisher):
            async with h.client() as customer, h.client() as other:
                await h.boot(customer); await h.boot(other)
                conversation = UUID(await create(h, customer))
                identity = await actor(h, customer); stranger = await actor(h, other)
                ticket = await publisher.capture(identity, conversation)
                assert ticket.nonce not in repr(ticket)
                for claimant, capability in ((stranger, ticket), (identity, GenerationTicket(ticket.id, 'wrong-nonce'))):
                    with pytest.raises(AuthError) as error:
                        await publisher.publish(claimant, capability, 'Controlled result')
                    assert error.value.status == 404
                saved = await publisher.publish(identity, ticket, 'Controlled result')
                assert saved['state'] == 'published'
                assert await publisher.publish(identity, ticket, 'Controlled result') == saved
                challenge, code = await issue(h, sender, customer)
                assert (await verify(h, customer, challenge, code)).status_code == 200
                with pytest.raises(AuthError) as error:
                    await publisher.publish(identity, ticket, 'Controlled result')
                assert error.value.status == 401
                with pytest.raises(AuthError) as error:
                    await publisher.publish(await actor(h, customer), ticket, 'Controlled result')
                assert error.value.status == 404
                assert row(target, "SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'", id=conversation)[0] == 1
    run(scenario())


def test_handoff_between_case_preread_and_chat_lock_discards_generation(target, monkeypatch):
    original = PublicationSQL.lock_conversation_case

    async def scenario():
        async with publications(target) as (h, sender, publisher):
            async with h.client() as customer:
                draft = await prepared(h, sender, customer, anonymous=True)
                conversation = UUID(draft['conversation_id']); identity = await actor(h, customer)
                ticket = await publisher.capture(identity, conversation)
                observed = asyncio.Event(); release = asyncio.Event()

                async def pause_after_preread(self, id):
                    result = await original(self, id)
                    if id == conversation:
                        observed.set(); await release.wait()
                    return result

                monkeypatch.setattr(PublicationSQL, 'lock_conversation_case', pause_after_preread)
                pending = asyncio.create_task(publisher.publish(identity, ticket, 'Late result'))
                try:
                    await asyncio.wait_for(observed.wait(), 15)
                    confirmed = await asyncio.wait_for(confirm(h, customer, draft), 15)
                    assert confirmed.status_code == 201, confirmed.text
                finally:
                    release.set()
                assert await asyncio.wait_for(pending, 15) == {'state': 'discarded', 'reason': 'STALE_GENERATION'}
                assert row(target, 'SELECT state FROM {s}.ai_generations WHERE id=:id', id=ticket.id)[0] == 'stale'
                assert row(target, "SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'", id=conversation)[0] == 0
    run(scenario())


def test_raw_language_basis_change_cannot_publish_old_language(target):
    async def scenario():
        async with publications(target) as (h, sender, publisher):
            async with h.client() as customer:
                await h.boot(customer); conversation = UUID(await create(h, customer))
                identity = await actor(h, customer); ticket = await publisher.capture(identity, conversation)
                async with publisher._repository.transaction(identity.database_context) as tx:
                    await tx._c.execute(tx.query("UPDATE {s}.conversations SET language='en' WHERE tenant_id=%s AND id=%s"),
                                        (identity.session.tenant_id, conversation))
                result = await publisher.publish(identity, ticket, 'Outdated language result')
                assert result == {'state': 'discarded', 'reason': 'STALE_GENERATION'}
                assert row(target, "SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'", id=conversation)[0] == 0
    run(scenario())


def test_ai_body_without_published_generation_graph_rolls_back(target):
    async def scenario():
        async with publications(target) as (h, sender, publisher):
            async with h.client() as customer:
                await h.boot(customer); conversation = UUID(await create(h, customer))
                identity = await actor(h, customer); ticket = await publisher.capture(identity, conversation)
                with pytest.raises(DatabaseError) as error:
                    async with publisher._repository.transaction(identity.database_context) as tx:
                        current = await tx.find(conversation, lock=True)
                        generation = await tx.generation(ticket.id, lock=True)
                        await tx.ai_message(current, generation, 'Body without terminal generation')
                assert error.value.sqlstate == '23514'
                assert row(target, 'SELECT version,last_sequence,last_event_sequence FROM {s}.conversations WHERE id=:id', id=conversation) == (1, 0, 0)
                assert row(target, 'SELECT state,message_id FROM {s}.ai_generations WHERE id=:id', id=ticket.id) == ('pending', None)
    run(scenario())


def test_staff_fault_after_message_event_insert_rolls_back_and_retries_same_key(target, monkeypatch):
    original = PublicationSQL.staff_message

    async def fail_after_insert(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise RuntimeError('QA injected after staff body/event insert')

    async def scenario():
        async with publications(target) as (h, sender, publisher):
            async with h.client() as customer, h.client() as staff:
                conversation, case, _ = await active(h, sender, target, customer, staff)
                state = await meta(staff, conversation); body = command(conversation, state); key = str(uuid4())
                before = row(target, 'SELECT version,last_sequence,last_event_sequence FROM {s}.conversations WHERE id=:id', id=UUID(conversation))
                monkeypatch.setattr(PublicationSQL, 'staff_message', fail_after_insert)
                result = await reply(h, staff, case, body, key)
                assert result.status_code == 500
                assert row(target, 'SELECT version,last_sequence,last_event_sequence FROM {s}.conversations WHERE id=:id', id=UUID(conversation)) == before
                assert row(target, "SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'", id=UUID(conversation))[0] == 0
                monkeypatch.setattr(PublicationSQL, 'staff_message', original)
                saved = await reply(h, staff, case, body, key)
                assert saved.status_code == 201, saved.text
                assert (await reply(h, staff, case, body, key)).json() == saved.json()
    run(scenario())


def test_old_handler_receipt_is_historical_but_new_send_requires_current_handler(target):
    async def scenario():
        async with publications(target) as (h, sender, publisher):
            async with h.client() as customer, h.client() as staff, h.client() as supervisor:
                conversation, case, person = await active(h, sender, target, customer, staff)
                state = await meta(staff, conversation); body = command(conversation, state); key = str(uuid4())
                saved = await reply(h, staff, case, body, key)
                assert saved.status_code == 201, saved.text
                other = operator(target, case); lead = operator(target, case, ('supervisor',))
                await login(h, supervisor, lead)
                moved = await reassign(h, supervisor, case, await meta(supervisor, conversation), other['case_membership'])
                assert moved.status_code == 200, moved.text
                assert (await reply(h, staff, case, body, key)).json() == saved.json()
                fresh = command(conversation, await meta(staff, conversation))
                denied = await reply(h, staff, case, fresh)
                assert denied.status_code == 409 and denied.json()['error']['code'] == 'STALE_MESSAGE'
                change(target, 'UPDATE {s}.staff_memberships SET active=false WHERE id=:id', id=person['case_membership'])
                assert (await reply(h, staff, case, body, key)).status_code == 404
                assert row(target, "SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'", id=UUID(conversation))[0] == 1
    run(scenario())


@pytest.mark.parametrize('deadline_check', ['service', 'sql'])
def test_expired_generation_is_discarded_without_message_or_event(target, monkeypatch, deadline_check):
    async def scenario():
        async with publications(target) as (h, sender, publisher):
            async with h.client() as customer:
                await h.boot(customer); conversation = UUID(await create(h, customer)); identity = await actor(h, customer)
                ticket = GenerationTicket(uuid4(), 'synthetic-expired-fixture')
                now = datetime.now(timezone.utc)
                with target['engine'].begin() as db:
                    insert(db, target['schema'], 'ai_generations', id=ticket.id, tenant_id=identity.session.tenant_id,
                           conversation_id=conversation, origin_session_id=identity.session.id, principal_id=None,
                           input_version=1, generation_epoch=1, language='id', nonce_hash=hashlib.sha256(ticket.nonce.encode()).hexdigest(),
                           created_at=now-timedelta(minutes=6), expires_at=now-timedelta(minutes=1))
                if deadline_check == 'sql':
                    # Controlled early service clock forces the real SQL deadline
                    # guard/savepoint path without waiting five minutes.
                    async def early_clock(self):
                        return now-timedelta(minutes=2)
                    monkeypatch.setattr(PublicationSQL, 'now', early_clock)
                assert await publisher.publish(identity, ticket, 'Expired result') == {'state': 'discarded', 'reason': 'STALE_GENERATION'}
                assert row(target, 'SELECT state FROM {s}.ai_generations WHERE id=:id', id=ticket.id)[0] == 'stale'
                assert row(target, 'SELECT version,last_sequence,last_event_sequence FROM {s}.conversations WHERE id=:id', id=conversation) == (1, 0, 0)
    run(scenario())


def test_customer_append_during_new_case_binding_returns_conflict_not_access_loss(target, monkeypatch):
    mark = CaseSQL.mark_committed
    find = ConversationSQL.find

    async def scenario():
        async with publications(target) as (h, sender, publisher):
            async with h.client() as customer:
                draft = await prepared(h, sender, customer, anonymous=True)
                conversation = UUID(draft['conversation_id'])
                bound = asyncio.Event(); release = asyncio.Event(); lookup = asyncio.Event()
                watch = False

                async def hold_binding(self, current, result):
                    saved = await mark(self, current, result)
                    if current.id == UUID(draft['draft_id']):
                        bound.set(); await release.wait()
                    return saved

                async def observe_lookup(self, id, *, lock=False):
                    if watch and id == conversation and lock:
                        lookup.set()
                    return await find(self, id, lock=lock)

                monkeypatch.setattr(CaseSQL, 'mark_committed', hold_binding)
                monkeypatch.setattr(ConversationSQL, 'find', observe_lookup)
                confirmation = asyncio.create_task(confirm(h, customer, draft))
                try:
                    await asyncio.wait_for(bound.wait(), 15)
                    watch = True
                    appended = asyncio.create_task(send(h, customer, str(conversation), body='Arrived during handoff'))
                    await asyncio.wait_for(lookup.wait(), 15)
                    await asyncio.sleep(0.1)
                finally:
                    release.set()
                committed, response = await asyncio.wait_for(asyncio.gather(confirmation, appended), 15)
                assert committed.status_code == 201, committed.text
                assert response.status_code == 409, response.text
                assert row(target, 'SELECT count(*) FROM {s}.messages WHERE conversation_id=:id', id=conversation)[0] == 0
    run(scenario())
