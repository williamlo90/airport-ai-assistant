"""Public answer graph + real knowledge/publication SQL. Models/vectors are controlled."""
import asyncio,copy,os
from contextlib import asynccontextmanager
from datetime import datetime,timezone
from pathlib import Path
from types import MethodType
from uuid import UUID,uuid4
import pytest
from test_agent_delivery_neon import target as delivery_target,vector_target,knowledge_target,auth_target,app as delivery_app,resume
from test_knowledge_vectors_neon import setup as knowledge_setup
from test_knowledge_evidence_neon import document,publish
from test_knowledge_neon import login,content
from test_staff_auth import run,seed,row
from test_drafts import actor
from test_conversations import create,send
from test_case_commit import prepared,confirm
from airport_assistant.database_config import MigrationDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.publication import PublicationSQL
from airport_assistant.modules.knowledge.evidence import KnowledgeEvidence,EvidencePolicy
from airport_assistant.modules.knowledge.retrieval import Search
from airport_assistant.modules.agent.answers import PublicAnswers,Grounding,KnowledgeAnswerUnavailable
from airport_assistant.modules.identity.customer_transaction import customer_transaction
from airport_assistant.modules.agent.workflow import RunRequest

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in isolated AI-ANSWER-01 Neon tests.')

@pytest.fixture(scope='module')
def target(delivery_target):
    t=delivery_target;settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):
        assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0028_public_answers')=='0028_public_answers'
    return t

@asynccontextmanager
async def app(target):
    async with delivery_app(target) as (h,sender,model,delivery):
        lifecycle,retrieval,embedding=knowledge_setup(h,target)
        evidence=KnowledgeEvidence(retrieval,EvidencePolicy(.7,'deterministic-answer-test-v1',300),b'k'*48)
        model.answer_calls=0
        async def answer(self,*args,**kwargs):
            self.answer_calls+=1
            if self.hook:await self.hook()
            return {'outcome':'answer','source_ids':['source_1']}
        model.answer=MethodType(answer,model)
        h.app.state.services.agent.evidence=evidence
        delivery.publication.answers=PublicAnswers(evidence)
        yield h,sender,model,delivery,lifecycle,retrieval,embedding

async def source(h,c,admin,lifecycle,retrieval,**changes):
    # Staff login deliberately requires an anonymous session. Reuse the existing
    # authorized admin session when one test publishes multiple documents.
    current=await c.get('/api/v1/staff/me') if c.cookies else None
    if current is None or current.status_code!=200:await login(h,c,admin)
    identity=await actor(h,c)
    values=dict(policy_reference=None,body='Parking visitors may wait in the public arrivals hall.',source_uri='urn:airport:parking:waiting')
    values.update(changes)
    d,v=await document(lifecycle,retrieval,identity,admin,**values)
    return d,v,identity

async def begin(h,c,delivery,*,document_key=None,service='parking',language='en',conversation=None,via_api=False):
    if conversation is None:
        await h.boot(c);conversation=await create(h,c)
        if language!='id':
            meta=(await c.get('/api/v1/conversations/'+conversation)).json()
            r=await c.patch('/api/v1/conversations/'+conversation+'/language',headers=h.headers(c),json={'language':language,'expected_version':meta['version']});assert r.status_code==200,r.text
    message=await send(h,c,conversation,body='Where can parking visitors wait?' if service=='parking' else 'Where is the lounge?')
    assert message.status_code==201,message.text
    identity=await actor(h,c)
    request=RunRequest(conversation_id=UUID(conversation),message_id=UUID(message.json()['message_id']),request_key=uuid4(),mode='answer',
        knowledge=Search(query='user_report',service_type=service,language=language,document_key=document_key,as_of=datetime.now(timezone.utc)))
    if via_api:
        result=await c.post('/api/v1/conversations/'+conversation+'/agent-runs',headers=h.headers(c),json=dict(message_id=message.json()['message_id'],request_key=str(uuid4()),purpose='answer',service_type=service))
        assert result.status_code==201,result.text
        id=UUID(result.json()['run_id'])
    else:id=await delivery.start(identity,request)
    return conversation,id,message.json()['message_id']

