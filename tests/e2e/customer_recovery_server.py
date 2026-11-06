"""Opt-in isolated native customer E2E host. Config loaded internally; never logged."""
import asyncio,json,os,secrets,sys,traceback
from pathlib import Path
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from uuid import UUID,uuid4
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'local/team/fe-qa-03';OUT.mkdir(exist_ok=True)
sys.path[:0]=[str(ROOT/'backend/src'),str(ROOT/'backend/tests/integration')]
os.environ['DB_TEST_ENV_FILE']=str(ROOT/'.env')
from sqlalchemy import text
from httpx import AsyncClient,ASGITransport
from test_private_source_schema_neon import target as base
from test_private_source_graph_neon import target as graph,staff
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.composition import ApplicationResources,database_loop_factory
from airport_assistant.main import create_app
from airport_assistant.config import Settings
from airport_assistant.auth_config import AuthSettings
from airport_assistant.database_config import RuntimeDatabaseSettings
from airport_assistant.mail_config import MailSettings
from airport_assistant.api.security import CSRF_COOKIE,SESSION_COOKIE
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.drafts.models import seal
from airport_assistant.infrastructure.security.passwords import hash_password

class Sender:
 async def send(self,message):(OUT/'otp.json').write_text(json.dumps({'code':message.code}),encoding='utf-8')

def graph_counts(t,draft):
 with t.engine.connect() as c:
  row=c.execute(text(f'SELECT id FROM "{t.schema}".cases WHERE origin_draft_id=:d'),dict(d=UUID(str(draft)))).all()
  assert len(row)<=1
  case=row[0][0] if row else UUID(int=0)
  result={'cases':len(row)}
  for table,column in [('action_consents','draft_id'),('outbox_jobs','action_draft_id'),('idempotency_records','draft_id'),('messages','case_id'),('case_history','case_id'),('audit_events','case_id')]:
   result[table]=c.execute(text(f'SELECT count(*) FROM "{t.schema}".{table} WHERE {column}=:id'),dict(id=UUID(str(draft)) if column in ('draft_id','action_draft_id') else case)).scalar_one()
  return result

