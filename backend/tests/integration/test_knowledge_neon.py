"""KNOW-01 isolated Neon lifecycle/RLS; positive readiness is controlled, no provider."""
import os,hashlib,asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from test_staff_auth import target as auth_target,run,row,change,seed,harness
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings,RuntimeDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.modules.knowledge.service import KnowledgeService,IndexProof
from airport_assistant.modules.knowledge.models import Selection
from airport_assistant.infrastructure.database.knowledge import PostgresKnowledge
from airport_assistant.auth_config import AuthSettings
from airport_assistant.config import Settings
from airport_assistant.main import create_app
from airport_assistant.api.security import SESSION_COOKIE

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in isolated knowledge lifecycle Neon tests.')
@pytest.fixture(scope='module')
def target(auth_target):
    t=auth_target;settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    suffix=hashlib.sha256(t['schema'].encode()).hexdigest()[:24]
    try:
        for _ in range(2):assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0024_knowledge')=='0024_knowledge'
        yield t
    finally:
        with t['engine'].begin() as c:
            assert t['schema'].startswith('auth03_test_');c.exec_driver_sql('DROP SCHEMA IF EXISTS "'+t['schema']+'" CASCADE')
            for prefix in ('reply_owner_','email_ingress_','mail_owner_','job_worker_','job_owner_','knowledge_lock_'):
                name=prefix+suffix
                if c.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:r'),dict(r=name)).first():c.exec_driver_sql('DROP ROLE "'+name+'"')

class ReadyIndex:
    def __init__(self):self.hook=None;self.calls=[]
    async def readiness(self,t,id,basis):
        self.calls.append((t,id,basis))
        if self.hook:await self.hook()
        return IndexProof(t,id,basis,uuid4(),datetime.now(timezone.utc)+timedelta(minutes=2))

@asynccontextmanager
async def app(t,ready=True):
    async with harness(t) as h:
        await h.db._pool.resize(min_size=0,max_size=2)
        index=ReadyIndex() if ready else None
        service=KnowledgeService(PostgresKnowledge(h.db,t['schema']),index)
        h.app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True),database_settings=RuntimeDatabaseSettings(DATABASE_URL=t['url']),auth_settings=AuthSettings(SESSION_SECRET=t['key']),services=replace(h.app.state.services,knowledge=service))
        yield h,service,index

def content(**changes):
    value=dict(body='Official parking policy version one.',source_uri='urn:airport:parking:v1',service_type='parking',language='en',visibility='public',allowed_roles=[],valid_from='2025-01-01T00:00:00Z',valid_to=None,applicability={},policy_reference='P-OLD',supersedes_version_id=None);value.update(changes);return value
async def login(h,c,p):
    await h.boot(c);r=await h.login(c,p['email']);assert r.status_code==200,r.text
async def create(h,c,p,**changes):
    r=await c.post('/api/v1/admin/knowledge/documents',headers=h.headers(c),json=dict(membership_id=str(p['membership']),document_key='parking-'+uuid4().hex,title='Parking policy',content=content(**changes)));assert r.status_code==201,r.text
    d=r.json();v=await c.get('/api/v1/admin/knowledge/documents/'+d['id']+'/versions',params={'membership_id':str(p['membership'])});assert v.status_code==200,v.text
    return d,v.json()['items'][0]
async def publish(h,c,p,v,**extra):return await c.post('/api/v1/admin/knowledge/versions/'+v['id']+'/publish',headers=h.headers(c),json=dict(membership_id=str(p['membership']),expected_revision=v['revision'],**extra))
async def identity(h,c):return await h.app.state.services.access.resolve(h.target['a'],c.cookies.get(SESSION_COOKIE))
def selection(d,**extra):
    value=dict(document_key=d['document_key'],service_type='parking',language='en',as_of='2025-06-01T00:00:00Z',policy_reference='P-OLD');value.update(extra);return Selection.model_validate(value)

