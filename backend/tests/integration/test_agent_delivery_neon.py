"""AI-03 actual graph/publication/handoff/translation SQL, controlled model only."""
import asyncio,os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from test_knowledge_vectors_neon import target as vector_target,knowledge_target,auth_target
from test_chat_publication import publications,active,command
from test_chat_handoff import meta,accept,close,reassign,operator,login
from test_staff_auth import run,row,change,seed,insert
from test_case_commit import prepared,confirm
from test_conversations import create,send
from test_drafts import actor
from test_conversation_events import poll
from airport_assistant.database_config import MigrationDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.agent_runs import PostgresAgentRuns
from airport_assistant.infrastructure.database.translations import PostgresTranslations,TranslationSQL
from airport_assistant.infrastructure.database.publication import PublicationSQL
from airport_assistant.modules.agent.models import Decision,Clarification,DraftProposal
from airport_assistant.modules.agent.service import AgentFoundation
from airport_assistant.modules.agent.workflow import AgentWorkflow
from airport_assistant.modules.agent.delivery import AgentDelivery
from airport_assistant.modules.agent.translation import StaffTranslations
from airport_assistant.modules.conversations.publication import StaffMessages
from airport_assistant.integrations.agent.graph import AgentGraph

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in isolated AI-03 Neon tests.')

@pytest.fixture(scope='module')
def target(vector_target):
    t=vector_target
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):
        assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0027_agent_delivery')=='0027_agent_delivery'
    with t['engine'].begin() as connection:
        for tenant in (t['a'],t['b']):
            org=insert(connection,t['schema'],'organizations',tenant_id=tenant,name='Airport support')
            insert(connection,t['schema'],'service_queues',tenant_id=tenant,organization_id=org,name='General support',service_type='general')
    return t

class Model:
    def __init__(self):self.calls=0;self.translations=0;self.hook=None
    async def advance(self,*args,**kwargs):
        self.calls+=1
        if self.hook:await self.hook()
        return Decision(kind='clarify',clarification=Clarification(question='Which airport service do you need?'))
    async def decide(self,*args,**kwargs):
        self.calls+=1
        return Decision(kind='draft',proposal=DraftProposal(category='information',service_type='general',summary='Please help with the customer inquiry.'))
    async def translate(self,*args):
        self.translations+=1
        if self.hook:await self.hook()
        return 'Your request is being reviewed. Reference PK-A-001.'

@asynccontextmanager
async def app(target):
    async with publications(target) as (h,sender,publication):
        await h.db._pool.resize(min_size=0,max_size=2)
        existing=h.app.state.services;model=Model()
        foundation=AgentFoundation(model,existing.conversations,existing.drafts)
        graph=AgentGraph(AgentWorkflow(foundation,PostgresAgentRuns(h.db,target['schema'])))
        delivery=AgentDelivery(graph,publication,target['key'].encode())
        repository=PostgresTranslations(h.db,target['schema'])
        translations=StaffTranslations(repository,StaffMessages(repository),model)
        h.app.state.services=replace(existing,agent=foundation,agent_workflow=graph,agent_delivery=delivery,staff_translations=translations)
        yield h,sender,model,delivery

async def start(h,c,conv,message,purpose='assist',**extra):
    return await c.post('/api/v1/conversations/'+str(conv)+'/agent-runs',headers=h.headers(c),json=dict(message_id=str(message),request_key=str(uuid4()),purpose=purpose,**extra))

async def resume(h,c,id):
    return await c.post('/api/v1/agent-runs/'+str(id)+'/resume',headers=h.headers(c))

def translation_request(conv,state):
    return dict(command(conv,state,body='Permintaan Anda sedang ditinjau. Reference PK-A-001.',language='id'),target_language='en',request_key=str(uuid4()))

async def preview(h,c,case,body):
    return await c.post('/api/v1/staff/cases/'+str(case)+'/translation-drafts',headers=h.headers(c),json=body)

async def approve(h,c,case,draft,key,**extra):
    return await c.post('/api/v1/staff/cases/'+str(case)+'/translation-drafts/'+draft['id']+'/send',headers={**h.headers(c),'Idempotency-Key':key},json=dict(text='Reviewed: '+draft['translated_body'],approved=True,expected_revision=draft['revision'],**extra))

