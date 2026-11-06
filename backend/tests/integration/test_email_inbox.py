"""Signed HTTP ingress and real Neon worker stages; no live provider."""
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime,timezone
import base64,hashlib,hmac,json,os,secrets,time
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from test_mail_delivery import target as delivery_target,mail_target,worker_target,publication_target,handoff_target,events_target,files_target,lifecycle_target,commit_target,adoption_target,draft_target,conversation_target,auth_target,isolate_jobs,scheduler,app as delivery_app,Lookup,send,due
from test_staff_auth import run,row,change
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.inbox import PostgresInbox
from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.modules.mail.inbox import WebhookIngress,InboxWorker,ReceivedContent,ReceiptContext
from airport_assistant.modules.jobs.service import JobWorker
from airport_assistant.mail_config import WebhookConfig
from airport_assistant.api.email_inbox import router

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in MAIL-03 isolated Neon.')
KEY='whsec_'+base64.b64encode(b'synthetic-signing-key-for-tests-01').decode()


@pytest.fixture(scope='module')
def target(delivery_target):
    t=delivery_target;role='email_ingress_'+hashlib.sha256(t['schema'].encode()).hexdigest()[:24]
    direct=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    try:
        for _ in range(2):assert migrate(direct.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0017_email_inbox')=='0017_email_inbox'
        change(t,"UPDATE {s}.email_accounts SET account_alias='airport-a',inbound_address='reply@airport.example' WHERE id=:id",id=t['mail_account'])
        yield t
    finally:
        assert t['schema'].startswith('auth03_test_') and role.startswith('email_ingress_')
        with t['engine'].begin() as c:
            c.exec_driver_sql('DROP SCHEMA IF EXISTS "'+t['schema']+'" CASCADE')
            c.exec_driver_sql('DROP ROLE IF EXISTS "'+role+'"')


@asynccontextmanager
async def app(target):
    async with delivery_app(target) as (h,sender):
        service=WebhookIngress(PostgresInbox(h.db,target['schema']),{'airport-a':WebhookConfig(account_ref='test-account',secrets=[KEY])})
        h.app.state.services=replace(h.app.state.services,email_ingress=service)
        h.app.include_router(router)
        yield h,sender


def event(kind='email.received',id=None,*,stamp=None):
    return json.dumps(dict(type=kind,created_at=stamp or datetime.now(timezone.utc).isoformat(),data={'email_id':id or 'received-'+uuid4().hex}),separators=(',',':')).encode()


def headers(raw,id=None,*,key=KEY,stamp=None):
    id=id or 'msg_'+uuid4().hex;stamp=str(int(time.time()) if stamp is None else stamp)
    signature=base64.b64encode(hmac.new(base64.b64decode(key[6:]),id.encode()+b'.'+stamp.encode()+b'.'+raw,hashlib.sha256).digest()).decode()
    return {'content-type':'application/json','svix-id':id,'svix-timestamp':stamp,'svix-signature':'v1,'+signature}


async def post(client,raw,signature=None,alias='airport-a'):
    return await client.post('/integrations/email/'+alias+'/events',content=raw,headers=signature or headers(raw))


class Fetcher(Lookup):
    def __init__(self):super().__init__();self.fetched=[]
    async def receive(self,snapshot):
        self.fetched.append(snapshot)
        return ReceivedContent('customer@example.com',snapshot['inbound_address'],'Re: help','Please check my request.',None,'<reply@customer.example>',None,None,None,0)

    async def context(self,snapshot):
        proof=await self.lookup(snapshot)
        return ReceiptContext(proof.provider_message_id,proof.rfc_message_id) if proof else None


def test_signature_dedupe_and_body_staged_once_before_case_identity(target):
    async def scenario():
        async with app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as client:
                provider='received-'+uuid4().hex;raw=event(id=provider);signature=headers(raw)
                assert (await post(client,raw,signature)).status_code==202
                assert (await post(client,raw,signature)).status_code==202
                assert (await post(client,raw,headers(raw,signature['svix-id'],key='whsec_'+base64.b64encode(b'x'*32).decode()))).status_code==401
                changed=event(id='changed')
                assert (await post(client,changed,headers(changed,signature['svix-id']))).status_code==409
                assert row(target,'SELECT count(*) FROM {s}.email_inbox WHERE provider_message_id=:id',id=provider)[0]==1
                repo=PostgresInbox(schedule._database,target['schema']);fetch=Fetcher()
                worker=JobWorker(schedule,{'email_fetch':InboxWorker(repo,PostgresMail(schedule._database,target['schema']),fetch,accounts=('test-account',))})
                assert await worker.run_once()=='succeeded'
                assert (await post(client,raw,headers(raw))).status_code==202
                assert await worker.run_once()=='succeeded'
                assert len(fetch.fetched)==1
                assert row(target,"SELECT count(*) FROM {s}.email_messages WHERE provider_message_id=:id AND direction='inbound'",id=provider)[0]==1
                assert row(target,"SELECT case_id,app_message_id,verification_state,delivery_status FROM {s}.email_messages WHERE provider_message_id=:id AND direction='inbound'",id=provider)==(None,None,'pending',None)
                assert row(target,'SELECT count(*) FROM {s}.email_inbox WHERE provider_message_id=:id AND processing_state=\'processed\'',id=provider)[0]==2
    run(scenario())


def test_distinct_events_update_known_receipt_without_reusing_poll_job(target):
    async def scenario():
        async with app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as c:
                transport=Fetcher();_,case_id,mail=await send(h,sender,target,c,schedule,transport)
                receipt=row(target,'SELECT provider_message_id FROM {s}.email_messages WHERE case_id=:id',id=UUID(case_id))[0]
                repo=PostgresInbox(schedule._database,target['schema']);worker=JobWorker(schedule,{'email_event':InboxWorker(repo,mail,transport,accounts=('test-account',))})
                stamp=datetime.now(timezone.utc).isoformat()
                for kind in ('email.delivered','email.bounced'):
                    response=await post(c,event(kind,receipt,stamp=stamp));assert response.status_code==202,response.text
                    change(target,"UPDATE {s}.outbox_jobs SET next_attempt_at=clock_timestamp() WHERE kind='email_event' AND state='queued'")
                    assert await worker.run_once()=='succeeded'
                assert row(target,'SELECT count(*) FROM {s}.email_inbox WHERE provider_message_id=:id',id=receipt)[0]==2
                assert row(target,'SELECT delivery_status,review_reason FROM {s}.email_messages WHERE case_id=:id',id=UUID(case_id))==('delivered','CONFLICTING_EVIDENCE')
                assert len(transport.sent)==1 and len(transport.lookups)==2
    run(scenario())


def test_fetch_failure_cap_is_review_and_no_raw_body_access(target):
    async def scenario():
        async with app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as c:
                provider='received-'+uuid4().hex
                assert (await post(c,event(id=provider))).status_code==202
                change(target,"UPDATE {s}.outbox_jobs SET max_attempts=1 WHERE kind='email_fetch' AND state='queued'")
                transport=Fetcher()
                async def unavailable(_):return None
                transport.receive=unavailable
                worker=JobWorker(schedule,{'email_fetch':InboxWorker(PostgresInbox(schedule._database,target['schema']),PostgresMail(schedule._database,target['schema']),transport,accounts=('test-account',))})
                assert await worker.run_once()=='succeeded'
                assert row(target,'SELECT processing_state,review_reason FROM {s}.email_inbox WHERE provider_message_id=:id',id=provider)==('review','FETCH_UNAVAILABLE')
    run(scenario())


def test_concurrent_signed_replays_commit_one_event_and_job(target):
    async def scenario():
        import asyncio
        async with app(target) as (h,sender):
            async with h.client() as a,h.client() as b:
                provider='received-'+uuid4().hex;raw=event(id=provider);signature=headers(raw)
                responses=await asyncio.gather(post(a,raw,signature),post(b,raw,signature))
                assert [r.status_code for r in responses]==[202,202]
                assert row(target,'SELECT count(*) FROM {s}.email_inbox WHERE provider_message_id=:id',id=provider)[0]==1
                assert row(target,'SELECT count(*) FROM {s}.outbox_jobs j JOIN {s}.email_inbox i ON i.tenant_id=j.tenant_id AND i.id=j.email_inbox_id WHERE i.provider_message_id=:id',id=provider)[0]==1
    run(scenario())


def test_event_job_failure_rolls_back_inbox_before_ack_then_retry_commits(target):
    async def scenario():
        async with app(target) as (h,sender):
            async with h.client() as c:
                provider='received-'+uuid4().hex;raw=event(id=provider);signature=headers(raw)
                with target['engine'].begin() as db:
                    db.exec_driver_sql('CREATE FUNCTION "'+target['schema']+'".fail_inbox_job() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.email_inbox_id IS NOT NULL THEN RAISE EXCEPTION \'test fault\';END IF;RETURN NEW;END $$')
                    db.exec_driver_sql('CREATE TRIGGER fail_inbox_job BEFORE INSERT ON "'+target['schema']+'".outbox_jobs FOR EACH ROW EXECUTE FUNCTION "'+target['schema']+'".fail_inbox_job()')
                try:
                    response=await post(c,raw,signature)
                    assert response.status_code==500,response.text
                    assert row(target,'SELECT count(*) FROM {s}.email_inbox WHERE provider_message_id=:id',id=provider)[0]==0
                finally:
                    with target['engine'].begin() as db:
                        db.exec_driver_sql('DROP TRIGGER fail_inbox_job ON "'+target['schema']+'".outbox_jobs')
                        db.exec_driver_sql('DROP FUNCTION "'+target['schema']+'".fail_inbox_job()')
                assert (await post(c,raw,signature)).status_code==202
                assert row(target,'SELECT count(*) FROM {s}.email_inbox WHERE provider_message_id=:id',id=provider)[0]==1
    run(scenario())
