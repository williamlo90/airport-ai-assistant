"""CASE-02: real Neon atomic graphs, retries and browser-owned confirmation."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import os
from pathlib import Path
from uuid import UUID,uuid4

import pytest
from sqlalchemy import text
from test_staff_auth import target as auth_target,run,row,change
from test_conversations import target as conversation_target,create as create_conversation,verified
from test_drafts import target as draft_target,drafts,create_draft,payload,actor,deadline
from test_anonymous_adoption import target as adoption_target,issue,verify
from airport_assistant.config import Settings
from airport_assistant.auth_config import AuthSettings
from airport_assistant.database_config import MigrationDatabaseSettings,RuntimeDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.case_repository import PostgresCases
from airport_assistant.modules.cases.service import CaseService
from airport_assistant.main import create_app

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Set RUN_NEON_AUTH_TESTS=1 for real case commit integration.')


@pytest.fixture(scope='module')
def target(adoption_target):
    t=adoption_target
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):
        assert migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0008_case_commit')=='0008_case_commit'
    return t


@asynccontextmanager
async def cases(target):
    async with drafts(target) as (h,sender):
        service=CaseService(PostgresCases(h.db,target['schema']))
        h.app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True),
            database_settings=RuntimeDatabaseSettings(DATABASE_URL=target['url']),auth_settings=AuthSettings(SESSION_SECRET=target['key']),
            services=replace(h.app.state.services,cases=service))
        yield h,sender


async def prepared(h,sender,c,*,anonymous=False):
    if anonymous:
        await h.boot(c);report=payload(category='information',service_type='general',booking_reference=None,contact_email=None)
    else:
        email='case'+uuid4().hex+'@example.com';await verified(h,sender,c,email);report=payload(contact_email=email)
    conv=await create_conversation(h,c)
    return await create_draft(h,c,conv,report,action='request_handoff' if anonymous else 'create_case')


async def confirm(h,c,preview,key=None,**changes):
    body=dict(expected_version=preview['version'],payload_hash=preview['payload_hash']);body.update(changes)
    return await c.post('/api/v1/action-drafts/'+preview['draft_id']+'/confirm',headers={**h.headers(c),'Idempotency-Key':key or str(uuid4())},json=body)


def counts(t,id):
    case=row(t,'SELECT id FROM {s}.cases WHERE origin_draft_id=:id',id=UUID(id))
    if not case:return (0,)*7
    result=[]
    for table,column in [('cases','id'),('messages','case_id'),('action_consents','case_id'),('case_participants','case_id'),('case_history','case_id'),('audit_events','case_id'),('outbox_jobs','case_id')]:
        result.append(row(t,'SELECT count(*) FROM {s}.'+table+' WHERE '+column+'=:id',id=case[0])[0])
    return tuple(result)


def test_confirm_builds_complete_graph_and_status_recovers_lost_response(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c);key=str(uuid4())
                response=await confirm(h,c,preview,key)
                assert response.status_code==201,response.text
                result=response.json();assert result['state']=='committed' and result['result_ref'].startswith('CASE-')
                assert counts(target,preview['draft_id'])==(1,1,1,1,1,1,1)
                status=await c.get('/api/v1/actions/'+preview['draft_id'])
                assert status.status_code==200 and status.json()['result_ref']==result['result_ref']
                assert status.json()['state']=='committed' and status.json()['case_id']==result['case_id']
                assert (await confirm(h,c,preview,key)).json()==result
                assert (await confirm(h,c,preview,str(uuid4()))).json()==result
                assert counts(target,preview['draft_id'])==(1,1,1,1,1,1,1)
                cancelled=await c.delete('/api/v1/action-drafts/'+preview['draft_id'],headers=h.headers(c))
                assert cancelled.status_code==409 and cancelled.json()['error']['result_ref']==result['result_ref']
                assert row(target,'SELECT state,attempts,payload FROM {s}.outbox_jobs WHERE case_id=:id',id=UUID(result['case_id']))==('queued',0,dict(case_id=result['case_id'],draft_id=preview['draft_id']))
    run(scenario())


def test_public_message_after_adoption_keeps_narrow_insert_privileges(target):
    from test_conversations import send
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c)
                sent=await send(h,c,preview['conversation_id'],body='Additional customer question.')
                assert sent.status_code==201,sent.text
                assert sent.json()['sequence']==1
                assert row(target,'SELECT resource_security_labels FROM {s}.messages WHERE id=:id',
                    id=UUID(sent.json()['message_id']))[0]==[]
    run(scenario())


def test_two_confirmations_same_draft_create_only_one_case(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c)
                results=await asyncio.gather(confirm(h,c,preview),confirm(h,c,preview))
                assert [value.status_code for value in results]==[201,201],[value.text for value in results]
                assert results[0].json()==results[1].json()
                assert counts(target,preview['draft_id'])==(1,1,1,1,1,1,1)
    run(scenario())


def test_same_key_different_request_conflicts_and_stale_review_cannot_commit(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                first=await prepared(h,sender,c);second=await create_draft(h,c,first['conversation_id'],first['payload']);key=str(uuid4())
                assert (await confirm(h,c,first,key)).status_code==201
                assert (await confirm(h,c,second,key)).status_code==409
                assert counts(target,second['draft_id'])==(0,)*7
                edited=await c.patch('/api/v1/action-drafts/'+second['draft_id'],headers=h.headers(c),json=dict(expected_version=1,payload=second['payload']))
                assert edited.status_code==200
                assert (await confirm(h,c,second)).status_code==409
                assert (await confirm(h,c,edited.json())).status_code==201
    run(scenario())


def test_anonymous_general_case_adopts_with_history_but_without_email_opt_in(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c,anonymous=True);old=await actor(h,c)
                result=await confirm(h,c,preview)
                assert result.status_code==201,result.text
                case_id=UUID(result.json()['case_id'])
                assert counts(target,preview['draft_id'])==(1,1,1,0,1,1,1)
                pending=await create_draft(h,c,preview['conversation_id'],preview['payload'],action='request_handoff')
                challenge,code=await issue(h,sender,c)
                upgraded=await verify(h,c,challenge,code)
                assert upgraded.status_code==200,upgraded.text
                principal=UUID(upgraded.json()['principal_id'])
                assert row(target,'SELECT owner_principal_id,owner_anonymous_session_id,followup_contact_id,version FROM {s}.cases WHERE id=:id',id=case_id)==(principal,None,None,2)
                assert row(target,'SELECT author_principal_id,author_session_id,origin_session_id FROM {s}.messages WHERE case_id=:id',id=case_id)==(principal,None,old.session.id)
                assert counts(target,preview['draft_id'])==(1,1,1,1,2,2,1)
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=UUID(pending['draft_id']))[0]=='cancelled'
                receipt=await c.get('/api/v1/actions/'+preview['draft_id'])
                assert receipt.status_code==200 and receipt.json()['result_ref']==result.json()['result_ref']
                assert receipt.json()['expires_at'] is None
                assert (await c.get('/api/v1/action-drafts/'+preview['draft_id'])).status_code==404
                assert (await confirm(h,c,preview)).status_code==404
                current=await actor(h,c)
                async with h.db.transaction(current.database_context) as db:
                    assert (await(await db.execute(f'SELECT public_reference FROM "{target["schema"]}".cases WHERE id=%s',(case_id,))).fetchone())[0]==result.json()['result_ref']
    run(scenario())


def test_unauthorized_contact_and_anonymous_booking_claim_do_not_commit(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c,h.client() as anon:
                good=await prepared(h,sender,c)
                for report in (payload(contact_email=None),payload(contact_email='unverified@example.com')):
                    preview=await create_draft(h,c,good['conversation_id'],report)
                    assert (await confirm(h,c,preview)).status_code==422
                    assert counts(target,preview['draft_id'])==(0,)*7
                await h.boot(anon);conv=await create_conversation(h,anon)
                preview=await create_draft(h,anon,conv)
                assert (await confirm(h,anon,preview)).status_code==401
                assert counts(target,preview['draft_id'])==(0,)*7
    run(scenario())


def test_wrong_browser_tenant_missing_key_and_csrf_cannot_confirm(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c,h.client() as other,h.client('b.example.test') as cross:
                preview=await prepared(h,sender,c);await h.boot(other);await h.boot(cross)
                url='/api/v1/action-drafts/'+preview['draft_id']+'/confirm'
                body=dict(expected_version=preview['version'],payload_hash=preview['payload_hash'])
                assert (await c.post(url,headers=h.headers(c),json=body)).status_code==400
                assert (await c.post(url,json=body)).status_code==403
                for stranger in (other,cross):assert (await confirm(h,stranger,preview)).status_code==404
                assert counts(target,preview['draft_id'])==(0,)*7
    run(scenario())


def test_fault_after_case_message_consent_rolls_back_everything_then_retry_works(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c);key=str(uuid4());s='"'+target['schema']+'"'
                with target['engine'].begin() as db:
                    db.exec_driver_sql(f"CREATE FUNCTION {s}.case02_fault() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN RAISE EXCEPTION 'Controlled outbox failure' USING ERRCODE='23514';END $$")
                    db.exec_driver_sql(f'CREATE TRIGGER case02_fault BEFORE INSERT ON {s}.outbox_jobs FOR EACH ROW EXECUTE FUNCTION {s}.case02_fault()')
                try:assert (await confirm(h,c,preview,key)).status_code==500
                finally:
                    with target['engine'].begin() as db:
                        db.exec_driver_sql(f'DROP TRIGGER case02_fault ON {s}.outbox_jobs');db.exec_driver_sql(f'DROP FUNCTION {s}.case02_fault()')
                assert counts(target,preview['draft_id'])==(0,)*7
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=UUID(preview['draft_id']))[0]=='pending'
                assert row(target,'SELECT count(*) FROM {s}.idempotency_records WHERE draft_id=:id',id=UUID(preview['draft_id']))[0]==0
                assert (await confirm(h,c,preview,key)).status_code==201
    run(scenario())


@pytest.mark.parametrize('stage',['draft','session'])
def test_expiry_during_graph_creation_never_commits_partial_case(target,monkeypatch,stage):
    from airport_assistant.infrastructure.database.case_repository import CaseSQL
    original=CaseSQL.mark_committed
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c)
                if stage=='draft':
                    await deadline(target,h,c,preview['draft_id'],future=True)
                    preview=(await c.get('/api/v1/action-drafts/'+preview['draft_id'])).json()
                else:
                    who=await actor(h,c)
                    change(target,"UPDATE {s}.sessions SET expires_at=clock_timestamp()+interval '4 seconds' WHERE id=:id",id=who.session.id)
                async def delayed(self,*args):await asyncio.sleep(5);return await original(self,*args)
                monkeypatch.setattr(CaseSQL,'mark_committed',delayed)
                response=await confirm(h,c,preview)
                assert response.status_code==(410 if stage=='draft' else 401),response.text
                assert counts(target,preview['draft_id'])==(0,)*7
                assert row(target,'SELECT state FROM {s}.action_drafts WHERE id=:id',id=UUID(preview['draft_id']))[0]==('expired' if stage=='draft' else 'pending')
    run(scenario())


def test_queue_deactivation_waits_for_confirm_and_runtime_cannot_edit_configuration(target,monkeypatch):
    from airport_assistant.infrastructure.database.case_repository import CaseSQL
    from airport_assistant.infrastructure.database.connections import DatabaseError
    original=CaseSQL.create_case
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        async def paused(self,*args):entered.set();await release.wait();return await original(self,*args)
        monkeypatch.setattr(CaseSQL,'create_case',paused)
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c);queue=UUID(preview['destination']['queue_id'])
                committing=asyncio.create_task(confirm(h,c,preview))
                await asyncio.wait_for(entered.wait(),8)
                updating=asyncio.create_task(asyncio.to_thread(change,target,'UPDATE {s}.service_queues SET active=false WHERE tenant_id=:t AND id=:id',t=target['a'],id=queue))
                try:
                    with pytest.raises(TimeoutError):await asyncio.wait_for(asyncio.shield(updating),.2)
                finally:release.set()
                assert (await committing).status_code==201
                await updating
                change(target,'UPDATE {s}.service_queues SET active=true WHERE tenant_id=:t AND id=:id',t=target['a'],id=queue)
                who=await actor(h,c)
                with pytest.raises(DatabaseError) as exc:
                    async with h.db.transaction(who.database_context) as db:
                        await db.execute(f'UPDATE "{target["schema"]}".service_queues SET name=%s WHERE tenant_id=%s AND id=%s',('Forbidden',target['a'],queue))
                assert exc.value.sqlstate=='42501'
                for statement,params in [
                    ('UPDATE {s}.organizations SET name=name WHERE tenant_id=%s AND id=%s',(target['a'],UUID(preview['destination']['organization_id']))),
                    ('UPDATE {s}.contacts SET email_address=email_address WHERE tenant_id=%s AND principal_id=%s',(target['a'],who.session.principal_id)),
                ]:
                    with pytest.raises(DatabaseError) as denied:
                        async with h.db.transaction(who.database_context) as db:await db.execute(statement.format(s='"'+target['schema']+'"'),params)
                    assert denied.value.sqlstate=='42501'
    run(scenario())


def test_deferred_constraint_rejects_orphan_case_graph_at_outer_commit(target):
    from airport_assistant.infrastructure.database.case_repository import CaseSQL
    from airport_assistant.infrastructure.database.connections import DatabaseError
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c);who=await actor(h,c)
                with pytest.raises(DatabaseError) as exc:
                    async with h.db.transaction(who.database_context) as db:
                        tx=CaseSQL(db,target['schema'],who.database_context)
                        draft=await tx.find_draft(UUID(preview['draft_id']))
                        contact=await tx.verified_contact(draft.payload.contact_email)
                        await tx.create_case(draft,uuid4(),uuid4(),contact,contact,uuid4())
                        # Intentionally omit committed draft + idempotency record.
                assert exc.value.sqlstate=='23514'
                assert counts(target,preview['draft_id'])==(0,)*7
    run(scenario())


@pytest.mark.parametrize('field',['summary','customer_report','organization_id'])
def test_deferred_graph_comparison_fails_closed_for_null_or_missing_json(target,field):
    from airport_assistant.infrastructure.database.case_repository import CaseSQL
    from airport_assistant.infrastructure.database.connections import DatabaseError
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c);who=await actor(h,c)
                with pytest.raises(DatabaseError) as exc:
                    async with h.db.transaction(who.database_context) as db:
                        tx=CaseSQL(db,target['schema'],who.database_context)
                        draft=await tx.find_draft(UUID(preview['draft_id']))
                        if field=='organization_id':
                            await db.execute(f'UPDATE "{target["schema"]}".action_drafts SET destination=destination-%s WHERE id=%s',(field,draft.id))
                        else:
                            await db.execute(f'UPDATE "{target["schema"]}".action_drafts SET payload=jsonb_set(payload,%s,\'null\'::jsonb) WHERE id=%s',([field],draft.id))
                        contact=await tx.verified_contact(draft.payload.contact_email)
                        result=await tx.create_case(draft,uuid4(),uuid4(),contact,contact,uuid4())
                        assert await tx.mark_committed(draft,result)
                        await tx.record_key(str(uuid4()),'a'*64,draft,result)
                assert exc.value.sqlstate=='23514'
                assert counts(target,preview['draft_id'])==(0,)*7
    run(scenario())


def test_one_actor_key_race_across_different_conversations_has_one_winner(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                first=await prepared(h,sender,c);conv=await create_conversation(h,c)
                second=await create_draft(h,c,conv,first['payload']);key=str(uuid4())
                result=await asyncio.gather(confirm(h,c,first,key),confirm(h,c,second,key))
                assert sorted(value.status_code for value in result)==[201,409]
                assert sum(counts(target,item['draft_id'])[0] for item in (first,second))==1
    run(scenario())


def test_confirmation_vs_cancel_keeps_one_consistent_terminal_result(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c)
                committed,cancelled=await asyncio.gather(confirm(h,c,preview),c.delete('/api/v1/action-drafts/'+preview['draft_id'],headers=h.headers(c)))
                if committed.status_code==201:
                    assert cancelled.status_code==409 and cancelled.json()['error']['result_ref']==committed.json()['result_ref']
                    assert counts(target,preview['draft_id'])==(1,1,1,1,1,1,1)
                else:
                    assert committed.status_code==409 and cancelled.status_code==200
                    assert counts(target,preview['draft_id'])==(0,)*7
    run(scenario())


def test_inactive_destination_requires_review_and_foreign_customer_cannot_read_graph(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c,h.client() as other:
                preview=await prepared(h,sender,c);queue=UUID(preview['destination']['queue_id'])
                change(target,'UPDATE {s}.service_queues SET active=false WHERE tenant_id=:tenant AND id=:id',tenant=target['a'],id=queue)
                try:assert (await confirm(h,c,preview)).status_code==409
                finally:change(target,'UPDATE {s}.service_queues SET active=true WHERE tenant_id=:tenant AND id=:id',tenant=target['a'],id=queue)
                result=await confirm(h,c,preview);assert result.status_code==201
                await verified(h,sender,other)
                assert (await other.get('/api/v1/actions/'+preview['draft_id'])).status_code==404
                foreign=await actor(h,other)
                async with h.db.transaction(foreign.database_context) as db:
                    for table,column in [('cases','id'),('messages','case_id'),('action_consents','case_id'),('case_history','case_id'),('outbox_jobs','case_id'),('audit_events','case_id'),('idempotency_records','result_case_id')]:
                        assert not await(await db.execute(f'SELECT id FROM "{target["schema"]}".{table} WHERE {column}=%s',(UUID(result.json()['case_id']),))).fetchall()
    run(scenario())


def test_lost_http_response_after_commit_recovers_via_action_status(target,monkeypatch):
    import httpx
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c:
                preview=await prepared(h,sender,c);key=str(uuid4());original=c._transport.handle_async_request
                async def lost(request):
                    response=await original(request)
                    await response.aclose()
                    raise httpx.ReadTimeout('Controlled response loss')
                with monkeypatch.context() as patch:
                    patch.setattr(c._transport,'handle_async_request',lost)
                    with pytest.raises(httpx.ReadTimeout):await confirm(h,c,preview,key)
                status=await c.get('/api/v1/actions/'+preview['draft_id'])
                assert status.json()['state']=='committed'
                replay=await confirm(h,c,preview,key)
                assert replay.status_code==201 and replay.json()['result_ref']==status.json()['result_ref']
                assert counts(target,preview['draft_id'])==(1,1,1,1,1,1,1)
    run(scenario())


def test_case_owner_other_session_gets_receipt_but_cannot_reconfirm_preview(target):
    async def scenario():
        async with cases(target) as (h,sender):
            async with h.client() as c,h.client() as other:
                preview=await prepared(h,sender,c);result=await confirm(h,c,preview)
                assert result.status_code==201
                await verified(h,sender,other,preview['payload']['contact_email'])
                status=await other.get('/api/v1/actions/'+preview['draft_id'])
                assert status.status_code==200 and status.json()['result_ref']==result.json()['result_ref']
                assert (await other.get('/api/v1/action-drafts/'+preview['draft_id'])).status_code==404
                assert (await confirm(h,other,preview)).status_code==404
    run(scenario())
