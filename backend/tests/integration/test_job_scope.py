"""Job-ID scope constrains claim AND recovery before any out-of-scope mutation."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from test_auth_mail_delivery import target as auth_mail_target,reply_target,inbox_target,delivery_target,mail_target,worker_target,publication_target,handoff_target,events_target,files_target,lifecycle_target,commit_target,adoption_target,draft_target,conversation_target,auth_target,inbox_app
from test_email_replies import scheduler,isolate_jobs
from test_job_worker import enqueue,expire
from test_staff_auth import run,row,change
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.jobs.service import Outcome
from airport_assistant.database_config import MigrationDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in bounded job scope Neon.')


@pytest.fixture(scope='module')
def target(auth_mail_target):
 t=auth_mail_target;s=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
 for _ in range(2):assert migrate(s.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'])=='0020_job_scope'
 return t


def test_scope_claim_and_pool_reset_leave_other_job_untouched(target):
 async def scenario():
  async with inbox_app(target) as (h,sender),scheduler(target) as q:
   async with h.client() as a,h.client() as b:
    first=await enqueue(h,sender,a,target);second=await enqueue(h,sender,b,target)
    q.job_scope_ids=(second,);lease=await q.claim(uuid4(),('case_created',),30)
    assert lease.id==second
    assert row(target,'SELECT state,attempts FROM {s}.outbox_jobs WHERE id=:id',id=first)==('queued',0)
    assert await q.finish(lease,Outcome('succeeded','OK'))
    q.job_scope_ids=();assert await q.claim(uuid4(),('case_created',),30) is None
    q.job_scope_ids=None;lease=await q.claim(uuid4(),('case_created',),30);assert lease.id==first
    assert await q.finish(lease,Outcome('succeeded','OK'))
 run(scenario())


def test_scope_reaper_does_not_touch_expired_foreign_job(target):
 async def scenario():
  async with inbox_app(target) as (h,sender),scheduler(target) as q:
   async with h.client() as a,h.client() as b:
    first=await enqueue(h,sender,a,target);second=await enqueue(h,sender,b,target)
    q.job_scope_ids=(first,);lease=await q.claim(uuid4(),('case_created',),30);assert lease.id==first;expire(target,first)
    q.job_scope_ids=(second,);own=await q.claim(uuid4(),('case_created',),30);assert own.id==second
    assert row(target,'SELECT state,attempts FROM {s}.outbox_jobs WHERE id=:id',id=first)==('leased',1)
    assert await q.finish(own,Outcome('succeeded','OK'))
    q.job_scope_ids=();assert await q.claim(uuid4(),('case_created',),30) is None
    assert row(target,'SELECT state FROM {s}.outbox_jobs WHERE id=:id',id=first)[0]=='leased'
    q.job_scope_ids=None;assert await q.claim(uuid4(),('case_created',),30) is None
    assert row(target,'SELECT state FROM {s}.outbox_jobs WHERE id=:id',id=first)[0]=='unknown'
 run(scenario())


def test_missing_scope_capability_fails_before_claim(target):
 async def scenario():
  async with inbox_app(target) as (h,sender),scheduler(target) as q:
   async with h.client() as a:
    id=await enqueue(h,sender,a,target);q.job_scope_ids=(id,)
    fn='"'+target['schema']+'".job_scope_supported()';role='"'+target['worker_role']+'"'
    with target['engine'].begin() as c:c.exec_driver_sql('REVOKE EXECUTE ON FUNCTION '+fn+' FROM '+role)
    try:
     with pytest.raises(DatabaseError):await q.claim(uuid4(),('case_created',),30)
     assert row(target,'SELECT state,attempts FROM {s}.outbox_jobs WHERE id=:id',id=id)==('queued',0)
    finally:
     with target['engine'].begin() as c:c.exec_driver_sql('GRANT EXECUTE ON FUNCTION '+fn+' TO '+role)
 run(scenario())
