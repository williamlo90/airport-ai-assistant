"""AUTH-02: real Neon browser binding, provenance, rollback and concurrency."""

import asyncio
import os
import time
from pathlib import Path
from uuid import UUID,uuid4

import pytest
from sqlalchemy import text

from test_staff_auth import target as auth_target,run,row,change
from test_conversations import target as conversation_target,create as create_conversation,send,verified
from test_drafts import target as draft_target,drafts,create_draft,actor,deadline,payload
from airport_assistant.database_config import MigrationDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.api.auth import SESSION_COOKIE,CSRF_COOKIE

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Set RUN_NEON_AUTH_TESTS=1 for real anonymous adoption integration.')


@pytest.fixture(scope='module')
def target(draft_target):
    t=draft_target
    before=row(t,'SELECT count(*) FROM {s}.messages')[0]
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):
        assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'])=='0007_anonymous_adoption'
    assert row(t,'SELECT count(*),count(origin_session_id) FROM {s}.messages')==(before,0)
    return t


async def issue(h,sender,c,email=None):
    response=await c.post('/api/v1/auth/challenges',headers=h.headers(c),json=dict(contact=email or 'adopt'+uuid4().hex+'@example.com',purpose='customer_login'))
    assert response.status_code==202
    id=response.json()['challenge_id']
    return id,sender.messages[id].code


async def verify(h,c,id,code,headers=None):
    return await c.post('/api/v1/auth/challenges/'+id+'/verify',headers=headers or h.headers(c),json=dict(code=code))


