"""Actual HTTP routes and restricted SQL; controlled identity/model/source, no paid calls."""
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from dataclasses import replace
import asyncio,json,os,traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID,uuid4

import pytest
from sqlalchemy import text
from fastapi import FastAPI
from httpx import AsyncClient,ASGITransport

from test_private_source_graph_neon import target as graph_target,schema_target,app as database_app,run,evidence,human,draft
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.agent_runs import PostgresAgentRuns
from airport_assistant.infrastructure.database.publication import PostgresPublication
from airport_assistant.infrastructure.database.conversation_events import PostgresConversationEvents
from airport_assistant.modules.agent.service import AgentFoundation
from airport_assistant.modules.agent.workflow import AgentWorkflow
from airport_assistant.modules.agent.delivery import AgentDelivery
from airport_assistant.modules.agent.models import Decision,Clarification,ToolCall
from airport_assistant.modules.conversations.ai_publication import PrivateAIPublication
from airport_assistant.modules.conversations.publication import AIPublication
from airport_assistant.modules.conversations.events import ConversationEvents,EventCursor
from airport_assistant.integrations.agent.graph import AgentGraph
from airport_assistant.api.agent import router as agent_router
from airport_assistant.api.conversations import router as conversation_router
from airport_assistant.api.conversation_events import router as event_router
from airport_assistant.api.dependencies import get_identity,get_mutation_identity
from airport_assistant.api.errors import install_error_handlers
from airport_assistant.api.boundary import RequestBoundary
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.identity.auth import Session,AuthError
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.cases.lifecycle import CaseLifecycle,StatusChange,ContinueCase
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_PRIVATE_AGENT_PORTAL_TESTS')!='1',reason='Explicit isolated private agent HTTP proof.')

@pytest.fixture(scope='module')
def target(graph_target):
    t=graph_target
    assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0031_private_mail')=='0031_private_mail'
    (Path(__file__).resolve().parents[3]/'local/team/private-agent-portal-target.json').write_text(json.dumps(dict(schema=t.schema,graph_role=t.graph_owner)),encoding='utf-8')
    yield t

class Model:
    def __init__(self):self.calls=0
    async def advance(self,*args,observations,**kwargs):
        self.calls+=1
        if not observations:return Decision(kind='tool',tool=ToolCall(call_id='book',name='read_booking',arguments={'reference':'B123'}))
        if len(observations)==1:return Decision(kind='tool',tool=ToolCall(call_id='payment',name='read_transaction',arguments={'reference':'P123'}))
        return Decision(kind='clarify',clarification=Clarification(question='Your parking booking is confirmed and the payment record says paid. What help do you need?'))

@asynccontextmanager
async def portal(t,*,private=True):
    async with database_app(t) as h:
        _,booking,payment,policy=evidence(t.a)
        source=SimpleNamespace(booking=AsyncMock(return_value=booking),transaction=AsyncMock(return_value=payment),policy=AsyncMock(return_value=policy))
        model=Model()
        foundation=AgentFoundation(model,h.conversations,h.drafts,source,h.access)
        graph=AgentGraph(AgentWorkflow(foundation,PostgresAgentRuns(h.db,t.schema),private_persistence=private))
        repository=PostgresPublication(h.db,t.schema)
        publication=AIPublication(repository,private_capability=PrivateAIPublication(repository,h.access) if private else None)
        delivery=AgentDelivery(graph,publication,b'controlled-local-portal-secret')
        events=ConversationEvents(PostgresConversationEvents(h.db,t.schema),EventCursor(b'controlled-local-portal-secret'),h.access)
        api=FastAPI();install_error_handlers(api)
        source_error=api.exception_handlers[SourceFailure]
        async def source_diagnostic(request,error):
            print('Private portal source boundary:',[(Path(frame.filename).name,frame.name,frame.lineno) for frame in traceback.extract_tb(error.__traceback__)])
            return await source_error(request,error)
        api.add_exception_handler(SourceFailure,source_diagnostic)
        api.add_middleware(RequestBoundary,max_body_bytes=1_048_576)
        api.state.services=SimpleNamespace(agent_delivery=delivery,ai_publication=publication,conversations=h.conversations,conversation_events=events)
        actor=[t.identity]
        api.dependency_overrides[get_identity]=lambda:actor[0]
        api.dependency_overrides[get_mutation_identity]=lambda:actor[0]
        for router in (agent_router,conversation_router,event_router):api.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            yield SimpleNamespace(client=client,h=h,model=model,source=source,delivery=delivery,actor=actor)

