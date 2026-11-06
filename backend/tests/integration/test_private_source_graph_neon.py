"""Actual private graph/RLS with controlled source/model, never real email/LLM."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_private_source_schema_neon import target as schema_target, scalar, catalog, public_rows
from airport_assistant.composition import database_loop_factory
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.connections import RuntimeDatabase, DatabaseError
from airport_assistant.infrastructure.database.provisioning import enable_login, runtime_url
from airport_assistant.infrastructure.database.conversation_repository import PostgresConversations
from airport_assistant.infrastructure.database.case_repository import PostgresCases, CaseSQL
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.infrastructure.database.agent_runs import PostgresAgentRuns
from airport_assistant.infrastructure.database.jobs import WorkerDatabase
from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.modules.conversations.service import ConversationService
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.modules.drafts.models import ReportPayload, DraftError
from airport_assistant.modules.cases.service import CaseService
from airport_assistant.modules.cases.lifecycle import CaseLifecycle
from airport_assistant.modules.identity.auth import Session, AuthError
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.source.labels import PrivateSourceAccess
from airport_assistant.modules.source.evidence import SourceEvidence, ObservedSource
from airport_assistant.modules.source.models import Booking, Transaction, Policy, SourceFailure
from airport_assistant.modules.agent.service import AgentFoundation
from airport_assistant.modules.agent.workflow import AgentWorkflow, RunRequest
from airport_assistant.modules.agent.models import Decision, DraftProposal
from airport_assistant.modules.mail.service import OutboundMail
from airport_assistant.modules.jobs.service import Lease
from airport_assistant.infrastructure.database.publication import PostgresPublication
from airport_assistant.infrastructure.database.translations import PostgresTranslations
from airport_assistant.modules.conversations.ai_publication import PrivateAIPublication
from airport_assistant.modules.agent.private_publication import PrivatePublicationPreparation
from airport_assistant.modules.agent.models import Clarification,ToolCall
from airport_assistant.modules.agent.translation import StaffTranslations,TranslationRequest,TranslationApproval
from airport_assistant.modules.conversations.publication import StaffMessages,StaffMessage
from airport_assistant.modules.conversations.handoff import ChatHandoff,AcceptChat
from airport_assistant.modules.identity.membership import Membership
from test_staff_auth import insert

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_PRIVATE_GRAPH_TESTS') != '1', reason='Opt-in isolated private graph SQL.')
HEAD = '0030_private_source_graph'


@pytest.fixture(scope='module')
def target(schema_target):
    t = schema_target
    name = 'source_graph_' + hashlib.sha256(t.schema.encode()).hexdigest()[:24]
    with t.engine.begin() as c:
        c.execute(text(f"UPDATE \"{t.schema}\".tenants SET supported_languages=ARRAY['en','id']"))
        before = public_rows(c,t.schema)
    try:
        # Only sanitized DB diagnostics: never repr(exception), parameters or URL.
        from airport_assistant.infrastructure.database import migrations as migrations_module
        original=migrations_module.command.upgrade
        def checked_upgrade(*args,**kwargs):
            try:return original(*args,**kwargs)
            except Exception as error:
                original_error=getattr(error,'orig',error)
                diagnostic=getattr(original_error,'diag',None)
                print('Graph migration:',type(original_error).__name__,getattr(original_error,'sqlstate',None),
                    getattr(diagnostic,'message_primary',None) or str(original_error).split('\n')[0][:240])
                raise
        migrations_module.command.upgrade=checked_upgrade
        try:
            assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision=HEAD) == HEAD
        finally:migrations_module.command.upgrade=original
        with t.engine.begin() as c:
            session, expires = c.execute(text(f'SELECT id,expires_at FROM "{t.schema}".sessions WHERE principal_id=:principal'),dict(principal=t.principal)).one()
            c.execute(text(f'''INSERT INTO "{t.schema}".contacts(tenant_id,principal_id,email_address,email_lookup_key,verified_at)
              VALUES(:tenant,:principal,'customer@example.com','customer@example.com',clock_timestamp())'''),dict(tenant=t.a,principal=t.principal))
            organization=c.execute(text(f'INSERT INTO "{t.schema}".organizations(tenant_id,name) VALUES(:tenant,\'Graph support\') RETURNING id'),dict(tenant=t.a)).scalar_one()
            c.execute(text(f'INSERT INTO "{t.schema}".service_queues(tenant_id,organization_id,name,service_type) VALUES(:tenant,:org,\'Parking support\',\'parking\')'),dict(tenant=t.a,org=organization))
            password=secrets.token_urlsafe(32)
            enable_login(c,t.roles[3],password)
        t.identity=AccessIdentity(Session(session,t.a,t.principal,'customer',expires,'b'*64))
        t.worker_url=runtime_url(t.url,t.roles[3],password)
        t.before_graph_rows=before
        t.graph_owner=name
        yield t
    finally:
        # Parent fixture removes its eight roles afterwards. This removes the
        # additional exact schema-owned graph role and objects first.
        assert t.schema.startswith('src_sql_test_') and name=='source_graph_'+hashlib.sha256(t.schema.encode()).hexdigest()[:24]
        with t.engine.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{t.schema}" CASCADE')
            c.exec_driver_sql(f'DROP ROLE IF EXISTS "{name}"')
        with t.engine.connect() as c:
            assert not c.execute(text('SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:schema) OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)'),dict(schema=t.schema,role=name)).scalar_one()
        report=Path(__file__).resolve().parents[3]/'local/team/private-graph-owner-cleanups'/f'{t.schema}.json'
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(dict(schema=t.schema,graph_role=name,graph_role_removed=True)),encoding='utf-8')


def run(coroutine):
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:
        return runner.run(coroutine)


class DB:
    def __init__(self, db):
        self.db=db;self.active=0
    @asynccontextmanager
    async def transaction(self,*args,**kwargs):
        try:
            async with self.db.transaction(*args,**kwargs) as c:
                self.active+=1
                try:yield c
                finally:self.active-=1
        except DatabaseError as error:
            diagnostic=getattr(error.__context__,'diag',None)
            print('Graph SQL:',error.sqlstate,getattr(diagnostic,'message_primary',None),getattr(diagnostic,'constraint_name',None))
            raise


class Grants:
    def __init__(self, db):
        self.db=db;self.calls=0;self.denied=set();self.failure=False;self.hook=None
    async def has_grants(self, **kwargs):
        assert self.db.active==0  # Source I/O is never inside a SQL lock unit.
        self.calls+=1
        if self.hook:
            hook,self.hook=self.hook,None
            hook()
        if self.failure:raise SourceFailure(503)
        return not bool(self.denied & set(kwargs['required_scopes']))


def evidence(tenant):
    stamp=datetime(2026,11,6,tzinfo=timezone.utc)
    book=Booking(reference='B123',source_updated_at=stamp,observed_at=stamp,service_type='parking',status='confirmed',
        start_at=stamp,end_at=stamp+timedelta(days=1),service_timezone='UTC',location='Terminal 1',policy_reference='POL1',allowed_scopes=('booking:read',))
    payment=Transaction(reference='P123',source_updated_at=stamp,observed_at=stamp,status='paid',booking_reference='B123',
        allowed_scopes=('payment_status:read','payment_details:read'),currency='IDR',amount_minor=10000,currency_exponent=0,refund_status='none')
    policy=Policy(reference='POL1',source_updated_at=stamp,observed_at=stamp,service_type='parking',version=1,effective_from=stamp,
        instructions={'en':'Contact support for help.','id':'Hubungi petugas untuk bantuan.'})
    return SourceEvidence.capture(tenant,(ObservedSource(book),ObservedSource(payment),ObservedSource(policy,book))),book,payment,policy


@asynccontextmanager
async def app(t):
    runtime=RuntimeDatabase(t.url,pool_max=1,acquire_timeout=5)
    try:
        await runtime.open()
        db=DB(runtime);grants=Grants(db);access=PrivateSourceAccess(grants)
        conversations=ConversationService(PostgresConversations(db,t.schema),access)
        repository=PostgresCases(db,t.schema)
        drafts=DraftService(repository,access);cases=CaseService(repository,access)
        yield SimpleNamespace(db=db,grants=grants,access=access,conversations=conversations,drafts=drafts,cases=cases,repository=repository)
    finally:await runtime.close()


def payload():
    return ReportPayload(category='transaction_issue',service_type='parking',summary='Help with parking payment',
        customer_report='Please check B123 and P123.',booking_reference='B123',transaction_reference='P123',contact_email='customer@example.com')


async def draft(t,h):
    conv=await h.conversations.create(t.identity,'en')
    await h.conversations.append(t.identity,conv.id,uuid4(),'Please check B123 and P123.')
    before=await h.conversations.get(t.identity,conv.id)
    observed,*_=evidence(t.a)
    result=await h.drafts.create(t.identity,conv.id,'create_case',payload(),source_evidence=observed)
    return result,before


def count(t,table):
    with t.engine.connect() as c:return scalar(c,t,'SELECT count(*) FROM {s}.'+table)


def test_upgrade_keeps_old_rows_and_narrow_graph_owner_and_mail_closed(target):
    t=target
    assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision=HEAD)==HEAD
    with t.engine.connect() as c:
        after=public_rows(c,t.schema)
        for table,rows in t.before_graph_rows.items():
            assert all(original in after[table] for original in rows)
        status=scalar(c,t,'SELECT {s}.private_source_schema_status()')
        assert status['graph_contract'] is True and not any(status[k] for k in ('outbound_contract','inbound_contract','history_contract'))
        row=c.execute(text('SELECT rolcanlogin,rolinherit,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=:role'),dict(role=t.graph_owner)).one()
        assert row==(False,False,False,False)
        for table,column in [('messages','body'),('email_messages','body'),('sessions','token_hash'),('cases','customer_report')]:
            assert not c.execute(text("SELECT has_column_privilege(:role,:table,:column,'SELECT')"),dict(role=t.graph_owner,table=t.schema+'.'+table,column=column)).scalar_one()
        assert not c.execute(text("SELECT pg_has_role(:runtime,:owner,'MEMBER')"),dict(runtime=t.roles[0],owner=t.graph_owner)).scalar_one()
        for name in ('inherit_private_source_graph','validate_private_source_graph'):
            assert not c.execute(text("SELECT has_function_privilege(:runtime,:name,'EXECUTE')"),dict(runtime=t.roles[0],name=t.schema+'.'+name+'()')).scalar_one()


def test_private_evidence_draft_and_confirmed_case_have_actual_inherited_graph(target):
    async def scenario():
        async with app(target) as h:
            created,before=await draft(target,h)
            current=await h.conversations.get(target.identity,created.conversation_id)
            assert (current.version,current.generation_epoch)==(before.version,before.generation_epoch)
            assert list(current.resource_security_labels)==list(created.resource_security_labels)
            result=await h.cases.confirm(target.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())
            receipt=await h.cases.status(target.identity,created.id)
            assert receipt['case_id']==result.case_id
            view=await CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema),h.access).get(target.identity,result.case_id)
            assert view['status']=='new'
            with target.engine.connect() as c:
                stored=scalar(c,target,'SELECT source_observations FROM {s}.cases WHERE id=:id',id=result.case_id)
                assert stored==created.source_evidence.to_records()
                for table,key in [('cases','id'),('messages','case_id'),('action_consents','case_id'),('case_history','case_id'),
                    ('audit_events','case_id'),('outbox_jobs','case_id'),('idempotency_records','result_case_id')]:
                    values=c.execute(text(f'SELECT resource_security_labels FROM "{target.schema}".{table} WHERE {key}=:id'),dict(id=result.case_id)).scalars().all()
                    assert values and all(value==list(created.resource_security_labels) for value in values)
    run(scenario())


def test_private_append_and_event_inherit_current_root_and_source_denial_holds_output(target):
    async def scenario():
        async with app(target) as h:
            created,_=await draft(target,h)
            message=await h.conversations.append(target.identity,created.conversation_id,uuid4(),'Additional customer message.')
            history=await h.conversations.customer_history(target.identity,created.conversation_id)
            assert history[-1].id==message.id
            with target.engine.connect() as c:
                assert scalar(c,target,'SELECT resource_security_labels FROM {s}.messages WHERE id=:id',id=message.id)==list(created.resource_security_labels)
                assert scalar(c,target,'SELECT resource_security_labels FROM {s}.conversation_events WHERE message_id=:id',id=message.id)==list(created.resource_security_labels)
            for scope in ('booking:read','payment_status:read','payment_details:read'):
                h.grants.denied={scope}
                with pytest.raises(AuthError):await h.conversations.customer_history(target.identity,created.conversation_id)
            h.grants.denied=set();h.grants.failure=True
            with pytest.raises(SourceFailure):await h.drafts.get(target.identity,created.id)
    run(scenario())


def test_denied_source_never_creates_a_case_or_outbox(target):
    async def scenario():
        async with app(target) as h:
            created,_=await draft(target,h)
            before=(count(target,'cases'),count(target,'outbox_jobs'))
            h.grants.denied={'payment_details:read'}
            with pytest.raises(AuthError):await h.cases.confirm(target.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())
            assert (count(target,'cases'),count(target,'outbox_jobs'))==before
    run(scenario())


def test_agent_private_proposal_read_reservations_and_atomic_draft_restart(target):
    async def scenario():
        async with app(target) as h:
            observed,book,payment,policy=evidence(target.a)
            model=SimpleNamespace(decide=AsyncMock(return_value=Decision(kind='draft',proposal=DraftProposal(
                category='transaction_issue',service_type='parking',summary='Help with parking payment'))))
            source=SimpleNamespace(booking=AsyncMock(return_value=book),transaction=AsyncMock(return_value=payment),policy=AsyncMock(return_value=policy))
            foundation=AgentFoundation(model,h.conversations,h.drafts,source,h.access)
            engine=AgentWorkflow(foundation,PostgresAgentRuns(h.db,target.schema),private_persistence=True)
            conv=await h.conversations.create(target.identity,'en')
            message=await h.conversations.append(target.identity,conv.id,uuid4(),'Please check B123 and P123.')
            request=RunRequest(conversation_id=conv.id,message_id=message.id,request_key=uuid4(),mode='draft',booking_references=('B123',),transaction_references=('P123',))
            id=await engine.start(target.identity,request)
            await engine.plan(await engine.load(target.identity,id))
            planned=await engine.load(target.identity,id)
            assert planned.run['phase']=='draft' and set(planned.run['proposal'])=={'draft','source_fact_hash'}
            result=await engine.draft(planned)
            assert result.draft.source_evidence==observed
            restored=await AgentWorkflow(foundation,PostgresAgentRuns(h.db,target.schema),private_persistence=True).load(target.identity,id)
            assert restored.result.draft==result.draft and restored.run['read_calls']==9
            assert model.decide.await_count==1
    run(scenario())


def staff(t,case):
    with t.engine.begin() as c:
        org,queue=c.execute(text(f'SELECT organization_id,queue_id FROM "{t.schema}".cases WHERE id=:id'),dict(id=case)).one()
        principal=insert(c,t.schema,'principals',tenant_id=t.a)
        member=insert(c,t.schema,'staff_memberships',tenant_id=t.a,principal_id=principal,organization_id=org)
        insert(c,t.schema,'membership_roles',tenant_id=t.a,membership_id=member,role='cs')
        insert(c,t.schema,'membership_queues',tenant_id=t.a,membership_id=member,organization_id=org,queue_id=queue)
        email='staff-'+uuid4().hex+'@example.com'
        contact=insert(c,t.schema,'contacts',tenant_id=t.a,principal_id=principal,email_address=email,email_lookup_key=email,verified_at=datetime.now(timezone.utc))
        version=uuid4()
        # Session/ACL fixture only; no password hashing or login work is run.
        c.execute(text(f'INSERT INTO "{t.schema}".staff_credentials(tenant_id,principal_id,contact_id,credential_version,password_hash) VALUES(:tenant,:principal,:contact,:version,:hash)'),
            dict(tenant=t.a,principal=principal,contact=contact,version=version,hash='$argon2id$'+'x'*64))
        session=insert(c,t.schema,'sessions',tenant_id=t.a,principal_id=principal,session_kind='staff',staff_credential_version=version,token_hash=secrets.token_hex(32),csrf_secret_hash='b'*64,expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    return AccessIdentity(Session(session,t.a,principal,'staff',datetime.now(timezone.utc)+timedelta(hours=1),'b'*64),
        (Membership(member,org,1,frozenset({'cs'}),frozenset({queue})),))


async def human(t,h):
    conv=await h.conversations.create(t.identity,'en')
    await h.conversations.append(t.identity,conv.id,uuid4(),'Please help with B123 and P123.')
    observed,*_=evidence(t.a)
    created=await h.drafts.create(t.identity,conv.id,'request_handoff',payload(),source_evidence=observed)
    receipt=await h.cases.confirm(t.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())
    person=staff(t,receipt.case_id)
    handoff=ChatHandoff(PostgresCaseLifecycle(h.db,t.schema))
    chat=await handoff.get(person,conv.id)
    await handoff.accept(person,conv.id,AcceptChat(expected_version=chat['version'],expected_case_version=chat['case_version']),uuid4().hex)
    chat=await handoff.get(person,conv.id)
    return conv.id,receipt.case_id,person,chat


def test_actual_private_ai_generation_message_event_receipt_and_waiting_refresh(target):
    async def scenario():
        async with app(target) as h:
            created,_=await draft(target,h)
            message=await h.conversations.append(target.identity,created.conversation_id,uuid4(),'Please explain the next step for B123 and P123.')
            _,book,payment,policy=evidence(target.a)
            model=SimpleNamespace(advance=AsyncMock(return_value=Decision(kind='clarify',clarification=Clarification(question='Would you like staff assistance?'))))
            source=SimpleNamespace(booking=AsyncMock(return_value=book),transaction=AsyncMock(return_value=payment),policy=AsyncMock(return_value=policy))
            foundation=AgentFoundation(model,h.conversations,h.drafts,source,h.access)
            engine=AgentWorkflow(foundation,PostgresAgentRuns(h.db,target.schema),private_persistence=True)
            id=await engine.start(target.identity,RunRequest(conversation_id=created.conversation_id,message_id=message.id,request_key=uuid4(),booking_references=('B123',),transaction_references=('P123',)))
            result=await engine.plan(await engine.load(target.identity,id))
            work=await engine.load(target.identity,id)
            observations=tuple([await foundation.execute_read(work.scope,ToolCall(call_id=name,name=name,arguments={'reference':reference}))
                for name,reference in [('read_booking','B123'),('read_transaction','P123'),('read_booking_policy','B123')]])
            result=replace(result,observations=observations)
            publication=PrivateAIPublication(PostgresPublication(h.db,target.schema),h.access)
            from airport_assistant.modules.conversations.publication import GenerationTicket
            ticket=GenerationTicket(id,secrets.token_urlsafe(32))
            await publication.capture_bound(target.identity,created.conversation_id,ticket,work.scope.conversation_version,work.scope.generation_epoch,requirements=result.labels)
            prepared=await PrivatePublicationPreparation(engine).prepare(target.identity,id,result)
            import time
            try:response=await publication.publish(target.identity,ticket,prepared.body,private_input=prepared.proof)
            finally:print('Private AI proof elapsed seconds:',round(time.monotonic()-prepared.proof.checked_at,3))
            assert response['state']=='published'
            assert (await publication.receipt(target.identity,ticket))['state']=='published'
            restored=await engine.load(target.identity,id)
            assert restored.run['phase']=='waiting_user' and restored.run['read_calls']==3
            with target.engine.connect() as c:
                for table,key in [('ai_generations','id'),('messages','generation_id')]:
                    assert scalar(c,target,'SELECT resource_security_labels FROM {s}.'+table+' WHERE '+key+'=:id',id=id)==list(created.resource_security_labels)
                assert scalar(c,target,'SELECT count(*) FROM {s}.conversation_events WHERE message_id=:id',id=response['message_id'])==1
            h.grants.denied={'payment_status:read'}
            with pytest.raises(AuthError):await publication.receipt(target.identity,ticket)
    run(scenario())


def test_private_handoff_translation_human_send_graph_and_revoked_queue(target):
    async def scenario():
        async with app(target) as h:
            conv,case,person,chat=await human(target,h)
            repository=PostgresTranslations(h.db,target.schema)
            messages=StaffMessages(repository,h.access,private_disclosure=True)
            model=SimpleNamespace(translate=AsyncMock(return_value='Your request is being reviewed.'))
            translations=StaffTranslations(repository,messages,model,private_translation=True)
            request=TranslationRequest(conversation_id=conv,channel='chat',body='Permintaan Anda sedang ditinjau.',language='id',target_language='en',
                expected_version=chat['case_version'],expected_conversation_version=chat['version'],expected_epoch=chat['generation_epoch'],request_key=uuid4())
            ready=await translations.prepare(person,case,request)
            assert ready['state']=='ready' and ready['source_context_hash'] and ready['resource_security_labels']
            assert count(target,'messages')>0
            key=uuid4().hex
            sent=await translations.approve(person,case,ready['id'],TranslationApproval(text=ready['translated_body'],approved=True,expected_revision=ready['revision']),key)
            saved=await translations.get(person,case,ready['id'])
            assert saved['state']=='sent' and saved['message_id']==sent['message_id'] and model.translate.await_count==1
            with target.engine.connect() as c:
                expected=scalar(c,target,'SELECT resource_security_labels FROM {s}.cases WHERE id=:id',id=case)
                for table,keycol in [('messages','id'),('conversation_events','message_id')]:
                    assert scalar(c,target,'SELECT resource_security_labels FROM {s}.'+table+' WHERE '+keycol+'=:id',id=sent['message_id'])==expected
                assert scalar(c,target,'SELECT resource_security_labels FROM {s}.chat_transitions WHERE case_id=:id ORDER BY created_at DESC LIMIT 1',id=case)==expected
            with target.engine.begin() as c:
                c.execute(text(f'DELETE FROM "{target.schema}".membership_queues WHERE membership_id=:id'),dict(id=person.memberships[0].id))
            before=count(target,'messages')
            with pytest.raises(AuthError):await translations.get(person,case,ready['id'])
            assert count(target,'messages')==before
    run(scenario())


def test_source_snapshot_substitution_rolls_back_entire_confirmation_graph(target):
    async def scenario():
        async with app(target) as h:
            created,_=await draft(target,h)
            _,book,payment,policy=evidence(target.a)
            changed=book.model_copy(update={'status':'cancelled'})
            substituted=SourceEvidence.capture(target.a,(ObservedSource(changed),ObservedSource(payment),ObservedSource(policy,changed)))
            tampered=replace(created,source_evidence=substituted)
            before={table:count(target,table) for table in ('cases','messages','action_consents','case_history','audit_events','outbox_jobs','idempotency_records')}
            with pytest.raises(DatabaseError) as error:
                async with h.repository.transaction(target.identity.database_context) as tx:
                    contact=await tx.verified_contact('customer@example.com')
                    result=await tx.create_case(tampered,uuid4(),uuid4(),contact,contact,uuid4())
                    assert await tx.mark_committed(created,result)
                    from airport_assistant.modules.cases.models import request_hash
                    await tx.record_key(uuid4().hex,request_hash(created.id,created.version,created.payload_hash),created,result)
            assert error.value.sqlstate=='23514'
            assert before=={table:count(target,table) for table in before}
            assert (await h.drafts.get(target.identity,created.id)).state=='pending'
    run(scenario())


@pytest.mark.parametrize('kind',['other_customer','foreign_tenant','anonymous','revoked'])
def test_actual_private_rls_denies_unrelated_or_revoked_sessions(target,kind):
    async def scenario():
        async with app(target) as h:
            created,_=await draft(target,h)
            tenant=target.b if kind=='foreign_tenant' else target.a
            with target.engine.begin() as c:
                principal=None if kind=='anonymous' else target.principal if kind=='revoked' else insert(c,target.schema,'principals',tenant_id=tenant)
                id=insert(c,target.schema,'sessions',tenant_id=tenant,principal_id=principal,session_kind='anonymous' if kind=='anonymous' else 'customer',
                    token_hash=secrets.token_hex(32),csrf_secret_hash='b'*64,expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
            actor=AccessIdentity(Session(id,tenant,principal,'anonymous' if kind=='anonymous' else 'customer',datetime.now(timezone.utc)+timedelta(hours=1),'b'*64))
            if kind=='revoked':
                # Same owner, otherwise-valid new session: prove that live
                # revocation, rather than unrelated ownership, removes access.
                assert await h.conversations.customer_history(actor,created.conversation_id)
                with target.engine.begin() as c:
                    c.execute(text(f'UPDATE "{target.schema}".sessions SET revoked_at=clock_timestamp() WHERE id=:id'),dict(id=id))
            with pytest.raises(AuthError):await h.drafts.get(actor,created.id)
            with pytest.raises(AuthError):await h.conversations.customer_history(actor,created.conversation_id)
    run(scenario())


def test_restricted_worker_legacy_snapshot_denies_private_graph_without_transport(target):
    async def scenario():
        async with app(target) as h:
            created,_=await draft(target,h)
            result=await h.cases.confirm(target.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())
            worker=WorkerDatabase(target.worker_url,schema=target.schema,pool_max=1)
            try:
                await worker.open()
                with target.engine.begin() as c:
                    id=scalar(c,target,'SELECT id FROM {s}.outbox_jobs WHERE case_id=:id',id=result.case_id)
                    worker_id,token=uuid4(),uuid4()
                    c.execute(text(f'''UPDATE "{target.schema}".outbox_jobs SET state='leased',attempts=1,lease_generation=1,
                        lease_worker_id=:worker,lease_token=:token,leased_at=clock_timestamp(),lease_until=clock_timestamp()+interval '300 seconds' WHERE id=:id'''),
                        dict(worker=worker_id,token=token,id=id))
                    # Test the classification guard independently of accounts,
                    # lease rejection or transport configuration.
                    c.exec_driver_sql(f'SET LOCAL ROLE "{target.roles[5]}"')
                    assert not scalar(c,target,'SELECT {s}.legacy_source_job_public(:tenant,:id)',tenant=target.a,id=id)
                lease=Lease(target.a,id,result.case_id,created.id,'case_created',1,1,worker_id,token)
                mail=PostgresMail(worker,target.schema)
                assert await mail.snapshot(lease) is None
                assert await mail.delivery_snapshot(lease) is None
                async with worker.transaction() as c:
                    for name in ('email_reply_snapshot','email_challenge_snapshot'):
                        assert (await(await c.execute(f'SELECT "{target.schema}".{name}(%s,%s,%s,%s,%s)',
                            (target.a,id,lease.worker_id,lease.token,1))).fetchone())[0] is None
            finally:await worker.close()
    run(scenario())


def test_concurrent_drafts_preserve_monotone_union_and_old_review_hash(target):
    async def scenario():
        from airport_assistant.modules.source.labels import merge_labels
        async with app(target) as first,app(target) as second:
            conv=await first.conversations.create(target.identity,'en')
            await first.conversations.append(target.identity,conv.id,uuid4(),'Please check my parking bookings.')
            _,book,_,_=evidence(target.a)
            bundles=[SourceEvidence.capture(target.a,(ObservedSource(book.model_copy(update={'reference':reference})),)) for reference in ('B234','B345')]
            reports=[payload().model_copy(update={'booking_reference':reference,'transaction_reference':None}) for reference in ('B234','B345')]
            async def create(h,index):
                return await h.drafts.create(target.identity,conv.id,'create_case',reports[index],source_evidence=bundles[index])
            results=await asyncio.gather(create(first,0),create(second,1),return_exceptions=True)
            successes=[r for r in results if not isinstance(r,Exception)]
            assert successes
            original=successes[0]
            for index,result in enumerate(results):
                if isinstance(result,Exception):
                    assert isinstance(result,DraftError) and result.code=='SOURCE_CONTEXT_CHANGED'
                    await create(first,index)
            current=await first.conversations.get(target.identity,conv.id)
            assert list(current.resource_security_labels)==merge_labels(*[b.labels for b in bundles]).to_records()
            with target.engine.connect() as c:
                stored=c.execute(text(f'SELECT payload_hash,version,resource_security_labels FROM "{target.schema}".action_drafts WHERE id=:id'),dict(id=original.id)).one()
                assert stored==(original.payload_hash,original.version,list(original.resource_security_labels))
            if list(original.resource_security_labels)!=list(current.resource_security_labels):
                with pytest.raises(DraftError) as error:await first.cases.confirm(target.identity,original.id,original.version,original.payload_hash,uuid4().hex,uuid4())
                assert error.value.code=='SOURCE_CONTEXT_CHANGED'
            from sqlalchemy.exc import IntegrityError
            with pytest.raises(IntegrityError):
                with target.engine.begin() as c:
                    c.execute(text(f'UPDATE "{target.schema}".conversations SET resource_security_labels=\'[]\'::jsonb WHERE id=:id'),dict(id=conv.id))
            assert (await first.conversations.get(target.identity,conv.id)).resource_security_labels==current.resource_security_labels
    run(scenario())


def test_public_case_compatibility_and_private_child_cannot_enter_public_case(target):
    async def scenario():
        from sqlalchemy.exc import IntegrityError
        async with app(target) as h:
            conv=await h.conversations.create(target.identity,'en')
            await h.conversations.append(target.identity,conv.id,uuid4(),'Please help me understand parking information.')
            report=payload().model_copy(update={'booking_reference':None,'transaction_reference':None})
            created=await h.drafts.create(target.identity,conv.id,'create_case',report)
            result=await h.cases.confirm(target.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())
            assert (await h.cases.status(target.identity,created.id))['case_id']==result.case_id
            with target.engine.begin() as c:
                job=scalar(c,target,'SELECT id FROM {s}.outbox_jobs WHERE case_id=:id',id=result.case_id)
                c.exec_driver_sql(f'SET LOCAL ROLE "{target.roles[5]}"')
                assert scalar(c,target,'SELECT {s}.legacy_source_job_public(:tenant,:id)',tenant=target.a,id=job)
            with pytest.raises(IntegrityError) as error:
                with target.engine.begin() as c:
                    # Even the fixture admin cannot commit a private descendant
                    # under a public immutable case and leave its old job public.
                    c.execute(text(f'UPDATE "{target.schema}".outbox_jobs SET resource_security_labels=CAST(:labels AS jsonb) WHERE id=:id'),
                        dict(id=job,labels=json.dumps(evidence(target.a)[0].labels.to_records())))
            assert error.value.orig.diag.message_primary=='Private child exceeds case aggregate'
            with target.engine.connect() as c:
                assert scalar(c,target,'SELECT resource_security_labels FROM {s}.outbox_jobs WHERE id=:id',id=job)==[]
            # An old public case/job can have an origin conversation which
            # later acquires private context. Historical consent is not resealed;
            # the legacy gate must reject the CURRENT whole root anyway.
            await h.drafts.create(target.identity,conv.id,'create_case',payload(),source_evidence=evidence(target.a)[0])
            with target.engine.begin() as c:
                assert scalar(c,target,'SELECT resource_security_labels FROM {s}.cases WHERE id=:id',id=result.case_id)==[]
                assert scalar(c,target,'SELECT payload_hash FROM {s}.action_drafts WHERE id=:id',id=created.id)==created.payload_hash
                assert scalar(c,target,'SELECT resource_security_labels FROM {s}.outbox_jobs WHERE id=:id',id=job)==[]
                c.exec_driver_sql(f'SET LOCAL ROLE "{target.roles[5]}"')
                assert not scalar(c,target,'SELECT {s}.legacy_source_job_public(:tenant,:id)',tenant=target.a,id=job)
    run(scenario())


def test_changed_source_facts_move_private_draft_run_to_review_without_draft_insert(target):
    async def scenario():
        async with app(target) as h:
            _,book,payment,policy=evidence(target.a)
            model=SimpleNamespace(decide=AsyncMock(return_value=Decision(kind='draft',proposal=DraftProposal(category='transaction_issue',service_type='parking',summary='Help with parking payment'))))
            source=SimpleNamespace(booking=AsyncMock(return_value=book),transaction=AsyncMock(return_value=payment),policy=AsyncMock(return_value=policy))
            foundation=AgentFoundation(model,h.conversations,h.drafts,source,h.access)
            engine=AgentWorkflow(foundation,PostgresAgentRuns(h.db,target.schema),private_persistence=True)
            conv=await h.conversations.create(target.identity,'en')
            message=await h.conversations.append(target.identity,conv.id,uuid4(),'Please check B123 and P123.')
            id=await engine.start(target.identity,RunRequest(conversation_id=conv.id,message_id=message.id,request_key=uuid4(),mode='draft',booking_references=('B123',),transaction_references=('P123',)))
            await engine.plan(await engine.load(target.identity,id))
            before=count(target,'action_drafts')
            source.booking.return_value=book.model_copy(update={'status':'cancelled'})
            result=await engine.draft(await engine.load(target.identity,id))
            assert result.status=='review' and result.reason=='source_context_changed'
            assert count(target,'action_drafts')==before
            with target.engine.connect() as c:
                assert scalar(c,target,'SELECT phase FROM {s}.agent_runs WHERE id=:id',id=id)=='review'
    run(scenario())


def test_context_reset_and_chat_recipient_helper_are_scoped_and_fail_closed(target):
    async def scenario():
        async with app(target) as h:
            async with h.db.transaction(target.identity.database_context) as c:
                values=await(await c.execute("SELECT current_setting('app.tenant_id'),current_setting('app.principal_id'),current_setting('app.actor_kind'),current_setting('app.session_id')")).fetchone()
                assert values==(str(target.a),str(target.principal),'customer',str(target.identity.session.id))
            async with h.db.transaction() as c:
                values=await(await c.execute("SELECT current_setting('app.tenant_id'),current_setting('app.principal_id'),current_setting('app.actor_kind'),current_setting('app.session_id')")).fetchone()
                assert values==('','','anonymous','')
            conv,case,person,chat=await human(target,h)
            async def recipient(identity,cid=case,vid=conv):
                async with h.db.transaction(identity.database_context if identity else None) as c:
                    return (await(await c.execute(f'SELECT "{target.schema}".source_chat_recipient(%s,%s)',(cid,vid))).fetchone())[0]
            assert await recipient(target.identity) is None and await recipient(None) is None
            assert await recipient(person,uuid4()) is None and await recipient(person,case,uuid4()) is None
            assert (await recipient(person))['participant_active'] is True
            async with h.db.transaction(person.database_context) as c:
                assert await(await c.execute(f'SELECT id FROM "{target.schema}".principals WHERE id=%s',(target.principal,))).fetchone() is None
            with target.engine.begin() as c:
                c.execute(text(f'UPDATE "{target.schema}".principals SET active=false WHERE id=:id'),dict(id=target.principal))
            try:
                assert (await recipient(person))['principal_active'] is False
            finally:
                with target.engine.begin() as c:c.execute(text(f'UPDATE "{target.schema}".principals SET active=true WHERE id=:id'),dict(id=target.principal))
            with target.engine.begin() as c:
                c.execute(text(f'UPDATE "{target.schema}".case_participants SET revoked_at=clock_timestamp() WHERE case_id=:id'),dict(id=case))
            assert (await recipient(person))['participant_active'] is False
            messages=StaffMessages(PostgresPublication(h.db,target.schema),h.access,private_disclosure=True)
            before=count(target,'messages')
            command=StaffMessage(conversation_id=conv,channel='chat',body='A reply that must stay blocked.',language='en',
                expected_version=chat['case_version'],expected_conversation_version=chat['version'],expected_epoch=chat['generation_epoch'])
            with pytest.raises(AuthError):await messages.send(person,case,command,uuid4().hex)
            assert count(target,'messages')==before
    run(scenario())
