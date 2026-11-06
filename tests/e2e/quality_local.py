"""Bounded actual FastAPI/Neon calibration and fresh local acceptance; no commercial ports."""
import asyncio,contextvars,json,os,secrets,sys,time,traceback
from pathlib import Path
from contextlib import contextmanager,asynccontextmanager
from uuid import UUID,uuid4
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'local/team/quality-final'
sys.path[:0]=[str(OUT),str(ROOT/'backend/src'),str(ROOT/'backend/tests/integration'),str(ROOT/'tests/e2e')]
from common import save,snapshot,memory,preflight,percentiles,freeze_guard,artifact
PROGRESS={}
PHASE=contextvars.ContextVar('quality_phase',default='setup');ACTIVE=contextvars.ContextVar('quality_db_depth',default=0)
class TraceDB:
 def __init__(self,inner):self.inner=inner;self.events=[]
 def __getattr__(self,n):return getattr(self.inner,n)
 @asynccontextmanager
 async def transaction(self,*args,**kw):
  start=time.monotonic();phase=PHASE.get();ok=False
  try:
   async with self.inner.transaction(*args,**kw) as c:
    entered=time.monotonic();self.events.append({'phase':phase,'event':'context_ready','ms':round((entered-start)*1000,3)});tok=ACTIVE.set(ACTIVE.get()+1)
    try:yield TraceConnection(c,self,phase)
    finally:ACTIVE.reset(tok);self.events.append({'phase':phase,'event':'transaction_body_finished','at':time.monotonic()})
   ok=True
  finally:self.events.append({'phase':phase,'event':'transaction_exit_including_commit_or_rollback','ok':ok,'ms':round((time.monotonic()-start)*1000,3),'at':time.monotonic()})
class TraceConnection:
 def __init__(self,c,trace,phase):self.c,self.trace,self.phase=c,trace,phase
 def __getattr__(self,n):return getattr(self.c,n)
 async def execute(self,*args,**kw):
  start=time.monotonic()
  try:return await self.c.execute(*args,**kw)
  finally:self.trace.events.append({'phase':self.phase,'event':'execute_or_pipeline_enqueue','ms':round((time.monotonic()-start)*1000,3)})
 @asynccontextmanager
 async def pipeline(self):
  start=time.monotonic()
  try:
   async with self.c.pipeline() as p:yield p
  finally:self.trace.events.append({'phase':self.phase,'event':'pipeline_synchronized','ms':round((time.monotonic()-start)*1000,3)})

