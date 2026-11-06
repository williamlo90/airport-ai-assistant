"""Independent MAIL-03 transaction, capability and recovery probes on Neon."""
import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import UUID, uuid4

import pytest
import httpx
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_email_inbox import (
    target, delivery_target, mail_target, worker_target, publication_target,
    handoff_target, events_target, files_target, lifecycle_target, commit_target,
    adoption_target, draft_target, conversation_target, auth_target, isolate_jobs,
    scheduler, app, event, headers, post, Fetcher, KEY, send, due,
)
from test_staff_auth import run, row, change, insert
from test_job_worker import expire
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.infrastructure.database.inbox import PostgresInbox
from airport_assistant.infrastructure.database.jobs import WorkerDatabase
from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.integrations.mail.resend import ResendTransport
from airport_assistant.mail_config import WebhookConfig
from airport_assistant.modules.jobs.service import JobWorker, Outcome
from airport_assistant.modules.mail.delivery import ReconcileMail
from airport_assistant.modules.mail.inbox import WebhookIngress, InboxWorker, ReceivedContent

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in independent MAIL-03 Neon probes.')
KEY_B = 'whsec_' + base64.b64encode(b'qa-other-account-signing-key-0001').decode()


def content(recipient='reply@airport.example', body='Untrusted inbound content'):
    return ReceivedContent('stranger@example.com', recipient, 'Re: issue', body, None,
                           '<qa-inbound@customer.example>', None, None, None, 0)


def second_account(t):
    if 'qa_inbox_account_b' not in t:
        with t['engine'].begin() as db:
            t['qa_inbox_account_b'] = insert(db, t['schema'], 'email_accounts', tenant_id=t['b'], provider='resend',
                provider_account_ref='qa-account-b', sending_identity='support@other.example', reply_address='reply@other.example',
                account_alias='qa-airport-b', inbound_address='reply@other.example', active=True)
    return t['qa_inbox_account_b']


def ingress_for_two(h, t):
    second_account(t)
    h.app.state.services = replace(h.app.state.services, email_ingress=WebhookIngress(PostgresInbox(h.db, t['schema']), {
        'airport-a': WebhookConfig(account_ref='test-account', secrets=[KEY]),
        'qa-airport-b': WebhookConfig(account_ref='qa-account-b', secrets=[KEY_B]),
        'qa-wrong': WebhookConfig(account_ref='qa-account-b', secrets=[KEY]),
    }))


def inbox(t, provider):
    return row(t, 'SELECT id,processing_state,review_reason,email_message_id FROM {s}.email_inbox WHERE provider_message_id=:p ORDER BY received_at LIMIT 1', p=provider)


def due_events(t):
    change(t, "UPDATE {s}.outbox_jobs SET next_attempt_at=clock_timestamp() WHERE kind='email_event' AND state IN('queued','retry')")


def test_deferred_commit_failure_cannot_ack_and_concurrent_retries_commit_once(target):
    async def scenario():
        async with app(target) as (h, _):
            async with h.client() as a, h.client() as b, h.client() as c:
                provider = 'qa-' + uuid4().hex; raw = event(id=provider); signature = headers(raw)
                q = '"' + target['schema'] + '"'
                with target['engine'].begin() as db:
                    db.exec_driver_sql('CREATE FUNCTION ' + q + '.qa_inbox_commit_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.email_inbox_id IS NOT NULL THEN RAISE EXCEPTION \'Synthetic deferred commit fault\'; END IF; RETURN NULL; END $$')
                    db.exec_driver_sql('CREATE CONSTRAINT TRIGGER qa_inbox_commit_fault AFTER INSERT ON ' + q + '.outbox_jobs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ' + q + '.qa_inbox_commit_fault()')
                try:
                    response = await post(a, raw, signature)
                    assert response.status_code == 500, response.text
                    assert inbox(target, provider) is None
                finally:
                    with target['engine'].begin() as db:
                        db.exec_driver_sql('DROP TRIGGER qa_inbox_commit_fault ON ' + q + '.outbox_jobs')
                        db.exec_driver_sql('DROP FUNCTION ' + q + '.qa_inbox_commit_fault()')
                responses = await asyncio.gather(*(post(client, raw, signature) for client in (a, b, c)))
                assert [r.status_code for r in responses] == [202, 202, 202]
                assert row(target, 'SELECT count(*) FROM {s}.email_inbox WHERE provider_message_id=:p', p=provider)[0] == 1
                assert row(target, 'SELECT count(*) FROM {s}.outbox_jobs WHERE email_inbox_id=:i', i=inbox(target, provider)[0])[0] == 1
    run(scenario())


