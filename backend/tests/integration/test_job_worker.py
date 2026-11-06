"""JOB-01 real restricted worker credential, leases and scheduler permissions."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import secrets
from uuid import UUID,uuid4
import pytest
from test_staff_auth import run,row,change
from test_chat_publication import target as publication_target,handoff_target,events_target,files_target,lifecycle_target,commit_target,adoption_target,draft_target,conversation_target,auth_target,publications
from test_case_commit import prepared,confirm
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.provisioning import enable_login,runtime_url
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.infrastructure.database.jobs import WorkerDatabase,PostgresScheduler
from airport_assistant.modules.jobs.service import Outcome,JobWorker

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in JOB-01 isolated Neon.')


@pytest.fixture(scope='module')
def target(publication_target):
    t=publication_target;suffix=hashlib.sha256(t['schema'].encode()).hexdigest()[:24]
    t['worker_role']='job_worker_'+suffix;t['job_owner']='job_owner_'+suffix
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    try:
        for _ in range(2):assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0014_job_worker')=='0014_job_worker'
        password=secrets.token_urlsafe(36)
        with t['engine'].begin() as c:enable_login(c,t['worker_role'],password)
        t['worker_url']=runtime_url(t['url'],t['worker_role'],password)
        yield t
    finally:
        assert t['schema'].startswith('auth03_test_') and t['worker_role']=='job_worker_'+suffix and t['job_owner']=='job_owner_'+suffix
        with t['engine'].begin() as c:
            c.exec_driver_sql('DROP SCHEMA IF EXISTS "'+t['schema']+'" CASCADE')
            for name in (t['worker_role'],t['job_owner']):c.exec_driver_sql('DROP ROLE IF EXISTS "'+name+'"')


@pytest.fixture(autouse=True)
def isolate_jobs(target):
    change(target,"UPDATE {s}.outbox_jobs SET state='dead',completed_at=clock_timestamp(),lease_worker_id=NULL,lease_token=NULL,leased_at=NULL,lease_until=NULL WHERE state IN('queued','leased','retry')")


@asynccontextmanager
async def scheduler(target):
    db=WorkerDatabase(target['worker_url'],schema=target['schema'])
    try:
        await db.open();yield PostgresScheduler(db,target['schema'])
    finally:await db.close()


async def enqueue(h,sender,c,target,*,safe=False,max_attempts=5):
    draft=await prepared(h,sender,c,anonymous=True);result=await confirm(h,c,draft);assert result.status_code==201,result.text
    id=row(target,'SELECT id FROM {s}.outbox_jobs WHERE action_draft_id=:id',id=UUID(draft['draft_id']))[0]
    if safe or max_attempts!=5:change(target,'UPDATE {s}.outbox_jobs SET replay_policy=:policy,max_attempts=:max WHERE id=:id',policy='safe' if safe else 'reconcile',max=max_attempts,id=id)
    return id


def expire(target,id):change(target,"UPDATE {s}.outbox_jobs SET leased_at=clock_timestamp()-interval '60 seconds',lease_until=clock_timestamp()-interval '1 second' WHERE id=:id",id=id)


def test_two_workers_one_claim_and_stale_token_generation_denied(target):
    async def scenario():
        async with publications(target) as (h,sender,pub),scheduler(target) as a,scheduler(target) as b:
            async with h.client() as c:
                id=await enqueue(h,sender,c,target,safe=True)
                leases=await asyncio.gather(a.claim(uuid4(),('case_created',),30),b.claim(uuid4(),('case_created',),30))
                assert sum(l is not None for l in leases)==1
                old=next(l for l in leases if l is not None);assert old.id==id
                expire(target,id);new=await b.claim(uuid4(),('case_created',),300)
                assert new.id==id and new.attempts==2 and new.generation==old.generation+1 and new.token!=old.token
                assert not await a.finish(old,Outcome('succeeded','OK'))
                assert not await b.finish(replace(new,generation=old.generation),Outcome('succeeded','OK'))
                assert await b.finish(new,Outcome('succeeded','OK'))
                assert not await b.finish(new,Outcome('succeeded','OK'))
                assert row(target,'SELECT state,attempts FROM {s}.outbox_jobs WHERE id=:id',id=id)==('succeeded',2)
    run(scenario())


def test_retry_schedule_restart_max_attempts_and_renewal(target):
    async def scenario():
        async with publications(target) as (h,sender,pub),scheduler(target) as a:
            async with h.client() as c:
                id=await enqueue(h,sender,c,target,safe=True,max_attempts=2)
                lease=await a.claim(uuid4(),('case_created',),30);assert await a.renew(lease,300)
                assert await a.finish(lease,Outcome('retry','RETRYABLE'))
                assert await a.claim(uuid4(),('case_created',),30) is None
                change(target,"UPDATE {s}.outbox_jobs SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=:id",id=id)
                async with scheduler(target) as restarted:
                    nextlease=await restarted.claim(uuid4(),('case_created',),30)
                    assert nextlease.attempts==2
                    assert await restarted.finish(nextlease,Outcome('retry','RETRYABLE'))
                assert row(target,'SELECT state,result_code,attempts FROM {s}.outbox_jobs WHERE id=:id',id=id)==('dead','RETRIES_EXHAUSTED',2)
    run(scenario())


def test_expired_uncertain_lease_and_handler_exception_are_unknown(target):
    async def scenario():
        async with publications(target) as (h,sender,pub),scheduler(target) as a:
            async with h.client() as c:
                id=await enqueue(h,sender,c,target)
                lease=await a.claim(uuid4(),('case_created',),30);expire(target,id)
                assert not await a.renew(lease,30) and not await a.finish(lease,Outcome('succeeded','OK'))
                assert await a.claim(uuid4(),('case_created',),30) is None
                assert row(target,'SELECT state,result_code FROM {s}.outbox_jobs WHERE id=:id',id=id)==('unknown','LEASE_EXPIRED')
                second=await enqueue(h,sender,c,target)
                async def fail(job):raise RuntimeError('Private provider error')
                assert await JobWorker(a,{'case_created':fail}).run_once()=='unknown'
                assert row(target,'SELECT state,result_code FROM {s}.outbox_jobs WHERE id=:id',id=second)==('unknown','HANDLER_ERROR')
    run(scenario())


def test_handler_runs_outside_transaction_and_heartbeat_keeps_lease(target):
    async def scenario():
        async with publications(target) as (h,sender,pub),scheduler(target) as a:
            async with h.client() as c:
                id=await enqueue(h,sender,c,target)
                async def handler(job):
                    # This NOWAIT would fail if claim held a transaction open.
                    with target['engine'].begin() as conn:
                        from sqlalchemy import text
                        conn.execute(text('SELECT id FROM "'+target['schema']+'".outbox_jobs WHERE id=:id FOR UPDATE NOWAIT'),{'id':job.id})
                    await asyncio.sleep(3.2)
                    assert row(target,'SELECT lease_until>clock_timestamp() FROM {s}.outbox_jobs WHERE id=:id',id=job.id)[0]
                    return Outcome('succeeded','OK')
                assert await JobWorker(a,{'case_created':handler},lease_seconds=3).run_once()=='succeeded'
                assert row(target,'SELECT state FROM {s}.outbox_jobs WHERE id=:id',id=id)[0]=='succeeded'
    run(scenario())


def test_api_cannot_schedule_and_worker_cannot_read_customer_tables(target):
    async def scenario():
        async with publications(target) as (h,sender,pub),scheduler(target) as a:
            async with h.client() as c:
                id=await enqueue(h,sender,c,target)
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction() as conn:
                        from psycopg import sql
                        await conn.execute(sql.SQL('SELECT {}.job_claim(%s,%s,30)').format(sql.Identifier(target['schema'])),(uuid4(),['case_created']))
                assert error.value.sqlstate=='42501'
                for table in ('cases','messages','outbox_jobs'):
                    with pytest.raises(DatabaseError) as error:
                        async with a._database.transaction() as conn:
                            from psycopg import sql
                            await conn.execute(sql.SQL('SELECT * FROM {}.{}').format(sql.Identifier(target['schema']),sql.Identifier(table)))
                    assert error.value.sqlstate=='42501'
                assert await a.claim(uuid4(),('unregistered_kind',),30) is None
                assert row(target,'SELECT state,attempts FROM {s}.outbox_jobs WHERE id=:id',id=id)==('queued',0)
    run(scenario())