async def run(t,stage):
 from sqlalchemy import text
 from httpx import AsyncClient,ASGITransport
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
 from airport_assistant.api.security import CSRF_COOKIE,SESSION_COOKIE
 from test_complete03_local_neon import Source,Sender,Embed,Model
 from test_private_source_graph_neon import staff
 from test_administration_ratings_neon import administrator
 from customer_recovery_server import graph_counts
 global PROGRESS
 records=[];checks=[];source_events=[];grant_events=[];model_events=[];PROGRESS={'records':records,'checks':checks,'sql_events':[], 'source_events':source_events,'grant_events':grant_events,'controlled_model_embedding_events':model_events};started=time.monotonic();cpu=time.process_time();origin='https://localhost'
 source=Source(t);sender=Sender();embed=Embed();model=Model()
 raw=RuntimeDatabase(t.url,pool_max=2,acquire_timeout=5,schema=t.schema);db=TraceDB(raw);PROGRESS['sql_events']=db.events
 for obj,methods,events in [(source,('booking','transaction','policy','relations'),source_events),(model,('advance','answer'),model_events),(embed,('embed',),model_events)]:
  for method in methods:
   original=getattr(obj,method)
   async def timed(*a,_call=original,_name=method,_events=events,**kw):
    assert ACTIVE.get()==0,'External port called within current task SQL transaction'
    at=time.monotonic()
    try:return await _call(*a,**kw)
    finally:_events.append({'phase':PHASE.get(),'port':_name,'ms':round((time.monotonic()-at)*1000,3),'at':at})
   setattr(obj,method,timed)
 with t.engine.begin() as c:
  c.execute(text(f'INSERT INTO "{t.schema}".tenant_hosts(tenant_id,hostname) VALUES(:a,:host),(:b,:foreign)'),{'a':t.a,'b':t.b,'host':'localhost','foreign':'foreign.localhost'})
  c.execute(text(f'UPDATE "{t.schema}".principals SET source_subject_ref=:subject WHERE id=:p'),{'p':t.principal,'subject':'complete03-customer'})
 app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True,source_enabled=True,private_source_enabled=True),database_settings=RuntimeDatabaseSettings(DATABASE_URL=t.url,pool_max=2),auth_settings=AuthSettings(SESSION_SECRET='synthetic-quality-final-'*3),verification_sender=sender,source_settings=SourceSettings(SOURCE_API_BASE_URL='https://controlled.invalid',SOURCE_API_TOKEN='x'*48,context_secret='y'*48,tenant_map={t.a:t.a}),mail_settings=MailSettings(enabled=False,auth_enabled=False,ingress_enabled=False,receiving_enabled=False,reply_enabled=False,accounts={},auth_routes={},webhooks={}),knowledge_settings=KnowledgeSettings(enabled=True,api_key='controlled-no-network'),agent_settings=AgentSettings(enabled=True,api_key='controlled-no-network',knowledge_min_score=.7,knowledge_policy_id='controlled-local-quality'),resources=ApplicationResources(db,source),schema=t.schema)
 s=app.state.services;s.agent.model=model;s.knowledge.retrieval.embeddings=embed
 for gate_name,gate in [('case',s.cases._private_content),('draft',s.drafts._private_content)]:
  assert gate.freshness_seconds==2
  oldclock=gate.clock
  def clock(_clock=oldclock,_gate=gate_name):
   value=_clock();db.events.append({'phase':PHASE.get(),'event':'native_gate_clock_read','gate':_gate,'at':value});return value
  gate.clock=clock
 access=s.cases._private_content.source_access;allow=access.allows
 async def grants(*a,**kw):
  before=time.monotonic()
  try:return await allow(*a,**kw)
  finally:grant_events.append({'phase':PHASE.get(),'started':before,'finished':time.monotonic(),'anchor_note':'Outer source grant starts before awaits; PrivateContentGate write clock starts after allows returns'})
 access.allows=grants
 requests=0
 async def req(c,method,path,body=None,want=200,key=None,label='setup'):
  nonlocal requests
  if requests>=160 or time.monotonic()-started>300 or memory()['peak_rss_mib']>768:raise RuntimeError('Resource/request/time guard stopped workload')
  requests+=1;headers={'Origin':str(c.base_url).rstrip('/')} if method!='GET' else {};proof=c.cookies.get(CSRF_COOKIE)
  if proof:headers['X-CSRF-Token']=proof
  if key:headers['Idempotency-Key']=key
  tok=PHASE.set(label);at=time.monotonic();response=None;data=None;error_type=None
  try:
   response=await c.request(method,'/api/v1'+path,headers=headers,**({'json':body} if body is not None else {}))
   data=response.json() if response.content else None
   if response.status_code not in ((want,) if isinstance(want,int) else want):raise AssertionError('Unexpected HTTP status; sanitized record retained')
   return data
  except BaseException as error:error_type=type(error).__name__;raise
  finally:
   records.append({'phase':label,'method':method,'route':path.split('?')[0].replace(str(t.principal),'{principal}'),'status':response.status_code if response is not None else None,'ms':round((time.monotonic()-at)*1000,3),'error_type':error_type,'error_code':data.get('error',{}).get('code') if isinstance(data,dict) else None,'resource':memory()})
   PHASE.reset(tok)
 async def conv(c,body,lang='en'):
  v=await req(c,'POST','/conversations',{'language':lang},201)
  m=await req(c,'POST','/conversations/'+v['conversation_id']+'/messages',{'body':body,'client_message_id':str(uuid4())},201)
  return v['conversation_id'],m['message_id']
 private_context=None
 async def preview(c,label,private=True):
  nonlocal private_context
  if private and private_context is not None:cid,mid=private_context
  else:cid,mid=await conv(c,('Please check B123 and P123. '+label) if private else 'Please explain public parking support.')
  if private and private_context is None:
   begin=await req(c,'POST','/conversations/'+cid+'/agent-runs',{'message_id':mid,'request_key':str(uuid4()),'purpose':'assist','booking_references':['B123'],'transaction_references':['P123']},201)
   r=await req(c,'POST','/agent-runs/'+begin['run_id']+'/resume',label=label+'_controlled_agent')
   assert r['state']=='published';private_context=(cid,mid)
  if private:
   await req(c,'GET','/conversations/'+cid,label='current_private_parent')
   with t.engine.connect() as conn:
    parent=conn.execute(text(f'SELECT mode,case_id IS NULL,jsonb_array_length(resource_security_labels)>0 FROM \"{t.schema}\".conversations WHERE id=:id'),{'id':UUID(cid)}).one()
   assert tuple(parent)==('ai',True,True),'Private parent must stay current, unlinked and source-labelled'
  payload={'category':'transaction_issue' if private else 'information','service_type':'parking','summary':label,'customer_report':'Please check B123 and P123.' if private else 'Please explain public parking support.','contact_email':'customer@example.com'}
  if private:payload.update(booking_reference='B123',transaction_reference='P123')
  d=await req(c,'POST','/action-drafts',{'conversation_id':cid,'action_type':'create_case','payload':payload},201,label=label+'_preview')
  return d,cid
 async def confirm(c,d,label):
  key=str(uuid4());r=await req(c,'POST','/action-drafts/'+d['draft_id']+'/confirm',{'expected_version':d['version'],'payload_hash':d['payload_hash']},want=(200,201,503),key=key,label=label)
  first_status=records[-1]['status'];counts=graph_counts(t,d['draft_id']);saved=all(v==1 for v in counts.values());rolled=all(v==0 for v in counts.values())
  receipt=await req(c,'GET','/actions/'+d['draft_id'],label=label+'_receipt')
  with t.engine.connect() as c:
   key_count=c.execute(text(f'SELECT count(*) FROM \"{t.schema}\".idempotency_records WHERE draft_id=:d AND key=:key'),{'d':UUID(d['draft_id']),'key':key}).scalar_one()
   actual_case=c.execute(text(f'SELECT id FROM \"{t.schema}\".cases WHERE origin_draft_id=:d'),{'d':UUID(d['draft_id'])}).scalar_one_or_none()
  identity_matches=receipt.get('case_id')==str(actual_case) and receipt.get('result_ref')=='CASE-'+str(actual_case).replace('-','')
  checks.append({'first_http_status':first_status,'outcome':'normal_ack' if first_status in (200,201) else 'committed_without_ack' if saved else 'rollback_no_commit','key_count':key_count,'receipt_matches_actual_case_ref':identity_matches,'name':label,'graph_counts':counts,'saved_one_graph':saved,'zero_graph':rolled,'receipt_state':receipt['state'],'key':key,'draft_id':d['draft_id']})
  assert saved and key_count==1 and identity_matches and receipt['state']=='committed','Private confirm did not finish; no automatic POST retry'
  return receipt['case_id']
 at=time.monotonic()
 async with app.router.lifespan_context(app):
  first_connection_ms=round((time.monotonic()-at)*1000,3)
  async with AsyncClient(transport=ASGITransport(app=app),base_url=origin) as customer,AsyncClient(transport=ASGITransport(app=app),base_url=origin) as cs,AsyncClient(transport=ASGITransport(app=app),base_url=origin) as admin,AsyncClient(transport=ASGITransport(app=app),base_url='https://foreign.localhost') as foreign:
   await req(customer,'POST','/auth/sessions',{})
   ch=await req(customer,'POST','/auth/challenges',{'contact':'customer@example.com','purpose':'customer_login'},202)
   code=sender.messages[UUID(ch['challenge_id'])].code;await req(customer,'POST','/auth/challenges/'+ch['challenge_id']+'/verify',{'code':code});del code
   d,_=await preview(customer,'Public staff seed',False);seed=await confirm(customer,d,'setup_seed_confirm')
   credentials={}
   for label,actor in [('cs',staff(t,UUID(seed))),('admin',administrator(t,UUID(seed)))]:
    password=secrets.token_urlsafe(24)
    with t.engine.begin() as c:
     c.execute(text(f'UPDATE "{t.schema}".staff_credentials SET password_hash=:h WHERE principal_id=:p'),{'h':hash_password(password),'p':actor.session.principal_id})
     contact=c.execute(text(f'SELECT email_address FROM "{t.schema}".contacts WHERE principal_id=:p'),{'p':actor.session.principal_id}).scalar_one()
    credentials[label]={'contact':contact,'password':password}
   members={}
   for label,c in [('cs',cs),('admin',admin)]:
    await req(c,'POST','/auth/sessions',{});await req(c,'POST','/staff/auth/login',credentials[label]);members[label]=(await req(c,'GET','/staff/me'))['memberships'][0]
   credentials.clear()
   # A minimal pilot exercises the exact measured paths before the longer cohort.
   await req(customer,'GET','/bookings/B123',label='pilot_source')
   d,cid=await preview(customer,stage+' pilot private');case=await confirm(customer,d,'pilot_private_confirm')
   await req(cs,'GET','/staff/cases/'+case+'?membership_id='+members['cs']['id'],label='pilot_staff')
   checks.append({'name':'pilot_completed_before_main','passed':True})
   # Real knowledge metadata, pgvector/RLS and selection; deterministic local embedding/model.
   am=members['admin']['id'];info={'body':'Parking visitors may wait in the arrivals hall.','source_uri':'urn:airport:parking:waiting','service_type':'parking','language':'en','visibility':'public','allowed_roles':[],'valid_from':'2025-01-01T00:00:00Z','valid_to':None,'applicability':{},'policy_reference':None,'supersedes_version_id':None}
   doc=await req(admin,'POST','/admin/knowledge/documents',{'membership_id':am,'document_key':'quality-parking','title':'Quality waiting guidance','content':info},201)
   v=(await req(admin,'GET','/admin/knowledge/documents/'+doc['id']+'/versions?membership_id='+am))['items'][0]
   async def transition(v,action):return await req(admin,'POST','/admin/knowledge/versions/'+v['id']+'/'+action,{'membership_id':am,'expected_revision':v['revision']})
   await transition(v,'index');index_calls=embed.calls
   for _ in range(2):assert(await req(admin,'GET','/admin/knowledge/versions/'+v['id']+'/index?membership_id='+am,label='index_status'))['ready']
   assert embed.calls==index_calls;await transition(v,'publish')
   ac,mid=await conv(customer,'Where can parking visitors wait?')
   begin=await req(customer,'POST','/conversations/'+ac+'/agent-runs',{'message_id':mid,'request_key':str(uuid4()),'purpose':'answer','service_type':'parking'},201)
   outcome=await req(customer,'POST','/agent-runs/'+begin['run_id']+'/resume',label='public_RAG_controlled_model')
   assert outcome['state']=='published'
   citations=await req(customer,'GET','/conversations/'+ac+'/messages/'+outcome['message_id']+'/citations',label='citation_read');assert citations['citations'][0]['version_id']==v['id']
   ops=[(customer,'/bookings/B123'),(customer,'/transactions/P123'),(customer,'/conversations/'+cid+'/events?limit=50'),(cs,'/staff/cases/'+case+'?membership_id='+members['cs']['id'])]
   for concurrency in (1,2):
    for offset in range(0,12,concurrency):
     batch=ops[offset%4:offset%4+concurrency] if offset%4+concurrency<=4 else [ops[offset%4],ops[0]]
     await asyncio.gather(*(req(c,'GET',path,label='reads_c'+str(concurrency)) for c,path in batch))
   for i in range(4):
    d,_=await preview(customer,stage+' new request '+str(i));await confirm(customer,d,'private_confirm_'+str(i))
   # Freshness/authority checks are read-only and never replay a business POST.
   await req(foreign,'POST','/auth/sessions',{})
   await req(foreign,'GET','/cases/'+case,want=(401,404),label='foreign_denial')
   source.denied=True
   try:await req(customer,'GET','/cases/'+case,want=(404,503),label='source_denial')
   finally:source.denied=False
   checks.append({'name':'foreign_and_source_denied_without_leak','passed':True})
   pool=raw._pool.get_stats()
 return {'stage':stage,'scope':'Actual ASGI/FastAPI/create_app/auth/RLS/current0038/Neon; source/model/embedding ports controlled, no socket/TLS/browser latency claim','records':records,'sql_events':db.events,'source_events':source_events,'grant_events':grant_events,'controlled_model_embedding_events':model_events,'checks':checks,'first_runtime_open_ms':first_connection_ms,'first_runtime_open_is_cold_resume_proof':False,'pool_stats':pool,'resources':memory(),'resource_samples':PROGRESS.get('resource_samples',[]),'cpu_seconds':round(time.process_time()-cpu,4),'wall_seconds':round(time.monotonic()-started,4),'cpu_core_equivalents':round((time.process_time()-cpu)/(time.monotonic()-started),4),'host_cpu_percent':round(100*(time.process_time()-cpu)/(time.monotonic()-started)/(os.cpu_count() or 1),3),'logical_cpus':os.cpu_count(),'requests':requests,'read_c1':percentiles([r['ms'] for r in records if r['phase']=='reads_c1']),'read_c2':percentiles([r['ms'] for r in records if r['phase']=='reads_c2']),'private_confirm':percentiles([r['ms'] for r in records if r['phase'].startswith('private_confirm_') and not r['phase'].endswith('_receipt')]),'paid_provider_requests':0,'emails_sent':0,'polling':'Explicit measured event GETs, not browser idle/active cadence; native cadence evidence reused','index_cache':'Two real index-status reads do not call embedding again; no query embedding cache exists','passed':True}