def test_same_browser_adopts_all_history_and_preserves_retry_identity(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c,h.client() as other:
                await h.boot(c);await h.boot(other)
                old=await actor(h,c);old_token=c.cookies.get(SESSION_COOKIE);old_csrf=c.cookies.get(CSRF_COOKIE)
                conv=await create_conversation(h,c);second=await create_conversation(h,c)
                key=uuid4();message=await send(h,c,conv,body='PK-A-001 Rp100.000 2026-11-10',client_id=key)
                await send(h,c,second,body='Second conversation')
                preview=await create_draft(h,c,conv)
                stranger=await create_conversation(h,other);other_preview=await create_draft(h,other,stranger)
                challenge,code=await issue(h,sender,c)
                response=await verify(h,c,challenge,code)
                assert response.status_code==200,response.text
                principal=UUID(response.json()['principal_id']);new=await actor(h,c)
                assert new.session.id!=old.session.id and new.session.principal_id==principal
                assert c.cookies.get(SESSION_COOKIE)!=old_token and c.cookies.get(CSRF_COOKIE)!=old_csrf
                for id,version in ((conv,3),(second,3)):
                    result=await c.get('/api/v1/conversations/'+id)
                    assert result.status_code==200 and result.json()['version']==version and result.json()['generation_epoch']==2
                    record=row(target,'SELECT customer_principal_id,anonymous_session_id,adopted_from_session_id,adopted_at IS NOT NULL FROM {s}.conversations WHERE id=:id',id=UUID(id))
                    assert record==(principal,None,old.session.id,True)
                record=row(target,'SELECT id,sequence,body,language,author_principal_id,author_session_id,origin_session_id FROM {s}.messages WHERE id=:id',id=UUID(message.json()['message_id']))
                assert record==(UUID(message.json()['message_id']),1,'PK-A-001 Rp100.000 2026-11-10','id',principal,None,old.session.id)
                retried=await send(h,c,conv,body='PK-A-001 Rp100.000 2026-11-10',client_id=key)
                assert retried.status_code==201 and retried.json()==message.json()
                assert row(target,'SELECT count(*) FROM {s}.messages WHERE conversation_id=:id',id=UUID(conv))[0]==1
                assert (await c.get('/api/v1/action-drafts/'+preview['draft_id'])).status_code==404
                assert row(target,'SELECT state,version,payload_hash,created_session_id FROM {s}.action_drafts WHERE id=:id',id=UUID(preview['draft_id']))==('cancelled',1,preview['payload_hash'],old.session.id)
                assert (await c.get('/api/v1/conversations/'+stranger)).status_code==404
                assert (await other.get('/api/v1/action-drafts/'+other_preview['draft_id'])).json()['state']=='pending'
                assert row(target,'SELECT revoked_at IS NOT NULL,replaced_by_session_id FROM {s}.sessions WHERE id=:id',id=old.session.id)==(True,new.session.id)
                assert row(target,'SELECT consumed_at IS NOT NULL FROM {s}.verification_challenges WHERE id=:id',id=UUID(challenge))[0]
                assert (await c.get('/api/v1/me',headers={'Cookie':SESSION_COOKIE+'='+old_token})).status_code==401
                fresh=await create_draft(h,c,conv)
                assert fresh['state']=='pending' and fresh['draft_id']!=preview['draft_id']
                async with h.db.transaction(new.database_context) as db:
                    values=await(await db.execute("SELECT nullif(current_setting('app.adoption_session',true),''),nullif(current_setting('app.adoption_principal',true),'')")).fetchone()
                    assert values==(None,None)
    run(scenario())


def test_stolen_challenge_code_and_cross_tenant_cannot_adopt_history(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c,h.client() as thief,h.client('b.example.test') as cross:
                await h.boot(c);await h.boot(thief);await h.boot(cross)
                old=await actor(h,c);conv=await create_conversation(h,c);preview=await create_draft(h,c,conv)
                challenge,code=await issue(h,sender,c)
                assert (await verify(h,thief,challenge,code)).status_code==422
                assert (await verify(h,cross,challenge,code)).status_code==422
                assert row(target,'SELECT anonymous_session_id,customer_principal_id FROM {s}.conversations WHERE id=:id',id=UUID(conv))==(old.session.id,None)
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=UUID(preview['draft_id']))[0]=='pending'
                wrong='00000000' if code!='00000000' else '11111111'
                assert (await verify(h,c,challenge,wrong)).status_code==422
                assert (await c.get('/api/v1/conversations/'+conv)).status_code==200
                assert (await verify(h,c,challenge,code)).status_code==200
                assert (await thief.get('/api/v1/conversations/'+conv)).status_code==404
    run(scenario())


def test_existing_account_gets_only_origin_history_and_no_new_source_grants(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as account,h.client() as origin,h.client() as bystander:
                email='existing'+uuid4().hex+'@example.com'
                principal=await verified(h,sender,account,email)
                established=await create_conversation(h,account)
                change(target,'UPDATE {s}.principals SET source_subject_ref=:ref WHERE id=:id',ref='server-recorded-subject',id=principal)
                await h.boot(origin);await h.boot(bystander)
                incoming=await create_conversation(h,origin);hidden=await create_conversation(h,bystander)
                key=uuid4();message=await send(h,origin,incoming,client_id=key)
                challenge,code=await issue(h,sender,origin,email)
                result=await verify(h,origin,challenge,code)
                assert result.status_code==200 and UUID(result.json()['principal_id'])==principal
                assert (await account.get('/api/v1/conversations/'+incoming)).status_code==200
                assert (await origin.get('/api/v1/conversations/'+established)).status_code==200
                assert (await account.get('/api/v1/conversations/'+hidden)).status_code==404
                assert (await send(h,account,incoming,client_id=key)).json()==message.json()
                assert row(target,'SELECT source_subject_ref FROM {s}.principals WHERE id=:id',id=principal)[0]=='server-recorded-subject'
                assert row(target,'SELECT version,generation_epoch,adopted_at FROM {s}.conversations WHERE id=:id',id=UUID(established))==(1,1,None)
    run(scenario())


def test_all_pending_drafts_invalidated_without_rewriting_terminal_previews(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);conv=await create_conversation(h,c)
                pending=await create_draft(h,c,conv);cancelled=await create_draft(h,c,conv);expired=await create_draft(h,c,conv)
                assert (await c.delete('/api/v1/action-drafts/'+cancelled['draft_id'],headers=h.headers(c))).status_code==200
                await deadline(target,h,c,expired['draft_id'])
                challenge,code=await issue(h,sender,c)
                assert (await verify(h,c,challenge,code)).status_code==200
                for preview,state in ((pending,'cancelled'),(cancelled,'cancelled'),(expired,'expired')):
                    assert row(target,'SELECT state,version FROM {s}.action_drafts WHERE id=:id',id=UUID(preview['draft_id']))==(state,1)
                    assert (await c.get('/api/v1/actions/'+preview['draft_id'])).status_code==404
    run(scenario())


def test_verification_replay_race_has_one_session_and_one_adoption(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);old=await actor(h,c);conv=await create_conversation(h,c)
                challenge,code=await issue(h,sender,c)
                headers={**h.headers(c),'Cookie':SESSION_COOKIE+'='+c.cookies.get(SESSION_COOKIE)+'; '+CSRF_COOKIE+'='+c.cookies.get(CSRF_COOKIE)}
                results=await asyncio.gather(verify(h,c,challenge,code,headers),verify(h,c,challenge,code,headers))
                assert sorted(result.status_code for result in results)==[200,401]
                assert row(target,'SELECT version,generation_epoch FROM {s}.conversations WHERE id=:id',id=UUID(conv))==(2,2)
                replacement=row(target,'SELECT replaced_by_session_id FROM {s}.sessions WHERE id=:id',id=old.session.id)[0]
                assert replacement is not None
                assert row(target,'SELECT count(*) FROM {s}.sessions WHERE id=:id',id=replacement)[0]==1
                assert (await verify(h,c,challenge,code,headers)).status_code==401
    run(scenario())


def install_fault(target,body,phase='conversation'):
    s='"'+target['schema']+'"'
    table,column=('action_drafts','state') if phase=='draft' else ('conversations','customer_principal_id')
    with target['engine'].begin() as db:
        db.exec_driver_sql(f"CREATE FUNCTION {s}.auth02_fault() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN {body} RETURN NEW;END $$")
        db.exec_driver_sql(f'CREATE TRIGGER auth02_fault BEFORE UPDATE OF {column} ON {s}.{table} FOR EACH ROW EXECUTE FUNCTION {s}.auth02_fault()')


def remove_fault(target):
    s='"'+target['schema']+'"'
    with target['engine'].begin() as db:
        db.exec_driver_sql(f'DROP TRIGGER IF EXISTS auth02_fault ON {s}.conversations')
        db.exec_driver_sql(f'DROP TRIGGER IF EXISTS auth02_fault ON {s}.action_drafts')
        db.exec_driver_sql(f'DROP FUNCTION IF EXISTS {s}.auth02_fault()')


def test_failure_mid_adoption_rolls_back_identity_history_drafts_and_consumption(target):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);old=await actor(h,c);conv=await create_conversation(h,c);preview=await create_draft(h,c,conv)
                message=await send(h,c,conv,body='Must survive rollback')
                challenge,code=await issue(h,sender,c)
                install_fault(target,"RAISE EXCEPTION 'Controlled adoption failure' USING ERRCODE='23514';",phase='draft')
                try:assert (await verify(h,c,challenge,code)).status_code==500
                finally:remove_fault(target)
                assert row(target,'SELECT customer_principal_id,anonymous_session_id,adopted_from_session_id FROM {s}.conversations WHERE id=:id',id=UUID(conv))==(None,old.session.id,None)
                assert row(target,'SELECT revoked_at,replaced_by_session_id FROM {s}.sessions WHERE id=:id',id=old.session.id)==(None,None)
                assert row(target,'SELECT consumed_at FROM {s}.verification_challenges WHERE id=:id',id=UUID(challenge))[0] is None
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=UUID(preview['draft_id']))[0]=='pending'
                assert row(target,'SELECT author_principal_id,author_session_id,origin_session_id,body FROM {s}.messages WHERE id=:id',id=UUID(message.json()['message_id']))==(None,old.session.id,None,'Must survive rollback')
                assert (await c.get('/api/v1/conversations/'+conv)).status_code==200
                assert (await verify(h,c,challenge,code)).status_code==200
    run(scenario())


