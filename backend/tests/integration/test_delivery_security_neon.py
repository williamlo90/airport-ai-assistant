"""Independent MAIL-02 capability, concurrency, status and evidence probes."""
import asyncio
from dataclasses import replace
from uuid import UUID, uuid4
import os

import pytest
from psycopg import sql
from sqlalchemy import text
from test_mail_delivery import (
    target, mail_target, worker_target, publication_target, handoff_target,
    events_target, files_target, lifecycle_target, commit_target, adoption_target,
    draft_target, conversation_target, auth_target, isolate_jobs, app, scheduler,
    case, due, Lookup, send as sent,
)
from test_staff_auth import run, row, change, seed
from test_chat_handoff import operator, login
from test_job_worker import expire
from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.infrastructure.database.jobs import WorkerDatabase
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.mail.service import OutboundMail, SendResult
from airport_assistant.modules.mail.delivery import ReconcileMail, DeliveryEvidence
from airport_assistant.modules.jobs.service import JobWorker, Outcome

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in isolated MAIL-02 QA probes.')


def test_child_lease_is_not_authority_for_other_email_or_receipt(target):
    async def scenario():
        async with app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as first, h.client() as second:
                transport = Lookup()
                _, case_a, repo = await sent(h, sender, target, first, schedule, transport)
                _, case_b, _ = await sent(h, sender, target, second, schedule, transport)
                due(target, case_a)
                lease = await schedule.claim(uuid4(), ('email_reconcile',), 30)
                snapshot = await repo.delivery_snapshot(lease)
                other = row(target, 'SELECT id,delivery_version,provider_message_id FROM {s}.email_messages WHERE case_id=:id', id=UUID(case_b))
                wrong = {**snapshot, 'id': str(other[0]), 'delivery_version': other[1]}
                assert await repo.observe(lease, wrong, DeliveryEvidence(other[2], 'delivered')) is False
                for forged in (replace(lease, tenant_id=target['b']), replace(lease, worker_id=uuid4()),
                               replace(lease, token=uuid4()), replace(lease, generation=lease.generation+1)):
                    assert await repo.delivery_snapshot(forged) is None
                with pytest.raises(DatabaseError) as error:
                    await repo.observe(lease, snapshot, DeliveryEvidence('unrelated-provider-receipt', 'delivered'))
                assert error.value.sqlstate == '22023'
                assert row(target, 'SELECT count(*) FROM {s}.email_delivery_events')[0] == 0
                assert row(target, 'SELECT delivery_status FROM {s}.email_messages WHERE id=:id', id=other[0])[0] == 'accepted'
    run(scenario())


def test_expiry_while_observe_waits_and_reclaimed_generation_reject_old_writer(target):
    async def scenario():
        async with app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer:
                transport = Lookup(); _, id, repo = await sent(h, sender, target, customer, schedule, transport)
                due(target, id); old = await schedule.claim(uuid4(), ('email_reconcile',), 30)
                snapshot = await repo.delivery_snapshot(old)
                evidence = DeliveryEvidence(snapshot['provider_message_id'], 'delivered')
                change(target, "UPDATE {s}.outbox_jobs SET leased_at=clock_timestamp()-interval '29 seconds',lease_until=clock_timestamp()+interval '1 second' WHERE id=:id", id=old.id)
                with target['engine'].begin() as holder:
                    holder.execute(text('SELECT id FROM "' + target['schema'] + '".email_messages WHERE id=:id FOR UPDATE'), {'id': UUID(snapshot['id'])})
                    pending = asyncio.create_task(repo.observe(old, snapshot, evidence))
                    await asyncio.sleep(1.2)
                assert await asyncio.wait_for(pending, 5) is False
                assert row(target, 'SELECT delivery_status FROM {s}.email_messages WHERE id=:id', id=UUID(snapshot['id']))[0] == 'accepted'
                fresh = await schedule.claim(uuid4(), ('email_reconcile',), 30)
                assert fresh.id == old.id and fresh.generation == old.generation + 1
                assert await repo.observe(old, snapshot, evidence) is False
                current = await repo.delivery_snapshot(fresh)
                assert await repo.observe(fresh, current, evidence) is True
                assert await schedule.finish(fresh, Outcome('succeeded', 'OK'))
                assert row(target, 'SELECT count(*) FROM {s}.email_delivery_events WHERE case_id=:id', id=UUID(id))[0] == 1
                assert len(transport.sent) == 1
    run(scenario())


