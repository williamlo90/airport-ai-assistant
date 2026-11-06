"""One fully composed local HTTP/Neon journey; external ports are controlled."""
import asyncio,json,os,secrets
from pathlib import Path
from uuid import UUID,uuid4
from contextlib import AsyncExitStack
import pytest
from sqlalchemy import text
from httpx import AsyncClient,ASGITransport
from test_administration_ratings_neon import target,rating_target,information_target,page_target,translation_target,email_target,mail_target,graph_target,schema_target,administrator
from test_private_source_graph_neon import app as setup_app,draft,staff,run,evidence
from airport_assistant.composition import ApplicationResources
from airport_assistant.main import create_app
from airport_assistant.config import Settings
from airport_assistant.auth_config import AuthSettings
from airport_assistant.agent_config import AgentSettings
from airport_assistant.knowledge_config import KnowledgeSettings
from airport_assistant.source_config import SourceSettings
from airport_assistant.mail_config import MailSettings
from airport_assistant.database_config import RuntimeDatabaseSettings
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.infrastructure.security.passwords import hash_password
from airport_assistant.modules.knowledge.retrieval import normalized
from airport_assistant.modules.agent.models import Decision,ToolCall,Clarification
from airport_assistant.modules.source.models import SourceFailure,RelationPage
from airport_assistant.api.auth import CSRF_COOKIE

ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'local/team'
pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_COMPLETE03_TESTS')!='1',reason='Explicit complete local HTTP composition proof.')

class Sender:
    def __init__(self):self.messages={}
    async def send(self,m):self.messages[m.challenge_id]=m
class Embed:
    def __init__(self):self.calls=0
    async def embed(self,texts):self.calls+=1;return [normalized([1.,.01]+[0.]*1534) for _ in texts]
class Scanner:
    def __init__(self):self.calls=0
    async def clean(self,body):self.calls+=1;return True
class Model:
    def __init__(self):self.calls=0
    async def advance(self,*args,observations,**kwargs):
        self.calls+=1
        if not observations:return Decision(kind='tool',tool=ToolCall(call_id='booking',name='read_booking',arguments={'reference':'B123'}))
        if len(observations)==1:return Decision(kind='tool',tool=ToolCall(call_id='transaction',name='read_transaction',arguments={'reference':'P123'}))
        return Decision(kind='clarify',clarification=Clarification(question='Your booking is confirmed and the transaction record says paid. What help do you need?'))
    async def answer(self,*args,**kwargs):self.calls+=1;return {'outcome':'answer','source_ids':['source_1']}
class Source:
    def __init__(self,t):self.t=t;_,self.book,self.payment,self.rules=evidence(t.a);self.calls=0;self.denied=False
    def check(self,c):
        assert c.tenant_id==self.t.a and c.subject=='complete03-customer';self.calls+=1
        if self.denied:raise SourceFailure(404)
    async def booking(self,c,r):self.check(c);assert r=='B123';return self.book
    async def transaction(self,c,r):self.check(c);assert r=='P123';return self.payment
    async def policy(self,c,r):self.check(c);return self.rules
    async def relations(self,c,limit=20,cursor=None):self.check(c);return RelationPage(items=(),next_cursor=None)
    async def aclose(self):pass
class LostAck:
    def __init__(self,app):self.app=app;self.path=None;self.count=0
    async def __call__(self,scope,receive,send):
        if scope['type']=='http' and scope['method']=='POST' and scope['path']==self.path:
            self.path=None;self.count+=1
            async def intercept(message):
                if message['type']=='http.response.start':
                    assert message['status']==201
                    await send(dict(type='http.response.start',status=503,headers=[(b'content-type',b'application/json'),(b'cache-control',b'no-store')]))
                elif message['type']=='http.response.body':
                    if not message.get('more_body'):await send(dict(type='http.response.body',body=json.dumps(dict(error=dict(code='REQUEST_FAILED',message='Uncertain result',request_id=str(uuid4()),retryable=False))).encode()))
            await self.app(scope,receive,intercept)
        else:await self.app(scope,receive,send)

