"""Independent MAIL-01 authorization, fencing and durable-outbox probes."""
import asyncio
from dataclasses import replace
import os
from uuid import UUID, uuid4

import pytest
from psycopg import sql
from sqlalchemy import text
from test_outbound_mail import (
    target, worker_target, publication_target, handoff_target, events_target,
    files_target, lifecycle_target, commit_target, adoption_target, draft_target,
    conversation_target, auth_target, isolate_jobs, mail_app, scheduler,
    base_scheduler, case, Transport,
)
from test_staff_auth import run, row, change, insert
from test_chat_publication import command, reply
from test_chat_handoff import operator, login, meta, accept
from test_drafts import actor
from test_conversations import create
from test_job_worker import expire
from airport_assistant.modules.conversations.publication import StaffMessage
from airport_assistant.infrastructure.database.publication import PublicationSQL
from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.infrastructure.database.jobs import WorkerDatabase
from airport_assistant.modules.mail.service import OutboundMail, SendResult
from airport_assistant.modules.jobs.service import JobWorker, Outcome

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in isolated MAIL QA probes.')


async def handler_context(h, sender, target, customer, staff):
    conversation, id = await case(h, sender, customer)
    person = operator(target, id); await login(h, staff, person)
    result = await accept(h, staff, conversation, await meta(staff, conversation))
    assert result.status_code == 200, result.text
    return conversation, id


def test_staff_enqueue_fault_rolls_message_event_job_back_then_same_key_retries(target, monkeypatch):
    original = PublicationSQL.enqueue_email

    async def fail_after_enqueue(self, *args):
        await original(self, *args)
        raise RuntimeError('QA injected after email job insert')

    async def scenario():
        async with mail_app(target) as (h, sender):
            async with h.client() as customer, h.client() as staff:
                conversation, id = await handler_context(h, sender, target, customer, staff)
                state = await meta(staff, conversation); body = command(conversation, state, channel='email')
                before = row(target, 'SELECT version,last_sequence,last_event_sequence FROM {s}.conversations WHERE id=:id', id=UUID(conversation))
                key = str(uuid4())
                monkeypatch.setattr(PublicationSQL, 'enqueue_email', fail_after_enqueue)
                response = await reply(h, staff, id, body, key)
                assert response.status_code == 500
                assert row(target, 'SELECT version,last_sequence,last_event_sequence FROM {s}.conversations WHERE id=:id', id=UUID(conversation)) == before
                assert row(target, "SELECT count(*) FROM {s}.outbox_jobs WHERE case_id=:id AND kind='email_send'", id=UUID(id))[0] == 0
                assert row(target, "SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'", id=UUID(conversation))[0] == 0
                monkeypatch.setattr(PublicationSQL, 'enqueue_email', original)
                saved = await reply(h, staff, id, body, key)
                assert saved.status_code == 201 and saved.json()['channel'] == 'email', saved.text
                assert (await reply(h, staff, id, body, key)).json() == saved.json()
                assert row(target, "SELECT count(*) FROM {s}.outbox_jobs WHERE case_id=:id AND kind='email_send'", id=UUID(id))[0] == 1
    run(scenario())


def test_raw_staff_email_without_outbox_job_cannot_commit(target):
    async def scenario():
        async with mail_app(target) as (h, sender):
            async with h.client() as customer, h.client() as staff:
                conversation, id = await handler_context(h, sender, target, customer, staff)
                state = await meta(staff, conversation)
                identity = await actor(h, staff)
                repository = h.app.state.services.staff_messages._repository
                with pytest.raises(DatabaseError) as error:
                    async with repository.transaction(identity.database_context) as tx:
                        current_case = await tx.find_case(UUID(id), lock=True)
                        chat = await tx.chat(UUID(conversation), lock=True)
                        await tx.staff_message(current_case, chat, StaffMessage.model_validate(command(conversation, state, channel='email')), str(uuid4()), '0' * 64)
                assert error.value.sqlstate == '23514'
                assert (await meta(staff, conversation))['version'] == state['version']
                assert row(target, "SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'", id=UUID(conversation))[0] == 0
    run(scenario())


