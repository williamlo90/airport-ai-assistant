"""CHAT-01 persisted polling, consent and isolation on isolated Neon roles."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
import os
from uuid import UUID,uuid4
import pytest
from test_staff_auth import run,row,change
from test_attachments import target as files_target,lifecycle_target,commit_target,adoption_target,draft_target,conversation_target,auth_target,files,upload
from test_case_commit import prepared,confirm
from test_drafts import create_draft,payload
from test_conversations import create as create_conversation,verified,send
from test_case_lifecycle import operator,login
from test_anonymous_adoption import issue,verify
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.conversation_events import PostgresConversationEvents
from airport_assistant.modules.conversations.events import ConversationEvents,EventCursor

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in CHAT-01 isolated Neon.')


@pytest.fixture(scope='module')
def target(files_target):
    t=files_target
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0011_conversation_events')=='0011_conversation_events'
    assert row(t,'SELECT count(*) FROM {s}.conversation_events WHERE event_type=\'message_received\'')[0]>=1
    return t


@asynccontextmanager
async def streams(target):
    async with files(target) as (h,sender,scan):
        h.app.state.services=replace(h.app.state.services,conversation_events=ConversationEvents(PostgresConversationEvents(h.db,target['schema']),EventCursor(target['key'].encode())))
        h.app.include_router(__import__('airport_assistant.api.conversation_events',fromlist=['router']).router)
        yield h,sender


async def poll(c,id,**params):return await c.get('/api/v1/conversations/'+str(id)+'/events',params=params)


def test_durable_message_language_events_and_bound_pagination(target):
    async def scenario():
        async with streams(target) as (h,sender):
            async with h.client() as c,h.client() as other,h.client('b.example.test') as tenant:
                await h.boot(c);id=await create_conversation(h,c);key=uuid4()
                first=await send(h,c,id,client_id=key,body='Original user text');assert first.status_code==201,first.text
                assert (await send(h,c,id,client_id=key,body='Original user text')).json()==first.json()
                assert (await c.patch('/api/v1/conversations/'+id+'/language',headers=h.headers(c),json=dict(expected_version=2,language='en'))).status_code==200
                assert (await send(h,c,id,body='Second original')).status_code==201
                page=await poll(c,id,limit=1);assert page.status_code==200,page.text
                data=page.json();assert data['has_more'] and data['items'][0]['payload']['body']=='Original user text'
                token=data['next_cursor'];second=await poll(c,id,limit=1,after=token)
                assert second.json()['items'][0]['event_type']=='language_changed'
                assert (await poll(c,id,limit=1,after=token)).json()['items']==second.json()['items']
                last=await poll(c,id,limit=1,after=second.json()['next_cursor'])
                assert last.json()['items'][0]['payload']['body']=='Second original'
                assert not (await poll(c,id,limit=1,after=last.json()['next_cursor'])).json()['items']
                assert (await poll(c,id,limit=2,after=token)).status_code==400
                assert (await poll(c,id,limit=1,after=token[:-2]+'xx')).status_code==400
                for stranger in (other,tenant):
                    await h.boot(stranger);assert (await poll(stranger,id,limit=1,after=token)).status_code==404
                assert row(target,'SELECT count(*) FROM {s}.conversation_events WHERE conversation_id=:id',id=UUID(id))[0]==3
    run(scenario())


def test_new_handoff_one_confirmation_staff_scope_and_adoption(target):
    async def scenario():
        async with streams(target) as (h,sender):
            async with h.client() as c,h.client() as staff,h.client() as admin:
                draft=await prepared(h,sender,c,anonymous=True);id=draft['conversation_id']
                assert (await send(h,c,id,body='Please help with this situation.')).status_code==201
                before=(await poll(c,id)).json();old_cursor=before['next_cursor']
                key=str(uuid4());results=await asyncio.gather(confirm(h,c,draft,key),confirm(h,c,draft,key))
                assert [r.status_code for r in results]==[201,201],[r.text for r in results]
                assert results[0].json()==results[1].json();case=results[0].json()['case_id']
                after=await poll(c,id,after=old_cursor);assert after.status_code==200,after.text
                assert after.json()['mode']=='waiting_staff' and len(after.json()['items'])==1
                assert after.json()['items'][0]['event_type']=='handoff_requested'
                assert row(target,'SELECT count(*) FROM {s}.cases WHERE origin_draft_id=:id',id=UUID(draft['draft_id']))[0]==1
                for client,roles,expected in ((staff,('cs',),200),(admin,('admin',),404)):
                    person=operator(target,case,roles);await login(h,client,person)
                    response=await poll(client,id);assert response.status_code==expected,response.text
                    if expected==200:
                        assert response.json()['items'][0]['payload']['body']=='Please help with this situation.'
                        change(target,'UPDATE {s}.staff_memberships SET active=false WHERE id=:id',id=person['case_membership'])
                        assert (await poll(client,id)).status_code==404
                challenge,code=await issue(h,sender,c);response=await verify(h,c,challenge,code);assert response.status_code==200,response.text
                assert (await poll(c,id)).json()['mode']=='waiting_staff'
                assert (await poll(c,id,after=old_cursor)).status_code==400
    run(scenario())


def test_existing_case_handoff_reuses_case_and_is_authorized(target):
    async def scenario():
        async with streams(target) as (h,sender):
            async with h.client() as c,h.client() as outsider:
                first=await prepared(h,sender,c);committed=await confirm(h,c,first);assert committed.status_code==201
                case=committed.json()['case_id'];id=await create_conversation(h,c)
                report=payload(contact_email=None)
                request=dict(conversation_id=id,action_type='request_handoff',case_id=case,payload=report)
                created=await c.post('/api/v1/action-drafts',headers=h.headers(c),json=request);assert created.status_code==201,created.text
                draft=created.json();assert case in draft['case_id'] and 'CASE-'+UUID(case).hex in draft['summary']
                assert (await upload(h,c,draft)).status_code==422
                result=await confirm(h,c,draft);assert result.status_code==201,result.text
                assert result.json()['case_id']==case and result.json()['message_id']==committed.json()['message_id']
                receipt=await c.get('/api/v1/actions/'+draft['draft_id']);assert receipt.status_code==200 and receipt.json()['case_id']==case
                assert row(target,'SELECT count(*) FROM {s}.cases WHERE id=:id',id=UUID(case))[0]==1
                assert row(target,'SELECT count(*) FROM {s}.outbox_jobs WHERE case_id=:id',id=UUID(case))[0]==1
                await verified(h,sender,outsider);otherid=await create_conversation(h,outsider)
                response=await outsider.post('/api/v1/action-drafts',headers=h.headers(outsider),json={**request,'conversation_id':otherid})
                assert response.status_code==404,response.text
    run(scenario())


def test_concurrent_distinct_drafts_do_not_create_two_handoffs(target):
    async def scenario():
        async with streams(target) as (h,sender):
            async with h.client() as c:
                first=await prepared(h,sender,c,anonymous=True)
                second=await create_draft(h,c,first['conversation_id'],first['payload'],action='request_handoff')
                results=await asyncio.gather(confirm(h,c,first),confirm(h,c,second))
                assert sorted(r.status_code for r in results)==[201,409],[r.text for r in results]
                assert row(target,"SELECT count(*) FROM {s}.conversation_events WHERE conversation_id=:id AND event_type='handoff_requested'",id=UUID(first['conversation_id']))[0]==1
                assert row(target,'SELECT count(*) FROM {s}.cases WHERE origin_conversation_id=:id',id=UUID(first['conversation_id']))[0]==1
    run(scenario())


def test_linked_case_lifecycle_is_gated_until_atomic_chat_transitions(target):
    from test_case_lifecycle import assign,status
    from test_drafts import actor
    from airport_assistant.infrastructure.database.connections import DatabaseError
    async def scenario():
        async with streams(target) as (h,sender):
            async with h.client() as c,h.client() as staff:
                draft=await prepared(h,sender,c,anonymous=True);result=await confirm(h,c,draft);assert result.status_code==201,result.text
                id=result.json()['case_id'];person=operator(target,id);await login(h,staff,person)
                assigned=await assign(h,staff,id,1,person['case_membership'])
                assert assigned.status_code==422 and assigned.json()['error']['code']=='CHAT_VERSION_REQUIRED'
                closed=await status(h,staff,id,1,'closed',close_reason='resolved',resolution='Manual close attempt')
                assert closed.status_code==422 and closed.json()['error']['code']=='CHAT_VERSION_REQUIRED'
                identity=await actor(h,staff)
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction(identity.database_context) as conn:
                        from psycopg import sql
                        await conn.execute(sql.SQL("UPDATE {}.cases SET status='closed',next_actor='none',close_reason='resolved',resolution='done',closed_at=clock_timestamp(),version=version+1 WHERE tenant_id=%s AND id=%s").format(sql.Identifier(target['schema'])),(target['a'],UUID(id)))
                assert error.value.sqlstate=='23514'
                assert row(target,'SELECT status,version FROM {s}.cases WHERE id=:id',id=UUID(id))==('new',1)
                assert (await poll(c,draft['conversation_id'])).json()['mode']=='waiting_staff'
    run(scenario())


def test_message_and_event_roll_back_together_and_resume_after_service_restart(target,monkeypatch):
    from airport_assistant.infrastructure.database.conversation_repository import ConversationSQL
    from test_drafts import actor
    original=ConversationSQL.append
    async def fault(self,*args):
        await original(self,*args)
        raise RuntimeError('Injected after event write')
    async def scenario():
        async with streams(target) as (h,sender):
            async with h.client() as c:
                await h.boot(c);id=await create_conversation(h,c)
                monkeypatch.setattr(ConversationSQL,'append',fault)
                result=await send(h,c,id);assert result.status_code==500
                assert (await poll(c,id)).json()['items']==[]
                assert row(target,'SELECT last_sequence,last_event_sequence FROM {s}.conversations WHERE id=:id',id=UUID(id))==(0,0)
                monkeypatch.setattr(ConversationSQL,'append',original)
                key=uuid4();result=await send(h,c,id,client_id=key);assert result.status_code==201,result.text
                saved=(await poll(c,id)).json()['items']
                restarted=ConversationEvents(PostgresConversationEvents(h.db,target['schema']),EventCursor(target['key'].encode()))
                assert (await restarted.poll(await actor(h,c),UUID(id)))['items'][0]['id']==UUID(saved[0]['id'])
                assert (await send(h,c,id,client_id=key)).status_code==201
                assert (await poll(c,id)).json()['items']==saved
    run(scenario())