def test_alias_key_server_mapping_and_event_namespaces_are_account_scoped(target):
    async def scenario():
        async with app(target) as (h, _):
            ingress_for_two(h, target)
            async with h.client() as c:
                provider = 'qa-' + uuid4().hex
                payload = json.loads(event(id=provider)); payload['data'].update(tenant_id=str(target['b']), provider_account_ref='qa-account-b')
                raw = json.dumps(payload).encode(); sig_a = headers(raw); sig_b = headers(raw, sig_a['svix-id'], key=KEY_B)
                assert (await post(c, raw, sig_a, 'qa-airport-b')).status_code == 401
                assert (await post(c, raw, sig_b)).status_code == 401
                assert (await post(c, raw, sig_a, 'qa-wrong')).status_code == 503
                assert row(target, 'SELECT count(*) FROM {s}.email_inbox WHERE provider_message_id=:p', p=provider)[0] == 0
                assert (await post(c, raw, sig_a)).status_code == 202
                assert (await post(c, raw, sig_b, 'qa-airport-b')).status_code == 202
                assert row(target, 'SELECT count(*) FROM {s}.email_inbox WHERE provider_event_id=:e AND tenant_id IN(:a,:b)', e=sig_a['svix-id'], a=target['a'], b=target['b'])[0] == 2
                assert row(target, 'SELECT email_account_id FROM {s}.email_inbox WHERE tenant_id=:t AND provider_event_id=:e', t=target['a'], e=sig_a['svix-id'])[0] == target['mail_account']
                change(target, 'UPDATE {s}.email_accounts SET active=false WHERE id=:id', id=target['mail_account'])
                try:
                    assert (await post(c, raw, headers(raw))).status_code == 503
                finally:
                    change(target, 'UPDATE {s}.email_accounts SET active=true WHERE id=:id', id=target['mail_account'])
    run(scenario())


def test_inbox_lock_expiry_and_reclaimed_generation_reject_stale_content(target):
    async def scenario():
        async with app(target) as (h, _), scheduler(target) as schedule:
            async with h.client() as c:
                provider = 'qa-' + uuid4().hex; assert (await post(c, event(id=provider))).status_code == 202
                old = await schedule.claim(uuid4(), ('email_fetch',), 30); repo = PostgresInbox(schedule._database, target['schema'])
                assert old.case_id is None and old.draft_id is None
                for forged in (replace(old, tenant_id=target['b']), replace(old, worker_id=uuid4()), replace(old, token=uuid4()), replace(old, generation=old.generation+1)):
                    assert await repo.snapshot(forged) is None
                    assert await repo.complete(forged, content().record(), None) is False
                change(target, "UPDATE {s}.outbox_jobs SET leased_at=clock_timestamp()-interval '29 seconds',lease_until=clock_timestamp()+interval '1 second' WHERE id=:id", id=old.id)
                with target['engine'].begin() as holder:
                    holder.execute(text('SELECT id FROM "' + target['schema'] + '".email_inbox WHERE id=:id FOR UPDATE'), {'id': inbox(target, provider)[0]})
                    pending = asyncio.create_task(repo.complete(old, content().record(), None))
                    await asyncio.sleep(1.2)
                assert await asyncio.wait_for(pending, 5) is False
                assert inbox(target, provider)[1:] == ('received', None, None)
                fresh = await schedule.claim(uuid4(), ('email_fetch',), 30)
                assert fresh.id == old.id and fresh.generation == old.generation+1
                assert await repo.complete(old, content().record(), None) is False
                assert await repo.complete(fresh, content().record(), None)
                assert await schedule.finish(fresh, Outcome('succeeded', 'OK'))
                assert row(target, 'SELECT count(*) FROM {s}.email_messages WHERE provider_message_id=:p', p=provider)[0] == 1
    run(scenario())