@pytest.mark.parametrize('language',['en','id'])
def test_api_answer_exact_text_citations_replay_and_authorized_historical_receipt(target,language):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,sender,model,delivery,k,r,embed):
            async with h.client() as staff,h.client() as c,h.client() as outsider,h.client('b.example.test') as foreign:
                text='Parking visitors may wait in the public arrivals hall.' if language=='en' else 'Pengunjung parking dapat menunggu di aula kedatangan umum.'
                doc,version,staff_actor=await source(h,staff,admin,k,r,language=language,body=text)
                conv,id,message=await begin(h,c,delivery,language=language,via_api=True)
                # Exercise the public API start for the first EN question in the isolated catalog.
                if language=='en':
                    request=await c.post('/api/v1/conversations/'+conv+'/agent-runs',headers=h.headers(c),json={'message_id':message,'request_key':str(uuid4()),'purpose':'answer','service_type':'parking'})
                    assert request.status_code==201 and request.json()['run_id']==str(id),request.text
                result=await resume(h,c,id);assert result.status_code==200,result.text
                data=result.json();assert data['state']=='published' and 'body' not in data
                assert (await resume(h,c,id)).json()==data and model.answer_calls==1
                metadata=row(target,'SELECT body,knowledge_evidence FROM {s}.messages WHERE id=:id',id=UUID(data['message_id']))
                assert text in metadata[0] and metadata[1]['citations'][0]['quote']==text
                path='/api/v1/conversations/'+conv+'/messages/'+data['message_id']+'/citations'
                receipt=await c.get(path);assert receipt.status_code==200,receipt.text
                assert receipt.json()['citations'][0]['version_id']==str(version['id'])
                for bad in (outsider,foreign):
                    await h.boot(bad);assert (await bad.get(path)).status_code==404
                await k.withdraw(staff_actor,version['id'],dict(membership_id=str(admin['membership']),expected_revision=2,reason='Retired test information'))
                historical=await c.get(path)
                assert historical.json()['status']=='historical_publication' and historical.json()['citations'][0]['quote']==text
    run(scenario())

@pytest.mark.parametrize('kind',['empty','low','multiple_documents'])
def test_no_usable_public_evidence_posts_safe_staff_offer_without_model(target,kind):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,sender,model,delivery,k,r,embed):
            async with h.client() as staff,h.client() as c:
                key='missing-document'
                if kind=='low':
                    doc,_,_=await source(h,staff,admin,k,r,body='lounge lounge lounge.')
                    key=doc['document_key']
                if kind=='multiple_documents':
                    await source(h,staff,admin,k,r)
                    await source(h,staff,admin,k,r,body='Parking visitors should use a different waiting hall.')
                    key=None
                conv,id,_=await begin(h,c,delivery,document_key=key)
                result=await resume(h,c,id);assert result.status_code==200,result.text
                assert result.json()['state']=='published' and model.answer_calls==0
                stored=row(target,'SELECT body,knowledge_evidence FROM {s}.messages WHERE id=:id',id=UUID(result.json()['message_id']))
                assert 'staff assistance' in stored[0] and stored[1] is None
    run(scenario())

@pytest.mark.parametrize('timing',['during_model','before_commit'])
def test_withdrawal_during_generation_or_commit_gap_never_publishes_old_claim(target,timing,monkeypatch):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,sender,model,delivery,k,r,embed):
            async with h.client() as staff,h.client() as c:
                doc,version,identity=await source(h,staff,admin,k,r)
                async def withdraw():
                    await k.withdraw(identity,version['id'],dict(membership_id=str(admin['membership']),expected_revision=2,reason='Retired during test'))
                if timing=='during_model':model.hook=withdraw
                else:
                    original=PublicationSQL.lock_knowledge_answer
                    async def changed(tx):await withdraw();await original(tx)
                    monkeypatch.setattr(PublicationSQL,'lock_knowledge_answer',changed)
                conv,id,_=await begin(h,c,delivery,document_key=doc['document_key'])
                result=await resume(h,c,id);assert result.status_code==200,result.text
                assert model.answer_calls==1
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND knowledge_evidence IS NOT NULL",id=UUID(conv))[0]==0
                if timing=='before_commit':
                    assert result.json()['state']=='published'
                    assert 'staff assistance' in row(target,'SELECT body FROM {s}.messages WHERE id=:id',id=UUID(result.json()['message_id']))[0]
    run(scenario())

def test_new_logical_source_between_consumption_and_commit_invalidates_catalog_scope(target,monkeypatch):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,sender,model,delivery,k,r,embed):
            async with h.client() as staff,h.client() as c:
                doc,_,identity=await source(h,staff,admin,k,r,service_type='lounge',body='The lounge is beside the arrivals hall.')
                original=PublicationSQL.lock_knowledge_answer
                async def phantom(tx):
                    await document(k,r,identity,admin,policy_reference=None,service_type='lounge',body='The lounge has revised access information.')
                    await original(tx)
                monkeypatch.setattr(PublicationSQL,'lock_knowledge_answer',phantom)
                conv,id,_=await begin(h,c,delivery,service='lounge')
                result=await resume(h,c,id);assert result.status_code==200,result.text
                assert result.json()['state']=='published' and model.answer_calls==1
                assert row(target,'SELECT knowledge_evidence FROM {s}.messages WHERE id=:id',id=UUID(result.json()['message_id']))[0] is None
    run(scenario())

