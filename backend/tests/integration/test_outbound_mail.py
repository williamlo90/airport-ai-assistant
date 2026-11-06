"""MAIL-01: isolated Neon, real roles/HTTP API, controlled delivery transport."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4
import pytest
from test_staff_auth import run, row, change, insert
from test_job_worker import target as worker_target, publication_target, handoff_target, events_target, files_target, lifecycle_target, commit_target, adoption_target, draft_target, conversation_target, auth_target, scheduler as base_scheduler, isolate_jobs
from test_chat_publication import publications, command, reply
from test_chat_handoff import meta, accept, operator, login
from test_case_commit import prepared, confirm
from test_drafts import create_draft
from airport_assistant.database_config import load_database_settings, MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.infrastructure.database.publication import PostgresPublication
from airport_assistant.modules.conversations.publication import StaffMessages
from airport_assistant.modules.mail.service import OutboundMail, SendResult
from airport_assistant.modules.jobs.service import JobWorker

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in MAIL-01 isolated Neon.')


@pytest.fixture(scope='module')
def target(worker_target):
    t = worker_target
    role = 'mail_owner_' + hashlib.sha256(t['schema'].encode()).hexdigest()[:24]
    direct = load_database_settings(MigrationDatabaseSettings, Path(os.environ.get('DB_TEST_ENV_FILE', '.env')))
    try:
        for _ in range(2):
            assert migrate(direct.url.get_secret_value(), schema=t['schema'], runtime_role=t['role'], policy_role=t['policy'], auth_role=t['owner'],target_revision='0015_outbound_mail') == '0015_outbound_mail'
        with t['engine'].begin() as c:
            t['mail_account'] = insert(c, t['schema'], 'email_accounts', tenant_id=t['a'], provider='resend', provider_account_ref='test-account', sending_identity='support@airport.example', reply_address='reply@airport.example', active=True)
        yield t
    finally:
        assert role.startswith('mail_owner_') and t['schema'].startswith('auth03_test_')
        with t['engine'].begin() as c:
            c.exec_driver_sql('DROP SCHEMA IF EXISTS "' + t['schema'] + '" CASCADE')
            c.exec_driver_sql('DROP ROLE IF EXISTS "' + role + '"')


@asynccontextmanager
async def scheduler(target):
    async with base_scheduler(target) as value:
        value.mail_accounts=('test-account',)
        yield value


class Transport:
    def __init__(self): self.sent = []
    async def send(self, envelope):
        self.sent.append(envelope)
        return SendResult('accepted', 'provider-' + uuid4().hex)


@asynccontextmanager
async def mail_app(target):
    async with publications(target) as (h, sender, _):
        h.app.state.services = replace(h.app.state.services, staff_messages=StaffMessages(PostgresPublication(h.db, target['schema'], mail_enabled=True)))
        yield h, sender


async def case(h, sender, c):
    draft = await prepared(h, sender, c)
    draft = await create_draft(h,c,draft['conversation_id'],draft['payload'],action='request_handoff')
    result = await confirm(h, c, draft)
    assert result.status_code == 201, result.text
    return draft['conversation_id'], result.json()['case_id']


def test_notification_persists_distinct_ids_and_no_resend(target):
    async def scenario():
        async with mail_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer:
                _, id = await case(h, sender, customer)
                transport = Transport()
                repo = PostgresMail(schedule._database, target['schema'])
                handler = OutboundMail(repo, transport, account_ref='test-account')
                worker = JobWorker(schedule, {'case_created': handler})
                assert await worker.run_once() == 'succeeded'
                assert len(transport.sent) == 1
                e = transport.sent[0]
                record = row(target, 'SELECT delivery_status,provider_message_id,rfc_message_id,requested_rfc_message_id,app_message_id,job_id FROM {s}.email_messages WHERE case_id=:id', id=UUID(id))
                assert record[0] == 'accepted' and record[1].startswith('provider-') and record[2] is None and record[3] == e.rfc_message_id
                assert record[4] == e.app_message_id and e.id != e.app_message_id and str(e.id) != record[1]
                assert 'customer_report' not in e.text and 'portal' not in e.text.lower()
                assert await worker.run_once() == 'idle' and len(transport.sent) == 1
    run(scenario())


def test_revoked_recipient_and_foreign_fence_block_send(target):
    async def scenario():
        async with mail_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer:
                _, id = await case(h, sender, customer)
                lease = await schedule.claim(uuid4(), ('case_created',), 30)
                repo = PostgresMail(schedule._database, target['schema'])
                assert await repo.snapshot(replace(lease, token=uuid4())) is None
                change(target, 'UPDATE {s}.case_participants SET revoked_at=clock_timestamp() WHERE case_id=:id', id=UUID(id))
                transport = Transport()
                assert (await OutboundMail(repo, transport, account_ref='test-account')(lease)).state == 'blocked'
                assert not transport.sent and row(target, 'SELECT count(*) FROM {s}.email_messages WHERE case_id=:id', id=UUID(id))[0] == 0
    run(scenario())


def test_staff_email_is_atomic_retry_safe_and_sends_body(target):
    async def scenario():
        async with mail_app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer, h.client() as staff:
                conversation, id = await case(h, sender, customer)
                person = operator(target, id)
                await login(h, staff, person)
                assert (await accept(h, staff, conversation, await meta(staff, conversation))).status_code == 200
                state = await meta(staff, conversation)
                key = str(uuid4())
                body = command(conversation, state, channel='email', body='You can extend parking at the service desk. No payment was changed.')
                response = await reply(h, staff, id, body, key)
                assert response.status_code == 201, response.text
                assert response.json()['channel'] == 'email'
                assert (await reply(h, staff, id, body, key)).json() == response.json()
                assert row(target, "SELECT count(*) FROM {s}.outbox_jobs WHERE case_id=:id AND kind='email_send'", id=UUID(id))[0] == 1
                transport = Transport()
                handler = OutboundMail(PostgresMail(schedule._database, target['schema']), transport, account_ref='test-account')
                worker = JobWorker(schedule, {'email_send': handler})
                assert await worker.run_once() == 'succeeded'
                assert len(transport.sent) == 1 and transport.sent[0].text == body['body']
                assert transport.sent[0].app_message_id == UUID(response.json()['message_id'])
                assert row(target, "SELECT count(*) FROM {s}.conversation_events WHERE message_id=:id AND event_type='staff_message'", id=UUID(response.json()['message_id']))[0] == 1
    run(scenario())


def test_account_routing_leaves_other_tenants_jobs_for_their_worker(target):
    async def scenario():
        async with mail_app(target) as (h, sender), scheduler(target) as a, scheduler(target) as b:
            with target['engine'].begin() as c:
                insert(c, target['schema'], 'email_accounts', tenant_id=target['b'], provider='resend', provider_account_ref='account-b', sending_identity='support@second.example', reply_address='reply@second.example', active=True)
            async with h.client() as customer_a, h.client(host='b.example.test') as customer_b:
                _, aid = await case(h, sender, customer_a)
                _, bid = await case(h, sender, customer_b)
                b.mail_accounts=('account-b',)
                first = await a.claim(uuid4(), ('case_created',), 30)
                second = await b.claim(uuid4(), ('case_created',), 30)
                assert first.tenant_id == target['a'] and first.case_id == UUID(aid)
                assert second.tenant_id == target['b'] and second.case_id == UUID(bid)
                assert await a.claim(uuid4(), ('case_created',), 30) is None
                assert row(target, 'SELECT attempts,state FROM {s}.outbox_jobs WHERE id=:id', id=second.id) == (1, 'leased')
                transport=Transport()
                repo=PostgresMail(b._database,target['schema'])
                assert (await OutboundMail(repo,transport,account_ref=('test-account','account-b'))(second)).state=='succeeded'
                assert transport.sent[0].sender=='support@second.example'
                assert transport.sent[0].provider_account_ref=='account-b'
    run(scenario())