@pytest.mark.parametrize('revocation', ['contact', 'principal', 'participant', 'account'])
def test_authority_revoked_after_service_checks_is_rejected_at_reservation(target, revocation):
    async def scenario():
        async with mail_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer:
                _, id = await case(h, sender, customer)
                lease = await schedule.claim(uuid4(), ('case_created',), 30)
                repository = PostgresMail(schedule._database, target['schema'])
                snapshot = await repository.snapshot(lease)
                original = repository.begin

                async def revoke_then_begin(*args):
                    if revocation == 'contact':
                        change(target, 'UPDATE {s}.contacts SET verified_at=NULL WHERE id=:id', id=UUID(snapshot['contact_id']))
                    elif revocation == 'principal':
                        change(target, 'UPDATE {s}.principals SET active=false WHERE id=:id', id=UUID(snapshot['principal_id']))
                    elif revocation == 'participant':
                        change(target, 'UPDATE {s}.case_participants SET revoked_at=clock_timestamp() WHERE case_id=:id', id=UUID(id))
                    else:
                        change(target, 'UPDATE {s}.email_accounts SET active=false WHERE id=:id', id=target['mail_account'])
                    return await original(*args)

                repository.begin = revoke_then_begin
                transport = Transport()
                try:
                    assert (await OutboundMail(repository, transport, account_ref='test-account')(lease)).state == 'blocked'
                    assert not transport.sent
                    assert row(target, 'SELECT count(*) FROM {s}.email_messages WHERE job_id=:id', id=lease.id)[0] == 0
                finally:
                    if revocation == 'account':
                        change(target, 'UPDATE {s}.email_accounts SET active=true WHERE id=:id', id=target['mail_account'])
    run(scenario())


def test_completion_waiting_on_email_row_rechecks_lease_deadline(target):
    async def scenario():
        async with mail_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer:
                await case(h, sender, customer)
                lease = await schedule.claim(uuid4(), ('case_created',), 30)
                repository = PostgresMail(schedule._database, target['schema'])
                current = await repository.snapshot(lease)
                envelope = OutboundMail(repository, Transport(), account_ref='test-account')._envelope(lease, current)
                assert await repository.begin(lease, current, envelope)
                change(target, "UPDATE {s}.outbox_jobs SET leased_at=clock_timestamp()-interval '29 seconds',lease_until=clock_timestamp()+interval '1 second' WHERE id=:id", id=lease.id)
                with target['engine'].begin() as holder:
                    holder.execute(text('SELECT id FROM "' + target['schema'] + '".email_messages WHERE id=:id FOR UPDATE'), {'id': envelope.id})
                    pending = asyncio.create_task(repository.complete(lease, envelope.id, SendResult('accepted', 'qa-provider-receipt')))
                    await asyncio.sleep(1.2)
                assert await asyncio.wait_for(pending, 5) is False
                assert row(target, 'SELECT delivery_status,provider_message_id FROM {s}.email_messages WHERE id=:id', id=envelope.id) == ('sending', None)
    run(scenario())


def test_uncertain_provider_attempt_is_persisted_and_never_blindly_resent(target):
    async def scenario():
        async with mail_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer:
                _, id = await case(h, sender, customer)
                transport = Transport()

                async def uncertain(envelope):
                    transport.sent.append(envelope)
                    raise TimeoutError('Synthetic lost provider response')

                transport.send = uncertain
                handler = OutboundMail(PostgresMail(schedule._database, target['schema']), transport, account_ref='test-account')
                worker = JobWorker(schedule, {'case_created': handler})
                assert await worker.run_once() == 'unknown'
                assert row(target, 'SELECT delivery_status,provider_message_id FROM {s}.email_messages WHERE case_id=:id', id=UUID(id)) == ('delivery_unknown', None)
                assert await worker.run_once() == 'idle' and len(transport.sent) == 1
    run(scenario())


def test_cancel_after_reservation_recovers_unknown_without_second_send(target):
    async def scenario():
        async with mail_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer:
                _, id = await case(h, sender, customer)
                transport = Transport(); entered = asyncio.Event()

                async def held(envelope):
                    transport.sent.append(envelope); entered.set()
                    await asyncio.Event().wait()

                transport.send = held
                worker = JobWorker(schedule, {'case_created': OutboundMail(PostgresMail(schedule._database, target['schema']), transport, account_ref='test-account')})
                pending = asyncio.create_task(worker.run_once())
                await asyncio.wait_for(entered.wait(), 10)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, 5)
                job = row(target, 'SELECT job_id FROM {s}.email_messages WHERE case_id=:id', id=UUID(id))[0]
                expire(target, job)
                assert await worker.run_once() == 'idle' and len(transport.sent) == 1
                assert row(target, 'SELECT state FROM {s}.outbox_jobs WHERE id=:id', id=job)[0] == 'unknown'
    run(scenario())