@pytest.mark.parametrize('scope,status',[('session',401),('challenge',422)])
def test_late_expiry_rolls_back_all_adoption_writes(target,scope,status):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);old=await actor(h,c);conv=await create_conversation(h,c);preview=await create_draft(h,c,conv)
                message=await send(h,c,conv)
                challenge,code=await issue(h,sender,c)
                table='sessions' if scope=='session' else 'verification_challenges';id=old.session.id if scope=='session' else UUID(challenge)
                change(target,"UPDATE {s}."+table+" SET expires_at=clock_timestamp()+interval '4 seconds' WHERE id=:id",id=id)
                install_fault(target,'PERFORM pg_sleep(5);')
                start=time.monotonic()
                try:result=await verify(h,c,challenge,code)
                finally:remove_fault(target)
                assert time.monotonic()-start>=4.5
                assert result.status_code==status,result.text
                assert row(target,'SELECT customer_principal_id,anonymous_session_id FROM {s}.conversations WHERE id=:id',id=UUID(conv))==(None,old.session.id)
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=UUID(preview['draft_id']))[0]=='pending'
                assert row(target,'SELECT consumed_at FROM {s}.verification_challenges WHERE id=:id',id=UUID(challenge))[0] is None
                assert row(target,'SELECT attempts FROM {s}.verification_challenges WHERE id=:id',id=UUID(challenge))[0]==1
                assert row(target,'SELECT author_principal_id,author_session_id,origin_session_id FROM {s}.messages WHERE id=:id',id=UUID(message.json()['message_id']))==(None,old.session.id,None)
                assert row(target,'SELECT revoked_at,replaced_by_session_id FROM {s}.sessions WHERE id=:id',id=old.session.id)==(None,None)
    run(scenario())