def test_actual_graph_publication_once_with_api_scope_csrf_and_cross_tenant_denial(target):
    async def scenario():
        async with app(target) as (h,sender,model,delivery):
            async with h.client() as c,h.client() as other,h.client('b.example.test') as foreign:
                await h.boot(c);await h.boot(other);await h.boot(foreign)
                conv=await create(h,c);message=(await send(h,c,conv,body='Where can I wait?')).json()['message_id']
                assert (await start(h,c,conv,message,tenant_id=str(target['b']))).status_code==422
                first=await start(h,c,conv,message);assert first.status_code==201,first.text
                id=first.json()['run_id'];assert (await start(h,c,conv,message)).json()['run_id']==id
                csrf=await c.post('/api/v1/agent-runs/'+id+'/resume');assert csrf.status_code==403
                for wrong in (other,foreign):assert (await resume(h,wrong,id)).status_code==404
                result=await resume(h,c,id);assert result.status_code==200,result.text
                assert result.json()['state']=='published' and 'body' not in result.json()
                assert (await resume(h,c,id)).json()==result.json() and model.calls==1
                events=(await poll(c,conv)).json()['items']
                assert sum(e['event_type']=='ai_message' for e in events)==1
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'",id=UUID(conv))[0]==1
    run(scenario())

@pytest.mark.parametrize('mode',['waiting_staff','human','ended'])
def test_delayed_real_graph_cannot_publish_after_handoff_accept_or_close(target,mode):
    async def scenario():
        async with app(target) as (h,sender,model,delivery):
            async with h.client() as c,h.client() as staff:
                draft=await prepared(h,sender,c,anonymous=True);conv=draft['conversation_id']
                message=(await send(h,c,conv,body='Please help me find the service desk.')).json()['message_id']
                started=await start(h,c,conv,message);assert started.status_code==201,started.text
                entered,release=asyncio.Event(),asyncio.Event()
                async def hold():entered.set();await release.wait()
                model.hook=hold
                pending=asyncio.create_task(resume(h,c,started.json()['run_id']))
                try:
                    await asyncio.wait_for(entered.wait(),15)
                    confirmed=await confirm(h,c,draft);assert confirmed.status_code==201,confirmed.text
                    case=confirmed.json()['case_id']
                    if mode!='waiting_staff':
                        person=operator(target,case);await login(h,staff,person)
                        accepted=await accept(h,staff,conv,await meta(staff,conv));assert accepted.status_code==200,accepted.text
                        if mode=='ended':assert (await close(h,staff,case,await meta(staff,conv))).status_code==200
                finally:release.set();result=await pending
                assert result.status_code==200,result.text
                assert result.json()['state']=='discarded'
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'",id=UUID(conv))[0]==0
    run(scenario())

def test_agent_handoff_requires_customer_confirmation_before_case_and_waiting_mode(target):
    seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,sender,model,delivery):
            async with h.client() as c:
                await h.boot(c);conv=await create(h,c)
                message=(await send(h,c,conv,body='I would like help from a staff member.')).json()['message_id']
                before=row(target,'SELECT count(*) FROM {s}.cases')[0]
                started=await start(h,c,conv,message,'handoff');result=await resume(h,c,started.json()['run_id'])
                assert result.status_code==200,result.text
                assert result.json()['state']=='draft' and row(target,'SELECT count(*) FROM {s}.cases')[0]==before
                draft=(await c.get('/api/v1/action-drafts/'+result.json()['draft_id'])).json()
                confirmed=await confirm(h,c,draft);assert confirmed.status_code==201,confirmed.text
                assert row(target,'SELECT mode FROM {s}.conversations WHERE id=:id',id=UUID(conv))[0]=='waiting_staff'
                assert row(target,'SELECT count(*) FROM {s}.cases')[0]==before+1
    run(scenario())

def test_translation_preview_then_edited_human_send_is_atomic_and_original_remains_private(target,monkeypatch):
    async def scenario():
        async with app(target) as (h,sender,model,delivery):
            async with h.client() as c,h.client() as staff,h.client() as other:
                conv,case,person=await active(h,sender,target,c,staff)
                body=translation_request(conv,await meta(staff,conv))
                before=row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'",id=UUID(conv))[0]
                result=await preview(h,staff,case,body);assert result.status_code==201,result.text
                draft=result.json();assert draft['state']=='ready' and draft['original_body']==body['body']
                assert (await preview(h,staff,case,body)).json()['id']==draft['id'] and model.translations==1
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'",id=UUID(conv))[0]==before
                assert (await c.get('/api/v1/staff/cases/'+case+'/translation-drafts/'+draft['id'])).status_code==401
                other_person=operator(target,case);await login(h,other,other_person)
                assert (await other.get('/api/v1/staff/cases/'+case+'/translation-drafts/'+draft['id'])).status_code==404
                original=TranslationSQL.sent_translation;fail=True
                async def interrupt(tx,*args):
                    nonlocal fail
                    if fail:fail=False;raise RuntimeError('Synthetic receipt interruption')
                    return await original(tx,*args)
                monkeypatch.setattr(TranslationSQL,'sent_translation',interrupt)
                key=str(uuid4());failed=await approve(h,staff,case,draft,key);assert failed.status_code==500
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'",id=UUID(conv))[0]==before
                saved=await approve(h,staff,case,draft,key);assert saved.status_code==201,saved.text
                assert (await approve(h,staff,case,draft,key)).json()==saved.json()
                record=(await staff.get('/api/v1/staff/cases/'+case+'/translation-drafts/'+draft['id'])).json()
                assert record['state']=='sent' and record['original_body']==body['body'] and record['reviewed_body']=='Reviewed: '+draft['translated_body']
                events=(await poll(c,conv)).json()['items'];last=events[-1]
                assert last['payload']['body']==record['reviewed_body'] and body['body'] not in str(last)
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'",id=UUID(conv))[0]==before+1
    run(scenario())

