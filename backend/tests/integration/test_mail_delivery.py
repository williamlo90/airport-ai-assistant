"""MAIL-02 real Neon scheduling/lifecycle/ACL; provider responses controlled."""
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import os
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from test_outbound_mail import target as mail_target,worker_target,publication_target,handoff_target,events_target,files_target,lifecycle_target,commit_target,adoption_target,draft_target,conversation_target,auth_target,isolate_jobs,scheduler,mail_app,case,Transport
from test_staff_auth import run,row,change
from test_chat_handoff import operator,login
from test_job_worker import expire
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.infrastructure.database.delivery_status import PostgresDeliveryStatus
from airport_assistant.modules.mail.service import OutboundMail,SendResult
from airport_assistant.modules.mail.delivery import ReconcileMail,DeliveryEvidence,DeliveryStatus
from airport_assistant.modules.jobs.service import JobWorker,Outcome
from airport_assistant.api.email_delivery import router

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in MAIL-02 isolated Neon.')


@pytest.fixture(scope='module')
def target(mail_target):
    t=mail_target
    direct=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):
        assert migrate(direct.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0016_mail_delivery')=='0016_mail_delivery'
    return t


@asynccontextmanager
async def app(target):
    async with mail_app(target) as (h,sender):
        h.app.state.services=replace(h.app.state.services,delivery_status=DeliveryStatus(PostgresDeliveryStatus(h.db,target['schema'])))
        h.app.include_router(router)
        yield h,sender


def due(target,case_id,*,max_attempts=5):
    change(target,"UPDATE {s}.outbox_jobs SET next_attempt_at=clock_timestamp(),max_attempts=:max WHERE case_id=:id AND kind='email_reconcile'",id=UUID(case_id),max=max_attempts)


class Lookup(Transport):
    def __init__(self,state='delivered'):
        super().__init__();self.state=state;self.lookups=[]
    async def lookup(self,snapshot):
        self.lookups.append(snapshot)
        return DeliveryEvidence(snapshot['provider_message_id'],self.state,'<actual-wire@provider.example>') if self.state else None


async def send(h,sender,target,c,schedule,transport):
    conversation,id=await case(h,sender,c)
    repo=PostgresMail(schedule._database,target['schema'])
    assert await JobWorker(schedule,{'case_created':OutboundMail(repo,transport,account_ref='test-account')}).run_once()=='succeeded'
    return conversation,id,repo


def test_queued_status_then_verified_delivery_and_scoped_metadata(target):
    async def scenario():
        async with app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as customer,h.client() as staff:
                _,id=await case(h,sender,customer);person=operator(target,id);await login(h,staff,person)
                url='/api/v1/staff/cases/'+id+'/email-deliveries'
                queued=await staff.get(url);assert queued.status_code==200,queued.text
                assert queued.json()['items'][0]['delivery_status']=='queued'
                assert queued.json()['items'][0]['needs_review'] is False
                assert (await customer.get(url)).status_code==401
                repo=PostgresMail(schedule._database,target['schema']);transport=Lookup()
                assert await JobWorker(schedule,{'case_created':OutboundMail(repo,transport,account_ref='test-account')}).run_once()=='succeeded'
                due(target,id)
                assert await JobWorker(schedule,{'email_reconcile':ReconcileMail(repo,transport,account_refs=('test-account',))}).run_once()=='succeeded'
                record=row(target,'SELECT delivery_status,rfc_message_id,needs_review,provider_status_at FROM {s}.email_messages WHERE case_id=:id',id=UUID(id))
                assert record==('delivered','<actual-wire@provider.example>',False,None)
                response=await staff.get(url);assert response.status_code==200,response.text
                assert response.json()['items'][0]['delivery_status']=='delivered'
                assert 'recipient' not in response.text and 'provider_message_id' not in response.text and 'body' not in response.text
                assert row(target,'SELECT status FROM {s}.cases WHERE id=:id',id=UUID(id))[0]!='closed'
                assert len(transport.sent)==1 and len(transport.lookups)==1
    run(scenario())


def test_live_parent_skips_reconcile_without_spending_attempts_then_no_receipt_review(target):
    async def scenario():
        async with app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as c:
                _,id=await case(h,sender,c);parent=await schedule.claim(uuid4(),('case_created',),30)
                repo=PostgresMail(schedule._database,target['schema']);transport=Lookup()
                service=OutboundMail(repo,transport,account_ref='test-account')
                snapshot=await repo.snapshot(parent);assert await repo.begin(parent,snapshot,service._envelope(parent,snapshot))
                due(target,id)
                assert await schedule.claim(uuid4(),('email_reconcile',),30) is None
                assert row(target,"SELECT attempts FROM {s}.outbox_jobs WHERE case_id=:id AND kind='email_reconcile'",id=UUID(id))[0]==0
                expire(target,parent.id)
                assert await JobWorker(schedule,{'email_reconcile':ReconcileMail(repo,transport,account_refs=('test-account',))}).run_once()=='succeeded'
                assert row(target,'SELECT delivery_status,needs_review,review_reason FROM {s}.email_messages WHERE case_id=:id',id=UUID(id))==('delivery_unknown',True,'NO_RECEIPT')
                assert not transport.sent and not transport.lookups
    run(scenario())


def test_identical_accepted_evidence_at_cap_creates_durable_review(target):
    async def scenario():
        async with app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as c:
                transport=Lookup('accepted');_,id,repo=await send(h,sender,target,c,schedule,transport)
                worker=JobWorker(schedule,{'email_reconcile':ReconcileMail(repo,transport,account_refs=('test-account',))})
                due(target,id,max_attempts=2);assert await worker.run_once()=='retry'
                due(target,id,max_attempts=2);assert await worker.run_once()=='succeeded'
                assert row(target,'SELECT delivery_status,needs_review,review_reason FROM {s}.email_messages WHERE case_id=:id',id=UUID(id))==('accepted',True,'PENDING_DELIVERY')
                assert row(target,'SELECT count(*) FROM {s}.email_delivery_events WHERE case_id=:id',id=UUID(id))[0]==2
                assert len(transport.sent)==1 and len(transport.lookups)==2
    run(scenario())


def test_unavailable_get_at_one_attempt_reviews_without_changing_accepted(target):
    async def scenario():
        async with app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as c:
                transport=Lookup(None);_,id,repo=await send(h,sender,target,c,schedule,transport)
                due(target,id,max_attempts=1)
                assert await JobWorker(schedule,{'email_reconcile':ReconcileMail(repo,transport,account_refs=('test-account',))}).run_once()=='succeeded'
                assert row(target,'SELECT delivery_status,review_reason FROM {s}.email_messages WHERE case_id=:id',id=UUID(id))==('accepted','LOOKUP_UNAVAILABLE')
                assert len(transport.sent)==1
    run(scenario())


def test_stale_callbacks_and_conflicting_terminal_evidence_do_not_regress(target):
    async def scenario():
        async with app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as c:
                transport=Lookup();_,id,repo=await send(h,sender,target,c,schedule,transport)
                due(target,id);lease=await schedule.claim(uuid4(),('email_reconcile',),30)
                now=datetime.now(timezone.utc)
                for state,seconds in [('delivered',20),('accepted',30),('bounced',40),('bounced',10)]:
                    snapshot=await repo.delivery_snapshot(lease)
                    evidence=DeliveryEvidence(snapshot['provider_message_id'],state,event_at=now-timedelta(seconds=seconds))
                    assert await repo.observe(lease,snapshot,evidence)
                assert row(target,'SELECT delivery_status,needs_review,review_reason FROM {s}.email_messages WHERE case_id=:id',id=UUID(id))==('delivered',True,'CONFLICTING_EVIDENCE')
                assert row(target,'SELECT count(*) FROM {s}.email_delivery_events WHERE case_id=:id',id=UUID(id))[0]==4
                latest=await repo.delivery_snapshot(lease)
                assert await repo.observe(lease,latest,evidence)
                assert row(target,'SELECT count(*) FROM {s}.email_delivery_events WHERE case_id=:id',id=UUID(id))[0]==4
                assert await schedule.finish(lease,Outcome('succeeded','OK'))
    run(scenario())


def test_saved_receipt_survives_lost_parent_finish_and_get_runs_outside_locks(target):
    async def scenario():
        async with app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as c,h.client() as staff:
                _,id=await case(h,sender,c);person=operator(target,id);await login(h,staff,person)
                parent=await schedule.claim(uuid4(),('case_created',),30)
                repo=PostgresMail(schedule._database,target['schema']);transport=Lookup()
                service=OutboundMail(repo,transport,account_ref='test-account')
                snapshot=await repo.snapshot(parent);envelope=service._envelope(parent,snapshot)
                assert await repo.begin(parent,snapshot,envelope)
                result=await transport.send(envelope)
                assert await repo.complete(parent,envelope.id,result)
                # Simulate loss of the scheduler finish acknowledgment after saving receipt.
                expire(target,parent.id);due(target,id)
                original=transport.lookup
                async def unlocked_lookup(snapshot):
                    from sqlalchemy import text
                    with target['engine'].begin() as db:
                        db.execute(text('SELECT id FROM "'+target['schema']+'".email_messages WHERE id=:id FOR UPDATE NOWAIT'),{'id':UUID(snapshot['id'])})
                        db.execute(text('SELECT id FROM "'+target['schema']+'".outbox_jobs WHERE case_id=:id FOR UPDATE NOWAIT'),{'id':UUID(id)})
                    return await original(snapshot)
                transport.lookup=unlocked_lookup
                assert await JobWorker(schedule,{'email_reconcile':ReconcileMail(repo,transport,account_refs=('test-account',))}).run_once()=='succeeded'
                assert len(transport.sent)==1 and len(transport.lookups)==1
                response=await staff.get('/api/v1/staff/cases/'+id+'/email-deliveries')
                assert response.status_code==200,response.text
                assert response.json()['items'][0]['delivery_status']=='delivered'
                assert response.json()['items'][0]['needs_review'] is False
    run(scenario())