def test_expiry_during_final_write_rolls_back_message_and_inbox_then_recovers(target):
    async def scenario():
        async with app(target) as (h, _), scheduler(target) as schedule:
            async with h.client() as c:
                provider = 'qa-' + uuid4().hex; assert (await post(c, event(id=provider))).status_code == 202
                lease = await schedule.claim(uuid4(), ('email_fetch',), 30); repo = PostgresInbox(schedule._database, target['schema'])
                q = '"' + target['schema'] + '"'
                with target['engine'].begin() as db:
                    db.exec_driver_sql('CREATE FUNCTION ' + q + '.qa_slow_inbox() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(1.3); RETURN NEW; END $$')
                    db.exec_driver_sql('CREATE TRIGGER qa_slow_inbox BEFORE UPDATE ON ' + q + '.email_inbox FOR EACH ROW EXECUTE FUNCTION ' + q + '.qa_slow_inbox()')
                try:
                    change(target, "UPDATE {s}.outbox_jobs SET leased_at=clock_timestamp()-interval '29 seconds',lease_until=clock_timestamp()+interval '1 second' WHERE id=:id", id=lease.id)
                    with pytest.raises(DatabaseError) as error:
                        await repo.complete(lease, content().record(), None)
                    assert error.value.sqlstate == 'Z0017'
                    assert inbox(target, provider)[1:] == ('received', None, None)
                    assert row(target, 'SELECT count(*) FROM {s}.email_messages WHERE provider_message_id=:p', p=provider)[0] == 0
                finally:
                    with target['engine'].begin() as db:
                        db.exec_driver_sql('DROP TRIGGER qa_slow_inbox ON ' + q + '.email_inbox')
                        db.exec_driver_sql('DROP FUNCTION ' + q + '.qa_slow_inbox()')
                fresh = await schedule.claim(uuid4(), ('email_fetch',), 30)
                assert fresh.generation == lease.generation+1
                assert await repo.complete(fresh, content().record(), None)
                assert await schedule.finish(fresh, Outcome('succeeded', 'OK'))
    run(scenario())


@pytest.mark.parametrize('changed', [False, True])
def test_parallel_events_materialize_once_and_network_holds_no_database_locks(target, changed):
    async def scenario():
        async with app(target) as (h, _), scheduler(target) as a, scheduler(target) as b:
            async with h.client() as c:
                provider = 'qa-' + uuid4().hex; raw = event(id=provider)
                assert (await post(c, raw)).status_code == 202 and (await post(c, raw)).status_code == 202
                ready = asyncio.Event(); transport = Fetcher(); entered = []

                async def receive(snap):
                    entered.append(snap)
                    position = len(entered)
                    if position == 2:ready.set()
                    await asyncio.wait_for(ready.wait(), 5)
                    with target['engine'].begin() as db:
                        db.execute(text('SELECT j.id FROM "' + target['schema'] + '".outbox_jobs j JOIN "' + target['schema'] + '".email_inbox i ON i.tenant_id=j.tenant_id AND i.id=j.email_inbox_id WHERE i.provider_message_id=:p FOR UPDATE OF j,i NOWAIT'), {'p': provider})
                    return content(body='Changed content' if changed and position == 2 else 'Original content')

                transport.receive = receive
                workers = [JobWorker(s, {'email_fetch': InboxWorker(PostgresInbox(s._database, target['schema']), PostgresMail(s._database, target['schema']), transport, accounts=('test-account',))}) for s in (a, b)]
                assert await asyncio.gather(*(w.run_once() for w in workers)) == ['succeeded', 'succeeded']
                assert len(entered) == 2
                assert row(target, 'SELECT count(*) FROM {s}.email_messages WHERE provider_message_id=:p', p=provider)[0] == 1
                assert row(target, "SELECT count(*) FILTER(WHERE processing_state='processed'),count(*) FILTER(WHERE review_reason='CONTENT_CHANGED') FROM {s}.email_inbox WHERE provider_message_id=:p", p=provider) == ((1, 1) if changed else (2, 0))
    run(scenario())