@pytest.mark.parametrize('change_kind',['reassign','revoke'])
def test_translation_current_handler_and_session_rechecked_after_model(target,change_kind):
    async def scenario():
        async with app(target) as (h,sender,model,delivery):
            async with h.client() as c,h.client() as staff,h.client() as supervisor:
                conv,case,person=await active(h,sender,target,c,staff)
                state=await meta(staff,conv);entered,release=asyncio.Event(),asyncio.Event()
                async def hold():entered.set();await release.wait()
                model.hook=hold
                pending=asyncio.create_task(preview(h,staff,case,translation_request(conv,state)))
                try:
                    await asyncio.wait_for(entered.wait(),15)
                    if change_kind=='revoke':
                        identity=await actor(h,staff)
                        change(target,'UPDATE {s}.sessions SET revoked_at=clock_timestamp() WHERE id=:id',id=identity.session.id)
                    else:
                        lead=operator(target,case,('supervisor',));other=operator(target,case);await login(h,supervisor,lead)
                        changed=await reassign(h,supervisor,case,await meta(supervisor,conv),other['case_membership']);assert changed.status_code==200,changed.text
                finally:release.set();result=await pending
                assert result.status_code in (401,404,409),result.text
                assert row(target,"SELECT count(*) FROM {s}.staff_translation_drafts WHERE case_id=:id AND state='ready'",id=UUID(case))[0]==0
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'",id=UUID(conv))[0]==0
    run(scenario())


def test_concurrent_restored_template_and_original_question_return_same_durable_receipt(target,monkeypatch):
    async def scenario():
        async with app(target) as (h,sender,model,delivery):
            async with h.client() as c:
                await h.boot(c);conv=await create(h,c)
                message=(await send(h,c,conv,body='I need help.')).json()['message_id']
                started=await start(h,c,conv,message);id=started.json()['run_id']
                publication=delivery.publication;original=publication.publish
                entered,release=asyncio.Event(),asyncio.Event()
                async def delay(identity,ticket,body):
                    if body=='Which airport service do you need?':
                        entered.set();await release.wait()
                    return await original(identity,ticket,body)
                monkeypatch.setattr(publication,'publish',delay)
                first=asyncio.create_task(resume(h,c,id))
                try:
                    await asyncio.wait_for(entered.wait(),15)
                    second=await resume(h,c,id)
                finally:release.set();late=await first
                assert late.status_code==second.status_code==200,(late.text,second.text)
                assert late.json()==second.json() and late.json()['state']=='published' and model.calls==1
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'",id=UUID(conv))[0]==1
    run(scenario())


def test_known_expired_generation_does_not_spend_model_budget(target,monkeypatch):
    async def scenario():
        from datetime import timedelta
        async with app(target) as (h,sender,model,delivery):
            async with h.client() as c:
                await h.boot(c);conv=await create(h,c)
                message=(await send(h,c,conv,body='Please explain the next step.')).json()['message_id']
                started=await start(h,c,conv,message);id=UUID(started.json()['run_id']);identity=await actor(h,c)
                work=await delivery.workflow.engine.load(identity,id)
                await delivery.publication.capture_bound(identity,UUID(conv),delivery.ticket(identity,id),work.run['conversation_version'],work.run['generation_epoch'])
                original=PublicationSQL.now
                async def future(tx):return await original(tx)+timedelta(minutes=6)
                monkeypatch.setattr(PublicationSQL,'now',future)
                result=await resume(h,c,id)
                assert result.status_code==200 and result.json()['state']=='discarded' and model.calls==0
    run(scenario())


def test_closed_case_rejects_approval_of_previously_ready_translation(target):
    async def scenario():
        async with app(target) as (h,sender,model,delivery):
            async with h.client() as c,h.client() as staff:
                conv,case,_=await active(h,sender,target,c,staff)
                result=await preview(h,staff,case,translation_request(conv,await meta(staff,conv)));assert result.status_code==201,result.text
                draft=result.json()
                ended=await close(h,staff,case,await meta(staff,conv));assert ended.status_code==200,ended.text
                rejected=await approve(h,staff,case,draft,str(uuid4()));assert rejected.status_code==409,rejected.text
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'",id=UUID(conv))[0]==0
    run(scenario())
