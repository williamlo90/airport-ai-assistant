"""MAIL-04: case correlation and proof-backed messages without browser sessions."""
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib,os
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from test_email_inbox import target as inbox_target,delivery_target,mail_target,worker_target,publication_target,handoff_target,events_target,files_target,lifecycle_target,commit_target,adoption_target,draft_target,conversation_target,auth_target,isolate_jobs,scheduler,app as inbox_app,Fetcher
from test_mail_delivery import case
from test_staff_auth import run,row,change
from test_chat_handoff import operator,login,accept,meta,close
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.infrastructure.database.replies import PostgresReplies
from airport_assistant.infrastructure.database.inbox import PostgresInbox
from airport_assistant.modules.mail.service import OutboundMail
from airport_assistant.modules.mail.replies import ReplyWorker,ChallengeWorker,ReplyTransport,code
from airport_assistant.modules.mail.inbox import ReceivedContent,InboxWorker
from airport_assistant.modules.jobs.service import JobWorker
from test_email_inbox import event,post

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in MAIL-04 isolated Neon.')
SECRET=b'synthetic-email-reply-secret-32-bytes-minimum'


@pytest.fixture(scope='module')
def target(inbox_target):
    t=inbox_target;name='reply_owner_'+hashlib.sha256(t['schema'].encode()).hexdigest()[:24]
    direct=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    try:
        for _ in range(2):assert migrate(direct.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0018_email_replies')=='0018_email_replies'
        yield t
    finally:
        assert name.startswith('reply_owner_') and t['schema'].startswith('auth03_test_')
        with t['engine'].begin() as c:
            c.exec_driver_sql('DROP SCHEMA IF EXISTS "'+t['schema']+'" CASCADE');c.exec_driver_sql('DROP ROLE IF EXISTS "'+name+'"')


async def setup_case(h,sender,target,c,schedule,*,closed=False,staff=None):
    conversation,id=await case(h,sender,c);transport=Fetcher()
    mail=OutboundMail(PostgresMail(schedule._database,target['schema']),transport,account_ref='test-account',reply_secret=SECRET)
    assert await JobWorker(schedule,{'case_created':mail}).run_once()=='succeeded'
    envelope=transport.sent[-1]
    if closed:
        person=operator(target,id);await login(h,staff,person)
        assert (await accept(h,staff,conversation,await meta(staff,conversation))).status_code==200
        assert (await close(h,staff,id,await meta(staff,conversation))).status_code==200
    return id,envelope,transport


async def inbound(h,target,c,schedule,envelope,text,*,sender=None,auth=True):
    email='received-'+uuid4().hex
    assert (await post(c,event(id=email))).status_code==202
    address=sender or envelope.recipient
    transport=Fetcher()
    async def receive(snapshot):
        return ReceivedContent(address,snapshot['inbound_address'],'Re: request',text,None,'<'+uuid4().hex+'@customer.example>',None,None,None,0,
          {'dkim':'pass','dmarc':'pass'} if auth else None,envelope.reply_to)
    transport.receive=receive
    wrapped=ReplyTransport(transport,SECRET)
    worker=InboxWorker(PostgresInbox(schedule._database,target['schema']),PostgresMail(schedule._database,target['schema']),wrapped,accounts=('test-account',))
    assert await JobWorker(schedule,{'email_fetch':worker}).run_once()=='succeeded'
    return row(target,"SELECT id FROM {s}.email_messages WHERE provider_message_id=:id AND direction='inbound'",id=email)[0]


def test_authenticated_reply_publishes_once_and_foreign_sender_is_rejected(target):
    async def scenario():
        async with inbox_app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as c:
                id,envelope,transport=await setup_case(h,sender,target,c,schedule)
                email=await inbound(h,target,c,schedule,envelope,'I still need help with my parking request.')
                repo=PostgresReplies(schedule._database,target['schema'])
                worker=JobWorker(schedule,{'email_reply':ReplyWorker(repo,SECRET)})
                assert await worker.run_once()=='succeeded'
                assert row(target,'SELECT state FROM {s}.email_reply_state WHERE email_id=:id',id=email)[0]=='published'
                assert row(target,"SELECT case_id,author_type,source_kind,channel,created_session_id FROM {s}.messages WHERE email_source_id=:id",id=email)==(UUID(id),'customer','email_reply','email',None)
                assert await worker.run_once()=='idle'
                bad=await inbound(h,target,c,schedule,envelope,'Send me private details.',sender='foreign@example.com')
                assert await worker.run_once()=='succeeded'
                assert row(target,'SELECT state FROM {s}.email_reply_state WHERE email_id=:id',id=bad)[0]=='rejected'
                assert row(target,'SELECT count(*) FROM {s}.messages WHERE email_source_id=:id',id=bad)[0]==0
    run(scenario())


def test_closed_case_requires_explicit_continuation_and_keeps_old_chat_ended(target):
    async def scenario():
        async with inbox_app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as c,h.client() as staff:
                id,envelope,_=await setup_case(h,sender,target,c,schedule,closed=True,staff=staff)
                repo=PostgresReplies(schedule._database,target['schema']);worker=JobWorker(schedule,{'email_reply':ReplyWorker(repo,SECRET)})
                await inbound(h,target,c,schedule,envelope,'Thank you')
                assert await worker.run_once()=='succeeded'
                assert row(target,'SELECT status FROM {s}.cases WHERE id=:id',id=UUID(id))[0]=='closed'
                replies=await staff.get('/api/v1/staff/cases/'+id+'/email-replies')
                assert replies.status_code==200 and replies.json()[0]['body']=='Thank you'
                assert (await c.get('/api/v1/staff/cases/'+id+'/email-replies')).status_code in (401,403)
                await inbound(h,target,c,schedule,envelope,'CONTINUE SAME ISSUE\nThe issue is still present.')
                assert await worker.run_once()=='succeeded'
                assert row(target,'SELECT status FROM {s}.cases WHERE id=:id',id=UUID(id))[0]=='in_progress'
                assert row(target,'SELECT mode FROM {s}.conversations WHERE case_id=:id',id=UUID(id))[0]=='ended'
                assert row(target,"SELECT count(*) FROM {s}.case_history WHERE case_id=:id AND event_type='reopened' AND email_source_id IS NOT NULL",id=UUID(id))[0]==1
    run(scenario())


def test_email_only_code_verifies_original_once_without_session_or_code_storage(target):
    async def scenario():
        async with inbox_app(target) as (h,sender),scheduler(target) as schedule:
            async with h.client() as c:
                id,envelope,transport=await setup_case(h,sender,target,c,schedule)
                original=await inbound(h,target,c,schedule,envelope,'Please check this request.',auth=False)
                repo=PostgresReplies(schedule._database,target['schema']);worker=JobWorker(schedule,{'email_reply':ReplyWorker(repo,SECRET)})
                assert await worker.run_once()=='succeeded'
                challenge=row(target,'SELECT id FROM {s}.email_reply_challenges WHERE email_id=:id',id=original)[0]
                assert await JobWorker(schedule,{'email_verify_send':ChallengeWorker(repo,transport,SECRET)}).run_once()=='succeeded'
                notification=transport.sent[-1];assert notification.recipient==envelope.recipient and 'CASE-' not in notification.text
                response=await inbound(h,target,c,schedule,notification,'VERIFY '+str(challenge)+' '+code(SECRET,challenge),auth=False)
                assert code(SECRET,challenge) not in row(target,'SELECT body FROM {s}.email_messages WHERE id=:id',id=response)[0]
                assert await worker.run_once()=='succeeded'
                assert row(target,'SELECT state FROM {s}.email_reply_challenges WHERE id=:id',id=challenge)[0]=='consumed'
                assert row(target,'SELECT count(*) FROM {s}.messages WHERE email_source_id=:id',id=original)[0]==1
                assert row(target,'SELECT count(*) FROM {s}.messages WHERE email_source_id=:id',id=response)[0]==0
    run(scenario())