def test_current_complete_local_http_journey_and_optional_same_schema_browser(target):
    t=target
    async def scenario():
        async with setup_app(t) as h:
            d,_=await draft(t,h);seed_case=(await h.cases.confirm(t.identity,d.id,d.version,d.payload_hash,uuid4().hex,uuid4())).case_id
            cs=staff(t,seed_case);admin=administrator(t,seed_case)
        credentials={}
        with t.engine.begin() as c:
            c.execute(text(f'INSERT INTO "{t.schema}".tenant_hosts(tenant_id,hostname) VALUES(:a,:host),(:b,:foreign)'),dict(a=t.a,b=t.b,host='localhost',foreign='foreign.localhost'))
            c.execute(text(f'UPDATE "{t.schema}".principals SET source_subject_ref=:subject WHERE tenant_id=:t AND id=:p'),dict(t=t.a,p=t.principal,subject='complete03-customer'))
            for label,person in [('cs',cs),('admin',admin)]:
                password=secrets.token_urlsafe(24)
                c.execute(text(f'UPDATE "{t.schema}".staff_credentials SET password_hash=:h WHERE tenant_id=:t AND principal_id=:p'),dict(h=hash_password(password),t=t.a,p=person.session.principal_id))
                contact=c.execute(text(f'SELECT email_address FROM "{t.schema}".contacts WHERE tenant_id=:t AND principal_id=:p'),dict(t=t.a,p=person.session.principal_id)).scalar_one()
                credentials[label]=dict(contact=contact,password=password)
        source=Source(t);sender=Sender();embed=Embed();scanner=Scanner();model=Model()
        db=RuntimeDatabase(t.url,pool_max=2,acquire_timeout=5,schema=t.schema)
        app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True,source_enabled=True,private_source_enabled=True),
            database_settings=RuntimeDatabaseSettings(DATABASE_URL=t.url,pool_max=2),auth_settings=AuthSettings(SESSION_SECRET='synthetic-complete03-only-'*3),verification_sender=sender,
            source_settings=SourceSettings(SOURCE_API_BASE_URL='https://controlled.invalid',SOURCE_API_TOKEN='x'*48,context_secret='y'*48,tenant_map={t.a:t.a}),
            mail_settings=MailSettings(enabled=False,auth_enabled=False,ingress_enabled=False,receiving_enabled=False,reply_enabled=False,accounts={},auth_routes={},webhooks={}),
            knowledge_settings=KnowledgeSettings(enabled=True,api_key='controlled-no-network'),
            agent_settings=AgentSettings(enabled=True,api_key='controlled-no-network',knowledge_min_score=.7,knowledge_policy_id='controlled-complete03-only'),resources=ApplicationResources(db,source),schema=t.schema)
        s=app.state.services;s.agent.model=model;s.knowledge.retrieval.embeddings=embed;s.attachments._scanner=scanner;s.staff_translations.model=model
        assert not app.dependency_overrides
        spec=app.openapi()
        required={'/api/v1/admin/knowledge/documents':('get','post'),'/api/v1/admin/knowledge/versions/{id}/index':('get','post'),'/api/v1/cases/{id}/ratings':('get','post'),'/api/v1/admin/staff/{principal_id}/memberships':('get','patch'),'/api/v1/cases/{id}/information':('post',)}
        for path,methods in required.items():assert all(m in spec['paths'][path] for m in methods)
        assert set(spec['components']['schemas']['IndexStatus']['properties'])=={'version_id','basis_hash','ready','generation','indexing_available'}
        assert 'items' in spec['components']['schemas']['DocumentPage']['properties']
        with t.engine.connect() as c:
            secured=c.execute(text('SELECT c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:s AND c.relname IN (:a,:b,:c,:d)'),dict(s=t.schema,a='ratings',b='staff_memberships',c='knowledge_documents',d='knowledge_versions')).all()
            assert len(secured)==4 and all(row==(True,True) for row in secured)
        wrapper=LostAck(app);transport=ASGITransport(app=wrapper)
        checks=[]
        async with app.router.lifespan_context(app),AsyncExitStack() as stack:
            clients=[await stack.enter_async_context(AsyncClient(transport=transport,base_url=origin)) for origin in ('https://localhost','https://localhost','https://localhost','https://localhost','https://foreign.localhost')]
            customer,ac,sc,stranger,foreign=clients
            async def req(c,method,path,body=None,want=200,extra=None,raw=None):
                headers={};origin=str(c.base_url).rstrip('/')
                if method!='GET':headers['Origin']=origin
                proof=c.cookies.get(CSRF_COOKIE)
                if proof:headers['X-CSRF-Token']=proof
                if extra:headers.update(extra)
                response=await c.request(method,'/api/v1'+path,headers=headers,**({'json':body} if body is not None else {}),**({'content':raw} if raw is not None else {}))
                assert response.status_code==want,(method,path,response.status_code,response.json().get('error',{}).get('code') if response.headers.get('content-type','').startswith('application/json') else None)
                if path!='/auth/sessions' or want==200:assert response.headers.get('cache-control')=='no-store'
                return response.json() if response.headers.get('content-type','').startswith('application/json') else response.content
            async def login(c,label):
                await req(c,'POST','/auth/sessions',{})
                await req(c,'POST','/staff/auth/login',credentials[label])
                return (await req(c,'GET','/staff/me'))['memberships'][0]
            am=await login(ac,'admin');cm=await login(sc,'cs')
            await req(customer,'POST','/auth/sessions',{});await req(stranger,'POST','/auth/sessions',{});await req(foreign,'POST','/auth/sessions',{})
            print('COMPLETE03 completed phase',len(checks)+1,flush=True);checks.append('real host/session/staff-login/secure-cookie composition')
            async def conv(c,body):
                v=await req(c,'POST','/conversations',{'language':'en'},201)
                m=await req(c,'POST','/conversations/'+v['conversation_id']+'/messages',dict(body=body,client_message_id=str(uuid4())),201)
                return v['conversation_id'],m['message_id']
            async def answer(c):
                conv_id,message_id=await conv(c,'Where can parking visitors wait?')
                started=await req(c,'POST','/conversations/'+conv_id+'/agent-runs',dict(message_id=message_id,request_key=str(uuid4()),purpose='answer',service_type='parking'),201)
                result=await req(c,'POST','/agent-runs/'+started['run_id']+'/resume')
                assert result['state']=='published'
                events=await req(c,'GET','/conversations/'+conv_id+'/events?limit=50')
                body=events['items'][-1]['payload']['body']
                citations=await req(c,'GET','/conversations/'+conv_id+'/messages/'+result['message_id']+'/citations')
                return conv_id,body,citations
            info=dict(body='Parking visitors may wait in the arrivals hall.',source_uri='urn:airport:parking:waiting',service_type='parking',language='en',visibility='public',allowed_roles=[],valid_from='2025-01-01T00:00:00Z',valid_to=None,applicability={},policy_reference=None,supersedes_version_id=None)
            doc=await req(ac,'POST','/admin/knowledge/documents',dict(membership_id=am['id'],document_key='complete03-parking',title='Parking waiting guidance',content=info),201)
            directory=await req(ac,'GET','/admin/knowledge/documents?membership_id='+am['id']+'&document_key=complete03-parking&limit=25');assert directory['items'][0]['id']==doc['id']
            versions=await req(ac,'GET','/admin/knowledge/documents/'+doc['id']+'/versions?membership_id='+am['id']);v1=versions['items'][0]
            async def transition(v,action,**extra):return await req(ac,'POST','/admin/knowledge/versions/'+v['id']+'/'+action,dict(membership_id=am['id'],expected_revision=v['revision'],**extra))
            assert not (await req(ac,'GET','/admin/knowledge/versions/'+v1['id']+'/index?membership_id='+am['id']))['ready']
            await transition(v1,'index');calls=embed.calls
            assert (await req(ac,'GET','/admin/knowledge/versions/'+v1['id']+'/index?membership_id='+am['id']))['ready'] and embed.calls==calls
            v1=await transition(v1,'publish');public_conv,old_body,old_citations=await answer(customer);assert info['body'] in old_body and old_citations['citations'][0]['version_id']==v1['id']
            await req(customer,'POST','/conversations/'+public_conv+'/ratings',dict(expected_version=0,score=4,comment='The waiting information was clear.'))
            current_doc=await req(ac,'GET','/admin/knowledge/documents/'+doc['id']+'?membership_id='+am['id'])
            info2=info|dict(body='Parking visitors must now wait in the public pickup zone.',supersedes_version_id=v1['id'])
            v2=await req(ac,'POST','/admin/knowledge/documents/'+doc['id']+'/versions',dict(membership_id=am['id'],expected_document_revision=current_doc['revision'],content=info2),201)
            await transition(v2,'index');await transition(v1,'withdraw',reason='Replaced by updated airport guidance.');v2=await transition(v2,'publish')
            _,new_body,new_citations=await answer(customer);assert info2['body'] in new_body and info['body'] not in new_body and new_citations['citations'][0]['version_id']==v2['id']
            await req(sc,'GET','/admin/knowledge/documents?membership_id='+cm['id'],want=404)
            await req(foreign,'GET','/admin/knowledge/documents?membership_id='+am['id'],want=401)
            await transition(v2,'withdraw',reason='Last applicable source withdrawn for verification.')
            _,fallback,fallback_citations=await answer(customer);assert 'staff assistance' in fallback and not fallback_citations['citations'] and info2['body'] not in fallback
            print('COMPLETE03 completed phase',len(checks)+1,flush=True);checks.append('admin draft/index/status/publish/new-version/withdraw/current factual answer and citation')
            challenge=await req(customer,'POST','/auth/challenges',dict(contact='customer@example.com',purpose='customer_login'),202)
            code=sender.messages[UUID(challenge['challenge_id'])].code
            await req(customer,'POST','/auth/challenges/'+challenge['challenge_id']+'/verify',dict(code=code));del code
            assert (await req(customer,'GET','/conversations/'+public_conv+'/ratings'))['rating']['score']==4
            cid,msg=await conv(customer,'Please check booking B123 and payment P123 before I contact staff.')
            begin=await req(customer,'POST','/conversations/'+cid+'/agent-runs',dict(message_id=msg,request_key=str(uuid4()),purpose='assist',booking_references=['B123'],transaction_references=['P123']),201)
            result=await req(customer,'POST','/agent-runs/'+begin['run_id']+'/resume');assert result['state']=='published'
            events=await req(customer,'GET','/conversations/'+cid+'/events?limit=50');assert 'paid' in events['items'][-1]['payload']['body']
            report=dict(category='transaction_issue',service_type='parking',summary='Payment confirmation help',customer_report='Please check the payment confirmation for B123 and P123.',contact_email='customer@example.com',booking_reference='B123',transaction_reference='P123',attachment_ids=[])
            d=await req(customer,'POST','/action-drafts',dict(conversation_id=cid,action_type='request_handoff',payload=report),201)
            upload=await req(customer,'POST','/attachments?draft_id='+d['draft_id']+'&expected_version='+str(d['version']),want=201,raw=b'%PDF-1.4\nControlled harmless integration fixture.',extra={'Content-Type':'application/pdf','Idempotency-Key':str(uuid4())});d=upload['draft'];attachment=upload['attachment']['id']
            saved=await req(customer,'POST','/action-drafts/'+d['draft_id']+'/confirm',dict(expected_version=d['version'],payload_hash=d['payload_hash']),201,extra={'Idempotency-Key':str(uuid4())});case=saved['case_id']
            assert (await req(customer,'GET','/cases?limit=25'))['items']
            chat=await req(sc,'GET','/staff/conversations/'+cid)
            await req(sc,'POST','/staff/conversations/'+cid+'/accept',dict(expected_version=chat['version'],expected_case_version=chat['case_version']),extra={'Idempotency-Key':str(uuid4())})
            current=await req(customer,'GET','/cases/'+case)
            preview=await req(customer,'POST','/cases/'+case+'/information-previews',dict(expected_version=current['version'],message='The original charge reference remains P123.',language='en'))
            ikey=str(uuid4());receipt=await req(customer,'POST','/cases/'+case+'/information',dict(confirmation_token=preview['confirmation_token'],confirmed=True),201,extra={'Idempotency-Key':ikey})
            assert (await req(customer,'GET','/cases/'+case+'/information-receipts/'+ikey))==receipt
            current=await req(sc,'GET','/staff/cases/'+case);chat=await req(sc,'GET','/staff/conversations/'+cid)
            key=str(uuid4());wrapper.path='/api/v1/staff/cases/'+case+'/messages'
            await req(sc,'POST','/staff/cases/'+case+'/messages',dict(conversation_id=cid,channel='chat',body='We received your report and will check the payment record.',language='en',expected_version=current['version'],expected_conversation_version=chat['version'],expected_epoch=chat['generation_epoch']),503,extra={'Idempotency-Key':key})
            message_receipt=await req(sc,'GET','/staff/cases/'+case+'/message-receipts/'+key+'?membership_id='+cm['id']);assert message_receipt['state']=='saved' and wrapper.count==1
            current=await req(sc,'GET','/staff/cases/'+case);chat=await req(sc,'GET','/staff/conversations/'+cid)
            closed=await req(sc,'PATCH','/staff/cases/'+case+'/status',dict(expected_version=current['version'],expected_conversation_version=chat['version'],status='closed',next_action='No action pending.',close_reason='resolved',resolution='The payment inquiry procedure was explained.'))
            rating=await req(customer,'POST','/cases/'+case+'/ratings',dict(expected_version=0,score=5,comment='Clear follow-up.'));assert rating['rating']['version']==1
            await req(customer,'POST','/cases/'+case+'/continue',dict(expected_version=closed['version'],intent='same_issue',message='The same confirmation issue still needs help.',client_request_id=str(uuid4())),extra={'Idempotency-Key':str(uuid4())})
            assert not (await req(customer,'GET','/cases/'+case+'/ratings'))['eligible']
            await req(stranger,'GET','/cases/'+case,want=404);await req(foreign,'GET','/cases/'+case,want=404);await req(ac,'GET','/staff/cases/'+case,want=404)
            source.denied=True;denied=await customer.get('/api/v1/cases/'+case);assert denied.status_code in (403,404) and 'customer_report' not in denied.text;source.denied=False
            resumed=await req(customer,'GET','/cases/'+case)
            assert resumed['status']=='in_progress' and resumed['assigned_membership_id']==cm['id'] and resumed['next_actor']=='staff'
            data=await req(customer,'GET','/attachments/'+attachment+'/download');assert data.startswith(b'%PDF-')
            print('COMPLETE03 completed phase',len(checks)+1,flush=True);checks.append('OTP adoption/private inquiry/draft/file/confirm/accept/add-info/one lost-ack chat/close/rate/continue/foreign and source denial')
            member=await req(ac,'GET','/admin/staff/'+str(cs.session.principal_id)+'/memberships?membership_id='+am['id'])
            await req(ac,'PATCH','/admin/staff/'+str(cs.session.principal_id)+'/memberships',dict(actor_membership_id=am['id'],actor_version=member['workspace']['membership_version'],expected_version=member['member']['version'],active=False,roles=['cs'],queue_ids=cm['queue_ids']))
            assert (await sc.get('/api/v1/staff/cases/'+case)).status_code in (401,403,404)
            print('COMPLETE03 completed phase',len(checks)+1,flush=True);checks.append('current admin membership edit revokes operational access')
            with t.engine.connect() as c:
                assert c.execute(text(f'SELECT count(*) FROM "{t.schema}".messages WHERE id=:id'),dict(id=UUID(message_receipt['message_id']))).scalar_one()==1
                assert c.execute(text(f'SELECT count(*) FROM "{t.schema}".outbox_jobs WHERE source_message_id=:id'),dict(id=UUID(message_receipt['message_id']))).scalar_one()==0
                assert c.execute(text(f'SELECT count(*) FROM "{t.schema}".ratings WHERE case_id=:id'),dict(id=UUID(case))).scalar_one()==1
            report=dict(passed=True,schema=t.schema,checks=checks,model_calls=model.calls,embedding_calls=embed.calls,source_calls=source.calls,controlled_scans=scanner.calls,real_emails=0,paid_calls=0,dependency_overrides=0,head='0038_staff_administration')
            (OUT/'complete-03-http-result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            if os.environ.get('RUN_COMPLETE03_BROWSER')=='1':
                import uvicorn
                from fastapi.staticfiles import StaticFiles
                from fastapi.responses import FileResponse,JSONResponse
                dist=ROOT/'frontend/dist';app.mount('/assets',StaticFiles(directory=dist/'assets'))
                @app.get('/{path:path}',include_in_schema=False)
                async def spa(path:str):
                    if path.startswith(('api/','health/')):return JSONResponse({},status_code=404)
                    resolved=(dist/path).resolve()
                    return FileResponse(resolved if resolved.is_relative_to(dist.resolve()) and resolved.is_file() else dist/'index.html')
                server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8457,ssl_certfile=str(ROOT/'local/tls/localhost.pem'),ssl_keyfile=str(ROOT/'local/tls/localhost-key.pem'),lifespan='off',access_log=False,log_level='warning',workers=1,proxy_headers=False))
                task=asyncio.create_task(server.serve());ready=OUT/'complete-03-browser-ready.json';stop=OUT/'complete-03-browser-stop'
                try:
                    for _ in range(100):
                        if server.started:break
                        if task.done():await task
                        await asyncio.sleep(.1)
                    assert server.started
                    ready.write_text(json.dumps(dict(origin='https://localhost:8457',schema=t.schema,document=doc['id'],admin=credentials['admin'])),encoding='utf-8')
                    deadline=asyncio.get_running_loop().time()+600
                    while not stop.exists() and asyncio.get_running_loop().time()<deadline and not task.done():await asyncio.sleep(.25)
                finally:
                    server.should_exit=True;await task;ready.unlink(missing_ok=True);stop.unlink(missing_ok=True)
                    (OUT/'complete-03-browser-cleanup.json').write_text(json.dumps(dict(server_closed=True,ready_removed=not ready.exists(),schema=t.schema)),encoding='utf-8')
    run(scenario())
