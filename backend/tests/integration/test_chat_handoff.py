"""CHAT-02 real locks and atomic graphs on isolated Neon, no real email/AI."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
import os
from uuid import UUID,uuid4
from unittest.mock import AsyncMock
import pytest
from test_staff_auth import run,row,change
from test_conversation_events import target as events_target,files_target,lifecycle_target,commit_target,adoption_target,draft_target,conversation_target,auth_target,streams as event_streams,poll
from test_case_commit import prepared,confirm
from test_case_lifecycle import operator,login,status
from test_conversations import send
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle,LifecycleSQL
from airport_assistant.modules.conversations.handoff import ChatHandoff
from airport_assistant.api.chat_handoff import router

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in CHAT-02 isolated Neon locks.')


@pytest.fixture(scope='module')
def target(events_target):
    t=events_target
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0012_chat_handoff')=='0012_chat_handoff'
    return t


@asynccontextmanager
async def chats(target):
    async with event_streams(target) as (h,sender):
        h.app.state.services=replace(h.app.state.services,chat_handoff=ChatHandoff(PostgresCaseLifecycle(h.db,target['schema'])))
        h.app.include_router(router)
        yield h,sender


async def handoff(h,sender,c):
    draft=await prepared(h,sender,c,anonymous=True)
    result=await confirm(h,c,draft);assert result.status_code==201,result.text
    return draft['conversation_id'],result.json()['case_id']


async def meta(c,id):
    response=await c.get('/api/v1/staff/conversations/'+str(id));assert response.status_code==200,response.text
    return response.json()


async def accept(h,c,id,state,key=None):
    return await c.post('/api/v1/staff/conversations/'+str(id)+'/accept',headers={**h.headers(c),'Idempotency-Key':key or str(uuid4())},json=dict(expected_version=state['version'],expected_case_version=state['case_version']))


async def reassign(h,c,case,state,member):
    return await c.patch('/api/v1/staff/cases/'+str(case)+'/assignment',headers=h.headers(c),json=dict(expected_version=state['case_version'],expected_conversation_version=state['version'],assignee_id=str(member) if member else None))


async def close(h,c,case,state):
    return await status(h,c,case,state['case_version'],'closed',expected_conversation_version=state['version'],close_reason='resolved',resolution='Staff explained the next steps.')


def test_accept_race_one_winner_and_stable_receipt_replay(target):
    async def scenario():
        async with chats(target) as (h,sender):
            async with h.client() as customer,h.client() as a,h.client() as b:
                id,case=await handoff(h,sender,customer);pa=operator(target,case);pb=operator(target,case)
                await login(h,a,pa);await login(h,b,pb);state=await meta(a,id)
                ka,kb=str(uuid4()),str(uuid4())
                results=await asyncio.gather(accept(h,a,id,state,ka),accept(h,b,id,state,kb))
                assert sorted(r.status_code for r in results)==[200,409],[r.text for r in results]
                index=next(i for i,r in enumerate(results) if r.status_code==200)
                client,key,person=(a,ka,pa) if index==0 else (b,kb,pb)
                receipt=results[index].json();assert receipt['handler_membership_id']==str(person['case_membership'])
                assert (await accept(h,client,id,state,key)).json()==receipt
                conflict=await accept(h,client,id,{**state,'version':state['version']+1},key)
                assert conflict.status_code==409 and conflict.json()['error']['code']=='IDEMPOTENCY_CONFLICT'
                current=await meta(client,id);assert current['mode']=='human'
                assert current['version']==state['version']+1 and current['generation_epoch']==state['generation_epoch']+1
                assert row(target,'SELECT assigned_membership_id,status,version FROM {s}.cases WHERE id=:id',id=UUID(case))==(person['case_membership'],'in_progress',2)
                events=(await poll(customer,id)).json()['items'];assert [e['event_type'] for e in events]==['handoff_requested','chat_accepted']
    run(scenario())


def test_supervisor_reassign_moves_handler_close_ends_and_receipt_is_historical(target):
    async def scenario():
        async with chats(target) as (h,sender):
            async with h.client() as customer,h.client() as staff,h.client() as supervisor:
                id,case=await handoff(h,sender,customer);person=operator(target,case);other=operator(target,case);lead=operator(target,case,('supervisor',))
                await login(h,staff,person);await login(h,supervisor,lead)
                start=await meta(staff,id);key=str(uuid4());accepted=await accept(h,staff,id,start,key);assert accepted.status_code==200,accepted.text
                state=await meta(supervisor,id)
                changed=await reassign(h,supervisor,case,state,other['case_membership']);assert changed.status_code==200,changed.text
                state=await meta(supervisor,id);assert state['handler_membership_id']==str(other['case_membership']) and state['mode']=='human'
                stale=await reassign(h,supervisor,case,{**state,'version':state['version']-1},person['case_membership']);assert stale.status_code==409
                ended=await close(h,supervisor,case,state);assert ended.status_code==200,ended.text
                current=await meta(staff,id);assert current['mode']=='ended' and current['handler_membership_id'] is None
                assert (await close(h,supervisor,case,state)).status_code==409
                # Old receipt is explicitly an operation snapshot, not current ownership.
                assert (await accept(h,staff,id,start,key)).json()==accepted.json()
                assert (await send(h,customer,id,body='A new message after end')).json()['error']['code']=='CONVERSATION_ENDED'
                events=(await poll(customer,id)).json()['items'];assert [e['event_type'] for e in events]==['handoff_requested','chat_accepted','chat_reassigned','chat_ended']
                assert len({e['id'] for e in events})==4
    run(scenario())


@pytest.mark.parametrize('op',['reassign','close'])
def test_accept_races_supervisor_action_with_one_serialized_winner(target,op):
    async def scenario():
        async with chats(target) as (h,sender):
            async with h.client() as customer,h.client() as staff,h.client() as supervisor:
                id,case=await handoff(h,sender,customer);person=operator(target,case);other=operator(target,case);lead=operator(target,case,('supervisor',))
                await login(h,staff,person);await login(h,supervisor,lead);state=await meta(staff,id)
                action=reassign(h,supervisor,case,state,other['case_membership']) if op=='reassign' else close(h,supervisor,case,state)
                results=await asyncio.gather(accept(h,staff,id,state),action)
                assert sorted(r.status_code for r in results)==[200,409],[r.text for r in results]
                current=await meta(supervisor,id)
                rowcase=row(target,'SELECT assigned_membership_id,status,version FROM {s}.cases WHERE id=:id',id=UUID(case))
                assert rowcase[2]==2 and current['version']==state['version']+1
                if current['mode']=='human':assert UUID(current['handler_membership_id'])==rowcase[0] and rowcase[1]=='in_progress'
                if current['mode']=='ended':assert rowcase[1]=='closed' and current['handler_membership_id'] is None
                if current['mode']=='waiting_staff':assert rowcase[0]==other['case_membership'] and rowcase[1]=='new'
    run(scenario())


def test_after_case_and_chat_write_fault_rolls_back_then_same_key_retries(target,monkeypatch):
    original=LifecycleSQL.transition
    async def scenario():
        async with chats(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id,case=await handoff(h,sender,customer);person=operator(target,case);await login(h,staff,person);state=await meta(staff,id);key=str(uuid4())
                monkeypatch.setattr(LifecycleSQL,'transition',AsyncMock(side_effect=RuntimeError('Injected before receipt')))
                result=await accept(h,staff,id,state,key);assert result.status_code==500
                assert await meta(staff,id)==state
                assert row(target,'SELECT status,version FROM {s}.cases WHERE id=:id',id=UUID(case))==('new',1)
                assert row(target,'SELECT count(*) FROM {s}.chat_transitions WHERE case_id=:id',id=UUID(case))[0]==0
                assert len((await poll(customer,id)).json()['items'])==1
                monkeypatch.setattr(LifecycleSQL,'transition',original)
                result=await accept(h,staff,id,state,key);assert result.status_code==200,result.text
    run(scenario())


def test_scope_version_and_membership_revocation_are_checked(target):
    async def scenario():
        async with chats(target) as (h,sender):
            async with h.client() as customer,h.client() as staff,h.client() as admin:
                id,case=await handoff(h,sender,customer);person=operator(target,case);adminperson=operator(target,case,('admin',))
                await login(h,staff,person);await login(h,admin,adminperson);state=await meta(staff,id)
                assert (await accept(h,admin,id,state)).status_code==404
                assert (await accept(h,customer,id,state)).status_code==401
                # Linked-case mutations cannot omit the reviewed conversation version.
                bad=await status(h,staff,case,1,'closed',close_reason='resolved',resolution='test')
                assert bad.status_code==422 and bad.json()['error']['code']=='CHAT_VERSION_REQUIRED'
                assert (await accept(h,staff,id,{**state,'version':state['version']+1})).status_code==409
                change(target,'UPDATE {s}.staff_memberships SET active=false WHERE id=:id',id=person['case_membership'])
                assert (await accept(h,staff,id,state)).status_code==404
                assert row(target,'SELECT status,version FROM {s}.cases WHERE id=:id',id=UUID(case))==('new',1)
    run(scenario())


def test_raw_new_message_to_ended_chat_is_rejected_and_counters_roll_back(target):
    from test_drafts import actor
    from airport_assistant.infrastructure.database.conversation_repository import PostgresConversations
    from airport_assistant.infrastructure.database.connections import DatabaseError
    async def scenario():
        async with chats(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id,case=await handoff(h,sender,customer);person=operator(target,case);await login(h,staff,person)
                state=await meta(staff,id);result=await close(h,staff,case,state);assert result.status_code==200,result.text
                before=row(target,'SELECT last_sequence,last_event_sequence,version FROM {s}.conversations WHERE id=:id',id=UUID(id))
                identity=await actor(h,customer)
                with pytest.raises(DatabaseError) as error:
                    async with PostgresConversations(h.db,target['schema']).transaction(identity.database_context) as tx:
                        conversation=await tx.find(UUID(id),lock=True)
                        await tx.append(conversation,uuid4(),uuid4(),'Raw late message')
                assert error.value.sqlstate=='23514'
                assert row(target,'SELECT last_sequence,last_event_sequence,version FROM {s}.conversations WHERE id=:id',id=UUID(id))==before
    run(scenario())
