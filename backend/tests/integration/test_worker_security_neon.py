"""Independent JOB-01 privilege, post-lock deadline and cancellation probes."""
import asyncio
from dataclasses import replace
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from psycopg import sql
from psycopg.types.json import Jsonb
from test_job_worker import (
    target, publication_target, handoff_target, events_target, files_target,
    lifecycle_target, commit_target, adoption_target, draft_target,
    conversation_target, auth_target, isolate_jobs, publications,
    scheduler, enqueue, expire,
)
from test_staff_auth import run, row, change
from test_drafts import actor
from airport_assistant.infrastructure.database.jobs import WorkerDatabase
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.jobs.service import JobWorker, Outcome

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS') != '1', reason='Opt-in isolated JOB QA probes.')


@pytest.mark.parametrize('grant_kind', ['contacts', 'message_column', 'schema_create', 'foreign_definer'])
def test_worker_startup_rejects_previously_misgranted_credentials(target, grant_kind):
    schema = '"' + target['schema'] + '"'
    role = '"' + target['worker_role'] + '"'
    commands = {
        'contacts': ('SELECT ON ' + schema + '.contacts',),
        'message_column': ('SELECT(body) ON ' + schema + '.messages',),
        'schema_create': ('CREATE ON SCHEMA ' + schema,),
        'foreign_definer': ('EXECUTE ON FUNCTION ' + schema + '.case_staff_scope(uuid,uuid,boolean)',),
    }
    privilege = commands[grant_kind][0]
    with target['engine'].begin() as db:
        db.exec_driver_sql('GRANT ' + privilege + ' TO ' + role)
    try:
        async def scenario():
            db = WorkerDatabase(target['worker_url'], schema=target['schema'])
            try:
                with pytest.raises(DatabaseError):
                    await db.open()
            finally:
                await db.close()
        run(scenario())
    finally:
        with target['engine'].begin() as db:
            db.exec_driver_sql('REVOKE ' + privilege + ' FROM ' + role)


@pytest.mark.parametrize('operation', ['renew', 'finish'])
def test_lease_expiring_while_waiting_for_row_lock_is_not_accepted(target, operation):
    async def scenario():
        async with publications(target) as (h, sender, _), scheduler(target) as worker:
            async with h.client() as customer:
                id = await enqueue(h, sender, customer, target)
                lease = await worker.claim(uuid4(), ('case_created',), 30)
                assert lease.id == id
                change(target, "UPDATE {s}.outbox_jobs SET leased_at=clock_timestamp()-interval '29 seconds',lease_until=clock_timestamp()+interval '1 second' WHERE id=:id", id=id)
                with target['engine'].begin() as holder:
                    holder.execute(text('SELECT id FROM "' + target['schema'] + '".outbox_jobs WHERE id=:id FOR UPDATE'), {'id': id})
                    pending = asyncio.create_task(worker.renew(lease, 30) if operation == 'renew' else worker.finish(lease, Outcome('succeeded', 'OK')))
                    await asyncio.sleep(1.2)
                assert await asyncio.wait_for(pending, 5) is False
                assert row(target, 'SELECT state,result_code FROM {s}.outbox_jobs WHERE id=:id', id=id) == ('leased', None)
    run(scenario())


def test_tenant_worker_token_and_generation_fences_are_all_required(target):
    async def scenario():
        async with publications(target) as (h, sender, _), scheduler(target) as worker:
            async with h.client() as customer:
                id = await enqueue(h, sender, customer, target)
                lease = await worker.claim(uuid4(), ('case_created',), 300)
                for altered in (replace(lease, tenant_id=target['b']), replace(lease, worker_id=uuid4()),
                                replace(lease, token=uuid4()), replace(lease, generation=lease.generation+1)):
                    assert await worker.renew(altered, 30) is False
                    assert await worker.finish(altered, Outcome('succeeded', 'OK')) is False
                assert row(target, 'SELECT state,attempts FROM {s}.outbox_jobs WHERE id=:id', id=id) == ('leased', 1)
                assert await worker.finish(lease, Outcome('blocked', 'BLOCKED')) is True
    run(scenario())


def test_cancelled_real_worker_recovers_uncertain_job_without_second_execution(target):
    async def scenario():
        async with publications(target) as (h, sender, _), scheduler(target) as worker:
            async with h.client() as customer:
                id = await enqueue(h, sender, customer, target)
                started = asyncio.Event(); stopped = asyncio.Event(); calls = 0

                async def work(lease):
                    nonlocal calls
                    calls += 1; started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        stopped.set()

                task = asyncio.create_task(JobWorker(worker, {'case_created': work}).run_once())
                await asyncio.wait_for(started.wait(), 5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                assert stopped.is_set() and calls == 1
                assert row(target, 'SELECT state FROM {s}.outbox_jobs WHERE id=:id', id=id)[0] == 'leased'
                expire(target, id)
                async with scheduler(target) as restarted:
                    assert await restarted.claim(uuid4(), ('case_created',), 30) is None
                assert row(target, 'SELECT state,result_code,attempts FROM {s}.outbox_jobs WHERE id=:id', id=id) == ('unknown', 'LEASE_EXPIRED', 1)
                assert calls == 1
    run(scenario())


def test_api_runtime_cannot_choose_safe_replay_or_mutate_scheduler_state(target):
    async def scenario():
        async with publications(target) as (h, sender, _):
            async with h.client() as customer:
                id = await enqueue(h, sender, customer, target)
                identity = await actor(h, customer)
                case, draft = row(target, 'SELECT case_id,action_draft_id FROM {s}.outbox_jobs WHERE id=:id', id=id)
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction(identity.database_context) as db:
                        await db.execute(sql.SQL("INSERT INTO {}.outbox_jobs(tenant_id,case_id,action_draft_id,dedupe_key,payload,replay_policy) VALUES(%s,%s,%s,%s,%s,'safe')").format(sql.Identifier(target['schema'])),
                                         (identity.session.tenant_id, case, draft, 'qa-forbidden-' + uuid4().hex,
                                          Jsonb({'case_id': str(case), 'draft_id': str(draft)})))
                assert error.value.sqlstate == '42501'
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction(identity.database_context) as db:
                        await db.execute(sql.SQL("UPDATE {}.outbox_jobs SET state='succeeded',completed_at=clock_timestamp() WHERE id=%s").format(sql.Identifier(target['schema'])), (id,))
                assert error.value.sqlstate == '42501'
                assert row(target, 'SELECT state,replay_policy,attempts FROM {s}.outbox_jobs WHERE id=:id', id=id) == ('queued', 'reconcile', 0)
    run(scenario())


def test_locked_expired_job_does_not_block_claiming_another_due_job(target):
    async def scenario():
        async with publications(target) as (h, sender, _), scheduler(target) as worker:
            async with h.client() as customer:
                old = await enqueue(h, sender, customer, target)
                lease = await worker.claim(uuid4(), ('case_created',), 30)
                assert lease.id == old
                expire(target, old)
                due = await enqueue(h, sender, customer, target)
                with target['engine'].begin() as holder:
                    holder.execute(text('SELECT id FROM "' + target['schema'] + '".outbox_jobs WHERE id=:id FOR UPDATE'), {'id': old})
                    next_lease = await asyncio.wait_for(worker.claim(uuid4(), ('case_created',), 30), 5)
                    assert next_lease.id == due
                assert await worker.finish(next_lease, Outcome('succeeded', 'OK'))
                assert await worker.claim(uuid4(), ('case_created',), 30) is None
                assert row(target, 'SELECT state FROM {s}.outbox_jobs WHERE id=:id', id=old)[0] == 'unknown'
    run(scenario())