async def question(p,t,body='Please check B123 and P123.'):
    conv=await p.h.conversations.create(t.identity,'en')
    message=await p.h.conversations.append(t.identity,conv.id,uuid4(),body)
    command=dict(message_id=str(message.id),request_key=str(uuid4()),purpose='assist',service_type='parking',booking_references=['B123'],transaction_references=['P123'])
    return conv.id,command


def test_root_packet_waiting_for_legitimate_case_link_fails_without_parent_lock_fallback(target,monkeypatch):
    """Real writer holds the uncommitted root link; reader sees the old hint."""
    from airport_assistant.infrastructure.database.publication import PublicationSQL
    from airport_assistant.infrastructure.database.connections import RuntimeDatabase
    from airport_assistant.modules.drafts.models import ReportPayload
    async def scenario():
        async with portal(target) as p:
            conv=await p.h.conversations.create(target.identity,'en')
            await p.h.conversations.append(target.identity,conv.id,uuid4(),'Please help with airport information.')
            preview=await p.h.drafts.create(target.identity,conv.id,'request_handoff',ReportPayload(category='information',service_type='general',summary='Public case link',customer_report='Please help with airport information.',contact_email='customer@example.com'))
            linked=asyncio.Event();release=asyncio.Event();barrier=asyncio.Event()
            original=p.h.repository.transaction
            @asynccontextmanager
            async def controlled(context):
                async with original(context) as tx:
                    yield tx
                    row=await(await tx._c.execute(tx.query('SELECT case_id FROM {s}.conversations WHERE tenant_id=%s AND id=%s'),(target.a,conv.id))).fetchone()
                    if row and row[0] is not None and not linked.is_set():
                        linked.set();await release.wait()
            monkeypatch.setattr(p.h.repository,'transaction',controlled)
            read_db=RuntimeDatabase(target.url,pool_max=1,schema=target.schema)
            await read_db.open()
            writer=None;reader=None;pid=[];observed_wait=False
            try:
                writer=asyncio.create_task(p.h.cases.confirm(target.identity,preview.id,preview.version,preview.payload_hash,uuid4().hex,uuid4()))
                await asyncio.wait_for(linked.wait(),20)
                async def read():
                    async with read_db.transaction(target.identity.database_context) as c:
                        pid.append((await(await c.execute('SELECT pg_backend_pid()')).fetchone())[0])
                        class Traced:
                            async def execute(self,query,parameters=None):
                                sql=query.as_string(c) if hasattr(query,'as_string') else query
                                if 'SELECT root.id,(SELECT id FROM generation)' in sql:barrier.set()
                                return await c.execute(query,parameters)
                        return await PublicationSQL(Traced(),target.schema,target.identity.database_context).ai_root_publication_packet(uuid4(),conv.id)
                reader=asyncio.create_task(read())
                await asyncio.wait_for(barrier.wait(),10)
                for _ in range(8):
                    with target.engine.connect() as admin:
                        observed_wait=admin.execute(text("SELECT coalesce(bool_or(wait_event_type='Lock'),false) FROM pg_stat_activity WHERE pid=:pid"),dict(pid=pid[0])).scalar_one()
                    if observed_wait:break
                    await asyncio.sleep(.05)
                assert observed_wait,'Reader must actually wait on the uncommitted root link'
                release.set()
                committed=await asyncio.wait_for(writer,10)
                with pytest.raises(DraftError) as error:await asyncio.wait_for(reader,10)
                assert error.value.code=='SOURCE_CONTEXT_CHANGED'
                async with read_db.transaction(target.identity.database_context) as c:
                    # Originally linked roots route directly to the legacy port.
                    assert await PublicationSQL(c,target.schema,target.identity.database_context).ai_root_publication_packet(uuid4(),conv.id) is None
                assert (await p.h.conversations.get(target.identity,conv.id)).case_id==committed.case_id
            finally:
                release.set()
                for task in (writer,reader):
                    if task is not None:
                        if not task.done():task.cancel()
                        try:await task
                        except (asyncio.CancelledError,Exception):pass
                await read_db.close()
    run(scenario())

