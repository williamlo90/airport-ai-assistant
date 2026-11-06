"""Isolated current-schema staff workspace host; no normal schema/provider writes."""
import asyncio,json,secrets,sys,traceback
from pathlib import Path
from contextlib import contextmanager
from uuid import UUID,uuid4
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'local/team/fe-qa-04';OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(Path(__file__).resolve().parent))
from customer_recovery_server import base,graph,migrate,RuntimeDatabase,ApplicationResources,create_app,Settings,RuntimeDatabaseSettings,AuthSettings,MailSettings,hash_password,staff,database_loop_factory,CSRF_COOKIE,SESSION_COOKIE,AccessIdentity,text,AsyncClient,ASGITransport
from test_staff_queue_neon import bulk_graphs
from test_staff_auth import insert
class Sender:
 def __init__(self):self.code=None
 async def send(self,message):self.code=message.code

def scalar(t,sql,**args):
 with t.engine.connect() as c:return c.execute(text(sql.format(s='"'+t.schema+'"')),args).scalar_one()

async def serve(t):
 import uvicorn
 from fastapi.staticfiles import StaticFiles
 from fastapi.responses import FileResponse,JSONResponse
 from urllib.parse import urlencode
 origin='https://localhost:8469';sender=Sender();checks=[]
 (OUT/'target.json').write_text(json.dumps({'schema':t.schema,'roles':list(t.roles),'graph_role':t.graph_owner}),encoding='utf-8')
 with t.engine.begin() as c:c.execute(text(f'INSERT INTO "{t.schema}".tenant_hosts(tenant_id,hostname) VALUES(:t,:host)'),dict(t=t.a,host='localhost'))
 db=RuntimeDatabase(t.url,pool_max=2,acquire_timeout=5,schema=t.schema)
 app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True,source_enabled=False,private_source_enabled=False,attachment_scanner_port=None),database_settings=RuntimeDatabaseSettings(DATABASE_URL=t.url,pool_max=2),auth_settings=AuthSettings(SESSION_SECRET='synthetic-staff-workspace-'*3),verification_sender=sender,mail_settings=MailSettings(enabled=False,auth_enabled=False,ingress_enabled=False,receiving_enabled=False,reply_enabled=False,accounts={},auth_routes={},webhooks={}),resources=ApplicationResources(db,None),schema=t.schema)
 assert not app.dependency_overrides
 async def req(c,method,path,body=None,want=200,key=None):
  h={}
  if method!='GET':h['Origin']=origin
  proof=c.cookies.get(CSRF_COOKIE)
  if proof:h['X-CSRF-Token']=proof
  if key:h['Idempotency-Key']=key
  r=await c.request(method,'/api/v1'+path,headers=h,**({'json':body} if body is not None else {}))
  if r.status_code not in ((want,) if isinstance(want,int) else want):
   (OUT/'http-failure.json').write_text(json.dumps({'method':method,'path':path,'status':r.status_code}),encoding='utf-8');raise AssertionError('Unexpected fixture HTTP status')
  return r.json() if r.content else None
 async with app.router.lifespan_context(app),AsyncClient(transport=ASGITransport(app=app),base_url=origin) as customer,AsyncClient(transport=ASGITransport(app=app),base_url=origin) as actor:
  await req(customer,'POST','/auth/sessions',{});ch=await req(customer,'POST','/auth/challenges',{'contact':'customer@example.com','purpose':'customer_login'},202)
  await req(customer,'POST','/auth/challenges/'+ch['challenge_id']+'/verify',{'code':sender.code});sender.code=None
  async def newcase(summary):
   conv=await req(customer,'POST','/conversations',{'language':'en'},201)
   await req(customer,'POST','/conversations/'+conv['conversation_id']+'/messages',{'body':summary,'client_message_id':str(uuid4())},201)
   d=await req(customer,'POST','/action-drafts',{'conversation_id':conv['conversation_id'],'action_type':'create_case','payload':{'category':'information','service_type':'general','summary':summary,'customer_report':summary,'contact_email':'customer@example.com'}},201)
   return await req(customer,'POST','/action-drafts/'+d['draft_id']+'/confirm',{'expected_version':d['version'],'payload_hash':d['payload_hash']},201,str(uuid4()))
  template=await newcase('WORKSPACE_MAIN_REPORT — synthetic airport inquiry');case=UUID(template['case_id'])
  main=staff(t,case);other=staff(t,case);password=secrets.token_urlsafe(24)
  with t.engine.begin() as c:
   org,q1=c.execute(text(f'SELECT organization_id,queue_id FROM "{t.schema}".cases WHERE id=:id'),dict(id=case)).one()
   ma=c.execute(text(f'SELECT id FROM "{t.schema}".staff_memberships WHERE principal_id=:p'),dict(p=main.session.principal_id)).scalar_one()
   mo=c.execute(text(f'SELECT id FROM "{t.schema}".staff_memberships WHERE principal_id=:p'),dict(p=other.session.principal_id)).scalar_one()
   c.execute(text(f'INSERT INTO "{t.schema}".membership_roles(tenant_id,membership_id,role) VALUES(:t,:m,:r)'),dict(t=t.a,m=ma,r='supervisor'))
   q2=insert(c,t.schema,'service_queues',tenant_id=t.a,organization_id=org,name='Second permitted queue',service_type='parking')
   qhidden=insert(c,t.schema,'service_queues',tenant_id=t.a,organization_id=org,name='Hidden queue',service_type='lounge')
   insert(c,t.schema,'membership_queues',tenant_id=t.a,membership_id=ma,organization_id=org,queue_id=q2)
   ob=insert(c,t.schema,'organizations',tenant_id=t.a,name='Admin-only other organization')
   qb=insert(c,t.schema,'service_queues',tenant_id=t.a,organization_id=ob,name='Foreign organization queue',service_type='general')
   mb=insert(c,t.schema,'staff_memberships',tenant_id=t.a,principal_id=main.session.principal_id,organization_id=ob)
   insert(c,t.schema,'membership_roles',tenant_id=t.a,membership_id=mb,role='admin')
   contact=c.execute(text(f'SELECT email_address FROM "{t.schema}".contacts WHERE principal_id=:p'),dict(p=main.session.principal_id)).scalar_one()
   c.execute(text(f'UPDATE "{t.schema}".staff_credentials SET password_hash=:h WHERE principal_id=:p'),dict(h=hash_password(password),p=main.session.principal_id))
  identity=AccessIdentity(await app.state.services.auth.current(t.a,customer.cookies.get(SESSION_COOKIE)))
  fixture={'schema':t.schema,'engine':t.engine}
  bulk_graphs(fixture,str(case),identity,q2,count=104);bulk_graphs(fixture,str(case),identity,qhidden,count=2)
  # Force routing only for one genuine second-organization case, then restore it.
  with t.engine.begin() as c:
   disabled=c.execute(text(f'UPDATE "{t.schema}".service_queues SET active=false WHERE tenant_id=:t AND active AND id<>:id RETURNING id'),dict(t=t.a,id=qb)).scalars().all()
  try:foreign=await newcase('FOREIGN_REPORT_MUST_STAY_HIDDEN')
  finally:
   with t.engine.begin() as c:
    for q in disabled:c.execute(text(f'UPDATE "{t.schema}".service_queues SET active=true WHERE id=:id'),dict(id=q))
  with t.engine.connect() as c:
   expected=[str(x) for x in c.execute(text(f'SELECT id FROM "{t.schema}".cases WHERE tenant_id=:t AND organization_id=:o AND queue_id IN(:a,:b) ORDER BY created_at,id'),dict(t=t.a,o=org,a=q1,b=q2)).scalars().all()]
   source=c.execute(text(f'SELECT id FROM "{t.schema}".cases WHERE queue_id=:q AND id<>:id ORDER BY id LIMIT 1'),dict(q=q1,id=case)).scalar_one()
   candidate=c.execute(text(f'SELECT id,public_reference FROM "{t.schema}".cases WHERE queue_id=:q ORDER BY id LIMIT 1'),dict(q=q2)).one()
  assert len(expected)==106 and expected[-1]==str(case)
  await req(actor,'POST','/auth/sessions',{});await req(actor,'POST','/staff/auth/login',{'contact':contact,'password':password})
  # Exact selected membership and queue scope, independent of principal role union.
  for path in ('/staff/cases/'+str(foreign['case_id']),'/staff/cases/'+str(case)+'?membership_id='+str(mb),'/staff/cases/'+str(foreign['case_id'])+'/capabilities?membership_id='+str(ma)):
   denied=await req(actor,'GET',path,want=404);assert 'customer_report' not in denied
  denied=await req(actor,'GET','/staff/queues/cases?membership_id='+str(mb),want=(403,404));assert 'items' not in denied
  checks.append({'name':'current0038_selected_membership_admin_other_org_cannot_borrow_CS_or_read_foreign_case'})
  seen=[];cursor=None;watermark=None
  while True:
   params={'membership_id':str(ma),'view':'all','limit':25}
   if cursor:params['cursor']=cursor
   data=await req(actor,'GET','/staff/queues/cases?'+urlencode(params));assert 'total' not in data
   assert all('customer_report' not in x for x in data['items'])
   watermark=watermark or data['watermark'];assert data['watermark']==watermark
   seen.extend(x['id'] for x in data['items'])
   if not data['has_more']:break
   cursor=data['next_cursor'];assert cursor
  assert seen==expected and len(set(seen))==106
  checks.append({'name':'current0038_real106case_pagination_25_no_duplicates_hidden_queue_or_fake_total','cases':106})
  # Candidate read is advisory; current permissions must be checked again at write.
  caps=await req(actor,'GET','/staff/cases/'+str(source)+'/capabilities?membership_id='+str(ma))
  eligible=await req(actor,'GET','/staff/cases/'+str(source)+'/eligible-assignees?membership_id='+str(ma))
  assert any(x['membership_id']==str(mo) for x in eligible['items'])
  with t.engine.begin() as c:c.execute(text(f'DELETE FROM "{t.schema}".membership_queues WHERE membership_id=:m AND queue_id=:q'),dict(m=mo,q=q1))
  try:
   await req(actor,'PATCH','/staff/cases/'+str(source)+'/assignment',{'expected_version':caps['case_version'],'assignee_id':str(mo)},422)
   assert scalar(t,'SELECT version FROM {s}.cases WHERE id=:id',id=source)==caps['case_version']
  finally:
   with t.engine.begin() as c:insert(c,t.schema,'membership_queues',tenant_id=t.a,membership_id=mo,organization_id=org,queue_id=q1)
  checks.append({'name':'current0038_previously_eligible_assignee_revoked_before_PATCH_rejected422_no_write'})
  current=await req(actor,'GET','/staff/cases/'+str(source))
  lookup=lambda reference:{'membership_id':str(ma),'expected_version':current['version'],'public_reference':reference}
  forbidden=await req(actor,'POST','/staff/cases/'+str(source)+'/duplicate-candidates/search',lookup(foreign['result_ref']))
  absent=await req(actor,'POST','/staff/cases/'+str(source)+'/duplicate-candidates/search',lookup('CASE-'+uuid4().hex))
  assert forbidden['items']==absent['items']==[] and 'customer_report' not in str(forbidden)
  allowed=await req(actor,'POST','/staff/cases/'+str(source)+'/duplicate-candidates/search',lookup(candidate[1]));assert len(allowed['items'])==1
  with t.engine.begin() as c:c.execute(text(f'DELETE FROM "{t.schema}".membership_queues WHERE membership_id=:m AND queue_id=:q'),dict(m=ma,q=q2))
  try:
   await req(actor,'PATCH','/staff/cases/'+str(source)+'/status',{'expected_version':current['version'],'status':'closed','close_reason':'duplicate','resolution':'Synthetic duplicate check','next_action':'See original case.','related_case_id':str(candidate[0])},404)
   assert scalar(t,'SELECT version FROM {s}.cases WHERE id=:id',id=source)==current['version']
  finally:
   with t.engine.begin() as c:insert(c,t.schema,'membership_queues',tenant_id=t.a,membership_id=ma,organization_id=org,queue_id=q2)
  await req(actor,'PATCH','/staff/cases/'+str(source)+'/status',{'expected_version':current['version'],'status':'closed','close_reason':'no_response','resolution':'Synthetic closure','next_action':'Contact support.'},422)
  assert scalar(t,'SELECT version FROM {s}.cases WHERE id=:id',id=source)==current['version']
  checks.append({'name':'current0038_duplicate_foreign_equals_absent_target_rechecked_and_no_response_requires_evidence'})
  dist=ROOT/'frontend/dist';app.mount('/assets',StaticFiles(directory=dist/'assets'))
  @app.get('/{path:path}',include_in_schema=False)
  async def spa(path:str):
   if path.startswith(('api/','health/')):return JSONResponse({},status_code=404)
   resolved=(dist/path).resolve();return FileResponse(resolved if resolved.is_relative_to(dist.resolve()) and resolved.is_file() else dist/'index.html',headers={'Cache-Control':'no-store'})
  server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8469,ssl_certfile=str(ROOT/'local/tls/localhost.pem'),ssl_keyfile=str(ROOT/'local/tls/localhost-key.pem'),lifespan='off',access_log=False,log_level='warning',workers=1,proxy_headers=False))
  task=asyncio.create_task(server.serve());stop=OUT/'stop';ready=OUT/'ready.json';stop.unlink(missing_ok=True)
  try:
   for _ in range(100):
    if server.started:break
    if task.done():await task
    await asyncio.sleep(.1)
   assert server.started
   ready.write_text(json.dumps({'origin':origin,'schema':t.schema,'contact':contact,'password':password,'membership':str(ma),'other_membership':str(mb),'case_id':str(case),'foreign_ref':foreign['result_ref'],'expected_ids':expected}),encoding='utf-8')
   deadline=asyncio.get_running_loop().time()+300
   while not stop.exists() and asyncio.get_running_loop().time()<deadline and not task.done():
    control=OUT/'control.json'
    if control.exists():
     cmd=json.loads(control.read_text());control.unlink();command=cmd['action'];result={}
     if command=='bump':
      current=await req(actor,'GET','/staff/cases/'+str(case));v=await req(actor,'PATCH','/staff/cases/'+str(case)+'/assignment',{'expected_version':current['version'],'assignee_id':str(mo)})
      result={'version':v['version'],'assigned_other':v['assigned_membership_id']==str(mo)}
     elif command=='revoke':
      with t.engine.begin() as c:c.execute(text(f'UPDATE "{t.schema}".staff_memberships SET active=false,version=version+1 WHERE id=:id'),dict(id=ma))
      await req(actor,'GET','/staff/cases/'+str(case)+'?membership_id='+str(ma),want=404)
      result={'revoked':True}
     elif command=='verify':
      with t.engine.connect() as c:
       row=c.execute(text(f'SELECT status,close_reason,followup_evidence,assigned_membership_id FROM "{t.schema}".cases WHERE id=:id'),dict(id=case)).one()
       history=c.execute(text(f'SELECT count(*) FROM "{t.schema}".case_history WHERE case_id=:id AND event_type=:event'),dict(id=case,event='assigned')).scalar_one()
      assert row[0]=='closed' and row[1]=='no_response' and row[2]=='Synthetic follow-up attempt recorded for this test; no email delivery claimed.' and row[3]==ma and history==2
      checks.append({'name':'native_actual_assignment_conflict_unknown_ACK_and_evidence_closure_single_graph','assignment_events':history,'closed':True,'evidence_exact':True})
      result={'verified':True}
     else:raise AssertionError('Unknown control')
     (OUT/(str(UUID(cmd['id']))+'.json')).write_text(json.dumps(result),encoding='utf-8')
    await asyncio.sleep(.1)
   assert stop.exists() and any(c['name'].startswith('native_actual') for c in checks)
  finally:
   server.should_exit=True;await task;ready.unlink(missing_ok=True);stop.unlink(missing_ok=True)
   (OUT/'server-result.json').write_text(json.dumps({'checks':checks,'schema':t.schema,'server_closed':True,'identity_overrides':len(app.dependency_overrides),'provider_calls':0,'emails_sent':0},indent=2),encoding='utf-8')

def main():
 try:
  with contextmanager(base.__wrapped__)() as t:
   with contextmanager(graph.__wrapped__)(t) as t:
    assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0038_staff_administration')=='0038_staff_administration'
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:runner.run(serve(t))
  (OUT/'cleanup.json').write_text(json.dumps({'schema':t.schema,'cleaned':True,'roles':8,'graph_role_removed':True}),encoding='utf-8')
 except Exception as error:
  frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)]
  (OUT/'server-error.json').write_text(json.dumps({'type':type(error).__name__,'frames':frames}),encoding='utf-8');raise SystemExit(1)
if __name__=='__main__':main()
