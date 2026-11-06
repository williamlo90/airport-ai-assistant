"""Private mail SQL/real restricted roles with controlled source and transport."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict,replace
from datetime import datetime,timedelta,timezone
import hashlib,json,os,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text

from test_private_source_graph_neon import target as graph_target,schema_target,app,draft,human,staff,count,run,evidence
from test_private_source_schema_neon import scalar
from airport_assistant.infrastructure.database import migrations as migration_module
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.jobs import WorkerDatabase
from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.infrastructure.database.replies import PostgresReplies
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.infrastructure.database.publication import PostgresPublication
from airport_assistant.modules.jobs.service import Lease
from airport_assistant.modules.mail.service import OutboundMail,SendResult
from airport_assistant.modules.mail.delivery import ReconcileMail,DeliveryEvidence
from airport_assistant.modules.mail.private_outbound import PrivateMailContext,envelope_hash,fingerprint
from airport_assistant.modules.mail.private_replies import PrivateReplies,ReplyCandidate,command_for
from airport_assistant.modules.mail.history import StaffMailHistory
from airport_assistant.modules.conversations.publication import StaffMessages,StaffMessage
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import ReportPayload
from airport_assistant.modules.mail.private_outbound import MailAuthorizationDenied
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.modules.cases.lifecycle import CaseLifecycle,StatusChange

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_PRIVATE_MAIL_TESTS')!='1',reason='Opt-in isolated private mail SQL.')
HEAD='0031_private_mail'

class TracedReplies(PostgresReplies):
    async def private_apply(self,lease,expected,command,*,before_commit,result_check):
        def verify(result):
            try:result_check(result)
            except Exception as error:
                trace=error.__traceback__
                while trace:
                    values=trace.tb_frame.f_locals
                    if trace.tb_frame.f_code.co_name=='verify_result':
                        wanted=values.get('expected',{})
                        if isinstance(result,dict):print('Receipt mismatched fields:',[key for key,value in wanted.items() if key not in result or result[key]!=value or type(result[key]) is not type(value)])
                        candidate=values.get('candidate')
                        if candidate and isinstance(result,dict) and isinstance(result.get('final_context'),dict):
                            wanted_context=PrivateReplies.final_basis(candidate,command,result.get('app_message_id'))
                            print('Final context mismatched fields:',[key for key,value in wanted_context.items() if result['final_context'].get(key)!=value])
                    trace=trace.tb_next
                raise
        try:return await super().private_apply(lease,expected,command,before_commit=before_commit,result_check=verify)
        except DatabaseError as error:
            diagnostic=getattr(error.__context__,'diag',None)
            print('Private reply SQL:',error.sqlstate,getattr(diagnostic,'message_primary',None));raise

@pytest.fixture(scope='module')
def target(graph_target):
    t=graph_target
    with t.engine.begin() as c:
        t.account=c.execute(text(f'''INSERT INTO "{t.schema}".email_accounts(tenant_id,provider,provider_account_ref,sending_identity,reply_address,active,account_alias,inbound_address)
            VALUES(:tenant,'resend','controlled-mail','support@airport.example.com','reply@airport.example.com',true,'private-mail','reply@airport.example.com') RETURNING id'''),dict(tenant=t.a)).scalar_one()
    async def public_case():
        async with app(t) as h:
            conv=await h.conversations.create(t.identity,'en')
            await h.conversations.append(t.identity,conv.id,uuid4(),'Please help with general information.')
            report=ReportPayload(category='information',service_type='general',summary='General information',customer_report='Please help.',contact_email='customer@example.com')
            created=await h.drafts.create(t.identity,conv.id,'create_case',report)
            return await h.cases.confirm(t.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())
    t.public_case=run(public_case()).case_id
    t.legacy_email,_=inbound(t,None,'Legacy challenge source.',authenticated=False)
    t.legacy_challenge=challenge_record(t,t.public_case,t.legacy_email,private=False)
    with t.engine.connect() as c:
        t.legacy_before=c.execute(text(f'SELECT created_at,expires_at,code_hash,state,attempts FROM "{t.schema}".email_reply_challenges WHERE id=:id'),dict(id=t.legacy_challenge)).one()
    original=migration_module.command.upgrade
    def checked(*args,**kwargs):
        try:return original(*args,**kwargs)
        except Exception as error:
            underlying=getattr(error,'orig',error);diag=getattr(underlying,'diag',None)
            print('Private mail migration:',type(underlying).__name__,getattr(underlying,'sqlstate',None),getattr(diag,'message_primary',None) or str(underlying).split('\n')[0][:220]);raise
    migration_module.command.upgrade=checked
    try:
        assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision=HEAD)==HEAD
    finally:migration_module.command.upgrade=original
    (Path(__file__).resolve().parents[3]/'local/team/private-mail-test-target.json').write_text(json.dumps(dict(schema=t.schema,graph_role=t.graph_owner)),encoding='utf-8')
    yield t

@asynccontextmanager
async def worker(t):
    db=WorkerDatabase(t.worker_url,schema=t.schema,pool_max=1)
    db.private_mail_required=True
    try:
        await db.open();yield db
    finally:await db.close()

def leased(t,job):
    worker_id,token=uuid4(),uuid4()
    with t.engine.begin() as c:
        row=c.execute(text(f'''UPDATE "{t.schema}".outbox_jobs SET state='leased',attempts=attempts+1,lease_worker_id=:worker,lease_token=:token,
            lease_generation=lease_generation+1,leased_at=statement_timestamp(),lease_until=statement_timestamp()+interval '300 seconds'
            WHERE id=:id RETURNING case_id,action_draft_id,kind,attempts,lease_generation'''),dict(worker=worker_id,token=token,id=job)).one()
    return Lease(t.a,job,row[0],row[1],row[2],row[3],row[4],worker_id,token)

async def prepared(t,h):
    created,_=await draft(t,h)
    result=await h.cases.confirm(t.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())
    with t.engine.connect() as c:job=scalar(c,t,'SELECT id FROM {s}.outbox_jobs WHERE case_id=:id AND kind=\'case_created\'',id=result.case_id)
    return created,result,leased(t,job)

def transport():return SimpleNamespace(send=AsyncMock(return_value=SendResult('accepted','controlled_'+uuid4().hex)))

def sender(t,db,h,out=None):
    out=out or transport()
    return OutboundMail(PostgresMail(db,t.schema),out,account_ref='controlled-mail',private_access=h.access,private_outbound=True),out

def test_upgrade_repeat_readiness_and_capability_acl(target):
    t=target
    assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision=HEAD)==HEAD
    async def scenario():
        async with worker(t) as db:
            async with db.transaction() as c:
                assert (await(await c.execute(f'SELECT "{t.schema}".private_mail_ready()')).fetchone())[0]
                status=(await(await c.execute(f'SELECT "{t.schema}".private_source_schema_status()')).fetchone())[0]
                assert all(status[key] for key in ('graph_contract','outbound_contract','inbound_contract','history_contract'))
            with t.engine.connect() as c:
                for table in ('cases','messages','email_messages','email_reply_state'):
                    assert not c.execute(text("SELECT has_table_privilege(:role,:table,'SELECT')"),dict(role=t.roles[3],table=t.schema+'.'+table)).scalar_one()
                assert not c.execute(text("SELECT has_function_privilege(:role,:function,'EXECUTE')"),dict(role=t.roles[0],function=t.schema+'.mail_private_snapshot(uuid,uuid,uuid,uuid,bigint)')).scalar_one()
    run(scenario())

def test_canonical_hash_matches_python_unicode_nested_nulls(target):
    value=dict(z=['English','Bahasa Indonesia','日本語','café',None,True,123],a=dict(b='quotation " line\n',a=False))
    with target.engine.begin() as c:
        c.exec_driver_sql(f'SET LOCAL ROLE "{target.roles[5]}"')
        assert scalar(c,target,'SELECT {s}.mail_fingerprint(CAST(:value AS jsonb))',value=json.dumps(value,ensure_ascii=False))==fingerprint(value)

def test_actual_outbound_reservation_send_complete_and_no_duplicate(target):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            created,result,lease=await prepared(target,h)
            send,out=sender(target,db,h)
            outcome=await send(lease)
            assert outcome.state=='succeeded'
            assert out.send.await_count==1
            assert (await send(lease)).state=='succeeded' and out.send.await_count==1
            with target.engine.connect() as c:
                email=c.execute(text(f'SELECT id,delivery_status,resource_security_labels,envelope_hash,approved_envelope FROM "{target.schema}".email_messages WHERE job_id=:id'),dict(id=lease.id)).one()
                assert email[1]=='accepted' and email[2]==list(created.resource_security_labels) and email[3]==fingerprint(email[4])
                reconcile=c.execute(text(f"SELECT resource_security_labels FROM \"{target.schema}\".outbox_jobs WHERE email_message_id=:id AND kind='email_reconcile'"),dict(id=email[0])).one()
                assert reconcile[0]==email[2]
    run(scenario())

@pytest.mark.parametrize('scope',['booking:read','payment_status:read','payment_details:read','outage'])
def test_private_send_denial_never_reserves_or_calls_provider(target,scope):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,_,lease=await prepared(target,h)
            if scope=='outage':h.grants.failure=True
            else:h.grants.denied={scope}
            send,out=sender(target,db,h)
            before=count(target,'email_messages')
            assert (await send(lease)).state=='blocked'
            assert count(target,'email_messages')==before
            out.send.assert_not_awaited()
    run(scenario())

def inbound(t,outbound_id,body='Please check this payment.',*,authenticated=True,challenge=None,response=None):
    with t.engine.begin() as c:
        ref=scalar(c,t,'SELECT coalesce(rfc_message_id,requested_rfc_message_id) FROM {s}.email_messages WHERE id=:id',id=outbound_id) if outbound_id else None
        id=uuid4();origin=uuid4();inbox=uuid4()
        digest=hashlib.sha256((str(id)+body).encode()).hexdigest()
        c.execute(text(f'''INSERT INTO "{t.schema}".email_inbox(tenant_id,id,email_account_id,provider_event_id,provider_message_id,event_type,event_at,body_digest,processing_state)
            VALUES(:tenant,:id,:account,:event,:provider,'email.received',clock_timestamp(),:digest,'received')'''),
            dict(tenant=t.a,id=inbox,account=t.account,event='event_'+id.hex,provider='inbound_'+id.hex,digest=digest))
        c.execute(text(f'''INSERT INTO "{t.schema}".outbox_jobs(tenant_id,id,kind,email_inbox_id,dedupe_key,payload,replay_policy)
            VALUES(:tenant,:id,'email_fetch',:inbox,:key,CAST(:payload AS jsonb),'safe')'''),
            dict(tenant=t.a,id=origin,inbox=inbox,key='fetch:'+str(inbox),payload=json.dumps(dict(inbox_id=str(inbox)))))
        c.execute(text(f'''INSERT INTO "{t.schema}".email_messages(tenant_id,id,email_account_id,job_id,direction,sender_address,recipient_address,subject,body,
            provider_message_id,verification_state,delivery_status,in_reply_to,references_header,inbound_to,content_digest,inbound_authentication,verification_challenge_id,verification_response_hash)
            VALUES(:tenant,:id,:account,:origin,'inbound','customer@example.com','reply@airport.example.com','Synthetic reply',:body,:provider,'pending',NULL,:ref,:ref,
              'reply@airport.example.com',:digest,CAST(:auth AS jsonb),:challenge,:response)'''),
            dict(tenant=t.a,id=id,account=t.account,origin=origin,body=body,provider='inbound_'+id.hex,ref=ref,digest=digest,
                auth=json.dumps(dict(dkim='pass' if authenticated else 'fail',dmarc='pass' if authenticated else 'fail')),challenge=challenge,response=response))
        c.execute(text(f'UPDATE "{t.schema}".email_inbox SET processing_state=\'processed\',email_message_id=:email WHERE id=:id'),dict(email=id,id=inbox))
        job=scalar(c,t,"SELECT id FROM {s}.outbox_jobs WHERE reply_email_id=:id AND kind='email_reply'",id=id)
    return id,leased(t,job)

def challenge_record(t,case,email,*,private=True,expired=False):
    id=uuid4()
    with t.engine.begin() as c:
        contact=scalar(c,t,'SELECT followup_contact_id FROM {s}.cases WHERE id=:id',id=case)
        c.execute(text(f'''UPDATE "{t.schema}".email_reply_state SET case_id=:case,principal_id=:principal,state='challenged',
            pending_text=(SELECT body FROM "{t.schema}".email_messages WHERE id=:email) WHERE email_id=:email'''),dict(case=case,principal=t.principal,email=email))
        columns=',created_at,expires_at' if expired else ''
        values=",clock_timestamp()-interval '6 minutes',clock_timestamp()-interval '1 minute'" if expired else ''
        c.execute(text(f'''INSERT INTO "{t.schema}".email_reply_challenges(tenant_id,id,email_id,case_id,email_account_id,principal_id,contact_id,recipient,code_hash{columns})
            VALUES(:tenant,:id,:email,:case,:account,:principal,:contact,'customer@example.com',:hash{values})'''),
            dict(tenant=t.a,id=id,email=email,case=case,account=t.account,principal=t.principal,contact=contact,hash='c'*64))
    return id

async def sent_case(t,h,db):
    created,result,lease=await prepared(t,h)
    send,out=sender(t,db,h)
    assert (await send(lease)).state=='succeeded'
    with t.engine.connect() as c:email=scalar(c,t,'SELECT id FROM {s}.email_messages WHERE job_id=:id',id=lease.id)
    return created,result,lease,email

def test_authenticated_private_reply_actual_receipt_history_and_duplicate(target):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            created,result,_,email=await sent_case(target,h,db)
            incoming,lease=inbound(target,email,'Tolong bantu periksa pembayaran ini. 日本語')
            replies=TracedReplies(db,target.schema)
            before=count(target,'messages')
            consumer=PrivateReplies(replies,h.access)
            assert (await consumer(lease)).state=='succeeded'
            assert count(target,'messages')==before+1
            assert (await consumer(lease)).state=='succeeded' and count(target,'messages')==before+1
            with target.engine.connect() as c:
                row=c.execute(text(f'SELECT state,app_message_id,resource_security_labels FROM "{target.schema}".email_reply_state WHERE email_id=:id'),dict(id=incoming)).one()
                assert row[0]=='published' and row[1] and row[2]==list(created.resource_security_labels)
                assert scalar(c,target,'SELECT count(*) FROM {s}.case_history WHERE email_source_id=:id',id=incoming)==1
                assert scalar(c,target,'SELECT version FROM {s}.cases WHERE id=:id',id=result.case_id)==2
    run(scenario())

def test_unproven_private_reply_stays_out_of_customer_messages(target):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,result,_,email=await sent_case(target,h,db)
            incoming,lease=inbound(target,email,'Untrusted quarantined text.',authenticated=False)
            before=count(target,'messages')
            assert (await PrivateReplies(TracedReplies(db,target.schema),h.access)(lease)).state=='succeeded'
            assert count(target,'messages')==before
            with target.engine.connect() as c:
                assert scalar(c,target,'SELECT state FROM {s}.email_reply_state WHERE email_id=:id',id=incoming)=='review'
                assert scalar(c,target,'SELECT app_message_id FROM {s}.email_messages WHERE id=:id',id=incoming) is None
    run(scenario())

def test_private_staff_approved_email_and_scoped_history(target):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            conv,case,person,chat=await human(target,h)
            repository=PostgresPublication(h.db,target.schema,mail_enabled=True)
            messages=StaffMessages(repository,h.access,private_disclosure=True)
            command=StaffMessage(conversation_id=conv,channel='email',body='Your payment is recorded as paid. 日本語',language='en',
                expected_version=chat['case_version'],expected_conversation_version=chat['version'],expected_epoch=chat['generation_epoch'])
            saved=await messages.send(person,case,command,uuid4().hex)
            with target.engine.connect() as c:job=scalar(c,target,"SELECT id FROM {s}.outbox_jobs WHERE source_message_id=:id AND kind='email_send'",id=saved['message_id'])
            lease=leased(target,job);send,out=sender(target,db,h)
            assert (await send(lease)).state=='succeeded'
            assert out.send.await_args.args[0].text==command.body
            history=StaffMailHistory(PostgresCaseLifecycle(h.db,target.schema),private_history=True)
            deliveries=await history.read(person,case,'deliveries')
            assert any(item['delivery_status']=='accepted' for item in deliveries['items'])
            assert all('recipient' not in item and 'body' not in item for item in deliveries['items'])
            with pytest.raises(AuthError):await history.read(target.identity,case,'deliveries')
            with target.engine.begin() as c:c.execute(text(f'DELETE FROM "{target.schema}".membership_queues WHERE membership_id=:id'),dict(id=person.memberships[0].id))
            with pytest.raises(AuthError):await history.read(person,case,'deliveries')
    run(scenario())

def test_private_reconcile_updates_known_delivery_without_resending(target):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,result,origin,email=await sent_case(target,h,db)
            with target.engine.begin() as c:
                c.execute(text(f"UPDATE \"{target.schema}\".outbox_jobs SET state='succeeded',lease_worker_id=NULL,lease_token=NULL,leased_at=NULL,lease_until=NULL,completed_at=clock_timestamp(),result_code='OK' WHERE id=:id"),dict(id=origin.id))
                id=scalar(c,target,"SELECT id FROM {s}.outbox_jobs WHERE email_message_id=:id AND kind='email_reconcile'",id=email)
                receipt=scalar(c,target,'SELECT provider_message_id FROM {s}.email_messages WHERE id=:id',id=email)
            lease=leased(target,id)
            out=SimpleNamespace(lookup=AsyncMock(return_value=DeliveryEvidence(receipt,'delivered')),send=AsyncMock(side_effect=AssertionError('Never resend.')))
            handler=ReconcileMail(PostgresMail(db,target.schema,private_delivery=True),out,account_refs=['controlled-mail'])
            h.grants.denied={'booking:read','payment_details:read'}
            assert (await handler(lease)).state=='succeeded'
            out.send.assert_not_awaited()
            with target.engine.connect() as c:
                assert scalar(c,target,'SELECT delivery_status FROM {s}.email_messages WHERE id=:id',id=email)=='delivered'
                assert scalar(c,target,'SELECT resource_security_labels FROM {s}.email_delivery_events WHERE email_message_id=:id',id=email)
    run(scenario())

@pytest.mark.parametrize('kind',['valid','wrong_code','expired'])
def test_bound_challenge_consumption_attempt_limit_and_expiry(target,kind):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,result,_,email=await sent_case(target,h,db)
            source,_=inbound(target,email,'Original private request.',authenticated=False)
            challenge=challenge_record(target,result.case_id,source,expired=kind=='expired')
            before=count(target,'messages')
            consumer=PrivateReplies(TracedReplies(db,target.schema),h.access)
            attempts=5 if kind=='wrong_code' else 1
            for _ in range(attempts):
                control,lease=inbound(target,email,'VERIFY <redacted>',authenticated=False,challenge=challenge,response=('d' if kind=='wrong_code' else 'c')*64)
                assert (await consumer(lease)).state=='succeeded'
                with target.engine.connect() as c:
                    assert scalar(c,target,'SELECT state FROM {s}.email_reply_state WHERE email_id=:id',id=control)==('verification_reply' if kind=='valid' else 'review')
            with target.engine.connect() as c:
                state,used=c.execute(text(f'SELECT state,attempts FROM "{target.schema}".email_reply_challenges WHERE id=:id'),dict(id=challenge)).one()
                assert (state,used)==({'valid':('consumed',1),'wrong_code':('failed',5),'expired':('pending',0)}[kind])
                assert count(target,'messages')==before+(1 if kind=='valid' else 0)
                assert scalar(c,target,'SELECT state FROM {s}.email_reply_state WHERE email_id=:id',id=source)==('published' if kind=='valid' else 'review' if kind=='wrong_code' else 'challenged')
    run(scenario())

def test_public_challenge_defaults_survive_populated_upgrade_and_new_insert(target):
    with target.engine.connect() as c:
        assert c.execute(text(f'SELECT created_at,expires_at,code_hash,state,attempts FROM "{target.schema}".email_reply_challenges WHERE id=:id'),dict(id=target.legacy_challenge)).one()==target.legacy_before
    email,_=inbound(target,None,'Public challenge after upgrade.',authenticated=False)
    id=challenge_record(target,target.public_case,email,private=False)
    with target.engine.connect() as c:
        assert scalar(c,target,'SELECT resource_security_labels FROM {s}.email_reply_challenges WHERE id=:id',id=id)==[]

@pytest.mark.parametrize('scope',['booking:read','payment_status:read','payment_details:read','outage'])
def test_reply_source_denial_has_no_publication(target,scope):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,_,_,email=await sent_case(target,h,db)
            incoming,lease=inbound(target,email)
            if scope=='outage':h.grants.failure=True
            else:h.grants.denied={scope}
            before=count(target,'messages')
            assert (await PrivateReplies(TracedReplies(db,target.schema),h.access)(lease)).state=='blocked'
            assert count(target,'messages')==before
            with target.engine.connect() as c:assert scalar(c,target,'SELECT state FROM {s}.email_reply_state WHERE email_id=:id',id=incoming)=='pending'
    run(scenario())

def test_candidate_read_only_and_forged_command_context_or_fence_rejected(target):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,_,_,email=await sent_case(target,h,db)
            incoming,lease=inbound(target,email)
            repo=TracedReplies(db,target.schema)
            with target.engine.connect() as c:initial=scalar(c,target,'SELECT to_jsonb(r) FROM {s}.email_reply_state r WHERE email_id=:id',id=incoming)
            raw=await repo.private_candidate(lease)
            candidate=ReplyCandidate.capture(lease,raw,time.monotonic());command=command_for(candidate,time.monotonic)
            with target.engine.connect() as c:assert scalar(c,target,'SELECT to_jsonb(r) FROM {s}.email_reply_state r WHERE email_id=:id',id=incoming)==initial
            before=count(target,'messages')
            for expected,proposed,active in [(raw,dict(command,body='Forged replacement'),lease),(dict(raw,source_email_id=str(uuid4())),command,lease),(raw,command,replace(lease,generation=lease.generation+1))]:
                assert await repo.private_apply(active,expected,proposed,before_commit=lambda:None,result_check=lambda value:None) is False
            assert count(target,'messages')==before
    run(scenario())

def test_lost_reply_result_recovers_once_and_postgrant_revocation_blocks(target):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,result,_,email=await sent_case(target,h,db)
            incoming,lease=inbound(target,email)
            repo=TracedReplies(db,target.schema);original=repo.private_apply
            async def lost(*args,**kwargs):await original(*args,**kwargs);raise RuntimeError('Synthetic lost commit result')
            repo.private_apply=lost
            before=count(target,'messages')
            assert (await PrivateReplies(repo,h.access)(lease)).state=='unknown'
            assert count(target,'messages')==before+1
            repo.private_apply=original
            assert (await PrivateReplies(repo,h.access)(lease)).state=='succeeded' and count(target,'messages')==before+1
            _,blocked=inbound(target,email,'Second private request.')
            def revoke():
                with target.engine.begin() as c:c.execute(text(f'UPDATE "{target.schema}".case_participants SET revoked_at=clock_timestamp() WHERE case_id=:id'),dict(id=result.case_id))
            h.grants.hook=revoke
            assert (await PrivateReplies(repo,h.access)(blocked)).state=='blocked'
            assert count(target,'messages')==before+1
    run(scenario())

@pytest.mark.parametrize('change',['source_revoke','recipient_change','fence_change'])
def test_pretransport_change_aborts_or_holds_unsent_without_send(target,change):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,_,lease=await prepared(target,h)
            send,out=sender(target,db,h);repo=send._repository;original=repo.private_send_snapshot
            async def changed(*args,**kwargs):
                if change=='source_revoke':h.grants.denied={'booking:read'}
                elif change=='recipient_change':
                    with target.engine.begin() as c:c.execute(text(f"UPDATE \"{target.schema}\".contacts SET email_address='changed@example.com' WHERE principal_id=:id"),dict(id=target.principal))
                else:
                    with target.engine.begin() as c:c.execute(text(f'UPDATE "{target.schema}".outbox_jobs SET lease_token=:token WHERE id=:id'),dict(token=uuid4(),id=lease.id))
                return await original(*args,**kwargs)
            repo.private_send_snapshot=changed
            try:
                assert (await send(lease)).state==('unknown' if change=='fence_change' else 'blocked')
                out.send.assert_not_awaited()
                with target.engine.connect() as c:assert scalar(c,target,'SELECT delivery_status FROM {s}.email_messages WHERE job_id=:id',id=lease.id)==('sending' if change=='fence_change' else 'failed')
            finally:
                if change=='recipient_change':
                    with target.engine.begin() as c:c.execute(text(f"UPDATE \"{target.schema}\".contacts SET email_address='customer@example.com' WHERE principal_id=:id"),dict(id=target.principal))
    run(scenario())

def test_postsend_revocation_still_records_outcome_and_lost_begin_never_resends(target):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,_,lease=await prepared(target,h)
            async def accepted(envelope):h.grants.denied={'booking:read'};return SendResult('accepted','controlled_after_revoke')
            out=SimpleNamespace(send=AsyncMock(side_effect=accepted));send,_=sender(target,db,h,out)
            assert (await send(lease)).state=='succeeded'
            assert (await send(lease)).state=='succeeded' and out.send.await_count==1
            h.grants.denied=set()
            _,_,other=await prepared(target,h)
            send,out=sender(target,db,h);repo=send._repository;original=repo.private_begin
            async def lost(*args,**kwargs):await original(*args,**kwargs);raise RuntimeError('Synthetic lost reservation result')
            repo.private_begin=lost
            assert (await send(other)).state=='unknown'
            repo.private_begin=original
            assert (await send(other)).state=='unknown'
            out.send.assert_not_awaited()
            with target.engine.connect() as c:assert scalar(c,target,'SELECT count(*) FROM {s}.email_messages WHERE job_id=:id',id=other.id)==1
    run(scenario())

def test_reservation_rejects_forged_content_routes_and_attempt_identity(target):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,_,lease=await prepared(target,h)
            send,out=sender(target,db,h);repo=send._repository
            raw=await repo.private_snapshot(lease);envelope=send._envelope(lease,raw)
            before=count(target,'email_messages')
            for fake in (replace(envelope,text='Unapproved replacement'),replace(envelope,recipient='other@example.com'),replace(envelope,app_message_id=uuid4()),replace(envelope,account_id=uuid4())):
                assert await repo.private_begin(lease,raw,fake,before_commit=lambda:None) is False
            # A stable-attempt mismatch must fail inside the real transaction,
            # including rollback of the automatically queued reconcile child.
            id=uuid4();fake=replace(envelope,id=id,delivery_key=f'mail/{target.a.hex}/{id.hex}',rfc_message_id=f'<{id.hex}@airport.example.com>')
            with pytest.raises(MailAuthorizationDenied):await repo.private_begin(lease,raw,fake,before_commit=lambda:None)
            assert count(target,'email_messages')==before
            out.send.assert_not_awaited()
    run(scenario())

@pytest.mark.parametrize('kind',['abort','complete'])
def test_expired_fence_while_waiting_email_lock_cannot_mutate_attempt(target,kind):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,_,lease=await prepared(target,h)
            send,_=sender(target,db,h);repo=send._repository
            raw=await repo.private_snapshot(lease);envelope=send._envelope(lease,raw)
            await repo.private_begin(lease,raw,envelope,before_commit=lambda:None)
            with target.engine.begin() as c:c.execute(text(f"UPDATE \"{target.schema}\".outbox_jobs SET lease_until=clock_timestamp()+interval '1 second' WHERE id=:id"),dict(id=lease.id))
            lock=target.engine.connect();tx=lock.begin()
            pending=None
            try:
                lock.execute(text(f'SELECT id FROM "{target.schema}".email_messages WHERE id=:id FOR UPDATE'),dict(id=envelope.id))
                operation=repo.abort_unsent(lease,envelope.id,envelope_hash(envelope)) if kind=='abort' else repo.private_complete(lease,envelope.id,envelope_hash(envelope),SendResult('accepted','late_result'))
                pending=asyncio.create_task(operation)
                await asyncio.sleep(1.25)
                tx.commit()
                assert await pending is False
            finally:
                if tx.is_active:tx.rollback()
                lock.close()
                if pending and not pending.done():pending.cancel();await asyncio.gather(pending,return_exceptions=True)
            with target.engine.connect() as c:
                assert c.execute(text(f'SELECT delivery_status,delivery_version,provider_message_id FROM "{target.schema}".email_messages WHERE id=:id'),dict(id=envelope.id)).one()==('sending',1,None)
    run(scenario())

@pytest.mark.parametrize('tamper',['disabled_guard','missing_history','public_execute','wrong_owner','unvalidated_constraint'])
def test_readiness_detects_real_catalog_drift(target,tamper):
    async def scenario():
        async with worker(target) as db:
            with target.engine.begin() as c:
                if tamper=='disabled_guard':c.exec_driver_sql(f'ALTER TABLE "{target.schema}".email_messages DISABLE TRIGGER private_mail_graph')
                elif tamper=='missing_history':c.exec_driver_sql(f'ALTER FUNCTION "{target.schema}".mail_private_history_context(uuid,text) RENAME TO temporarily_hidden_history')
                elif tamper=='public_execute':c.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION "{target.schema}".mail_private_history_context(uuid,text) TO PUBLIC')
                elif tamper=='wrong_owner':
                    c.exec_driver_sql(f'GRANT CREATE ON SCHEMA "{target.schema}" TO "{target.graph_owner}"')
                    c.exec_driver_sql(f'ALTER FUNCTION "{target.schema}".mail_private_snapshot(uuid,uuid,uuid,uuid,bigint) OWNER TO "{target.graph_owner}"')
                    c.exec_driver_sql(f'REVOKE CREATE ON SCHEMA "{target.schema}" FROM "{target.graph_owner}"')
                else:
                    c.exec_driver_sql(f'ALTER TABLE "{target.schema}".email_messages DROP CONSTRAINT private_labels_canonical')
                    c.exec_driver_sql(f'ALTER TABLE "{target.schema}".email_messages ADD CONSTRAINT private_labels_canonical CHECK("{target.schema}".source_labels_valid(resource_security_labels)) NOT VALID')
            try:
                async with db.transaction() as c:assert (await(await c.execute(f'SELECT "{target.schema}".private_mail_ready()')).fetchone())[0] is False
                runtime=RuntimeDatabase(target.url,pool_max=1,schema=target.schema)
                runtime.require_private_mail_history(target.schema)
                try:
                    with pytest.raises(DatabaseError):await runtime.open()
                finally:await runtime.close()
            finally:
                with target.engine.begin() as c:
                    if tamper=='disabled_guard':c.exec_driver_sql(f'ALTER TABLE "{target.schema}".email_messages ENABLE TRIGGER private_mail_graph')
                    elif tamper=='missing_history':c.exec_driver_sql(f'ALTER FUNCTION "{target.schema}".temporarily_hidden_history(uuid,text) RENAME TO mail_private_history_context')
                    elif tamper=='public_execute':c.exec_driver_sql(f'REVOKE ALL ON FUNCTION "{target.schema}".mail_private_history_context(uuid,text) FROM PUBLIC')
                    elif tamper=='wrong_owner':
                        c.exec_driver_sql(f'GRANT CREATE ON SCHEMA "{target.schema}" TO "{target.roles[5]}"')
                        c.exec_driver_sql(f'ALTER FUNCTION "{target.schema}".mail_private_snapshot(uuid,uuid,uuid,uuid,bigint) OWNER TO "{target.roles[5]}"')
                        c.exec_driver_sql(f'REVOKE CREATE ON SCHEMA "{target.schema}" FROM "{target.roles[5]}"')
                    else:c.exec_driver_sql(f'ALTER TABLE "{target.schema}".email_messages VALIDATE CONSTRAINT private_labels_canonical')
            async with db.transaction() as c:assert (await(await c.execute(f'SELECT "{target.schema}".private_mail_ready()')).fetchone())[0]
    run(scenario())

def test_private_runtime_history_admission_and_large_history_pagination(target):
    async def scenario():
        runtime=RuntimeDatabase(target.url,pool_max=1,schema=target.schema)
        runtime.require_private_mail_history(target.schema)
        try:
            await runtime.open()
            async with runtime.transaction() as c:assert (await(await c.execute("SELECT current_setting('TimeZone')")).fetchone())[0]=='UTC'
        finally:await runtime.close()
        async with app(target) as h:
            conv,case,person,chat=await human(target,h)
            # Valid synthetic rows at the SQL fixture layer, in one bounded
            # serial request. No 101 model calls, sends or worker processes.
            with target.engine.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id',:tenant,true),set_config('app.principal_id',:principal,true),set_config('app.actor_kind','staff',true),set_config('app.session_id',:session,true)"),
                    dict(tenant=str(target.a),principal=str(person.session.principal_id),session=str(person.session.id)))
                c.exec_driver_sql(f'''DO $$ DECLARE i int;mid uuid;seq bigint;v bigint;epoch bigint;d uuid;casever bigint;BEGIN
                  SELECT origin_draft_id,version INTO d,casever FROM "{target.schema}".cases WHERE id='{case}';
                  FOR i IN 1..101 LOOP
                    SELECT version,generation_epoch INTO v,epoch FROM "{target.schema}".conversations WHERE id='{conv}';
                    UPDATE "{target.schema}".conversations SET version=version+1,last_sequence=last_sequence+1 WHERE id='{conv}' RETURNING last_sequence INTO seq;
                    mid:=gen_random_uuid();
                    INSERT INTO "{target.schema}".messages(tenant_id,id,conversation_id,author_principal_id,author_type,source_kind,body,language,client_message_id,sequence,created_session_id,
                      sender_membership_id,staff_request_key,request_hash,basis_case_version,basis_conversation_version,basis_epoch,delivery_channel)
                      VALUES('{target.a}',mid,'{conv}','{person.session.principal_id}','staff','staff_reply','Synthetic queued staff reply.','en',gen_random_uuid(),seq,'{person.session.id}',
                        '{person.memberships[0].id}',gen_random_uuid()::text,repeat('f',64),casever,v,epoch,'email');
                    INSERT INTO "{target.schema}".outbox_jobs(tenant_id,case_id,action_draft_id,kind,source_message_id,dedupe_key,payload)
                      VALUES('{target.a}','{case}',d,'email_send',mid,'fixture:'||mid::text,jsonb_build_object('case_id','{case}','draft_id',d::text));
                  END LOOP;END $$''')
            h.grants.failure=True  # staff reads use operational ACL, no personal source grant
            history=StaffMailHistory(PostgresCaseLifecycle(h.db,target.schema),private_history=True)
            result=await history.read(person,case,'deliveries')
            assert result['has_more'] and len(result['items'])==100
            assert all(item['delivery_status']=='queued' and item['needs_review'] is False for item in result['items'])
            assert all(set(item)<=set(('job_id','app_message_id','email_message_id','delivery_status','delivery_version','needs_review','review_reason','queued_at','provider_status_at','last_checked_at')) for item in result['items'])
    run(scenario())

def test_invalid_sql_reply_receipt_rolls_back_real_writes_before_commit(target):
    class Cursor:
        def __init__(self,cursor):self.cursor=cursor
        async def fetchone(self):
            row=await self.cursor.fetchone()
            return (dict(row[0],message_case_id=str(uuid4())),)
    class Connection:
        def __init__(self,connection):self.connection=connection
        async def execute(self,query,params):
            cursor=await self.connection.execute(query,params)
            return Cursor(cursor) if 'email_private_reply_apply' in query.as_string() else cursor
    class Corrupt:
        def __init__(self,db):self.db=db
        @asynccontextmanager
        async def transaction(self):
            async with self.db.transaction() as c:yield Connection(c)
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,case,_,email=await sent_case(target,h,db)
            incoming,lease=inbound(target,email)
            before=(count(target,'messages'),count(target,'case_history'))
            assert (await PrivateReplies(TracedReplies(Corrupt(db),target.schema),h.access)(lease)).state=='unknown'
            assert (count(target,'messages'),count(target,'case_history'))==before
            with target.engine.connect() as c:
                assert scalar(c,target,'SELECT state FROM {s}.email_reply_state WHERE email_id=:id',id=incoming)=='pending'
                assert scalar(c,target,'SELECT version FROM {s}.cases WHERE id=:id',id=case.case_id)==1
    run(scenario())

@pytest.mark.parametrize('body',['Thank you','CONTINUE SAME ISSUE\nThe same issue remains.'])
def test_closed_private_case_thanks_stays_closed_explicit_continuation_reopens(target,body):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            conv,case,person,chat=await human(target,h)
            lifecycle=CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema),h.access)
            await lifecycle.status(person,case,StatusChange(expected_version=chat['case_version'],expected_conversation_version=chat['version'],status='closed',
                next_action='No further action.',close_reason='resolved',resolution='Synthetic resolution.'))
            with target.engine.connect() as c:
                job=scalar(c,target,"SELECT id FROM {s}.outbox_jobs WHERE case_id=:id AND kind='case_created'",id=case)
                before=scalar(c,target,'SELECT version FROM {s}.cases WHERE id=:id',id=case)
            lease=leased(target,job);send,_=sender(target,db,h)
            assert (await send(lease)).state=='succeeded'
            with target.engine.connect() as c:email=scalar(c,target,'SELECT id FROM {s}.email_messages WHERE job_id=:id',id=job)
            _,reply=inbound(target,email,body)
            assert (await PrivateReplies(TracedReplies(db,target.schema),h.access)(reply)).state=='succeeded'
            with target.engine.connect() as c:
                status,version,reason=c.execute(text(f'SELECT status,version,close_reason FROM "{target.schema}".cases WHERE id=:id'),dict(id=case)).one()
                if body=='Thank you':assert (status,version,reason)==('closed',before,'resolved')
                else:assert status=='in_progress' and version==before+1 and reason is None
    run(scenario())

@pytest.mark.parametrize('kind',['abort','complete'])
def test_fence_expiry_during_update_raises_and_rolls_back(target,kind):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,_,lease=await prepared(target,h)
            send,_=sender(target,db,h);repo=send._repository
            raw=await repo.private_snapshot(lease);envelope=send._envelope(lease,raw)
            await repo.private_begin(lease,raw,envelope,before_commit=lambda:None)
            with target.engine.begin() as c:
                c.exec_driver_sql(f'''CREATE FUNCTION "{target.schema}".test_fence_pause() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
                    BEGIN PERFORM pg_sleep(1.1);RETURN NEW;END $$''')
                c.exec_driver_sql(f'REVOKE ALL ON FUNCTION "{target.schema}".test_fence_pause() FROM PUBLIC')
                c.exec_driver_sql(f'CREATE TRIGGER zz_test_fence_pause BEFORE UPDATE ON "{target.schema}".email_messages FOR EACH ROW EXECUTE FUNCTION "{target.schema}".test_fence_pause()')
                c.execute(text(f"UPDATE \"{target.schema}\".outbox_jobs SET lease_until=clock_timestamp()+interval '600 milliseconds' WHERE id=:id"),dict(id=lease.id))
            try:
                with pytest.raises(DatabaseError) as error:
                    if kind=='abort':await repo.abort_unsent(lease,envelope.id,envelope_hash(envelope))
                    else:await repo.private_complete(lease,envelope.id,envelope_hash(envelope),SendResult('accepted','expired_during_write'))
                assert error.value.sqlstate=='23514'
            finally:
                with target.engine.begin() as c:
                    c.exec_driver_sql(f'DROP TRIGGER zz_test_fence_pause ON "{target.schema}".email_messages')
                    c.exec_driver_sql(f'DROP FUNCTION "{target.schema}".test_fence_pause()')
            with target.engine.connect() as c:
                assert c.execute(text(f'SELECT delivery_status,delivery_version,provider_message_id FROM "{target.schema}".email_messages WHERE id=:id'),dict(id=envelope.id)).one()==('sending',1,None)
    run(scenario())

def test_tokenized_reply_route_is_bound_and_correlates_without_rfc_header(target):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            _,_,lease=await prepared(target,h)
            out=transport()
            send=OutboundMail(PostgresMail(db,target.schema),out,account_ref='controlled-mail',private_access=h.access,private_outbound=True,
                reply_secret=b'synthetic-private-thread-secret-for-test-only')
            assert (await send(lease)).state=='succeeded'
            envelope=out.send.await_args.args[0]
            assert envelope.reply_token_hash and '+r-' in envelope.reply_to
            incoming,reply=inbound(target,envelope.id,'Reply matched by the authenticated route token.')
            with target.engine.begin() as c:
                c.execute(text(f'UPDATE "{target.schema}".email_messages SET in_reply_to=NULL,references_header=NULL,inbound_to=:route WHERE id=:id'),dict(route=envelope.reply_to,id=incoming))
            assert (await PrivateReplies(TracedReplies(db,target.schema),h.access)(reply)).state=='succeeded'
    run(scenario())

def test_approved_provenance_is_immutable_and_completion_is_idempotent(target):
    async def scenario():
        from sqlalchemy.exc import IntegrityError
        async with app(target) as h,worker(target) as db:
            _,_,lease,email=await sent_case(target,h,db)
            with target.engine.connect() as c:
                stored=c.execute(text(f'SELECT envelope_hash,provider_message_id,delivery_version,resource_security_labels FROM "{target.schema}".email_messages WHERE id=:id'),dict(id=email)).one()
            repo=PostgresMail(db,target.schema)
            assert await repo.private_complete(lease,email,stored[0],SendResult('accepted',stored[1])) is True
            assert await repo.private_complete(lease,email,stored[0],SendResult('failed')) is False
            for mutation in ("body=body||' forged'","recipient_address='other@example.com'","approved_envelope=approved_envelope||'{\"text\":\"forged\"}'::jsonb"):
                with pytest.raises(IntegrityError) as error:
                    with target.engine.begin() as c:c.execute(text(f'UPDATE "{target.schema}".email_messages SET '+mutation+' WHERE id=:id'),dict(id=email))
                assert error.value.orig.sqlstate=='23514'
            with target.engine.connect() as c:
                assert c.execute(text(f'SELECT envelope_hash,provider_message_id,delivery_version,resource_security_labels FROM "{target.schema}".email_messages WHERE id=:id'),dict(id=email)).one()==stored
    run(scenario())