def main():
 stage=sys.argv[1];assert stage in {'calibration','acceptance'};preflight()
 if os.environ.get('QUALITY_COHORT') and stage!='acceptance':raise RuntimeError('Follow-up cannot rerun calibration')
 lock=freeze_guard('local') if stage=='acceptance' else None
 if artifact(stage+'-local-result.json').exists():raise RuntimeError('Result already exists; explicit new run namespace required')
 os.environ['DB_TEST_ENV_FILE']=str(ROOT/'.env')
 from test_private_source_schema_neon import target as base
 from test_private_source_graph_neon import target as graph
 from airport_assistant.infrastructure.database.migrations import migrate
 from airport_assistant.composition import database_loop_factory
 before=snapshot();save(stage+'-local-before.json',before);start=time.monotonic();result={};schema=None
 try:
  with contextmanager(base.__wrapped__)() as t:
   with contextmanager(graph.__wrapped__)(t) as t:
    schema=t.schema;save(stage+'-target.json',{'schema':schema,'roles':list(t.roles),'graph_role':t.graph_owner})
    migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0038_staff_administration')
    async def monitored():
     async def watch():
      while True:
       m=memory();PROGRESS.setdefault('resource_samples',[]).append({'at':time.monotonic(),**m})
       if m['peak_rss_mib']>768:raise RuntimeError('Sampled process memory ceiling exceeded')
       await asyncio.sleep(.5)
     async with asyncio.timeout(300):
      async with asyncio.TaskGroup() as group:
       task=group.create_task(run(t,stage));monitor=group.create_task(watch());value=await task;monitor.cancel();return value
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:result=runner.run(monitored())
 except Exception as e:
  while hasattr(e,'exceptions') and e.exceptions:e=e.exceptions[0]
  result={**PROGRESS,'passed':False,'error_type':type(e).__name__,'frames':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(e.__traceback__)]}
 finally:
  after=snapshot();save(stage+'-local-after.json',after);result['changed_inputs']=[p for p in before.keys()|after.keys() if before.get(p)!=after.get(p)];result['schema']=schema;result['freeze_hash']=lock['freeze_hash'] if lock else None;result['job_seconds']=round(time.monotonic()-start,2);result['frozen_files']=len(before)
  result['passed']=result.get('passed',False) and not result['changed_inputs'];save(stage+'-local-result.json',result);print(stage,'local PASS',result['passed'],'schema_cleanup_manifests',schema,flush=True)
 return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