def test_sql_guard_rejects_forged_quote_even_if_application_metadata_validation_is_faulty(target,monkeypatch):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,sender,model,delivery,k,r,embed):
            async with h.client() as staff,h.client() as c:
                doc,_,_=await source(h,staff,admin,k,r)
                validator=delivery.publication.answers;original=validator.validate_transaction
                async def forged(*args):
                    metadata=copy.deepcopy(await original(*args));metadata['citations'][0]['quote']='Invented airport policy.'
                    return metadata
                monkeypatch.setattr(validator,'validate_transaction',forged)
                conv,id,_=await begin(h,c,delivery,document_key=doc['document_key'])
                result=await resume(h,c,id);assert result.status_code==200,result.text
                assert result.json()['state']=='published'
                stored=row(target,'SELECT body,knowledge_evidence FROM {s}.messages WHERE id=:id',id=UUID(result.json()['message_id']))
                assert 'staff assistance' in stored[0] and stored[1] is None
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'",id=UUID(conv))[0]==1
    run(scenario())

def test_catalog_write_contention_fails_closed_without_wait_or_retry(target):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,sender,model,delivery,k,r,embed):
            await h.db._pool.resize(min_size=0,max_size=1)  # One held owner + one runtime connection, total two.
            async with h.client() as staff,h.client() as c:
                doc,_,_=await source(h,staff,admin,k,r)
                conv,id,_=await begin(h,c,delivery,document_key=doc['document_key'])
                with target['engine'].begin() as connection:
                    connection.exec_driver_sql('LOCK TABLE "'+target['schema']+'".knowledge_documents IN ROW EXCLUSIVE MODE')
                    result=await asyncio.wait_for(resume(h,c,id),20)
                assert result.status_code==200,result.text
                assert result.json()['state']=='published' and model.answer_calls==1
                assert row(target,'SELECT knowledge_evidence FROM {s}.messages WHERE id=:id',id=UUID(result.json()['message_id']))[0] is None
    run(scenario())

def test_handoff_during_answer_model_invalidates_actual_graph_publication(target):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,sender,model,delivery,k,r,embed):
            async with h.client() as staff,h.client() as c:
                doc,_,_=await source(h,staff,admin,k,r)
                draft=await prepared(h,sender,c,anonymous=True);conv=draft['conversation_id']
                # prepared uses the tenant's default language id; align it before generation.
                meta=(await c.get('/api/v1/conversations/'+conv)).json()
                changed=await c.patch('/api/v1/conversations/'+conv+'/language',headers=h.headers(c),json={'language':'en','expected_version':meta['version']});assert changed.status_code==200
                async def handoff():
                    result=await confirm(h,c,draft);assert result.status_code==201,result.text
                model.hook=handoff
                conv,id,_=await begin(h,c,delivery,document_key=doc['document_key'],conversation=conv)
                result=await resume(h,c,id);assert result.status_code==200,result.text
                assert result.json()['state']=='discarded'
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'",id=UUID(conv))[0]==0
    run(scenario())


@pytest.mark.parametrize('fields',[('issued_at',),('expires_at',),('issued_at','expires_at')])
def test_raw_runtime_null_evidence_timestamp_rolls_back_message_event_and_counters(target,fields):
    admin=seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,sender,model,delivery,k,r,embed):
            async with h.client() as staff,h.client() as c:
                doc,_,_=await source(h,staff,admin,k,r)
                conv,_,_=await begin(h,c,delivery,document_key=doc['document_key'])
                identity=await actor(h,c);publication=delivery.publication
                selection=Search(query='parking',service_type='parking',language='en',document_key=doc['document_key'],as_of=datetime.now(timezone.utc))
                evidence=await h.app.state.services.agent.evidence.collect(identity,selection)
                grounding=Grounding(selection,evidence.snapshot,('source_1',));answers=publication.answers
                body=answers.compiled('en',evidence,grounding.source_ids)
                ticket=await publication.capture(identity,UUID(conv))
                before=row(target,'SELECT version,last_sequence,(SELECT count(*) FROM {s}.conversation_events WHERE conversation_id=:id) FROM {s}.conversations WHERE id=:id',id=UUID(conv))
                with pytest.raises(KnowledgeAnswerUnavailable):
                    async with customer_transaction(publication._repository,identity) as tx:
                        conversation=await publication._conversation(tx,identity,UUID(conv))
                        generation=await tx.generation(ticket.id,lock=True)
                        metadata=await answers.validate_transaction(identity,grounding,tx,'en',body)
                        for field in fields:metadata[field]=None
                        await tx.ai_message(conversation,generation,body,knowledge_evidence=metadata)
                assert row(target,'SELECT version,last_sequence,(SELECT count(*) FROM {s}.conversation_events WHERE conversation_id=:id) FROM {s}.conversations WHERE id=:id',id=UUID(conv))==before
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'",id=UUID(conv))[0]==0
                assert model.answer_calls==0
    run(scenario())
