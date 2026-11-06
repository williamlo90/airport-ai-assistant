"""CHAT-03 real database guards, controlled delayed generator, no AI provider."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
import os
from uuid import UUID,uuid4
import pytest
from test_staff_auth import run,row,change
from test_chat_handoff import target as handoff_target,events_target,files_target,lifecycle_target,commit_target,adoption_target,draft_target,conversation_target,auth_target,chats,handoff,meta,accept,close,reassign,operator,login
from test_conversation_events import poll
from test_case_commit import prepared,confirm
from test_drafts import actor
from test_conversations import create,send
from airport_assistant.database_config import MigrationDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.publication import PostgresPublication,PublicationSQL
from airport_assistant.modules.conversations.publication import StaffMessages,AIPublication
from airport_assistant.api.staff_messages import router

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in CHAT-03 Neon publication guards.')


@pytest.fixture(scope='module')
def target(handoff_target):
    t=handoff_target
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0013_chat_publication')=='0013_chat_publication'
    return t


@asynccontextmanager
async def publications(target):
    async with chats(target) as (h,sender):
        repository=PostgresPublication(h.db,target['schema'])
        h.app.state.services=replace(h.app.state.services,staff_messages=StaffMessages(repository),ai_publication=AIPublication(repository))
        h.app.include_router(router)
        yield h,sender,h.app.state.services.ai_publication


def command(id,state,**changes):return dict({'conversation_id':str(id),'body':'The customer report has been received.','language':'en','channel':'chat','expected_version':state['case_version'],'expected_conversation_version':state['version'],'expected_epoch':state['generation_epoch']},**changes)


async def reply(h,c,case,body,key=None):
    return await c.post('/api/v1/staff/cases/'+str(case)+'/messages',headers={**h.headers(c),'Idempotency-Key':key or str(uuid4())},json=body)


async def active(h,sender,target,customer,staff):
    id,case=await handoff(h,sender,customer);person=operator(target,case);await login(h,staff,person)
    accepted=await accept(h,staff,id,await meta(staff,id));assert accepted.status_code==200,accepted.text
    return id,case,person


def test_staff_reply_dedup_original_language_event_and_historical_receipt(target):
    async def scenario():
        async with publications(target) as (h,sender,pub):
            async with h.client() as customer,h.client() as staff,h.client() as supervisor:
                id,case,person=await active(h,sender,target,customer,staff);state=await meta(staff,id);body=command(id,state);key=str(uuid4())
                results=await asyncio.gather(reply(h,staff,case,body,key),reply(h,staff,case,body,key))
                assert [r.status_code for r in results]==[201,201],[r.text for r in results]
                assert results[0].json()==results[1].json()
                receipt=results[0].json();assert receipt['state']=='saved'
                conflict=await reply(h,staff,case,{**body,'body':'Different text'},key);assert conflict.status_code==409
                event=(await poll(customer,id)).json()['items'][-1]
                assert event['event_type']=='staff_message' and event['payload']['author_type']=='staff'
                assert event['payload']['body']==body['body'] and event['payload']['language']=='en'
                lead=operator(target,case,('supervisor',));await login(h,supervisor,lead)
                ended=await close(h,supervisor,case,await meta(supervisor,id));assert ended.status_code==200,ended.text
                assert (await reply(h,staff,case,body,key)).json()==receipt
                assert (await reply(h,staff,case,body)).json()['error']['code']=='STALE_MESSAGE'
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'",id=UUID(id))[0]==1
    run(scenario())


@pytest.mark.parametrize('operation',['close','reassign'])
def test_reply_races_lifecycle_one_commit_no_stale_body(target,operation):
    async def scenario():
        async with publications(target) as (h,sender,pub):
            async with h.client() as customer,h.client() as staff,h.client() as supervisor:
                id,case,_=await active(h,sender,target,customer,staff);lead=operator(target,case,('supervisor',));other=operator(target,case)
                await login(h,supervisor,lead);state=await meta(staff,id)
                action=close(h,supervisor,case,state) if operation=='close' else reassign(h,supervisor,case,state,other['case_membership'])
                results=await asyncio.gather(reply(h,staff,case,command(id,state)),action)
                assert sum(r.status_code in (200,201) for r in results)==1 and sum(r.status_code==409 for r in results)==1,[r.text for r in results]
                count=row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'",id=UUID(id))[0]
                assert count==(1 if results[0].status_code==201 else 0)
    run(scenario())


@pytest.mark.parametrize('mode',['waiting_staff','human','ended'])
def test_delayed_fake_generator_cannot_publish_after_handoff(target,mode):
    async def scenario():
        async with publications(target) as (h,sender,pub):
            async with h.client() as customer,h.client() as staff:
                draft=await prepared(h,sender,customer,anonymous=True);id=UUID(draft['conversation_id']);identity=await actor(h,customer)
                ticket=await pub.capture(identity,id);started=asyncio.Event();release=asyncio.Event()
                async def controlled_generator():
                    started.set();await release.wait()
                    return await pub.publish(identity,ticket,'Controlled late answer; not a live model.')
                pending=asyncio.create_task(controlled_generator());await started.wait()
                confirmed=await asyncio.wait_for(confirm(h,customer,draft),15);assert confirmed.status_code==201,confirmed.text
                case=confirmed.json()['case_id']
                if mode!='waiting_staff':
                    person=operator(target,case);await login(h,staff,person)
                    result=await accept(h,staff,id,await meta(staff,id));assert result.status_code==200,result.text
                    if mode=='ended':assert (await close(h,staff,case,await meta(staff,id))).status_code==200
                release.set();result=await pending
                assert result==dict(state='discarded',reason='STALE_GENERATION')
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'",id=id)[0]==0
                assert not any(e['event_type']=='ai_message' for e in (await poll(customer,id)).json()['items'])
    run(scenario())


def test_ai_published_once_concurrent_jobs_stale_and_input_correction(target):
    async def scenario():
        async with publications(target) as (h,sender,pub):
            async with h.client() as customer:
                await h.boot(customer);id=UUID(await create(h,customer));identity=await actor(h,customer)
                first=await pub.capture(identity,id);second=await pub.capture(identity,id)
                outputs=await asyncio.gather(pub.publish(identity,first,'Controlled A'),pub.publish(identity,second,'Controlled B'))
                assert sorted(r['state'] for r in outputs)==['discarded','published']
                winner=first if outputs[0]['state']=='published' else second;body='Controlled A' if winner is first else 'Controlled B'
                saved=await pub.publish(identity,winner,body);assert saved['state']=='published'
                events=(await poll(customer,id)).json()['items'];assert len(events)==1 and events[0]['event_type']=='ai_message'
                assert events[0]['payload']['author_type']=='ai' and events[0]['payload']['body']==body
                ticket=await pub.capture(identity,id)
                assert (await send(h,customer,str(id),body='Correction to the original request.')).status_code==201
                assert (await pub.publish(identity,ticket,'Outdated correction response'))['state']=='discarded'
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='ai'",id=id)[0]==1
    run(scenario())


def test_ai_insert_event_rollback_before_finish_and_same_ticket_retry(target,monkeypatch):
    original=PublicationSQL.published_generation
    async def fail(*args):raise RuntimeError('Injected after body/event insert')
    async def scenario():
        async with publications(target) as (h,sender,pub):
            async with h.client() as customer:
                await h.boot(customer);id=UUID(await create(h,customer));identity=await actor(h,customer);ticket=await pub.capture(identity,id)
                monkeypatch.setattr(PublicationSQL,'published_generation',fail)
                with pytest.raises(RuntimeError):await pub.publish(identity,ticket,'Controlled retry body')
                assert (await poll(customer,id)).json()['items']==[]
                assert row(target,'SELECT version,last_sequence,last_event_sequence FROM {s}.conversations WHERE id=:id',id=id)==(1,0,0)
                monkeypatch.setattr(PublicationSQL,'published_generation',original)
                restarted=AIPublication(PostgresPublication(h.db,target['schema']))
                first=await restarted.publish(identity,ticket,'Controlled retry body')
                assert (await restarted.publish(identity,ticket,'Controlled retry body'))==first
                assert len((await poll(customer,id)).json()['items'])==1
    run(scenario())


def test_generation_deadline_crossed_inside_insert_savepoint_discards_atomically(target,monkeypatch):
    original=PublicationSQL.ai_message
    calls=[]
    async def expire_after_precheck(self,conversation,generation,body):
        calls.append(True)
        remaining=(await(await self._c.execute('SELECT extract(epoch FROM(%s-clock_timestamp()))',(generation['expires_at'],))).fetchone())[0]
        await asyncio.sleep(max(float(remaining),0)+0.05)
        return await original(self,conversation,generation,body)
    async def scenario():
        async with publications(target) as (h,sender,pub):
            async with h.client() as customer:
                await h.boot(customer);id=UUID(await create(h,customer));identity=await actor(h,customer);ticket=await pub.capture(identity,id)
                # An administrative fixture creates a short-deadline copy before
                # publication starts, keeping all guards enabled.
                from sqlalchemy import text
                original_ticket=ticket;ticket=replace(ticket,id=uuid4())
                with target['engine'].begin() as connection:
                    schema='"'+target['schema']+'"'
                    connection.execute(text('INSERT INTO '+schema+'.ai_generations(tenant_id,id,conversation_id,origin_session_id,principal_id,input_version,generation_epoch,language,nonce_hash,created_at,expires_at) SELECT tenant_id,:new_id,conversation_id,origin_session_id,principal_id,input_version,generation_epoch,language,nonce_hash,CURRENT_TIMESTAMP-interval \'4 minutes\',CURRENT_TIMESTAMP+interval \'5 seconds\' FROM '+schema+'.ai_generations WHERE id=:old_id'),{'new_id':ticket.id,'old_id':original_ticket.id})
                monkeypatch.setattr(PublicationSQL,'ai_message',expire_after_precheck)
                result=await pub.publish(identity,ticket,'Late deadline controlled output')
                assert calls==[True]
                assert result==dict(state='discarded',reason='STALE_GENERATION')
                assert row(target,'SELECT version,last_sequence,last_event_sequence FROM {s}.conversations WHERE id=:id',id=id)==(1,0,0)
                assert row(target,'SELECT state FROM {s}.ai_generations WHERE id=:id',id=ticket.id)[0]=='stale'
                assert (await poll(customer,id)).json()['items']==[]
    run(scenario())


def test_email_unavailable_and_nonhandler_or_revoked_sender_writes_nothing(target):
    async def scenario():
        async with publications(target) as (h,sender,pub):
            async with h.client() as customer,h.client() as staff,h.client() as other:
                id,case,person=await active(h,sender,target,customer,staff);otherperson=operator(target,case);await login(h,other,otherperson)
                state=await meta(staff,id)
                assert (await reply(h,other,case,command(id,state))).json()['error']['code']=='STALE_MESSAGE'
                unavailable=await reply(h,staff,case,command(id,state,channel='email'))
                assert unavailable.status_code==503 and unavailable.json()['error']['code']=='MAIL_UNAVAILABLE'
                change(target,'UPDATE {s}.staff_memberships SET active=false WHERE id=:id',id=person['case_membership'])
                assert (await reply(h,staff,case,command(id,state))).status_code==404
                assert row(target,"SELECT count(*) FROM {s}.messages WHERE conversation_id=:id AND author_type='staff'",id=UUID(id))[0]==0
    run(scenario())