def test_account_scoped_worker_does_not_claim_or_reap_other_tenant_jobs(target):
    async def scenario():
        with target['engine'].begin() as db:
            insert(db, target['schema'], 'email_accounts', tenant_id=target['b'], provider='resend', provider_account_ref='qa-second-account',
                   sending_identity='support-b@airport.example', reply_address='reply-b@airport.example', active=True)
        async with mail_app(target) as (h, sender), scheduler(target) as first, base_scheduler(target) as second:
            second.mail_accounts = ('qa-second-account',)
            async with h.client('b.example.test') as customer_b, h.client() as customer_a:
                _, case_b = await case(h, sender, customer_b)
                _, case_a = await case(h, sender, customer_a)
                lease_a = await first.claim(uuid4(), ('case_created',), 30)
                assert str(lease_a.case_id) == case_a
                assert row(target, 'SELECT state,attempts FROM {s}.outbox_jobs WHERE case_id=:id', id=UUID(case_b)) == ('queued', 0)
                lease_b = await second.claim(uuid4(), ('case_created',), 30)
                assert str(lease_b.case_id) == case_b
                expire(target, lease_b.id)
                assert await first.claim(uuid4(), ('case_created',), 30) is None
                assert row(target, 'SELECT state FROM {s}.outbox_jobs WHERE id=:id', id=lease_b.id)[0] == 'leased'
                assert await second.claim(uuid4(), ('case_created',), 30) is None
                assert row(target, 'SELECT state FROM {s}.outbox_jobs WHERE id=:id', id=lease_b.id)[0] == 'unknown'
                assert await first.finish(lease_a, Outcome('blocked', 'BLOCKED'))
    run(scenario())


def test_mail_rpc_role_and_fence_do_not_allow_direct_body_reads(target):
    async def scenario():
        async with mail_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer:
                await case(h, sender, customer)
                lease = await schedule.claim(uuid4(), ('case_created',), 30)
                repository = PostgresMail(schedule._database, target['schema'])
                for forged in (replace(lease, tenant_id=target['b']), replace(lease, worker_id=uuid4()),
                               replace(lease, token=uuid4()), replace(lease, generation=lease.generation+1)):
                    assert await repository.snapshot(forged) is None
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction() as db:
                        await db.execute(sql.SQL('SELECT {}.mail_snapshot(%s,%s,%s,%s,%s)').format(sql.Identifier(target['schema'])),
                                         (lease.tenant_id, lease.id, lease.worker_id, lease.token, lease.generation))
                assert error.value.sqlstate == '42501'
                for table in ('contacts', 'email_messages', 'messages'):
                    with pytest.raises(DatabaseError) as error:
                        async with schedule._database.transaction() as db:
                            await db.execute(sql.SQL('SELECT * FROM {}.{}').format(sql.Identifier(target['schema']), sql.Identifier(table)))
                    assert error.value.sqlstate == '42501'
    run(scenario())


def test_existing_ai_publication_still_uses_chat_default_after_mail_migration(target):
    async def scenario():
        async with mail_app(target) as (h, sender):
            async with h.client() as customer:
                await h.boot(customer); conversation = UUID(await create(h, customer)); identity = await actor(h, customer)
                publisher = h.app.state.services.ai_publication
                ticket = await publisher.capture(identity, conversation)
                saved = await publisher.publish(identity, ticket, 'Controlled AI regression output')
                assert saved['state'] == 'published'
                assert row(target, 'SELECT delivery_channel,author_type FROM {s}.messages WHERE id=:id', id=UUID(str(saved['message_id']))) == ('chat', 'ai')
    run(scenario())


def test_worker_startup_rejects_partial_mail_rpc_grants(target):
    function = '"' + target['schema'] + '".mail_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text)'
    role = '"' + target['worker_role'] + '"'
    with target['engine'].begin() as db:
        db.exec_driver_sql('REVOKE EXECUTE ON FUNCTION ' + function + ' FROM ' + role)
    try:
        async def scenario():
            database = WorkerDatabase(target['worker_url'], schema=target['schema'])
            try:
                with pytest.raises(DatabaseError):
                    await database.open()
            finally:
                await database.close()
        run(scenario())
    finally:
        with target['engine'].begin() as db:
            db.exec_driver_sql('GRANT EXECUTE ON FUNCTION ' + function + ' TO ' + role)
