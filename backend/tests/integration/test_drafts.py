"""CASE-01 schema0006 coverage; current adoption is tested at0007."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path
from uuid import UUID,uuid4

import pytest
from sqlalchemy import text

from test_conversations import target as conversation_target,conversations,create as create_conversation,verified,send
from test_staff_auth import target as auth_target,run,row,change,insert,seed
from airport_assistant.auth_config import AuthSettings
from airport_assistant.config import Settings
from airport_assistant.database_config import MigrationDatabaseSettings,RuntimeDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.draft_repository import PostgresDrafts
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.modules.drafts.models import DraftError,seal,require_confirmation
from airport_assistant.api.auth import SESSION_COOKIE
from airport_assistant.main import create_app

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Set RUN_NEON_AUTH_TESTS=1 for real draft integration.')


@pytest.fixture(scope='module')
def target(conversation_target):
    t=conversation_target
    async def preserve():
        async with conversations(t) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=await create_conversation(h,c)
                assert (await send(h,c,id,body='Preserved through draft upgrade')).status_code==201
                return UUID(id)
    preserved=run(preserve())
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):
        assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0006_action_drafts')=='0006_action_drafts'
    assert row(t,'SELECT body FROM {s}.messages WHERE conversation_id=:id',id=preserved)[0]=='Preserved through draft upgrade'
    with t['engine'].begin() as db:
        for tenant in (t['a'],t['b']):
            org=insert(db,t['schema'],'organizations',tenant_id=tenant,name='Airport A Services' if tenant==t['a'] else 'Airport B Services')
            for number,service in enumerate(('general','parking','lounge'),10):
                insert(db,t['schema'],'service_queues',tenant_id=tenant,id=UUID(int=number),organization_id=org,name=service+' support',service_type=service)
    return t


@pytest.fixture(autouse=True)
def restore_active_routes(target):
    yield
    change(target,'UPDATE {s}.service_queues SET active=true')
    change(target,'UPDATE {s}.organizations SET active=true')


@asynccontextmanager
async def drafts(target):
    async with conversations(target) as (h,sender):
        service=DraftService(PostgresDrafts(h.db,target['schema']))
        h.app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True),
            database_settings=RuntimeDatabaseSettings(DATABASE_URL=target['url']),auth_settings=AuthSettings(SESSION_SECRET=target['key']),
            services=replace(h.app.state.services,drafts=service))
        yield h,sender


def payload(**changes):
    values=dict(category='booking_issue',service_type='parking',summary='Perpanjangan parkir',
                customer_report='Penerbangan saya terlambat. Booking PK-A-001 berakhir 2026-11-10. Biaya Rp100.000.',
                booking_reference='PK-A-001',contact_email='User@example.com',attachment_ids=[])
    return {**values,**changes}


async def create_draft(h,c,conversation_id,report=None,action='create_case'):
    result=await c.post('/api/v1/action-drafts',headers=h.headers(c),json=dict(conversation_id=str(conversation_id),action_type=action,payload=report or payload()))
    assert result.status_code==201,result.text
    return result.json()


async def actor(h,c,tenant=None):
    return await h.app.state.services.access.resolve(tenant or h.target['a'],c.cookies.get(SESSION_COOKIE))


async def deadline(target,h,c,id,*,future=False):
    current=await h.app.state.services.drafts.get(await actor(h,c),UUID(id))
    now=row(target,'SELECT clock_timestamp()')[0]
    expires=now+timedelta(seconds=4) if future else now-timedelta(seconds=1)
    changed=seal(replace(current,created_at=expires-timedelta(minutes=15),expires_at=expires))
    change(target,'UPDATE {s}.action_drafts SET created_at=:created,expires_at=:expires,payload_hash=:hash WHERE id=:id',
           created=changed.created_at,expires=changed.expires_at,hash=changed.payload_hash,id=changed.id)
    return changed


def test_preview_correction_hash_and_stale_approval(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c)
                first=await create_draft(h,c,conv)
                id=first['draft_id'];base='/api/v1/action-drafts/'+id
                assert first['version']==1 and first['state']=='pending'
                assert first['destination']['queue_id']==str(UUID(int=11))
                assert payload()['customer_report'] in first['summary']
                assert first['payload']['contact_email']=='User@example.com'
                timestamps=row(target,'SELECT created_at,updated_at FROM {s}.action_drafts WHERE id=:id',id=UUID(id))
                assert timestamps[1]>=timestamps[0]
                assert 'actor_principal_id' not in first and 'created_session_id' not in first
                assert (await c.get(base)).json()==first
                status=await c.get('/api/v1/actions/'+id)
                assert status.json()['state']=='pending' and status.json()['result_ref'] is None
                assert status.headers['cache-control']=='no-store'
                report=payload();report.update(summary='Mohon diperiksa',contact_email='Other@example.com')
                edited=await c.patch(base,headers=h.headers(c),json=dict(expected_version=1,payload=report))
                assert edited.status_code==200
                new=edited.json();assert new['version']==2 and new['payload_hash']!=first['payload_hash']
                assert new['expires_at']==first['expires_at']
                stored=await h.app.state.services.drafts.get(await actor(h,c),UUID(id))
                with pytest.raises(DraftError) as exc:
                    require_confirmation(stored,expected_version=1,payload_hash=first['payload_hash'],now=stored.created_at)
                assert exc.value.status==409
                require_confirmation(stored,expected_version=2,payload_hash=new['payload_hash'],now=stored.created_at)
                assert (await c.patch(base,headers=h.headers(c),json=dict(expected_version=1,payload=report))).status_code==409
                assert (await c.post(base+'/confirm',headers=h.headers(c),json=dict(expected_version=2,payload_hash=new['payload_hash']))).status_code==404
                assert row(target,'SELECT count(*) FROM {s}.action_drafts WHERE id=:id',id=UUID(id))[0]==1
    run(scenario())


def test_owner_and_creator_session_tenant_isolation(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c,h.client() as other,h.client('b.example.test') as cross:
                await h.boot(c);await h.boot(other);await h.boot(cross)
                conv=await create_conversation(h,c);draft=await create_draft(h,c,conv)
                base='/api/v1/action-drafts/'+draft['draft_id']
                for stranger in (other,cross):
                    assert (await stranger.get(base)).status_code==404
                    assert (await stranger.delete(base,headers=h.headers(stranger))).status_code==404
                    assert (await stranger.post('/api/v1/action-drafts',headers=h.headers(stranger),json=dict(conversation_id=conv,action_type='create_case',payload=payload()))).status_code==404
                email='account'+uuid4().hex+'@example.com'
                await verified(h,sender,c,email);await verified(h,sender,other,email)
                owned=await create_conversation(h,c)
                new=await create_draft(h,c,owned)
                assert (await other.get('/api/v1/conversations/'+owned)).status_code==200
                assert (await other.get('/api/v1/action-drafts/'+new['draft_id'])).status_code==404
                assert (await c.get(base)).status_code==404 # original anonymous session was revoked
    run(scenario())


def test_cancel_is_persistent_idempotent_and_not_delete(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c);draft=await create_draft(h,c,conv,action='request_handoff')
                base='/api/v1/action-drafts/'+draft['draft_id']
                first=await c.delete(base,headers=h.headers(c));again=await c.delete(base,headers=h.headers(c))
                assert first.status_code==again.status_code==200 and first.json()==again.json()
                assert first.json()['state']=='cancelled'
                assert (await c.patch(base,headers=h.headers(c),json=dict(expected_version=1,payload=payload()))).status_code==409
                assert (await c.get('/api/v1/actions/'+draft['draft_id'])).json()['state']=='cancelled'
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=UUID(draft['draft_id']))[0]=='cancelled'
    run(scenario())


@pytest.mark.parametrize('operation',['read','edit','cancel'])
def test_expired_state_commits_even_when_mutation_returns_410(target,operation):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c);draft=await create_draft(h,c,conv)
                id=draft['draft_id'];await deadline(target,h,c,id)
                base='/api/v1/action-drafts/'+id
                if operation=='read':
                    result=await c.get(base);assert result.status_code==200 and result.json()['state']=='expired'
                elif operation=='edit':assert (await c.patch(base,headers=h.headers(c),json=dict(expected_version=1,payload=payload()))).status_code==410
                else:assert (await c.delete(base,headers=h.headers(c))).status_code==410
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=UUID(id))[0]=='expired'
                assert (await c.get('/api/v1/actions/'+id)).json()['state']=='expired'
    run(scenario())


def test_parallel_edits_and_edit_cancel_have_no_lost_update(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c);draft=await create_draft(h,c,conv)
                base='/api/v1/action-drafts/'+draft['draft_id']
                one=payload();one['summary']='One';two=payload();two['summary']='Two'
                results=await asyncio.gather(c.patch(base,headers=h.headers(c),json=dict(expected_version=1,payload=one)),
                    c.patch(base,headers=h.headers(c),json=dict(expected_version=1,payload=two)))
                assert sorted(result.status_code for result in results)==[200,409]
                results=await asyncio.gather(c.patch(base,headers=h.headers(c),json=dict(expected_version=2,payload=one)),
                    c.delete(base,headers=h.headers(c)))
                assert results[1].status_code==200 and results[0].status_code in (200,409)
                assert (await c.get(base)).json()['state']=='cancelled'
    run(scenario())


def test_routing_is_server_owned_fallback_and_missing_route_not_success(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client('b.example.test') as c:
                await h.boot(c);conv=await create_conversation(h,c)
                change(target,"UPDATE {s}.service_queues SET active=false WHERE tenant_id=:tenant AND service_type='parking'",tenant=target['b'])
                draft=await create_draft(h,c,conv)
                assert draft['destination']['service_type']=='general' and draft['destination']['organization_name']=='Airport B Services'
                change(target,'UPDATE {s}.service_queues SET active=false WHERE tenant_id=:tenant',tenant=target['b'])
                result=await c.post('/api/v1/action-drafts',headers=h.headers(c),json=dict(conversation_id=conv,action_type='create_case',payload=payload()))
                assert result.status_code==503 and result.json()['error']['code']=='DRAFT_DESTINATION_UNAVAILABLE'
                assert row(target,'SELECT count(*) FROM {s}.action_drafts WHERE conversation_id=:id',id=UUID(conv))[0]==1
    run(scenario())


def test_get_keeps_destination_snapshot_until_explicit_correction(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c);first=await create_draft(h,c,conv)
                base='/api/v1/action-drafts/'+first['draft_id']
                change(target,'UPDATE {s}.service_queues SET name=:name WHERE tenant_id=:tenant AND id=:id',name='Updated parking queue',tenant=target['a'],id=UUID(int=11))
                try:
                    assert (await c.get(base)).json()==first
                    edited=await c.patch(base,headers=h.headers(c),json=dict(expected_version=1,payload=payload()))
                    assert edited.status_code==200 and edited.json()['payload_hash']!=first['payload_hash']
                    assert edited.json()['destination']['queue_name']=='Updated parking queue'
                finally:change(target,'UPDATE {s}.service_queues SET name=:name WHERE tenant_id=:tenant AND id=:id',name='parking support',tenant=target['a'],id=UUID(int=11))
    run(scenario())


@pytest.mark.parametrize('extra',[
    {'destination_queue_id':str(uuid4())},{'source_observations':[]},
    {'resource_security_labels':[]},{'attachment_ids':[str(uuid4())]},
])
def test_client_cannot_inject_routing_source_authority_or_attachments(target,extra):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c)
                result=await c.post('/api/v1/action-drafts',headers=h.headers(c),json=dict(conversation_id=conv,action_type='create_case',payload=payload(**extra)))
                assert result.status_code==422
                assert row(target,'SELECT count(*) FROM {s}.action_drafts WHERE conversation_id=:id',id=UUID(conv))[0]==0
    run(scenario())


def test_csrf_staff_and_revoked_sessions_cannot_modify_drafts(target):
    staff=seed(target)
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c);draft=await create_draft(h,c,conv)
                base='/api/v1/action-drafts/'+draft['draft_id']
                assert (await c.delete(base)).status_code==403
                old=await actor(h,c)
                assert (await c.post('/api/v1/auth/logout',headers=h.headers(c))).status_code==204
                with pytest.raises(AuthError) as exc:await h.app.state.services.drafts.cancel(old,UUID(draft['draft_id']))
                assert exc.value.status==401
                await h.boot(c)
                assert (await h.login(c,staff['email'])).status_code==200
                assert (await c.get(base)).status_code==403
    from airport_assistant.modules.identity.auth import AuthError
    run(scenario())


def test_runtime_rls_session_binding_terminal_immutability_and_hash_integrity(target):
    from airport_assistant.infrastructure.database.connections import DatabaseError
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c,h.client() as other:
                email='rls'+uuid4().hex+'@example.com'
                await verified(h,sender,c,email);await verified(h,sender,other,email)
                conv=await create_conversation(h,c);draft=await create_draft(h,c,conv);id=UUID(draft['draft_id'])
                stranger=await actor(h,other);owner=await actor(h,c)
                async with h.db.transaction(stranger.database_context) as db:
                    assert not await(await db.execute(f'SELECT id FROM "{target["schema"]}".action_drafts WHERE id=%s',(id,))).fetchall()
                for statement,params in [
                    ('UPDATE {s}.action_drafts SET created_session_id=%s WHERE id=%s',(stranger.session.id,id)),
                    ('DELETE FROM {s}.action_drafts WHERE id=%s',(id,)),
                ]:
                    with pytest.raises(DatabaseError) as error:
                        async with h.db.transaction(owner.database_context) as db:await db.execute(statement.format(s='"'+target['schema']+'"'),params)
                    assert error.value.sqlstate=='42501'
                await c.delete('/api/v1/action-drafts/'+str(id),headers=h.headers(c))
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction(owner.database_context) as db:
                        await db.execute(f'UPDATE "{target["schema"]}".action_drafts SET state=%s WHERE id=%s',('pending',id))
                assert error.value.sqlstate=='23514'
                other_draft=await create_draft(h,c,conv)
                change(target,'UPDATE {s}.action_drafts SET payload_hash=:hash WHERE id=:id',hash='0'*64,id=UUID(other_draft['draft_id']))
                failed=await c.get('/api/v1/action-drafts/'+other_draft['draft_id'])
                assert failed.status_code==500 and 'Perpanjangan' not in failed.text
    run(scenario())


@pytest.mark.parametrize('operation',['edit','cancel','read'])
def test_deadline_passed_while_operation_runs_is_not_internal_error(target,monkeypatch,operation):
    from airport_assistant.infrastructure.database.draft_repository import DraftSQL
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c);draft=await create_draft(h,c,conv)
                id=draft['draft_id'];await deadline(target,h,c,id,future=True)
                name={'edit':'edit_draft','cancel':'terminal','read':'find_draft'}[operation]
                original=getattr(DraftSQL,name);entered=[]
                async def delayed(self,*args):
                    entered.append(True)
                    await asyncio.sleep(5)
                    return await original(self,*args)
                monkeypatch.setattr(DraftSQL,name,delayed)
                base='/api/v1/action-drafts/'+id
                if operation=='edit':result=await c.patch(base,headers=h.headers(c),json=dict(expected_version=1,payload=payload()))
                elif operation=='cancel':result=await c.delete(base,headers=h.headers(c))
                else:result=await c.get(base)
                assert entered
                assert result.status_code==(200 if operation=='read' else 410),result.text
                assert row(target,'SELECT state,version FROM {s}.action_drafts WHERE id=:id',id=UUID(id))==('expired',1)
    run(scenario())


@pytest.mark.parametrize('operation',['create','edit'])
def test_session_expiry_at_draft_write_is_401_without_partial_changes(target,monkeypatch,operation):
    from airport_assistant.infrastructure.database.draft_repository import DraftSQL
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c);existing=await create_draft(h,c,conv)
                owner=await actor(h,c)
                name='create_draft' if operation=='create' else 'edit_draft';original=getattr(DraftSQL,name)
                async def delayed(self,*args):await asyncio.sleep(3);return await original(self,*args)
                monkeypatch.setattr(DraftSQL,name,delayed)
                change(target,"UPDATE {s}.sessions SET expires_at=clock_timestamp()+interval '2 seconds' WHERE id=:id",id=owner.session.id)
                if operation=='create':
                    result=await c.post('/api/v1/action-drafts',headers=h.headers(c),json=dict(conversation_id=conv,action_type='create_case',payload=payload()))
                else:result=await c.patch('/api/v1/action-drafts/'+existing['draft_id'],headers=h.headers(c),json=dict(expected_version=1,payload=payload(summary='Changed')))
                assert result.status_code==401,result.text
                assert row(target,'SELECT count(*),max(version) FROM {s}.action_drafts WHERE conversation_id=:id',id=UUID(conv))==(1,1)
    run(scenario())


def test_restart_preserves_preview_version_and_pending_state(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c);draft=await create_draft(h,c,conv)
                cookies=dict(c.cookies)
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                c.cookies.update(cookies)
                assert (await c.get('/api/v1/action-drafts/'+draft['draft_id'])).json()==draft
    run(scenario())