def test_inflight_post_heartbeat_does_not_spend_single_reconcile_attempt(target):
    async def scenario():
        async with app(target) as (h, sender), scheduler(target) as parent_schedule, scheduler(target) as child_schedule:
            async with h.client() as customer:
                _, id = await case(h, sender, customer)
                entered = asyncio.Event(); release = asyncio.Event(); transport = Lookup()

                async def hold_post(envelope):
                    transport.sent.append(envelope); entered.set()
                    await release.wait()
                    return SendResult('accepted', 'held-provider-' + uuid4().hex)

                transport.send = hold_post
                repo = PostgresMail(parent_schedule._database, target['schema'])
                parent = asyncio.create_task(JobWorker(parent_schedule, {'case_created': OutboundMail(repo, transport, account_ref='test-account')}, lease_seconds=3).run_once())
                try:
                    await asyncio.wait_for(entered.wait(), 10)
                    due(target, id, max_attempts=1)
                    for _ in range(3):
                        assert await child_schedule.claim(uuid4(), ('email_reconcile',), 30) is None
                        await asyncio.sleep(1.1)
                    assert row(target, "SELECT state,attempts FROM {s}.outbox_jobs WHERE case_id=:id AND kind='email_reconcile'", id=UUID(id)) == ('queued', 0)
                finally:
                    release.set()
                assert await asyncio.wait_for(parent, 10) == 'succeeded'
                child_repo = PostgresMail(child_schedule._database, target['schema'])
                result = await JobWorker(child_schedule, {'email_reconcile': ReconcileMail(child_repo, transport, account_refs=('test-account',))}).run_once()
                assert result == 'succeeded'
                assert len(transport.sent) == 1 and len(transport.lookups) == 1
                assert row(target, 'SELECT delivery_status,needs_review FROM {s}.email_messages WHERE case_id=:id', id=UUID(id)) == ('delivered', False)
    run(scenario())


def test_queued_status_is_nonnull_and_acl_is_current_without_private_fields(target):
    async def scenario():
        async with app(target) as (h, sender):
            async with h.client() as customer, h.client() as staff, h.client() as admin, h.client() as outsider:
                _, id = await case(h, sender, customer)
                person = operator(target, id); await login(h, staff, person)
                await login(h, admin, operator(target, id, ('admin',)))
                await login(h, outsider, seed(target))
                path = '/api/v1/staff/cases/' + id + '/email-deliveries'
                response = await staff.get(path)
                assert response.status_code == 200, response.text
                item = response.json()['items'][0]
                assert item['delivery_status'] == 'queued' and item['needs_review'] is False
                assert item['review_reason'] is None and response.headers['cache-control'] == 'no-store'
                assert not {'body', 'recipient', 'sender', 'provider_message_id', 'delivery_key'} & set(item)
                assert (await customer.get(path)).status_code == 401
                assert (await admin.get(path)).status_code == 404
                assert (await outsider.get(path)).status_code == 404
                change(target, 'UPDATE {s}.staff_memberships SET active=false WHERE id=:id', id=person['case_membership'])
                assert (await staff.get(path)).status_code == 404
    run(scenario())


def test_identical_accepted_proof_dedupes_but_final_review_is_not_lost(target):
    async def scenario():
        async with app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer, h.client() as staff:
                transport = Lookup('accepted'); _, id, repo = await sent(h, sender, target, customer, schedule, transport)
                await login(h, staff, operator(target, id))
                worker = JobWorker(schedule, {'email_reconcile': ReconcileMail(repo, transport, account_refs=('test-account',))})
                for _ in range(2):
                    due(target, id, max_attempts=3)
                    assert await worker.run_once() == 'retry'
                assert row(target, 'SELECT count(*) FROM {s}.email_delivery_events WHERE case_id=:id', id=UUID(id))[0] == 1
                due(target, id, max_attempts=3)
                assert await worker.run_once() == 'succeeded'
                assert row(target, 'SELECT count(*) FROM {s}.email_delivery_events WHERE case_id=:id', id=UUID(id))[0] == 2
                response = await staff.get('/api/v1/staff/cases/' + id + '/email-deliveries')
                assert response.status_code == 200, response.text
                assert response.json()['items'][0]['needs_review'] is True
                assert response.json()['items'][0]['review_reason'] == 'PENDING_DELIVERY'
                assert len(transport.sent) == 1 and len(transport.lookups) == 3
    run(scenario())