def test_lost_body_commit_ack_and_new_event_restart_do_not_refetch_or_publish(target):
    async def scenario():
        async with app(target) as (h, _), scheduler(target) as schedule:
            async with h.client() as c:
                before = row(target, 'SELECT (SELECT count(*) FROM {s}.cases),(SELECT count(*) FROM {s}.messages)')
                provider = 'qa-' + uuid4().hex; raw = event(id=provider); assert (await post(c, raw)).status_code == 202
                repo = PostgresInbox(schedule._database, target['schema']); transport = Fetcher(); original = repo.complete

                async def lost_ack(*args):
                    assert await original(*args)
                    raise TimeoutError('Synthetic response lost after content commit')

                repo.complete = lost_ack
                assert await JobWorker(schedule, {'email_fetch': InboxWorker(repo, PostgresMail(schedule._database, target['schema']), transport, accounts=('test-account',))}).run_once() == 'unknown'
                message = inbox(target, provider)[3]
                assert inbox(target, provider)[1:3] == ('processed', None)
                assert (await post(c, raw)).status_code == 202
                async with scheduler(target) as restarted:
                    fresh_repo = PostgresInbox(restarted._database, target['schema'])
                    assert await JobWorker(restarted, {'email_fetch': InboxWorker(fresh_repo, PostgresMail(restarted._database, target['schema']), transport, accounts=('test-account',))}).run_once() == 'succeeded'
                assert len(transport.fetched) == 1
                assert row(target, 'SELECT count(*) FROM {s}.email_messages WHERE provider_message_id=:p', p=provider)[0] == 1
                assert row(target, 'SELECT bool_and(email_message_id=:m) FROM {s}.email_inbox WHERE provider_message_id=:p', m=message, p=provider)[0]
                assert row(target, 'SELECT (SELECT count(*) FROM {s}.cases),(SELECT count(*) FROM {s}.messages)') == before
    run(scenario())


def test_new_delivery_event_after_terminal_poll_and_unmatched_receipt_review(target):
    async def scenario():
        async with app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                transport = Fetcher(); _, case_id, mail = await send(h, sender, target, c, schedule, transport)
                receipt = row(target, 'SELECT provider_message_id FROM {s}.email_messages WHERE case_id=:c', c=UUID(case_id))[0]
                due(target, case_id)
                assert await JobWorker(schedule, {'email_reconcile': ReconcileMail(mail, transport, account_refs=('test-account',))}).run_once() == 'succeeded'
                worker = JobWorker(schedule, {'email_event': InboxWorker(PostgresInbox(schedule._database, target['schema']), mail, transport, accounts=('test-account',))})
                for kind, stamp in (('email.bounced', datetime.now(timezone.utc)), ('email.sent', datetime.now(timezone.utc)-timedelta(seconds=30))):
                    assert (await post(c, event(kind, receipt, stamp=stamp.isoformat()))).status_code == 202
                    due_events(target); assert await worker.run_once() == 'succeeded'
                assert row(target, 'SELECT delivery_status,review_reason FROM {s}.email_messages WHERE case_id=:c', c=UUID(case_id)) == ('delivered', 'CONFLICTING_EVIDENCE')
                assert row(target, "SELECT state FROM {s}.outbox_jobs WHERE case_id=:c AND kind='email_reconcile'", c=UUID(case_id))[0] == 'succeeded'
                assert len(transport.sent) == 1 and len(transport.lookups) == 3
                unknown = 'unknown-' + uuid4().hex
                assert (await post(c, event('email.delivered', unknown))).status_code == 202
                change(target, "UPDATE {s}.outbox_jobs SET max_attempts=1,next_attempt_at=clock_timestamp() WHERE kind='email_event' AND state='queued'")
                assert await worker.run_once() == 'succeeded'
                assert inbox(target, unknown)[1:] == ('review', 'UNMATCHED_RECEIPT', None)
                assert len(transport.lookups) == 3
    run(scenario())


