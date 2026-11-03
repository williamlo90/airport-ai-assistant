"""BEHAV-01 on real restricted Neon, including cross-connection races."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime,timezone,timedelta
import os
from pathlib import Path
from uuid import UUID,uuid4

import pytest
from sqlalchemy import text
from test_staff_auth import target as auth_target, harness, run, row, change, seed, insert
from test_customer_auth import ControlledSender

from airport_assistant.auth_config import AuthSettings
from airport_assistant.config import Settings
from airport_assistant.database_config import MigrationDatabaseSettings,RuntimeDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.conversation_repository import PostgresConversations
from airport_assistant.modules.conversations.service import ConversationService
from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.api.auth import SESSION_COOKIE
from airport_assistant.main import create_app

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Set RUN_NEON_AUTH_TESTS=1 for real conversation integration.')


@pytest.fixture(scope='module')
def target(auth_target):
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    t=auth_target
    preserved=seed(t)
    with t['engine'].begin() as db:
        session=insert(db,t['schema'],'sessions',tenant_id=t['a'],session_kind='anonymous',
                       token_hash='a'*64,csrf_secret_hash='b'*64,
                       expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    for _ in range(2):
        assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'])=='0005_conversations'
    assert row(t,'SELECT id FROM {s}.principals WHERE id=:id',id=preserved['principal'])[0]==preserved['principal']
    assert row(t,'SELECT token_hash,csrf_secret_hash,revoked_at FROM {s}.sessions WHERE id=:id',id=session)==('a'*64,'b'*64,None)
    return t


@asynccontextmanager
async def conversations(target):
    sender=ControlledSender()
    async with harness(target,sender=sender) as h:
        service=ConversationService(PostgresConversations(h.db,target['schema']))
        h.app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True),
            database_settings=RuntimeDatabaseSettings(DATABASE_URL=target['url']),auth_settings=AuthSettings(SESSION_SECRET=target['key']),
            services=replace(h.app.state.services,conversations=service))
        yield h,sender


async def verified(h,sender,c,email=None):
    await h.boot(c)
    issue=await c.post('/api/v1/auth/challenges',headers=h.headers(c),json=dict(contact=email or 'conversation'+uuid4().hex+'@example.com',purpose='customer_login'))
    assert issue.status_code==202
    challenge=issue.json()['challenge_id']
    result=await c.post('/api/v1/auth/challenges/'+challenge+'/verify',headers=h.headers(c),json=dict(code=sender.messages[challenge].code))
    assert result.status_code==200
    return UUID(result.json()['principal_id'])


async def create(h,c,language='id'):
    result=await c.post('/api/v1/conversations',headers=h.headers(c),json=dict(language=language))
    assert result.status_code==201,result.text
    return result.json()['conversation_id']


async def send(h,c,id,body='Saya butuh bantuan.',client_id=None):
    return await c.post(f'/api/v1/conversations/{id}/messages',headers={key:value for key,value in h.headers(c).items() if value is not None},
                        json=dict(body=body,client_message_id=str(client_id or uuid4())))


def test_ownership_read_sequence_replay_and_conflict(target):
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c,h.client() as other,h.client('b.example.test') as cross:
                await h.boot(c);await h.boot(other);await h.boot(cross)
                id=await create(h,c)
                key=uuid4();first=await send(h,c,id,client_id=key)
                assert first.status_code==201 and first.json()['sequence']==1 and first.json()['processing_state']=='received'
                replay=await send(h,c,id,client_id=key)
                assert replay.status_code==201 and replay.json()==first.json()
                conflict=await send(h,c,id,body='Berbeda',client_id=key)
                assert conflict.status_code==409 and conflict.json()['error']['code']=='MESSAGE_CONFLICT'
                assert (await send(h,c,id)).json()['sequence']==2
                meta=await c.get('/api/v1/conversations/'+id)
                assert meta.status_code==200 and meta.json()['version']==3 and meta.json()['last_sequence']==2
                assert meta.headers['cache-control']=='no-store'
                assert not set(meta.json())&{'anonymous_session_id','customer_principal_id','tenant_id'}
                missing=await other.get('/api/v1/conversations/'+str(uuid4()))
                for stranger in (other,cross):
                    denied=await stranger.get('/api/v1/conversations/'+id)
                    assert denied.status_code==missing.status_code==404
                    assert denied.json()['error']['code']==missing.json()['error']['code']
                    assert (await send(h,stranger,id)).status_code==404
                record=row(target,'SELECT count(*),min(sequence),max(sequence) FROM {s}.messages WHERE conversation_id=:id',id=UUID(id))
                assert tuple(record)==(2,1,2)
                record=row(target,'SELECT customer_principal_id,anonymous_session_id FROM {s}.conversations WHERE id=:id',id=UUID(id))
                assert record[0] is None and record[1] is not None
    run(scenario())


def test_verified_owner_is_principal_not_browser_and_anonymous_not_adopted_yet(target):
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as first,h.client() as second,h.client() as outsider:
                email='shared'+uuid4().hex+'@example.com'
                await h.boot(first);anon=await create(h,first)
                principal=await verified(h,sender,first,email)
                # AUTH-02 adoption deliberately remains a later atomic upgrade task.
                assert (await first.get('/api/v1/conversations/'+anon)).status_code==404
                assert await verified(h,sender,second,email)==principal
                await verified(h,sender,outsider)
                id=await create(h,first,'en')
                assert (await second.get('/api/v1/conversations/'+id)).status_code==200
                assert (await outsider.get('/api/v1/conversations/'+id)).status_code==404
                key=uuid4()
                sent=await send(h,first,id,client_id=key)
                assert (await send(h,second,id,client_id=key)).json()==sent.json()
                record=row(target,'SELECT customer_principal_id,anonymous_session_id FROM {s}.conversations WHERE id=:id',id=UUID(id))
                assert record[0]==principal and record[1] is None
    run(scenario())


def test_parallel_duplicate_and_distinct_messages_have_one_ordered_commit(target):
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=await create(h,c);key=uuid4()
                duplicate=await asyncio.gather(*(send(h,c,id,client_id=key) for _ in range(4)))
                assert all(value.status_code==201 for value in duplicate)
                assert len({value.json()['message_id'] for value in duplicate})==1
                distinct=await asyncio.gather(*(send(h,c,id,body=f'Pesan {i}') for i in range(6)))
                assert all(value.status_code==201 for value in distinct)
                assert sorted(value.json()['sequence'] for value in distinct)==list(range(2,8))
                assert tuple(row(target,'SELECT count(*),count(DISTINCT sequence),max(sequence) FROM {s}.messages WHERE conversation_id=:id',id=UUID(id)))==(7,7,7)
    run(scenario())


def test_language_version_guard_preserves_old_body_and_replay(target):
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=await create(h,c);key=uuid4()
                initial=await send(h,c,id,body='Parkir TX-A-001 Rp100.000 2026-11-10',client_id=key)
                url=f'/api/v1/conversations/{id}/language'
                changed=await c.patch(url,headers=h.headers(c),json=dict(language='en',expected_version=2))
                assert changed.status_code==200 and changed.json()['version']==3 and changed.json()['generation_epoch']==2
                assert (await c.patch(url,headers=h.headers(c),json=dict(language='id',expected_version=2))).status_code==409
                assert (await send(h,c,id,body='Parkir TX-A-001 Rp100.000 2026-11-10',client_id=key)).json()==initial.json()
                assert (await send(h,c,id,body='Next message')).status_code==201
                with target['engine'].connect() as db:
                    records=db.execute(text(f'SELECT body,language FROM "{target["schema"]}".messages WHERE conversation_id=:id ORDER BY sequence'),dict(id=UUID(id))).all()
                    assert [tuple(record) for record in records]==[('Parkir TX-A-001 Rp100.000 2026-11-10','id'),('Next message','en')]
    run(scenario())


@pytest.mark.parametrize('body',['','   ','\t\n','x\x00y','a'*4001])
def test_invalid_message_never_allocates_sequence(target,body):
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=await create(h,c)
                assert (await send(h,c,id,body=body)).status_code==422
                assert (await send(h,c,id)).json()['sequence']==1
    run(scenario())


def test_csrf_public_claims_staff_and_unsupported_language_denied(target):
    staff=seed(target)
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                assert (await c.post('/api/v1/conversations',headers={'Origin':'https://a.example.test'},json=dict(language='id'))).status_code==401
                await h.boot(c)
                assert (await c.post('/api/v1/conversations',json=dict(language='id'))).status_code==403
                assert (await c.post('/api/v1/conversations',headers=h.headers(c),json=dict(language='id',owner_principal_id=str(uuid4())))).status_code==422
                assert (await c.post('/api/v1/conversations?tenant_id=claim',headers=h.headers(c),json=dict(language='id'))).status_code==400
                id=await create(h,c)
                assert (await c.get('/api/v1/conversations/'+id,headers={'X-Session-ID':str(uuid4())})).status_code==400
                assert (await h.login(c,staff['email'])).status_code==200
                assert (await c.get('/api/v1/conversations/'+id)).status_code==403
                assert (await c.post('/api/v1/conversations',headers=h.headers(c),json=dict(language='id'))).status_code==403
    run(scenario())


def test_revoked_session_stale_identity_and_direct_rls_fail_closed(target):
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c,h.client() as other:
                await h.boot(c);await h.boot(other);id=await create(h,c)
                actor=await h.app.state.services.access.resolve(target['a'],c.cookies.get(SESSION_COOKIE))
                stranger=await h.app.state.services.access.resolve(target['a'],other.cookies.get(SESSION_COOKIE))
                async with h.db.transaction(stranger.database_context) as db:
                    assert not await (await db.execute(f'SELECT id FROM "{target["schema"]}".conversations WHERE id=%s',(UUID(id),))).fetchall()
                assert (await c.post('/api/v1/auth/logout',headers=h.headers(c))).status_code==204
                from airport_assistant.modules.identity.auth import AuthError
                with pytest.raises(AuthError) as exc:
                    await h.app.state.services.conversations.append(actor,UUID(id),uuid4(),'Stale request')
                assert exc.value.status==401
                async with h.db.transaction(actor.database_context) as db:
                    assert not await (await db.execute(f'SELECT id FROM "{target["schema"]}".conversations')).fetchall()
                async with h.db.transaction() as db:
                    assert (await (await db.execute("SELECT current_setting('app.session_id',true)")).fetchone())[0]==''
    run(scenario())


def test_failed_insert_rolls_back_counter_and_version(target,monkeypatch):
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=await create(h,c)
                first=await send(h,c,id)
                with monkeypatch.context() as patch:
                    patch.setattr('airport_assistant.modules.conversations.service.uuid4',lambda:UUID(first.json()['message_id']))
                    failed=await send(h,c,id,body='Will fail duplicate PK')
                    assert failed.status_code==500
                    assert 'duplicate' not in failed.text.lower()
                assert (await send(h,c,id)).json()['sequence']==2
                assert (await c.get('/api/v1/conversations/'+id)).json()['version']==3
    run(scenario())


def test_logout_waits_for_inflight_message_transaction(target,monkeypatch):
    from airport_assistant.infrastructure.database.conversation_repository import ConversationSQL
    original=ConversationSQL.append
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        async def paused(self,*args):
            entered.set()
            await release.wait()
            return await original(self,*args)
        monkeypatch.setattr(ConversationSQL,'append',paused)
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=await create(h,c)
                sending=asyncio.create_task(send(h,c,id))
                await asyncio.wait_for(entered.wait(),5)
                logout=asyncio.create_task(c.post('/api/v1/auth/logout',headers=h.headers(c)))
                try:
                    with pytest.raises(TimeoutError):await asyncio.wait_for(asyncio.shield(logout),.2)
                finally:
                    release.set()
                assert (await sending).status_code==201
                assert (await logout).status_code==204
                assert (await send(h,c,id)).status_code==401
    run(scenario())


def test_restart_retains_messages_and_retry_identity(target):
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=await create(h,c);key=uuid4()
                first=await send(h,c,id,client_id=key)
                cookies=dict(c.cookies)
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                c.cookies.update(cookies)
                assert (await c.get('/api/v1/conversations/'+id)).json()['last_sequence']==1
                assert (await send(h,c,id,client_id=key)).json()==first.json()
    run(scenario())


def test_parallel_conflicting_bodies_and_client_key_scope(target):
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=await create(h,c);other=await create(h,c);key=uuid4()
                results=await asyncio.gather(send(h,c,id,body='One',client_id=key),send(h,c,id,body='Two',client_id=key))
                assert sorted(result.status_code for result in results)==[201,409]
                assert (await send(h,c,other,client_id=key)).json()['sequence']==1
    run(scenario())


def test_tenant_language_allowlist_is_checked(target):
    change(target,'UPDATE {s}.tenants SET supported_languages=ARRAY[\'en\'] WHERE id=:id',id=target['b'])
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client('b.example.test') as c:
                await h.boot(c)
                assert (await c.post('/api/v1/conversations',headers=h.headers(c),json=dict(language='id'))).status_code==422
                await create(h,c,'en')
    try:run(scenario())
    finally:change(target,'UPDATE {s}.tenants SET supported_languages=ARRAY[\'id\',\'en\'] WHERE id=:id',id=target['b'])


def test_runtime_cannot_rewrite_ownership_messages_or_source_classification(target):
    from airport_assistant.infrastructure.database.connections import DatabaseError
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=UUID(await create(h,c));await send(h,c,id)
                actor=await h.app.state.services.access.resolve(target['a'],c.cookies.get(SESSION_COOKIE))
                s='"'+target['schema']+'"'
                for statement,params,sqlstate in [
                    (f'UPDATE {s}.conversations SET anonymous_session_id=%s WHERE id=%s',(uuid4(),id),'42501'),
                    (f'DELETE FROM {s}.conversations WHERE id=%s',(id,),'42501'),
                    (f'UPDATE {s}.messages SET body=%s WHERE conversation_id=%s',('Rewritten',id),'42501'),
                    (f'INSERT INTO {s}.messages(tenant_id,conversation_id,author_session_id,author_type,source_kind,body,language,client_message_id,sequence) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                     (target['a'],id,actor.session.id,'customer','source_fact','Fake source fact','id',uuid4(),99),'23514'),
                    (f'INSERT INTO {s}.messages(tenant_id,conversation_id,author_session_id,author_type,body,language,client_message_id,sequence) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                     (target['a'],id,uuid4(),'customer','Forged author','id',uuid4(),99),'42501'),
                ]:
                    with pytest.raises(DatabaseError) as error:
                        async with h.db.transaction(actor.database_context) as db:
                            await db.execute(statement,params)
                    assert error.value.sqlstate==sqlstate
                async with h.db.transaction() as db:
                    assert not await (await db.execute(f'SELECT id FROM {s}.messages')).fetchall()
                assert (await c.get('/api/v1/conversations/'+str(id))).json()['last_sequence']==1
    run(scenario())


@pytest.mark.parametrize('stage',['create','read','counter','insert','replay','language','after_language'])
def test_natural_expiry_during_operation_is_401_and_never_commits_partial_write(target,monkeypatch,stage):
    from airport_assistant.infrastructure.database.conversation_repository import ConversationSQL
    async def scenario():
        async with conversations(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=await create(h,c);key=uuid4()
                if stage=='replay':assert (await send(h,c,id,client_id=key)).status_code==201
                actor=await h.app.state.services.access.resolve(target['a'],c.cookies.get(SESSION_COOKIE))
                if stage=='insert':
                    from psycopg import AsyncConnection,sql
                    original=AsyncConnection.execute
                    async def execute(self,query,*args,**kwargs):
                        if isinstance(query,sql.Composable) and '.messages(' in query.as_string(self) and query.as_string(self).startswith('INSERT INTO'):
                            await asyncio.sleep(3)
                        return await original(self,query,*args,**kwargs)
                    monkeypatch.setattr(AsyncConnection,'execute',execute)
                else:
                    name={'create':'create','read':'find','counter':'duplicate','replay':'duplicate',
                          'language':'change_language','after_language':'change_language'}[stage]
                    original=getattr(ConversationSQL,name)
                    async def delayed(self,*args,**kwargs):
                        if stage=='after_language':
                            value=await original(self,*args,**kwargs)
                            await asyncio.sleep(3)
                            return value
                        await asyncio.sleep(3)
                        return await original(self,*args,**kwargs)
                    monkeypatch.setattr(ConversationSQL,name,delayed)
                change(target,"UPDATE {s}.sessions SET expires_at=clock_timestamp()+interval '2 seconds' WHERE id=:id",id=actor.session.id)
                if stage=='create':result=await c.post('/api/v1/conversations',headers=h.headers(c),json=dict(language='id'))
                elif stage=='read':result=await c.get('/api/v1/conversations/'+id)
                elif stage in {'language','after_language'}:
                    result=await c.patch(f'/api/v1/conversations/{id}/language',headers=h.headers(c),json=dict(language='en',expected_version=1))
                else:result=await send(h,c,id,client_id=key)
                assert result.status_code==401,result.text
                assert result.json()['error']['code']=='AUTH_REQUIRED'
                expected=1 if stage=='replay' else 0
                assert row(target,'SELECT count(*) FROM {s}.messages WHERE conversation_id=:id',id=UUID(id))[0]==expected
                assert tuple(row(target,'SELECT last_sequence,version,generation_epoch,language FROM {s}.conversations WHERE id=:id',id=UUID(id)))==(expected,expected+1,1,'id')
                assert row(target,'SELECT count(*) FROM {s}.conversations WHERE anonymous_session_id=:id',id=actor.session.id)[0]==1
    run(scenario())