def test_private_http_source_answer_events_and_receipt_recovery_without_model_replay(target,monkeypatch):
    from airport_assistant.infrastructure.database.conversation_repository import ConversationSQL
    original=ConversationSQL.customer_read_projection;used=[]
    async def traced(self,*args,**kwargs):
        used.append(True)
        return await original(self,*args,**kwargs)
    monkeypatch.setattr(ConversationSQL,'customer_read_projection',traced)
    async def scenario():
        async with portal(target) as p:
            assert (await p.client.get('/api/v1/agent-capabilities')).json()=={'private_assistance':True}
            conv,command=await question(p,target)
            started=await p.client.post(f'/api/v1/conversations/{conv}/agent-runs',json=command)
            assert started.status_code==201,started.text
            id=started.json()['run_id']
            assert p.model.calls==0
            pending=await p.client.get(f'/api/v1/agent-runs/{id}/receipt')
            assert pending.json()==dict(run_id=id,state='unresolved') and p.model.calls==0
            answered=await p.client.post(f'/api/v1/agent-runs/{id}/resume')
            assert answered.status_code==200,answered.text
            assert answered.json()['state']=='published'
            calls=p.model.calls
            saved=await p.client.get(f'/api/v1/agent-runs/{id}/receipt')
            assert saved.json()==answered.json() and p.model.calls==calls
            events=await p.client.get(f'/api/v1/conversations/{conv}/events?limit=50')
            assert events.status_code==200,events.text
            assert any(item['event_type']=='ai_message' for item in events.json()['items'])
            p.h.grants.denied={'payment_details:read'}
            denied=await p.client.get(f'/api/v1/agent-runs/{id}/receipt')
            assert denied.status_code==404
            assert (await p.client.get(f'/api/v1/conversations/{conv}/events?limit=50')).status_code==404
            assert p.model.calls==calls
            assert used
            p.h.grants.denied.clear();p.h.grants.failure=True
            outage=await p.client.get(f'/api/v1/agent-runs/{id}/receipt')
            assert outage.status_code==503 and outage.json()['error']['code']=='SOURCE_UNAVAILABLE'
            assert p.model.calls==calls
    run(scenario())

def test_private_off_and_unmentioned_reference_reject_before_model_and_source(target):
    async def scenario():
        async with portal(target,private=False) as p:
            assert (await p.client.get('/api/v1/agent-capabilities')).json()=={'private_assistance':False}
            conv,command=await question(p,target)
            response=await p.client.post(f'/api/v1/conversations/{conv}/agent-runs',json=command)
            assert response.status_code==503 and response.json()['error']['code']=='AGENT_PRIVATE_UNAVAILABLE'
            assert p.model.calls==0;p.source.booking.assert_not_awaited();p.source.transaction.assert_not_awaited()
        async with portal(target) as p:
            conv,command=await question(p,target,'Please help with airport information.')
            response=await p.client.post(f'/api/v1/conversations/{conv}/agent-runs',json=command)
            assert response.status_code==422
            assert p.model.calls==0;p.source.booking.assert_not_awaited()
    run(scenario())


def identity(t,kind):
    principal=t.principal if kind=='customer' else None
    expires=datetime.now(timezone.utc)+timedelta(hours=1)
    with t.engine.begin() as c:
        id=c.execute(text(f'''INSERT INTO "{t.schema}".sessions(tenant_id,principal_id,session_kind,token_hash,csrf_secret_hash,expires_at)
          VALUES(:tenant,:principal,:kind,:token,:csrf,:expires) RETURNING id'''),dict(tenant=t.a,principal=principal,kind=kind,token=uuid4().hex*2,csrf='b'*64,expires=expires)).scalar_one()
    return AccessIdentity(Session(id,t.a,principal,kind,expires,'b'*64))

def actor_context(connection,actor):
    for key,value in dict(tenant_id=actor.session.tenant_id,principal_id=actor.session.principal_id,
            actor_kind=actor.session.kind,session_id=actor.session.id).items():
        connection.execute(text('SELECT set_config(:key,:value,true)'),dict(key='app.'+key,value=str(value) if value is not None else ''))


@pytest.mark.parametrize('kind',['customer','anonymous'])
def test_atomic_public_owner_history_empty_order_bound_and_foreign_author_exclusion(target,kind):
    async def scenario():
        actor=identity(target,kind)
        async with database_app(target) as h:
            conv=await h.conversations.create(actor,'en')
            assert await h.conversations.customer_history(actor,conv.id)==()
            for index in range(9):await h.conversations.append(actor,conv.id,uuid4(),f'Own message {index}')
            # Controlled persisted foreign-author row: RLS still scopes the
            # parent, and the optimized body query independently scopes authors.
            with target.engine.begin() as c:
                actor_context(c,actor)
                other=c.execute(text(f'INSERT INTO "{target.schema}".principals(tenant_id) VALUES(:tenant) RETURNING id'),dict(tenant=target.a)).scalar_one()
                c.execute(text(f'UPDATE "{target.schema}".conversations SET last_sequence=10,version=version+1 WHERE id=:id'),dict(id=conv.id))
                c.execute(text(f'''INSERT INTO "{target.schema}".messages(tenant_id,conversation_id,author_principal_id,author_type,body,language,client_message_id,sequence)
                  VALUES(:tenant,:conv,:principal,'customer','Foreign author text','en',:request,10)'''),dict(tenant=target.a,conv=conv.id,principal=other,request=uuid4()))
            rows=await h.conversations.customer_history(actor,conv.id)
            assert [row.body for row in rows]==[f'Own message {index}' for index in range(1,9)]
            with pytest.raises(AuthError):
                await h.conversations.get(identity(target,'anonymous'),conv.id)
    run(scenario())