def test_real_lifecycle_versions_audit_history_and_default_index_gate(target):
    p=seed(target,roles=('admin',))
    async def scenario():
        async with app(target,ready=False) as (h,k,index):
            async with h.client() as c,h.client() as public:
                await login(h,c,p);await h.boot(public)
                d,v=await create(h,c,p);assert d['revision']==2 and v['status']=='draft'
                anon=await identity(h,public);assert await k.select(anon,selection(d)) is None
                blocked=await publish(h,c,p,v);assert blocked.status_code==503 and blocked.json()['error']['code']=='KNOWLEDGE_INDEX_UNAVAILABLE'
                assert row(target,'SELECT count(*) FROM {s}.knowledge_audit WHERE document_id=:d',d=UUID(d['id']))[0]==2
                k.index=ReadyIndex();good=await publish(h,c,p,v);assert good.status_code==200,good.text;published=good.json();assert published['revision']==2 and published['status']=='published'
                assert (await k.select(anon,selection(d)))['id']==UUID(v['id'])
                assert await k.select(anon,selection(d,policy_reference='MISSING')) is None
                edit=await c.patch('/api/v1/admin/knowledge/versions/'+v['id'],headers=h.headers(c),json=dict(membership_id=str(p['membership']),expected_revision=2,content=content(body='Must not rewrite published content')));assert edit.status_code==409
                latest_doc=await c.get('/api/v1/admin/knowledge/documents/'+d['id'],params={'membership_id':str(p['membership'])});assert latest_doc.json()['revision']==3
                new=await c.post('/api/v1/admin/knowledge/documents/'+d['id']+'/versions',headers=h.headers(c),json=dict(membership_id=str(p['membership']),expected_document_revision=3,content=content(body='New policy applies to new bookings.',policy_reference='P-NEW',supersedes_version_id=v['id'])));assert new.status_code==201,new.text
                second=new.json();r=await publish(h,c,p,second);assert r.status_code==200,r.text
                assert (await k.select(anon,selection(d)))['id']==UUID(v['id'])
                assert (await k.select(anon,selection(d,policy_reference='P-NEW')))['id']==UUID(second['id'])
                withdrawn=await c.post('/api/v1/admin/knowledge/versions/'+v['id']+'/withdraw',headers=h.headers(c),json=dict(membership_id=str(p['membership']),expected_revision=2,reason='Policy source withdrawn.'));assert withdrawn.status_code==200,withdrawn.text
                assert await k.select(anon,selection(d)) is None
                assert (await k.select(anon,selection(d,policy_reference='P-NEW')))['id']==UUID(second['id'])
                audit=await c.get('/api/v1/admin/knowledge/documents/'+d['id']+'/audit',params=dict(membership_id=str(p['membership']),limit=2));assert audit.status_code==200,audit.text
                assert [x['event_type'] for x in audit.json()['items']]==['document_created','version_created'] and audit.json()['has_more']
                later=await c.get('/api/v1/admin/knowledge/documents/'+d['id']+'/audit',params=dict(membership_id=str(p['membership']),after=audit.json()['next_after']));assert len(later.json()['items'])==4
                assert all(r.headers['cache-control']=='no-store' for r in (blocked,good,new,withdrawn,audit,later))
    run(scenario())