async def serve(t):
 (OUT/'target.json').write_text(json.dumps({'schema':t.schema,'roles':list(t.roles),'graph_role':t.graph_owner}),encoding='utf-8')
 import uvicorn
 from fastapi.staticfiles import StaticFiles
 from fastapi.responses import FileResponse,JSONResponse
 origin='https://localhost:8468'
 with t.engine.begin() as c:c.execute(text(f'INSERT INTO "{t.schema}".tenant_hosts(tenant_id,hostname) VALUES(:t,:host)'),dict(t=t.a,host='localhost'))
 db=RuntimeDatabase(t.url,pool_max=2,acquire_timeout=5,schema=t.schema)
 app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True,source_enabled=False,private_source_enabled=False,attachment_scanner_port=None),database_settings=RuntimeDatabaseSettings(DATABASE_URL=t.url,pool_max=2),auth_settings=AuthSettings(SESSION_SECRET='synthetic-customer-recovery-'*3),verification_sender=Sender(),mail_settings=MailSettings(enabled=False,auth_enabled=False,ingress_enabled=False,receiving_enabled=False,reply_enabled=False,accounts={},auth_routes={},webhooks={}),resources=ApplicationResources(db,None),schema=t.schema)
 assert not app.dependency_overrides
 async def req(client,method,path,body=None,want=200,key=None):
  headers={}
  if method!='GET':headers['Origin']=origin
  proof=client.cookies.get(CSRF_COOKIE)
  if proof:headers['X-CSRF-Token']=proof
  if key:headers['Idempotency-Key']=key
  response=await client.request(method,'/api/v1'+path,headers=headers,**({'json':body} if body is not None else {}))
  if response.status_code not in ((want,) if isinstance(want,int) else want):
   (OUT/'http-failure.json').write_text(json.dumps({'method':method,'path':path,'status':response.status_code}),encoding='utf-8')
   raise AssertionError('Unexpected test HTTP status')
  return response.json() if response.content else None
 async with app.router.lifespan_context(app),AsyncClient(transport=ASGITransport(app=app),base_url=origin) as edge,AsyncClient(transport=ASGITransport(app=app),base_url=origin) as cs:
  await req(edge,'POST','/auth/sessions',{})
  challenge=await req(edge,'POST','/auth/challenges',{'contact':'customer@example.com','purpose':'customer_login'},202)
  otp=json.loads((OUT/'otp.json').read_text());(OUT/'otp.json').unlink()
  await req(edge,'POST','/auth/challenges/'+challenge['challenge_id']+'/verify',{'code':otp['code']})
  del otp
  async def preview(summary='General airport information'):
   conv=await req(edge,'POST','/conversations',{'language':'en'},201)
   await req(edge,'POST','/conversations/'+conv['conversation_id']+'/messages',{'body':'Please explain airport information.','client_message_id':str(uuid4())},201)
   return await req(edge,'POST','/action-drafts',{'conversation_id':conv['conversation_id'],'action_type':'create_case','payload':{'category':'information','service_type':'general','summary':summary,'customer_report':'Please explain airport information.','contact_email':'customer@example.com'}},201)
  async def confirm(d,key=None,hash=None):
   return await req(edge,'POST','/action-drafts/'+d['draft_id']+'/confirm',{'expected_version':d['version'],'payload_hash':hash or d['payload_hash']},want=(200,201),key=key or str(uuid4()))
  d=await preview();key=str(uuid4());one,two=await asyncio.gather(confirm(d,key),confirm(d,key));assert one==two
  counts=graph_counts(t,d['draft_id']);assert all(v==1 for v in counts.values()),counts
  checks=[{'name':'current0038_two_simultaneous_confirmations_same_key_single_durable_graph','counts':counts}]
  # Same actor/key cannot commit another draft.
  stale=await preview('Changed review');before=graph_counts(t,stale['draft_id'])
  await req(edge,'POST','/action-drafts/'+stale['draft_id']+'/confirm',{'expected_version':stale['version'],'payload_hash':stale['payload_hash']},409,key)
  edited=await req(edge,'PATCH','/action-drafts/'+stale['draft_id'],{'expected_version':stale['version'],'payload':{**stale['payload'],'summary':'Updated reviewed summary'}})
  for basis in (stale,{**edited,'payload_hash':'0'*64}):
   await req(edge,'POST','/action-drafts/'+basis['draft_id']+'/confirm',{'expected_version':basis['version'],'payload_hash':basis['payload_hash']},409,str(uuid4()))
  assert graph_counts(t,stale['draft_id'])==before
  checks.append({'name':'current0038_key_reuse_stale_version_and_wrong_hash_no_graph','counts':before})
  destination=await preview();queue=UUID(destination['destination']['queue_id'])
  with t.engine.begin() as c:c.execute(text(f'UPDATE "{t.schema}".service_queues SET active=false WHERE id=:id'),dict(id=queue))
  try:
   await req(edge,'POST','/action-drafts/'+destination['draft_id']+'/confirm',{'expected_version':destination['version'],'payload_hash':destination['payload_hash']},409,str(uuid4()))
   assert all(v==0 for v in graph_counts(t,destination['draft_id']).values())
  finally:
   with t.engine.begin() as c:c.execute(text(f'UPDATE "{t.schema}".service_queues SET active=true WHERE id=:id'),dict(id=queue))
  checks.append({'name':'current0038_inactive_destination_conflict_no_partial_graph'})
  expired=await preview();identity=AccessIdentity(await app.state.services.auth.current(t.a,edge.cookies.get(SESSION_COOKIE)))
  current=await app.state.services.drafts.get(identity,UUID(expired['draft_id']));past=datetime.now(timezone.utc)-timedelta(minutes=1)
  aged=seal(replace(current,created_at=past-timedelta(minutes=15),expires_at=past))
  with t.engine.begin() as c:c.execute(text(f'UPDATE "{t.schema}".action_drafts SET created_at=:created,expires_at=:expires,payload_hash=:hash WHERE id=:id'),dict(created=aged.created_at,expires=aged.expires_at,hash=aged.payload_hash,id=aged.id))
  await req(edge,'POST','/action-drafts/'+expired['draft_id']+'/confirm',{'expected_version':expired['version'],'payload_hash':expired['payload_hash']},410,str(uuid4()))
  assert (await req(edge,'GET','/actions/'+expired['draft_id']))['state']=='expired'
  assert all(v==0 for v in graph_counts(t,expired['draft_id']).values())
  checks.append({'name':'current0038_aged_expired_fixture_410_persisted_expired_no_graph','production_ttl_changed':False})
  # Prepare a real scoped staff login for the browser case closure control.
  person=staff(t,UUID(one['case_id']));password=secrets.token_urlsafe(24)
  with t.engine.begin() as c:
   contact=c.execute(text(f'SELECT email_address FROM "{t.schema}".contacts WHERE principal_id=:p'),dict(p=person.session.principal_id)).scalar_one()
   membership=c.execute(text(f'SELECT id FROM "{t.schema}".staff_memberships WHERE principal_id=:p'),dict(p=person.session.principal_id)).scalar_one()
   c.execute(text(f'INSERT INTO "{t.schema}".membership_roles(tenant_id,membership_id,role) VALUES(:t,:m,:role)'),dict(t=t.a,m=membership,role='supervisor'))
   c.execute(text(f'UPDATE "{t.schema}".staff_credentials SET password_hash=:h WHERE principal_id=:p'),dict(h=hash_password(password),p=person.session.principal_id))
  await req(cs,'POST','/auth/sessions',{});await req(cs,'POST','/staff/auth/login',{'contact':contact,'password':password})
  actual_membership=(await req(cs,'GET','/staff/me'))['memberships'][0]['id']
  dist=ROOT/'frontend/dist';app.mount('/assets',StaticFiles(directory=dist/'assets'))
  @app.get('/{path:path}',include_in_schema=False)
  async def spa(path:str):
   if path.startswith(('api/','health/')):return JSONResponse({},status_code=404)
   resolved=(dist/path).resolve();return FileResponse(resolved if resolved.is_relative_to(dist.resolve()) and resolved.is_file() else dist/'index.html',headers={'Cache-Control':'no-store'})
  server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8468,ssl_certfile=str(ROOT/'local/tls/localhost.pem'),ssl_keyfile=str(ROOT/'local/tls/localhost-key.pem'),lifespan='off',access_log=False,log_level='warning',workers=1,proxy_headers=False))
  task=asyncio.create_task(server.serve());stop=OUT/'stop';ready=OUT/'ready.json';stop.unlink(missing_ok=True)
  try:
   for _ in range(100):
    if server.started:break
    if task.done():await task
    await asyncio.sleep(.1)
   assert server.started
   ready.write_text(json.dumps({'origin':origin,'schema':t.schema}),encoding='utf-8')
   deadline=asyncio.get_running_loop().time()+300
   while not stop.exists() and asyncio.get_running_loop().time()<deadline and not task.done():
    control=OUT/'control.json'
    if control.exists():
     command=json.loads(control.read_text());control.unlink();case=str(UUID(command['case_id']));draft_id=str(UUID(command['draft_id']))
     if command['action']=='close':
      current=await req(cs,'GET','/staff/cases/'+case)
      assigned=await req(cs,'PATCH','/staff/cases/'+case+'/assignment',{'expected_version':current['version'],'assignee_id':actual_membership})
      closed=await req(cs,'PATCH','/staff/cases/'+case+'/status',{'expected_version':assigned['version'],'status':'closed','next_action':'No action pending.','close_reason':'resolved','resolution':'The airport information procedure was explained.'})
      (OUT/'closed.json').write_text(json.dumps({'closed':closed['status']=='closed','assigned':closed['assigned_membership_id']==actual_membership}),encoding='utf-8')
     elif command['action']=='verify':
      counts=graph_counts(t,draft_id)
      with t.engine.connect() as c:
       state=c.execute(text(f'SELECT status,next_actor,assigned_membership_id FROM "{t.schema}".cases WHERE id=:id'),dict(id=UUID(case))).one()
       reopens=c.execute(text(f'SELECT count(*) FROM "{t.schema}".case_history WHERE case_id=:id AND event_type=:event'),dict(id=UUID(case),event='reopened')).scalar_one()
      assert counts['cases']==counts['action_consents']==counts['outbox_jobs']==counts['idempotency_records']==1,counts
      assert tuple(state)==('in_progress','staff',UUID(actual_membership)) and reopens==1
      checks.append({'name':'native_submission_continuation_current_sql_single_graph_and_reopen','counts':counts,'reopens':reopens,'retained_assignee':True})
      (OUT/'verified.json').write_text(json.dumps({'verified':True}),encoding='utf-8')
    await asyncio.sleep(.1)
   assert stop.exists(),'Native browser did not finish in bounded window.'
   assert any(c['name'].startswith('native_submission') for c in checks),'Native SQL verification missing.'
  finally:
   server.should_exit=True;await task;ready.unlink(missing_ok=True);stop.unlink(missing_ok=True);(OUT/'otp.json').unlink(missing_ok=True)
   (OUT/'server-result.json').write_text(json.dumps({'checks':checks,'server_closed':True,'schema':t.schema,'normal_identity_overrides':len(app.dependency_overrides),'provider_calls':0,'emails_sent':0},indent=2),encoding='utf-8')

def main():
 try:
  with contextmanager(base.__wrapped__)() as t:
   with contextmanager(graph.__wrapped__)(t) as t:
    assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0038_staff_administration')=='0038_staff_administration'
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:runner.run(serve(t))
  (OUT/'cleanup.json').write_text(json.dumps({'cleaned':True,'schema':t.schema,'roles':8,'graph_role_removed':True}),encoding='utf-8')
 except Exception as error:
  frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)]
  (OUT/'server-error.json').write_text(json.dumps({'type':type(error).__name__,'frames':frames}),encoding='utf-8');raise SystemExit(1)
if __name__=='__main__':main()