def test_atomic_joined_parent_commit_during_grants_rejects_second_actual_packet(target,monkeypatch):
    from airport_assistant.infrastructure.database.conversation_repository import ConversationSQL
    original=ConversationSQL.customer_read_projection;parents=[];heads=[]
    async def traced(self,*args,**kwargs):
        packet=await original(self,*args,**kwargs)
        if packet is not None and packet.parent is not None:
            parents.append(packet.parent['version']);heads.append(packet.conversation.version)
        return packet
    monkeypatch.setattr(ConversationSQL,'customer_read_projection',traced)
    async def scenario():
        async with database_app(target) as h,database_app(target) as writer:
            conv,case,person,chat=await human(target,h)
            lifecycle=CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema),h.access)
            closed=await lifecycle.status(person,case,StatusChange(expected_version=chat['case_version'],expected_conversation_version=chat['version'],
                status='closed',close_reason='resolved',resolution='Synthetic result for snapshot test.',next_action='No further action.'))
            continuation=CaseLifecycle(PostgresCaseLifecycle(writer.db,target.schema),writer.access)
            parents.clear();heads.clear();has_grants=h.grants.has_grants;changed=False
            async def grant(**kwargs):
                nonlocal changed
                allowed=await has_grants(**kwargs)
                if not changed:
                    changed=True
                    # The ended chat stays unchanged while a legitimate
                    # customer continuation commits a new parent version,
                    # including its required history, on another connection.
                    await continuation.continue_case(target.identity,case,ContinueCase(expected_version=closed['version'],intent='same_issue',
                        message='The same synthetic issue needs help.',client_request_id=uuid4()))
                return allowed
            h.grants.has_grants=grant
            with pytest.raises(DraftError) as error:await h.conversations.get(target.identity,conv)
            assert error.value.code=='SOURCE_CONTEXT_CHANGED'
            assert len(parents)==2 and parents[1]==parents[0]+1
            assert changed and heads[0]==heads[1]
    run(scenario())


def test_actual_session_revocation_during_source_grants_blocks_second_loader(target):
    async def scenario():
        actor=identity(target,'customer')
        async with database_app(target) as h:
            conv=await h.conversations.create(actor,'en')
            await h.conversations.append(actor,conv.id,uuid4(),'Please check B123 and P123.')
            from airport_assistant.modules.drafts.models import ReportPayload
            await h.drafts.create(actor,conv.id,'create_case',ReportPayload(category='booking_issue',service_type='parking',summary='Synthetic support',customer_report='Please check B123 and P123.',booking_reference='B123',transaction_reference='P123',contact_email='customer@example.com'),source_evidence=evidence(target.a)[0])
            has_grants=h.grants.has_grants;changed=False
            async def grant(**kwargs):
                nonlocal changed
                allowed=await has_grants(**kwargs)
                if not changed:
                    changed=True
                    with target.engine.begin() as c:
                        c.execute(text(f'UPDATE "{target.schema}".sessions SET revoked_at=clock_timestamp() WHERE id=:id'),dict(id=actor.session.id))
                return allowed
            h.grants.has_grants=grant
            with pytest.raises(AuthError) as error:await h.conversations.get(actor,conv.id)
            assert error.value.status in {401,404}
            assert changed
    run(scenario())


def test_actual_post_grant_transaction_exit_delay_expires_without_clock_reanchor(target):
    async def scenario():
        async with database_app(target) as h:
            created,_=await draft(target,h)
            granted=False;delays=[];has_grants=h.grants.has_grants;transaction=h.db.transaction
            async def grant(**kwargs):
                nonlocal granted
                allowed=await has_grants(**kwargs);granted=True;return allowed
            @asynccontextmanager
            async def delayed(*args,**kwargs):
                async with transaction(*args,**kwargs) as connection:
                    yield connection
                    if granted:
                        delays.append(True)
                        await connection.execute('SELECT pg_sleep(2.1)')
            h.grants.has_grants=grant;h.db.transaction=delayed
            with pytest.raises(SourceFailure) as error:await h.conversations.get(target.identity,created.conversation_id)
            assert error.value.status==503
            assert delays
    run(scenario())