def test_acl_org_role_scope_tenant_isolation_and_raw_runtime_guards(target):
    p=seed(target,roles=('admin',));cs=seed(target,roles=('cs',));other=seed(target,roles=('admin',));foreign=seed(target,roles=('admin',),tenant=target['b'])
    async def scenario():
        async with app(target) as (h,k,index):
            async with h.client() as c,h.client() as staff,h.client() as outsider,h.client('b.example.test') as tenantb,h.client() as anon:
                await login(h,c,p);await login(h,staff,cs);await login(h,outsider,other);await login(h,tenantb,foreign);await h.boot(anon)
                d,v=await create(h,c,p,visibility='staff',allowed_roles=['cs'])
                actor=await identity(h,c)
                from airport_assistant.infrastructure.database.connections import DatabaseError
                # NULL proof hashes must fail CHECK rather than pass as SQL UNKNOWN.
                for expires,hash_expr in (("clock_timestamp()+interval '1 minute'",'NULL'),("clock_timestamp()-interval '1 second'",'basis_hash')):
                    with pytest.raises(DatabaseError):
                        async with h.db.transaction(actor.database_context) as db:
                            await db.execute("SELECT set_config('app.knowledge_membership_id',%s,true)",(str(p['membership']),))
                            await db.execute('UPDATE "'+target['schema']+'".knowledge_versions SET status=%s,revision=revision+1,index_generation=gen_random_uuid(),index_basis_hash='+hash_expr+',index_valid_until='+expires+' WHERE id=%s',('published',UUID(v['id'])))
                assert row(target,'SELECT status,revision FROM {s}.knowledge_versions WHERE id=:id',id=UUID(v['id']))==('draft',1)
                assert (await publish(h,c,p,v)).status_code==200
                assert await k.select(await identity(h,anon),selection(d)) is None
                assert await k.select(await identity(h,staff),selection(d)) is None
                # A CS role in another organization does not complete a roleless membership here.
                with target['engine'].begin() as db:
                    from test_staff_auth import insert
                    mid=insert(db,target['schema'],'staff_memberships',tenant_id=p['tenant'],principal_id=cs['principal'],organization_id=p['org'])
                    insert(db,target['schema'],'membership_roles',tenant_id=p['tenant'],membership_id=mid,role='supervisor')
                assert await k.select(await identity(h,staff),selection(d)) is None
                with target['engine'].begin() as db:insert(db,target['schema'],'membership_roles',tenant_id=p['tenant'],membership_id=mid,role='cs')
                assert (await k.select(await identity(h,staff),selection(d)))['id']==UUID(v['id'])
                change(target,"DELETE FROM {s}.membership_roles WHERE membership_id=:m AND role='cs'",m=mid)
                assert await k.select(await identity(h,staff),selection(d)) is None
                for client,member in ((staff,cs),(outsider,other),(tenantb,foreign)):
                    r=await client.get('/api/v1/admin/knowledge/versions/'+v['id'],params=dict(membership_id=str(member['membership'])));assert r.status_code==404,r.text
                assert (await anon.get('/api/v1/admin/knowledge/versions/'+v['id'],params=dict(membership_id=str(p['membership'])))).status_code==401
                for headers in ({'Origin':'https://a.example.test'},{**h.headers(c),'Origin':'https://evil.example.test'}):assert (await c.post('/api/v1/admin/knowledge/versions/'+v['id']+'/publish',headers=headers,json=dict(membership_id=str(p['membership']),expected_revision=2))).status_code==403
                for extra in (dict(index_ready=True),dict(tenant_id=str(target['b'])),dict(expected_revision=True)):
                    body=dict(membership_id=str(p['membership']),expected_revision=2);body.update(extra)
                    assert (await c.post('/api/v1/admin/knowledge/versions/'+v['id']+'/publish',headers=h.headers(c),json=body)).status_code==422
                actor=await identity(h,c)
                # Runtime cannot tamper audit, delete versions, mutate document generation or edit published body.
                for statement in ('DELETE FROM {s}.knowledge_versions WHERE id=%s',"UPDATE {s}.knowledge_audit SET reason='fake' WHERE version_id=%s",'UPDATE {s}.knowledge_documents SET revision=revision+1 WHERE id=%s',"UPDATE {s}.knowledge_versions SET body='forged',revision=revision+1 WHERE id=%s"):
                    from airport_assistant.infrastructure.database.connections import DatabaseError
                    with pytest.raises(DatabaseError):
                        async with h.db.transaction(actor.database_context) as db:
                            await db.execute("SELECT set_config('app.knowledge_membership_id',%s,true)",(str(p['membership']),))
                            await db.execute(statement.format(s='"'+target['schema']+'"'),(UUID(d['id'] if 'documents SET' in statement else v['id']),))
                assert row(target,"SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:s AND c.relname LIKE 'knowledge_%' AND c.relkind='r' AND NOT(c.relrowsecurity AND c.relforcerowsecurity)",s=target['schema'])[0]==0
    run(scenario())