def test_account_subset_reaper_leaves_other_account_lease_for_its_owner(target):
    async def scenario():
        async with app(target) as (h, _), scheduler(target) as a, scheduler(target) as b:
            ingress_for_two(h, target); b.mail_accounts = ('qa-account-b',)
            async with h.client() as c:
                provider_a = 'qa-' + uuid4().hex; provider_b = 'qa-' + uuid4().hex
                raw_b = event(id=provider_b)
                assert (await post(c, event(id=provider_a))).status_code == 202
                assert (await post(c, raw_b, headers(raw_b, key=KEY_B), 'qa-airport-b')).status_code == 202
                old_b = await b.claim(uuid4(), ('email_fetch',), 30); assert old_b.tenant_id == target['b']
                expire(target, old_b.id)
                lease_a = await a.claim(uuid4(), ('email_fetch',), 30); assert lease_a.tenant_id == target['a']
                assert row(target, 'SELECT state,attempts,lease_generation FROM {s}.outbox_jobs WHERE id=:id', id=old_b.id) == ('leased', 1, old_b.generation)
                repo_a = PostgresInbox(a._database, target['schema'])
                assert await repo_a.complete(lease_a, content().record(), None)
                assert await a.finish(lease_a, Outcome('succeeded', 'OK'))
                new_b = await b.claim(uuid4(), ('email_fetch',), 30)
                assert new_b.id == old_b.id and new_b.generation == old_b.generation+1
                repo_b = PostgresInbox(b._database, target['schema'])
                assert await repo_b.complete(old_b, content('reply@other.example').record(), None) is False
                assert await repo_b.complete(new_b, content('reply@other.example').record(), None)
                assert await b.finish(new_b, Outcome('succeeded', 'OK'))
                assert inbox(target, provider_b)[1] == 'processed'
    run(scenario())


def test_raw_inbox_privileges_and_incomplete_worker_capabilities_fail_closed(target):
    async def scenario():
        async with app(target) as (h, _), scheduler(target) as schedule:
            async with h.client() as c:
                assert (await post(c, event())).status_code == 202
                for db, statements in (
                    (h.db, ('SELECT * FROM {s}.email_inbox', 'DELETE FROM {s}.email_inbox', 'SELECT {s}.email_inbox_snapshot(NULL,NULL,NULL,NULL,NULL)')),
                    (schedule._database, ('SELECT * FROM {s}.email_inbox', "UPDATE {s}.email_inbox SET processing_state='processed'", 'SELECT {s}.email_webhook_accept(NULL,NULL,NULL,NULL)')),
                ):
                    for statement in statements:
                        with pytest.raises(DatabaseError) as error:
                            async with db.transaction() as connection:
                                await connection.execute(sql.SQL(statement).format(s=sql.Identifier(target['schema'])))
                        assert error.value.sqlstate == '42501'
        function = '"' + target['schema'] + '".email_inbox_complete(uuid,uuid,uuid,uuid,bigint,jsonb,text)'
        role = '"' + target['worker_role'] + '"'
        with target['engine'].begin() as db:db.exec_driver_sql('REVOKE EXECUTE ON FUNCTION ' + function + ' FROM ' + role)
        try:
            database = WorkerDatabase(target['worker_url'], schema=target['schema']); database.mail_inbox_required = True
            try:
                with pytest.raises(DatabaseError):await database.open()
            finally:await database.close()
        finally:
            with target['engine'].begin() as db:db.exec_driver_sql('GRANT EXECUTE ON FUNCTION ' + function + ' TO ' + role)
    run(scenario())


