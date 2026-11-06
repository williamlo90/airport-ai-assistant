"""Independent CHAT-01 ACL, cursor, handoff and transaction probes."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import os
from pathlib import Path
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from test_attachments import (
    target as attachment_target, lifecycle_target, commit_target, adoption_target,
    draft_target, conversation_target, auth_target, files, upload,
)
from test_staff_auth import run, row
from test_conversations import create, send
from test_drafts import payload, actor
from test_case_commit import prepared, confirm
from test_anonymous_adoption import issue, verify
from airport_assistant.database_config import MigrationDatabaseSettings, RuntimeDatabaseSettings, load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.conversation_events import PostgresConversationEvents
from airport_assistant.infrastructure.database.case_repository import CaseSQL
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.conversations.events import ConversationEvents, EventCursor
from airport_assistant.main import create_app
from airport_assistant.config import Settings
from airport_assistant.auth_config import AuthSettings

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in isolated CHAT QA probes.')


@pytest.fixture(scope='module')
def target(attachment_target):
    t = attachment_target
    async def legacy_case():
        async with files(t) as (h, sender, _):
            async with h.client() as c:
                draft = await prepared(h, sender, c, anonymous=True)
                result = await confirm(h, c, draft)
                assert result.status_code == 201, result.text
                return result.json()['case_id'], dict(c.cookies)
    t['qa_legacy_case'], t['qa_legacy_cookies'] = run(legacy_case())
    settings = load_database_settings(MigrationDatabaseSettings, Path(os.environ.get('DB_TEST_ENV_FILE', '.env')))
    for _ in range(2):
        assert migrate(settings.url.get_secret_value(), schema=t['schema'], runtime_role=t['role'],
                       policy_role=t['policy'], auth_role=t['owner'], target_revision='0011_conversation_events') == '0011_conversation_events'
    return t


@asynccontextmanager
async def chat(target):
    async with files(target) as (h, sender, scan):
        service = ConversationEvents(PostgresConversationEvents(h.db, target['schema']), EventCursor(target['key'].encode()))
        h.app = create_app(Settings(env='test', database_enabled=True, auth_enabled=True),
                           database_settings=RuntimeDatabaseSettings(DATABASE_URL=target['url']),
                           auth_settings=AuthSettings(SESSION_SECRET=target['key']),
                           services=replace(h.app.state.services, conversation_events=service))
        yield h, sender, scan


async def handoff(h, c, conversation, case=None):
    result = await c.post('/api/v1/action-drafts', headers=h.headers(c), json={
        'conversation_id': conversation, 'action_type': 'request_handoff',
        'payload': payload(category='information', service_type='general', booking_reference=None, contact_email=None),
        **({'case_id': case} if case else {}),
    })
    assert result.status_code == 201, result.text
    return result.json()


def test_existing_case_handoff_rejects_evidence_without_losing_valid_confirmation(target):
    async def scenario():
        async with chat(target) as (h, sender, _):
            async with h.client() as c:
                original = await prepared(h, sender, c)
                created = await confirm(h, c, original)
                assert created.status_code == 201, created.text
                conversation = await create(h, c)
                draft = await handoff(h, c, conversation, created.json()['case_id'])
                invalid = await c.patch('/api/v1/action-drafts/' + draft['draft_id'], headers=h.headers(c),
                                        json={'expected_version': draft['version'],
                                              'payload': {**draft['payload'], 'contact_email': 'unsupported@example.com'}})
                assert invalid.status_code == 422, invalid.text
                assert invalid.json()['error']['code'] == 'VALIDATION_ERROR'
                added = await upload(h, c, draft)
                assert added.status_code == 422, added.text
                assert row(target, 'SELECT count(*) FROM {s}.attachments WHERE draft_id=:id', id=UUID(draft['draft_id']))[0] == 0
                result = await confirm(h, c, draft)
                assert result.status_code == 201, result.text
                assert result.json()['case_id'] == created.json()['case_id']
    run(scenario())


def test_legacy_anonymous_case_handoff_uses_lock_without_case_write_privilege(target):
    async def scenario():
        async with chat(target) as (h, sender, _):
            async with h.client() as c:
                c.cookies.update(target['qa_legacy_cookies'])
                case = target['qa_legacy_case']
                assert (await c.get('/api/v1/cases/' + case)).status_code == 200
                conversation = await create(h, c)
                draft = await handoff(h, c, conversation, case)
                result = await confirm(h, c, draft)
                assert result.status_code == 201, result.text
                identity = await actor(h, c)
                repo = h.app.state.services.conversation_events._repository
                with pytest.raises(DatabaseError):
                    async with repo.transaction(identity.database_context) as tx:
                        await tx._c.execute(tx.query("UPDATE {s}.cases SET status='in_progress',version=version+1 WHERE tenant_id=%s AND id=%s"),
                                            (identity.session.tenant_id, UUID(case)))
                assert row(target, 'SELECT status,version FROM {s}.cases WHERE id=:id', id=UUID(case)) == ('new', 1)
    run(scenario())


def test_concurrent_existing_case_handoffs_link_only_one_conversation(target):
    async def scenario():
        async with chat(target) as (h, sender, _):
            async with h.client() as c:
                original = await prepared(h, sender, c)
                created = await confirm(h, c, original)
                assert created.status_code == 201, created.text
                case = created.json()['case_id']
                first = await handoff(h, c, await create(h, c), case)
                second = await handoff(h, c, await create(h, c), case)
                results = await asyncio.gather(confirm(h, c, first), confirm(h, c, second))
                assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
                assert row(target, 'SELECT count(*) FROM {s}.conversations WHERE case_id=:id', id=UUID(case))[0] == 1
                assert row(target, 'SELECT count(*) FROM {s}.conversation_events WHERE case_id=:id', id=UUID(case))[0] == 1
                assert row(target, 'SELECT count(*) FROM {s}.outbox_jobs WHERE case_id=:id', id=UUID(case))[0] == 1
    run(scenario())


def test_event_order_reconnect_dedup_and_cursor_context_isolation(target):
    async def scenario():
        async with chat(target) as (h, sender, _):
            async with h.client() as c, h.client() as stranger, h.client('b.example.test') as cross:
                await h.boot(c); await h.boot(stranger); await h.boot(cross)
                conversation = await create(h, c)
                path = '/api/v1/conversations/' + conversation + '/events'
                key = uuid4()
                assert (await send(h, c, conversation, body='First', client_id=key)).status_code == 201
                assert (await send(h, c, conversation, body='First', client_id=key)).status_code == 201
                current = await c.get('/api/v1/conversations/' + conversation)
                changed = await c.patch('/api/v1/conversations/' + conversation + '/language', headers=h.headers(c),
                                        json={'language': 'en', 'expected_version': current.json()['version']})
                assert changed.status_code == 200, changed.text
                results = await asyncio.gather(send(h, c, conversation, body='Second'), send(h, c, conversation, body='Third'))
                assert all(r.status_code == 201 for r in results)
                first = await c.get(path, params={'limit': 2})
                assert first.status_code == 200, first.text
                page = first.json()
                assert [e['sequence'] for e in page['items']] == [1, 2]
                assert [e['event_type'] for e in page['items']] == ['message_received', 'language_changed']
                assert page['has_more'] is True
                after = {'after': page['next_cursor'], 'limit': 2}
                second = await c.get(path, params=after)
                assert second.status_code == 200, second.text
                assert [e['sequence'] for e in second.json()['items']] == [3, 4]
                assert (await c.get(path, params=after)).json()['items'] == second.json()['items']
                assert (await c.get(path, params={**after, 'limit': 3})).status_code == 400
                other = await create(h, c)
                assert (await c.get('/api/v1/conversations/' + other + '/events', params=after)).status_code == 400
                for outsider in (stranger, cross):
                    assert (await outsider.get(path, params={'after': 'invalid'})).status_code == 404
                assert row(target, 'SELECT count(*) FROM {s}.conversation_events WHERE conversation_id=:id', id=UUID(conversation))[0] == 4
    run(scenario())


def test_waiting_handoff_adoption_preserves_events_and_invalidates_old_cursor(target):
    async def scenario():
        async with chat(target) as (h, sender, _):
            async with h.client() as c:
                await h.boot(c)
                conversation = await create(h, c)
                assert (await send(h, c, conversation)).status_code == 201
                draft = await handoff(h, c, conversation)
                result = await confirm(h, c, draft)
                assert result.status_code == 201, result.text
                path = '/api/v1/conversations/' + conversation + '/events'
                before = await c.get(path)
                assert before.status_code == 200, before.text
                before = before.json()
                assert before['mode'] == 'waiting_staff'
                assert [e['event_type'] for e in before['items']] == ['message_received', 'handoff_requested']
                challenge, code = await issue(h, sender, c)
                adopted = await verify(h, c, challenge, code)
                assert adopted.status_code == 200, adopted.text
                assert (await c.get(path, params={'after': before['next_cursor']})).status_code == 400
                after = await c.get(path)
                assert after.status_code == 200, after.text
                assert after.json()['items'] == before['items']
                assert after.json()['mode'] == 'waiting_staff'
                assert after.json()['generation_epoch'] == before['generation_epoch'] + 1
    run(scenario())


def test_fault_after_handoff_binding_rolls_back_link_event_and_case(target, monkeypatch):
    async def scenario():
        async with chat(target) as (h, sender, _):
            async with h.client() as c:
                await h.boot(c)
                conversation = await create(h, c)
                draft = await handoff(h, c, conversation)
                monkeypatch.setattr(CaseSQL, 'record_key', AsyncMock(side_effect=RuntimeError('QA injected failure')))
                result = await confirm(h, c, draft)
                assert result.status_code == 500
                assert row(target, 'SELECT case_id,mode,generation_epoch,last_event_sequence FROM {s}.conversations WHERE id=:id',
                           id=UUID(conversation)) == (None, 'ai', 1, 0)
                assert row(target, 'SELECT count(*) FROM {s}.conversation_events WHERE conversation_id=:id', id=UUID(conversation))[0] == 0
                assert row(target, 'SELECT count(*) FROM {s}.cases WHERE origin_draft_id=:id', id=UUID(draft['draft_id']))[0] == 0
    run(scenario())


def test_raw_runtime_cannot_insert_events_or_link_without_confirmation(target):
    async def scenario():
        async with chat(target) as (h, sender, _):
            async with h.client() as c:
                original = await prepared(h, sender, c)
                result = await confirm(h, c, original)
                assert result.status_code == 201, result.text
                conversation = await create(h, c)
                identity = await actor(h, c)
                repo = h.app.state.services.conversation_events._repository
                with pytest.raises(DatabaseError) as error:
                    async with repo.transaction(identity.database_context) as tx:
                        await tx._c.execute(tx.query("INSERT INTO {s}.conversation_events(tenant_id,conversation_id,sequence,event_type,language) VALUES(%s,%s,1,'language_changed','en')"),
                                            (identity.session.tenant_id, UUID(conversation)))
                assert error.value.sqlstate == '42501'
                with pytest.raises(DatabaseError) as error:
                    async with repo.transaction(identity.database_context) as tx:
                        await tx._c.execute(tx.query("UPDATE {s}.conversations SET case_id=%s,mode='waiting_staff',version=version+1,generation_epoch=generation_epoch+1 WHERE tenant_id=%s AND id=%s"),
                                            (UUID(result.json()['case_id']), identity.session.tenant_id, UUID(conversation)))
                assert error.value.sqlstate == '23514'
    run(scenario())