def test_inflight_append_finishes_before_upgrade_and_old_snapshot_is_rejected(target,monkeypatch):
    from airport_assistant.infrastructure.database.conversation_repository import ConversationSQL
    from airport_assistant.modules.identity.auth import AuthError
    original=ConversationSQL.append
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        async def paused(self,*args):
            entered.set();await release.wait()
            return await original(self,*args)
        monkeypatch.setattr(ConversationSQL,'append',paused)
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);old=await actor(h,c);conv=await create_conversation(h,c);preview=await create_draft(h,c,conv)
                challenge,code=await issue(h,sender,c)
                sending=asyncio.create_task(send(h,c,conv))
                await asyncio.wait_for(entered.wait(),5)
                upgrading=asyncio.create_task(verify(h,c,challenge,code))
                try:
                    with pytest.raises(TimeoutError):await asyncio.wait_for(asyncio.shield(upgrading),.2)
                finally:release.set()
                sent=await sending;upgraded=await upgrading
                assert sent.status_code==201 and upgraded.status_code==200
                principal=UUID(upgraded.json()['principal_id'])
                assert row(target,'SELECT author_principal_id,origin_session_id FROM {s}.messages WHERE id=:id',id=UUID(sent.json()['message_id']))==(principal,old.session.id)
                with pytest.raises(AuthError) as exc:await h.app.state.services.conversations.append(old,UUID(conv),uuid4(),'Stale append')
                assert exc.value.status==401
                with pytest.raises(AuthError):await h.app.state.services.drafts.cancel(old,UUID(preview['draft_id']))
    run(scenario())


def test_runtime_cannot_spoof_adoption_scope_or_write_provenance(target):
    from airport_assistant.infrastructure.database.connections import DatabaseError
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c,h.client() as thief:
                await h.boot(c);await h.boot(thief)
                old=await actor(h,c);stranger=await actor(h,thief);conv=UUID(await create_conversation(h,c))
                async with h.db.transaction(stranger.database_context) as db:
                    await db.execute("SELECT set_config('app.adoption_tenant',%s,true),set_config('app.adoption_session',%s,true),set_config('app.adoption_principal',%s,true)",
                        (str(target['a']),str(old.session.id),str(uuid4())))
                    assert not await(await db.execute(f'SELECT id FROM "{target["schema"]}".conversations WHERE id=%s',(conv,))).fetchall()
                async with h.db.transaction(old.database_context) as db:
                    privileges=await(await db.execute("SELECT has_column_privilege(%s,%s,'body','SELECT'),has_column_privilege(current_user,%s,'adopted_from_session_id','UPDATE'),has_column_privilege(current_user,%s,'origin_session_id','INSERT')",
                        (target['owner'],target['schema']+'.messages',target['schema']+'.conversations',target['schema']+'.messages'))).fetchone()
                    assert privileges==(False,False,False)
                with pytest.raises(DatabaseError) as exc:
                    async with h.db.transaction(old.database_context) as db:
                        await db.execute(f'UPDATE "{target["schema"]}".conversations SET customer_principal_id=%s WHERE id=%s',(uuid4(),conv))
                assert exc.value.sqlstate=='42501'
                with target['engine'].connect() as db:
                    function=db.execute(text("SELECT p.prosecdef,p.proconfig,r.rolname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname=:s AND p.proname='auth_verify'"),dict(s=target['schema'])).one()
                    assert function[0] and function[1]==['search_path=pg_catalog'] and function[2]==target['owner']
    run(scenario())


@pytest.mark.parametrize('reason',['expired_challenge','consumed','attempt_limit'])
def test_invalid_challenge_never_changes_history_or_drafts(target,reason):
    async def scenario():
        async with drafts(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);old=await actor(h,c);conv=await create_conversation(h,c);preview=await create_draft(h,c,conv)
                challenge,code=await issue(h,sender,c)
                update={'expired_challenge':"expires_at=clock_timestamp()-interval '1 second'",'consumed':'consumed_at=clock_timestamp()',
                        'attempt_limit':'attempts=max_attempts'}[reason]
                change(target,'UPDATE {s}.verification_challenges SET '+update+' WHERE id=:id',id=UUID(challenge))
                assert (await verify(h,c,challenge,code)).status_code==422
                assert row(target,'SELECT customer_principal_id,anonymous_session_id FROM {s}.conversations WHERE id=:id',id=UUID(conv))==(None,old.session.id)
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=UUID(preview['draft_id']))[0]=='pending'
                assert row(target,'SELECT revoked_at,replaced_by_session_id FROM {s}.sessions WHERE id=:id',id=old.session.id)==(None,None)
    run(scenario())