def test_pending_inbound_and_existing_outbound_shapes_remain_database_enforced(target):
    async def scenario():
        async with app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                provider = 'qa-' + uuid4().hex; assert (await post(c, event(id=provider))).status_code == 202
                repo = PostgresInbox(schedule._database, target['schema']); lease = await schedule.claim(uuid4(), ('email_fetch',), 30)
                assert await repo.complete(lease, content().record(), None)
                assert await schedule.finish(lease, Outcome('succeeded', 'OK'))
                _, case_id, _ = await send(h, sender, target, c, schedule, Fetcher())
                for fragment, params in (("verification_state='verified'", {}), ("case_id=:c", {'c': UUID(case_id)}), ("delivery_status='accepted'", {}), ("subject=:s", {'s': 'bad\r\nsubject'})):
                    with pytest.raises(IntegrityError):
                        change(target, 'UPDATE {s}.email_messages SET ' + fragment + ' WHERE provider_message_id=:p', p=provider, **params)
                for fragment, params in (("case_id=NULL", {}), ("delivery_status=NULL", {}), ("subject=:s", {'s': 'bad\r\nsubject'}), ("body=:b", {'b': 'x'*8001})):
                    with pytest.raises(IntegrityError):
                        change(target, 'UPDATE {s}.email_messages SET ' + fragment + ' WHERE case_id=:c', c=UUID(case_id), **params)
                assert row(target, 'SELECT verification_state,case_id,app_message_id FROM {s}.email_messages WHERE provider_message_id=:p', p=provider) == ('pending', None, None)
    run(scenario())


@pytest.mark.parametrize('tracking', ['opened', 'clicked'])
def test_real_adapter_callback_after_tracking_commits_verified_historical_delivery(target, tracking):
    async def scenario():
        async with app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as c:
                outgoing = Fetcher(); _, case_id, mail = await send(h, sender, target, c, schedule, outgoing)
                receipt, from_address, to_address = row(target,
                    'SELECT provider_message_id,sender_address,recipient_address FROM {s}.email_messages WHERE case_id=:c', c=UUID(case_id))
                provider_response = dict(object='email', id=receipt, to=[to_address], last_event=tracking,
                    message_id='<actual-context@provider.example>', **{'from': from_address})
                requests = []
                def response(request):
                    requests.append(request)
                    assert request.method == 'GET' and str(request.url) == 'https://api.resend.com/emails/' + receipt
                    return httpx.Response(200, json=provider_response)
                raw = event('email.delivered', receipt)
                assert (await post(c, raw)).status_code == 202
                due_events(target)
                async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                    transport = ResendTransport({'test-account': 'synthetic-key'}, client=client)
                    worker = JobWorker(schedule, {'email_event': InboxWorker(PostgresInbox(schedule._database, target['schema']), mail, transport, accounts=('test-account',))})
                    assert await worker.run_once() == 'succeeded'
                assert inbox(target, receipt)[1:3] == ('processed', None)
                assert row(target, 'SELECT delivery_status,rfc_message_id,needs_review FROM {s}.email_messages WHERE case_id=:c', c=UUID(case_id)) == ('delivered', '<actual-context@provider.example>', False)
                assert row(target, 'SELECT state,provider_event_at FROM {s}.email_delivery_events WHERE case_id=:c', c=UUID(case_id)) == ('delivered', datetime.fromisoformat(json.loads(raw)['created_at']))
                assert len(outgoing.sent) == 1 and len(requests) == 1
    run(scenario())