def test_probe_edit_and_admin_revocation_cannot_publish_stale_basis(target):
    p=seed(target,roles=('admin','cs'))
    async def scenario():
        async with app(target) as (h,k,index):
            async with h.client() as c:
                await login(h,c,p);d,v=await create(h,c,p)
                async def edit_during_probe():
                    index.hook=None
                    r=await c.patch('/api/v1/admin/knowledge/versions/'+v['id'],headers=h.headers(c),json=dict(membership_id=str(p['membership']),expected_revision=1,content=content(body='Revised source text')));assert r.status_code==200,r.text
                index.hook=edit_during_probe;r=await publish(h,c,p,v);assert r.status_code==409,r.text
                assert row(target,'SELECT status,revision FROM {s}.knowledge_versions WHERE id=:id',id=UUID(v['id']))==('draft',2)
                async def revoke_during_probe():
                    index.hook=None;change(target,"DELETE FROM {s}.membership_roles WHERE membership_id=:m AND role='admin'",m=p['membership'])
                index.hook=revoke_during_probe
                try:
                    r=await publish(h,c,p,{**v,'revision':2});assert r.status_code==404,r.text
                finally:
                    with target['engine'].begin() as db:
                        from test_staff_auth import insert
                        insert(db,target['schema'],'membership_roles',tenant_id=p['tenant'],membership_id=p['membership'],role='admin')
                assert row(target,"SELECT count(*) FROM {s}.knowledge_audit WHERE document_id=:d AND event_type='published'",d=UUID(d['id']))[0]==0
    run(scenario())

def test_concurrent_overlapping_publish_has_one_winner_and_atomic_audit(target):
    p=seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,k,index):
            async with h.client() as c,h.client() as second:
                await login(h,c,p);await login(h,second,p);d,v1=await create(h,c,p)
                r=await c.post('/api/v1/admin/knowledge/documents/'+d['id']+'/versions',headers=h.headers(c),json=dict(membership_id=str(p['membership']),expected_document_revision=2,content=content(body='Conflicting interval.')));assert r.status_code==201,r.text
                v2=r.json();barrier=asyncio.Event();seen=0
                async def meet():
                    nonlocal seen
                    seen+=1
                    if seen==2:barrier.set()
                    await asyncio.wait_for(barrier.wait(),10)
                index.hook=meet
                results=await asyncio.gather(publish(h,c,p,v1),publish(h,second,p,v2))
                assert sorted(r.status_code for r in results)==[200,409],[r.text for r in results]
                assert row(target,"SELECT count(*) FROM {s}.knowledge_versions WHERE document_id=:d AND status='published'",d=UUID(d['id']))[0]==1
                assert row(target,"SELECT count(*) FROM {s}.knowledge_audit WHERE document_id=:d AND event_type='published'",d=UUID(d['id']))[0]==1
                assert row(target,'SELECT revision FROM {s}.knowledge_documents WHERE id=:d',d=UUID(d['id']))[0]==4
    run(scenario())

def test_applicability_boundaries_and_foreign_document_predecessor(target):
    p=seed(target,roles=('admin',))
    async def scenario():
        async with app(target) as (h,k,index):
            async with h.client() as c,h.client() as public:
                await login(h,c,p);await h.boot(public)
                d,v=await create(h,c,p,valid_to='2026-01-01T00:00:00Z',applicability=dict(terminal='T2',booking_created_from='2025-02-01T00:00:00Z',booking_created_to='2025-03-01T00:00:00Z'))
                assert (await publish(h,c,p,v)).status_code==200
                who=await identity(h,public)
                base=dict(terminal='T2',booking_created_at='2025-02-01T00:00:00Z')
                assert (await k.select(who,selection(d,as_of='2025-01-01T00:00:00Z',**base)))['id']==UUID(v['id'])
                for changes in (dict(as_of='2026-01-01T00:00:00Z'),dict(terminal=None),dict(terminal='T1'),dict(booking_created_at=None),dict(booking_created_at='2025-03-01T00:00:00Z'),dict(policy_reference='NO-FALLBACK'),dict(language='id')):
                    params={**base,**changes};assert await k.select(who,selection(d,**params)) is None
                other,ov=await create(h,c,p)
                r=await c.post('/api/v1/admin/knowledge/documents/'+other['id']+'/versions',headers=h.headers(c),json=dict(membership_id=str(p['membership']),expected_document_revision=2,content=content(supersedes_version_id=v['id'])));assert r.status_code==422,r.text
                assert row(target,'SELECT revision FROM {s}.knowledge_documents WHERE id=:d',d=UUID(other['id']))[0]==2
    run(scenario())
