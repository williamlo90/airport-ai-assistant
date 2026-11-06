"""CASE-03 on real Neon; schema0008 graphs survive lifecycle upgrades."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import os
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from test_staff_auth import target as auth_target,run,row,change,seed,insert
from test_conversations import target as conversation_target
from test_drafts import target as draft_target
from test_anonymous_adoption import target as adoption_target
from test_case_commit import target as commit_target,cases,prepared,confirm
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings,RuntimeDatabaseSettings
from airport_assistant.auth_config import AuthSettings
from airport_assistant.config import Settings
from airport_assistant.main import create_app
from airport_assistant.modules.cases.lifecycle import CaseLifecycle
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Set RUN_NEON_AUTH_TESTS=1 for case lifecycle Neon integration.')


@pytest.fixture(scope='module')
def target(commit_target):
    t=commit_target
    settings=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):assert __import__('airport_assistant.infrastructure.database.migrations',fromlist=['migrate']).migrate(settings.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0009_case_lifecycle')=='0009_case_lifecycle'
    return t


@asynccontextmanager
async def lifecycle(target):
    async with cases(target) as (h,sender):
        service=CaseLifecycle(PostgresCaseLifecycle(h.db,target['schema']))
        h.app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True),database_settings=RuntimeDatabaseSettings(DATABASE_URL=target['url']),auth_settings=AuthSettings(SESSION_SECRET=target['key']),services=replace(h.app.state.services,case_lifecycle=service))
        yield h,sender


async def new_case(h,sender,c):
    preview=await prepared(h,sender,c)
    result=await confirm(h,c,preview,str(uuid4()))
    assert result.status_code==201,result.text
    return result.json()['case_id']


def operator(target,id,roles=('cs',)):
    person=seed(target,roles=roles)
    case=row(target,'SELECT organization_id,queue_id FROM {s}.cases WHERE id=:id',id=UUID(id))
    with target['engine'].begin() as c:
        mid=insert(c,target['schema'],'staff_memberships',tenant_id=target['a'],principal_id=person['principal'],organization_id=case[0])
        for role in roles:insert(c,target['schema'],'membership_roles',tenant_id=target['a'],membership_id=mid,role=role)
        insert(c,target['schema'],'membership_queues',tenant_id=target['a'],membership_id=mid,organization_id=case[0],queue_id=case[1])
    return {**person,'case_membership':mid}


async def login(h,c,person):
    await h.boot(c)
    result=await h.login(c,person['email']);assert result.status_code==200,result.text


async def assign(h,c,id,version,mid):
    return await c.patch('/api/v1/staff/cases/'+id+'/assignment',headers=h.headers(c),json=dict(expected_version=version,assignee_id=str(mid) if mid else None))


async def status(h,c,id,version,value,**extra):
    return await c.patch('/api/v1/staff/cases/'+id+'/status',headers=h.headers(c),json=dict(expected_version=version,status=value,next_action='Langkah berikutnya jelas.',**extra))


def test_self_claim_status_closure_and_customer_continuation(target):
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,staff,person)
                initial=await customer.get('/api/v1/cases/'+id);assert initial.status_code==200,initial.text
                result=await assign(h,staff,id,1,person['case_membership']);assert result.status_code==200,result.text
                assert result.json()['status']=='in_progress' and result.json()['version']==2
                for version,value in ((2,'awaiting_customer'),(3,'in_progress'),(4,'awaiting_external'),(5,'in_progress')):
                    result=await status(h,staff,id,version,value);assert result.status_code==200,result.text
                result=await status(h,staff,id,6,'closed',close_reason='resolved',resolution='Penjelasan telah diberikan.')
                assert result.status_code==200,result.text
                closed=result.json();assert closed['next_actor']=='none' and closed['resolution']=='Penjelasan telah diberikan.'
                command=dict(expected_version=7,intent='same_issue',message='Masalah yang sama belum selesai.',client_request_id=str(uuid4()))
                reopened=await customer.post('/api/v1/cases/'+id+'/continue',headers=h.headers(customer),json=command)
                assert reopened.status_code==200,reopened.text
                assert reopened.json()['status']=='in_progress' and reopened.json()['version']==8
                assert reopened.json()['close_reason'] is None and reopened.json()['closed_at'] is None
                assert any(e['resolution']=='Penjelasan telah diberikan.' for e in reopened.json()['history'])
                retry=await customer.post('/api/v1/cases/'+id+'/continue',headers=h.headers(customer),json=command)
                assert retry.status_code==200 and retry.json()['version']==8
                assert row(target,'SELECT count(*) FROM {s}.case_history WHERE case_id=:id',id=UUID(id))[0]==8
    run(scenario())


def test_supervisor_assignment_and_admin_or_other_org_cannot_read(target):
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as staff,h.client() as admin,h.client() as outsider:
                id=await new_case(h,sender,customer);supervisor=operator(target,id,('supervisor',));cs=operator(target,id);adminperson=operator(target,id,('admin',));other=seed(target)
                await login(h,staff,supervisor);await login(h,admin,adminperson);await login(h,outsider,other)
                for c in (admin,outsider):
                    response=await c.get('/api/v1/staff/cases/'+id);assert response.status_code==404,response.text
                result=await assign(h,staff,id,1,cs['case_membership']);assert result.status_code==200,result.text
                assert result.json()['status']=='new'
                response=await status(h,staff,id,2,'in_progress');assert response.status_code==200,response.text
                response=await assign(h,staff,id,3,supervisor['case_membership']);assert response.status_code==200,response.text
                assert response.json()['assigned_membership_id']==str(supervisor['case_membership'])
                queue=await staff.get('/api/v1/staff/queues/cases?status=in_progress');assert queue.status_code==200,queue.text
                assert id in {r['id'] for r in queue.json()['items']}
    run(scenario())


@pytest.mark.parametrize('revocation',['membership','credential'])
def test_reopen_without_active_assignee_returns_new_and_old_closure_is_preserved(target,revocation):
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,staff,person)
                assert (await assign(h,staff,id,1,person['case_membership'])).status_code==200
                result=await status(h,staff,id,2,'closed',close_reason='no_response',resolution='Ditutup setelah tindak lanjut.',followup_evidence='Petugas menghubungi dua kali, belum ada respons.')
                assert result.status_code==200,result.text
                if revocation=='membership':change(target,'UPDATE {s}.staff_memberships SET active=false WHERE id=:id',id=person['case_membership'])
                else:change(target,'UPDATE {s}.staff_credentials SET active=false WHERE principal_id=:id',id=person['principal'])
                result=await customer.post('/api/v1/cases/'+id+'/continue',headers=h.headers(customer),json=dict(expected_version=3,intent='same_issue',message='Saya kembali untuk masalah yang sama.',client_request_id=str(uuid4())))
                assert result.status_code==200,result.text
                assert result.json()['status']=='new' and result.json()['assigned_membership_id'] is None
                assert any(e['close_reason']=='no_response' for e in result.json()['history'])
    run(scenario())


def test_two_cs_self_claims_have_one_winner_and_no_partial_history(target):
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as first,h.client() as second:
                id=await new_case(h,sender,customer);a=operator(target,id);b=operator(target,id)
                await login(h,first,a);await login(h,second,b)
                results=await asyncio.gather(assign(h,first,id,1,a['case_membership']),assign(h,second,id,1,b['case_membership']))
                assert sorted(r.status_code for r in results)==[200,409],[r.text for r in results]
                result=await first.get('/api/v1/staff/cases/'+id);assert result.json()['version']==2
                assert row(target,'SELECT count(*) FROM {s}.case_history WHERE case_id=:id',id=UUID(id))[0]==2
                winner=next(r.json()['assigned_membership_id'] for r in results if r.status_code==200)
                loser=second if winner==str(a['case_membership']) else first
                person=b if loser is second else a
                forbidden=await assign(h,loser,id,2,person['case_membership'])
                assert forbidden.status_code==403,forbidden.text
    run(scenario())


def test_close_vs_status_race_and_stale_reopen_key_conflict(target):
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as a,h.client() as b:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,a,person);await login(h,b,person)
                assert (await assign(h,a,id,1,person['case_membership'])).status_code==200
                results=await asyncio.gather(status(h,a,id,2,'closed',close_reason='customer_cancelled',resolution='Pelanggan membatalkan.'),status(h,b,id,2,'awaiting_customer'))
                assert sorted(r.status_code for r in results)==[200,409],[r.text for r in results]
                current=(await a.get('/api/v1/staff/cases/'+id)).json()
                if current['status']!='closed':
                    current=(await status(h,a,id,3,'closed',close_reason='resolved',resolution='Penanganan selesai.')).json()
                command=dict(expected_version=current['version'],intent='same_issue',message='Belum selesai.',client_request_id=str(uuid4()))
                result=await customer.post('/api/v1/cases/'+id+'/continue',headers=h.headers(customer),json=command)
                assert result.status_code==200,result.text
                conflict=await customer.post('/api/v1/cases/'+id+'/continue',headers=h.headers(customer),json={**command,'message':'Isi berbeda dengan key sama.'})
                assert conflict.status_code==409 and conflict.json()['error']['code']=='IDEMPOTENCY_CONFLICT'
                stale=await status(h,a,id,current['version'],'closed',close_reason='resolved',resolution='Write lama.')
                assert stale.status_code==409
    run(scenario())


def test_customer_foreign_tenant_admin_and_unverified_cannot_mutate(target):
    from test_conversations import verified
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as stranger,h.client('b.example.test') as other,h.client() as anon:
                id=await new_case(h,sender,customer);await verified(h,sender,stranger);await verified(h,sender,other);await h.boot(anon)
                for c in (stranger,other,anon):
                    result=await c.get('/api/v1/cases/'+id);assert result.status_code==404,result.text
                for c in (customer,anon):
                    result=await status(h,c,id,1,'closed',close_reason='resolved',resolution='Bukan kewenangan pelanggan.')
                    assert result.status_code==401,result.text
                command=dict(expected_version=1,intent='same_issue',message='Lanjut.',client_request_id=str(uuid4()))
                assert (await anon.post('/api/v1/cases/'+id+'/continue',headers=h.headers(anon),json=command)).status_code==401
                assert (await stranger.post('/api/v1/cases/'+id+'/continue',headers=h.headers(stranger),json=command)).status_code==404
    run(scenario())


def test_missing_csrf_invalid_transition_and_closure_fields_leave_case_unchanged(target):
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,staff,person)
                result=await staff.patch('/api/v1/staff/cases/'+id+'/assignment',headers={'Origin':'https://a.example.test'},json=dict(expected_version=1,assignee_id=str(person['case_membership'])))
                assert result.status_code==403,result.text
                assert (await status(h,staff,id,1,'awaiting_customer')).status_code==409
                assert (await status(h,staff,id,1,'closed')).status_code==422
                assert (await status(h,staff,id,1,'closed',close_reason='no_response',resolution='Pasif.')).status_code==422
                assert (await assign(h,staff,id,1,uuid4())).status_code==422
                assert row(target,'SELECT status,version FROM {s}.cases WHERE id=:id',id=UUID(id))==('new',1)
    run(scenario())


def test_fault_before_history_rolls_back_status_and_retry_succeeds(target,monkeypatch):
    from airport_assistant.infrastructure.database.case_lifecycle import LifecycleSQL
    original=LifecycleSQL.history
    async def fail(*args,**kwargs):raise RuntimeError('Controlled lifecycle fault')
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,staff,person)
                monkeypatch.setattr(LifecycleSQL,'history',fail)
                failed=await assign(h,staff,id,1,person['case_membership'])
                assert failed.status_code==500,failed.text
                assert 'Controlled' not in failed.text
                assert row(target,'SELECT status,version,assigned_membership_id FROM {s}.cases WHERE id=:id',id=UUID(id))==('new',1,None)
                assert row(target,'SELECT count(*) FROM {s}.case_history WHERE case_id=:id',id=UUID(id))[0]==1
                monkeypatch.setattr(LifecycleSQL,'history',original)
                result=await assign(h,staff,id,1,person['case_membership']);assert result.status_code==200,result.text
    run(scenario())


def test_membership_revocation_waits_for_atomic_update_then_next_request_denies(target,monkeypatch):
    from airport_assistant.infrastructure.database.case_lifecycle import LifecycleSQL
    original=LifecycleSQL.history;revocation=None
    async def scenario():
        nonlocal revocation
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,staff,person)
                async def hook(tx,*args,**kwargs):
                    nonlocal revocation
                    revocation=asyncio.create_task(asyncio.to_thread(change,target,'UPDATE {s}.staff_memberships SET active=false WHERE id=:id',id=person['case_membership']))
                    await asyncio.sleep(.4);assert not revocation.done()
                    return await original(tx,*args,**kwargs)
                monkeypatch.setattr(LifecycleSQL,'history',hook)
                result=await assign(h,staff,id,1,person['case_membership']);assert result.status_code==200,result.text
                await asyncio.wait_for(revocation,10)
                result=await staff.get('/api/v1/staff/cases/'+id);assert result.status_code==404,result.text
    run(scenario())


def test_runtime_cannot_update_without_history_or_forge_future_history(target):
    from test_drafts import actor
    from airport_assistant.infrastructure.database.connections import DatabaseError
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,staff,person)
                identity=await actor(h,staff)
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction(identity.database_context) as c:
                        await c.execute(f'UPDATE "{target["schema"]}".cases SET next_action=%s,version=version+1 WHERE id=%s',('No history',UUID(id)))
                assert error.value.sqlstate=='23514'
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction(identity.database_context) as c:
                        await c.execute(f'''INSERT INTO "{target['schema']}".case_history(tenant_id,case_id,actor_principal_id,event_type,previous_version,resulting_version,from_status,to_status,next_actor,next_action)
                          VALUES(%s,%s,%s,'status_changed',9,10,'new','new','staff','Forged')''',(target['a'],UUID(id),person['principal']))
                assert error.value.sqlstate=='23514'
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction(identity.database_context) as c:
                        await c.execute(f'UPDATE "{target["schema"]}".cases SET next_action=%s,version=version+1 WHERE id=%s',('Wrong event label',UUID(id)))
                        await c.execute(f'''INSERT INTO "{target['schema']}".case_history(tenant_id,case_id,actor_principal_id,event_type,previous_version,resulting_version,from_status,to_status,next_actor,next_action)
                          VALUES(%s,%s,%s,'assigned',1,2,'new','new','staff','Wrong event label')''',(target['a'],UUID(id),person['principal']))
                assert error.value.sqlstate=='23514'
                async with h.db.transaction(identity.database_context) as c:
                    for table in ('action_consents','idempotency_records','outbox_jobs'):
                        assert (await(await c.execute(f'SELECT count(*) FROM "{target["schema"]}".{table}')).fetchone())[0]==0
                assert row(target,'SELECT version FROM {s}.cases WHERE id=:id',id=UUID(id))[0]==1
    run(scenario())


def test_customer_cannot_forge_identity_or_future_created_history_or_audit(target):
    from test_drafts import actor
    from airport_assistant.infrastructure.database.connections import DatabaseError
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer:
                id=await new_case(h,sender,customer);identity=await actor(h,customer)
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction(identity.database_context) as c:
                        await c.execute(f'UPDATE "{target["schema"]}".cases SET owner_principal_id=owner_principal_id WHERE id=%s',(UUID(id),))
                assert error.value.sqlstate=='42501'
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction(identity.database_context) as c:
                        await c.execute(f'INSERT INTO "{target["schema"]}".cases(tenant_id,id,status) VALUES(%s,%s,%s)',(target['a'],uuid4(),'closed'))
                assert error.value.sqlstate=='42501'
                for event,previous,resulting in (('identity_verified',1,2),('created',9,10)):
                    with pytest.raises(DatabaseError) as error:
                        async with h.db.transaction(identity.database_context) as c:
                            await c.execute(f'INSERT INTO "{target["schema"]}".case_history(tenant_id,case_id,actor_principal_id,event_type,previous_version,resulting_version) VALUES(%s,%s,%s,%s,%s,%s)',
                                (target['a'],UUID(id),identity.session.principal_id,event,previous,resulting))
                    assert error.value.sqlstate in ('23514','42501')
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction(identity.database_context) as c:
                        await c.execute(f'''INSERT INTO "{target['schema']}".audit_events(tenant_id,case_id,action_draft_id,event_type,actor_principal_id)
                          SELECT tenant_id,id,origin_draft_id,'identity_verified',%s FROM "{target['schema']}".cases WHERE id=%s''',(identity.session.principal_id,UUID(id)))
                assert error.value.sqlstate=='42501'
                assert row(target,'SELECT count(*) FROM {s}.case_history WHERE case_id=:id',id=UUID(id))[0]==1
    run(scenario())


def test_auth_owner_has_row_lock_but_cannot_rewrite_principal_id(target):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    person=seed(target)
    with target['engine'].begin() as c:
        c.exec_driver_sql(f'SET LOCAL ROLE "{target["owner"]}"')
        assert c.execute(text(f'SELECT id FROM "{target["schema"]}".principals WHERE id=:id FOR SHARE'),dict(id=person['principal'])).scalar_one()==person['principal']
    with pytest.raises(DBAPIError) as error:
        with target['engine'].begin() as c:
            c.exec_driver_sql(f'SET LOCAL ROLE "{target["owner"]}"')
            c.execute(text(f'UPDATE "{target["schema"]}".principals SET id=id WHERE id=:id'),dict(id=person['principal']))
    assert error.value.orig.sqlstate=='42501'


def test_get_returns_history_prefix_matching_captured_case_version(target,monkeypatch):
    from airport_assistant.infrastructure.database.case_lifecycle import LifecycleSQL
    original=LifecycleSQL.public_case;captured=asyncio.Event();resume=asyncio.Event()
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,staff,person)
                async def pause(tx,case):
                    if tx._context.actor_kind=='customer' and case['version']==1:
                        captured.set();await asyncio.wait_for(resume.wait(),10)
                    return await original(tx,case)
                monkeypatch.setattr(LifecycleSQL,'public_case',pause)
                pending=asyncio.create_task(customer.get('/api/v1/cases/'+id));await asyncio.wait_for(captured.wait(),10)
                result=await assign(h,staff,id,1,person['case_membership']);assert result.status_code==200,result.text
                resume.set();result=await pending;assert result.status_code==200,result.text
                old=result.json();assert old['version']==1 and max(e['resulting_version'] for e in old['history'])==1
                current=(await customer.get('/api/v1/cases/'+id)).json()
                assert current['version']==2 and max(e['resulting_version'] for e in current['history'])==2
    run(scenario())


def test_anonymous_case_adoption_and_case02_commit_receipts_still_work(target):
    from test_case_commit import test_anonymous_general_case_adopts_with_history_but_without_email_opt_in,test_confirm_builds_complete_graph_and_status_recovers_lost_response
    test_anonymous_general_case_adopts_with_history_but_without_email_opt_in(target)
    test_confirm_builds_complete_graph_and_status_recovers_lost_response(target)


def test_staff_expiry_during_update_rolls_back_and_returns401(target,monkeypatch):
    from test_drafts import actor
    from airport_assistant.infrastructure.database.case_lifecycle import LifecycleSQL
    original=LifecycleSQL.history
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,staff,person)
                identity=await actor(h,staff)
                change(target,"UPDATE {s}.sessions SET expires_at=clock_timestamp()+interval '5 seconds' WHERE id=:id",id=identity.session.id)
                async def pause(tx,*args,**kwargs):
                    await asyncio.sleep(5.1);return await original(tx,*args,**kwargs)
                monkeypatch.setattr(LifecycleSQL,'history',pause)
                result=await assign(h,staff,id,1,person['case_membership'])
                assert result.status_code==401,result.text
                assert row(target,'SELECT status,version,assigned_membership_id FROM {s}.cases WHERE id=:id',id=UUID(id))==('new',1,None)
    run(scenario())


def test_duplicate_closure_requires_authorized_same_owner_case(target):
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as customer,h.client() as othercustomer,h.client() as staff:
                id=await new_case(h,sender,customer);foreign=await new_case(h,sender,othercustomer);person=operator(target,id);await login(h,staff,person)
                result=await status(h,staff,id,1,'closed',close_reason='duplicate',resolution='Duplikat.',related_case_id=foreign)
                assert result.status_code==404,result.text
                from test_conversations import create
                from test_drafts import create_draft,payload
                email=row(target,'SELECT email_address FROM {s}.contacts WHERE principal_id=(SELECT owner_principal_id FROM {s}.cases WHERE id=:id)',id=UUID(id))[0]
                conv=await create(h,customer);draft=await create_draft(h,customer,conv,payload(contact_email=email))
                result=await confirm(h,customer,draft,str(uuid4()));assert result.status_code==201,result.text
                related=result.json()['case_id']
                result=await status(h,staff,id,1,'closed',close_reason='duplicate',resolution='Pengajuan sama tercatat dua kali.',related_case_id=related)
                assert result.status_code==200,result.text
                assert result.json()['close_reason']=='duplicate'
                assert result.json()['related_case_ref']==row(target,'SELECT public_reference FROM {s}.cases WHERE id=:id',id=UUID(related))[0]
    run(scenario())


def test_customer_cannot_reuse_private_adoption_scope_to_read_foreign_assignee(target):
    from test_conversations import verified
    from test_drafts import actor
    async def scenario():
        async with lifecycle(target) as (h,sender):
            async with h.client() as owner,h.client() as stranger,h.client() as staff:
                id=await new_case(h,sender,owner);await verified(h,sender,stranger)
                person=operator(target,id);await login(h,staff,person)
                result=await assign(h,staff,id,1,person['case_membership']);assert result.status_code==200,result.text
                metadata=row(target,'SELECT created_session_id,owner_principal_id,organization_id,queue_id FROM {s}.cases WHERE id=:id',id=UUID(id))
                other=await actor(h,stranger)
                async with h.db.transaction(other.database_context) as c:
                    for key,value in (('tenant',target['a']),('session',metadata[0]),('principal',metadata[1])):
                        await c.execute("SELECT set_config(%s,%s,true)",('app.adoption_'+key,str(value)))
                    assert (await(await c.execute(f'SELECT "{target["schema"]}".case_current_assignee(%s)',(UUID(id),))).fetchone())[0] is None
                    assert (await(await c.execute(f'SELECT "{target["schema"]}".case_target_membership(%s,%s,%s)',(person['case_membership'],metadata[2],metadata[3]))).fetchone())[0] is None
                own=await actor(h,owner)
                async with h.db.transaction(own.database_context) as c:
                    assert (await(await c.execute(f'SELECT "{target["schema"]}".case_current_assignee(%s)',(UUID(id),))).fetchone())[0]==person['case_membership']
    run(scenario())