def test_api_cannot_forge_delivery_and_worker_cannot_rewrite_event_ledger(target):
    async def scenario():
        async with app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer:
                transport = Lookup(); _, id, repo = await sent(h, sender, target, customer, schedule, transport)
                due(target, id); lease = await schedule.claim(uuid4(), ('email_reconcile',), 30)
                snapshot = await repo.delivery_snapshot(lease)
                assert await repo.observe(lease, snapshot, DeliveryEvidence(snapshot['provider_message_id'], 'delivered'))
                for operation in ('snapshot_rpc', 'email_update', 'event_insert'):
                    with pytest.raises(DatabaseError) as error:
                        async with h.db.transaction() as db:
                            schema = sql.Identifier(target['schema'])
                            if operation == 'snapshot_rpc':
                                await db.execute(sql.SQL('SELECT {}.mail_delivery_snapshot(%s,%s,%s,%s,%s)').format(schema),
                                                 (lease.tenant_id, lease.id, lease.worker_id, lease.token, lease.generation))
                            elif operation == 'email_update':
                                await db.execute(sql.SQL("UPDATE {}.email_messages SET delivery_status='delivered' WHERE id=%s").format(schema), (UUID(snapshot['id']),))
                            else:
                                await db.execute(sql.SQL("INSERT INTO {}.email_delivery_events(tenant_id,case_id,email_message_id,observation_key,state) VALUES(%s,%s,%s,%s,'delivered')").format(schema),
                                                 (lease.tenant_id, lease.case_id, UUID(snapshot['id']), '0' * 64))
                    assert error.value.sqlstate == '42501'
                for verb in ('UPDATE', 'DELETE'):
                    with pytest.raises(DatabaseError) as error:
                        async with schedule._database.transaction() as db:
                            statement = "UPDATE {s}.email_delivery_events SET state='failed'" if verb == 'UPDATE' else 'DELETE FROM {s}.email_delivery_events'
                            await db.execute(sql.SQL(statement).format(s=sql.Identifier(target['schema'])))
                    assert error.value.sqlstate == '42501'
                assert row(target, 'SELECT count(*) FROM {s}.email_delivery_events WHERE case_id=:id', id=UUID(id))[0] == 1
    run(scenario())


@pytest.mark.parametrize('provider_state,review', [('delivered', False), ('accepted', True)])
def test_lost_observation_ack_uses_committed_evidence_and_exposes_only_needed_review(target, provider_state, review):
    async def scenario():
        async with app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer, h.client() as staff:
                transport = Lookup(provider_state); _, id, repo = await sent(h, sender, target, customer, schedule, transport)
                await login(h, staff, operator(target, id)); due(target, id)
                original = repo.observe

                async def lost_ack(*args):
                    assert await original(*args)
                    raise TimeoutError('Synthetic response lost after observation commit')

                repo.observe = lost_ack
                worker = JobWorker(schedule, {'email_reconcile': ReconcileMail(repo, transport, account_refs=('test-account',))})
                assert await worker.run_once() == 'unknown'
                response = await staff.get('/api/v1/staff/cases/' + id + '/email-deliveries')
                assert response.status_code == 200, response.text
                assert response.json()['items'][0]['delivery_status'] == provider_state
                assert response.json()['items'][0]['needs_review'] is review
                assert len(transport.sent) == 1 and len(transport.lookups) == 1
    run(scenario())


def test_partial_delivery_rpc_grant_is_rejected_at_worker_startup(target):
    function = '"' + target['schema'] + '".mail_delivery_observe(uuid,uuid,uuid,uuid,bigint,uuid,bigint,jsonb,text)'
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


def test_duplicate_observations_refresh_metadata_and_preserve_unresolved_review(target):
    async def scenario():
        async with app(target) as (h, sender), scheduler(target) as schedule:
            async with h.client() as customer:
                transport = Lookup(); _, id, repo = await sent(h, sender, target, customer, schedule, transport)
                due(target, id); lease = await schedule.claim(uuid4(), ('email_reconcile',), 30)

                async def observe(state, reason=None):
                    snapshot = await repo.delivery_snapshot(lease)
                    assert await repo.observe(lease, snapshot, DeliveryEvidence(snapshot['provider_message_id'], state), reason)
                    return row(target, 'SELECT delivery_status,delivery_version,last_checked_at,needs_review,review_reason FROM {s}.email_messages WHERE case_id=:id', id=UUID(id))

                first = await observe('accepted')
                duplicate = await observe('accepted')
                assert duplicate[1] == first[1] + 1 and duplicate[2] > first[2]
                assert row(target, 'SELECT count(*) FROM {s}.email_delivery_events WHERE case_id=:id', id=UUID(id))[0] == 1
                pending = await observe('accepted', 'PENDING_DELIVERY')
                repeated = await observe('accepted')
                assert pending[3:] == repeated[3:] == (True, 'PENDING_DELIVERY')
                assert repeated[2] > pending[2]
                assert row(target, 'SELECT count(*) FROM {s}.email_delivery_events WHERE case_id=:id', id=UUID(id))[0] == 2
                delivered = await observe('delivered')
                assert delivered[0] == 'delivered' and delivered[3:] == (False, None)
                conflict = await observe('bounced')
                replay = await observe('delivered')
                assert conflict[0] == replay[0] == 'delivered'
                assert conflict[3:] == replay[3:] == (True, 'CONFLICTING_EVIDENCE')
                assert row(target, 'SELECT count(*) FROM {s}.email_delivery_events WHERE case_id=:id', id=UUID(id))[0] == 4
                assert len(transport.sent) == 1
    run(scenario())
