"""API-FE-03 on isolated Neon schema/roles; no external sender or model."""
import hashlib
import os
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from test_staff_auth import target as auth_target,run,row,change,seed,insert
from test_conversations import target as conversation_target
from test_drafts import target as draft_target,actor
from test_anonymous_adoption import target as adoption_target
from test_case_commit import target as commit_target,prepared,confirm
from test_case_lifecycle import target as lifecycle_target,lifecycle,operator,login,new_case,assign,status
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.modules.cases.capabilities import StaffCapabilities,AssigneeCursor

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in isolated staff capability Neon tests.')


@pytest.fixture(scope='module')
def target(lifecycle_target):
    t=lifecycle_target
    direct=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    suffix=hashlib.sha256(t['schema'].encode()).hexdigest()[:24]
    roles=('job_worker_'+suffix,'job_owner_'+suffix)
    try:
        for _ in range(2):
            assert migrate(direct.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0021_staff_capabilities')=='0021_staff_capabilities'
        yield t
    finally:
        # Match the existing scheduler fixture: drop only this random test schema
        # before its schema-bound owners. DROP OWNED needs unavailable privileges.
        with t['engine'].begin() as c:
            assert t['schema'].startswith('auth03_test_')
            c.exec_driver_sql('DROP SCHEMA IF EXISTS "'+t['schema']+'" CASCADE')
            for role in roles:
                assert role.endswith(suffix)
                exists=c.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:r'),{'r':role}).scalar_one_or_none()
                if exists:
                    c.exec_driver_sql('DROP ROLE "'+role+'"')


@asynccontextmanager
async def app(target):
    async with lifecycle(target) as (h,sender):
        h.app.state.services=replace(h.app.state.services,staff_capabilities=StaffCapabilities(PostgresCaseLifecycle(h.db,target['schema']),AssigneeCursor(target['key'].encode())))
        yield h,sender


async def cap(c,id,mid,**params):
    return await c.get('/api/v1/staff/cases/'+id+'/capabilities',params=dict(membership_id=str(mid),**params))


async def directory(c,id,mid,**params):
    return await c.get('/api/v1/staff/cases/'+id+'/eligible-assignees',params=dict(membership_id=str(mid),**params))


def test_selected_scope_labels_roles_errors_and_legacy_detail(target):
    async def scenario():
        async with app(target) as (h,sender):
            async with h.client() as customer,h.client() as cs,h.client() as admin:
                id=await new_case(h,sender,customer);person=operator(target,id);administrator=operator(target,id,('admin',))
                await login(h,cs,person);await login(h,admin,administrator)
                r=await cap(cs,id,person['case_membership']);assert r.status_code==200,r.text
                value=r.json();assert value['self_claim_membership_id']==str(person['case_membership'])
                assert value['allowed_actions']==['claim','close'] and value['actor_principal_id']==str(person['principal'])
                assert value['organization']['id']==str(row(target,'SELECT organization_id FROM {s}.cases WHERE id=:id',id=UUID(id))[0])
                assert value['organization']['name'] and value['queue']['name'] and value['assignee'] is None
                assert (await cap(cs,id,person['membership'])).status_code==404
                assert (await cap(admin,id,administrator['case_membership'])).status_code==404
                assert (await directory(cs,id,person['case_membership'])).status_code==403
                assert (await cs.get('/api/v1/staff/cases/'+id)).status_code==200
                assert (await cs.get('/api/v1/staff/cases/'+id,params={'membership_id':str(person['membership'])})).status_code==404
                for tail,code in [('?membership_id=bad',422),('?membership_id='+str(person['case_membership'])+'&view=mine',400),('?membership_id='+str(person['case_membership'])+'&membership_id='+str(person['case_membership']),400)]:
                    failure=await cs.get('/api/v1/staff/cases/'+id+'/capabilities'+tail);assert failure.status_code==code and failure.headers['cache-control']=='no-store'
                r=await cap(customer,id,person['case_membership']);assert r.status_code==401 and r.headers['cache-control']=='no-store'
                assert not any(k in str(value) for k in ('email_address','password_hash','csrf','token_hash'))
    run(scenario())


def test_status_assignment_capability_parity_and_case_version_race(target):
    async def scenario():
        async with app(target) as (h,sender):
            async with h.client() as customer,h.client() as cs,h.client() as supervisor:
                id=await new_case(h,sender,customer);person=operator(target,id);boss=operator(target,id,('supervisor',));await login(h,cs,person);await login(h,supervisor,boss)
                first=(await cap(supervisor,id,boss['case_membership'])).json();assert first['self_claim_membership_id'] is None and 'assign' in first['allowed_actions']
                assert (await assign(h,supervisor,id,1,person['case_membership'])).json()['status']=='new'
                assert (await assign(h,cs,id,1,person['case_membership'])).status_code==409
                current=(await cap(cs,id,person['case_membership'])).json();assert current['allowed_statuses']==['in_progress']
                assert (await status(h,cs,id,2,'in_progress')).status_code==200
                # Any queue-authorized CS may update an assigned case, per existing policy.
                other=operator(target,id)
                async with h.client() as peer:
                    await login(h,peer,other)
                    assert 'update_status' in (await cap(peer,id,other['case_membership'])).json()['allowed_actions']
                    assert (await status(h,peer,id,3,'awaiting_customer')).status_code==200
                change(target,'UPDATE {s}.staff_credentials SET active=false WHERE principal_id=:p',p=person['principal'])
                stale=(await cap(supervisor,id,boss['case_membership'])).json();assert stale['assignee'] is None and stale['allowed_statuses']==[]
                closed=await status(h,supervisor,id,4,'closed',close_reason='resolved',resolution='Synthetic outcome');assert closed.status_code==200,closed.text
                final=(await cap(supervisor,id,boss['case_membership'])).json();assert final['allowed_actions']==[] and final['allowed_close_reasons']==[]
                assert (await directory(supervisor,id,boss['case_membership'],cursor='invalid')).status_code==400
                assert (await directory(supervisor,id,boss['case_membership'])).json()['items']==[]
    run(scenario())


@pytest.mark.parametrize('revoke',['membership','credential','queue','organization','role','contact'])
def test_revocation_after_snapshot_blocks_metadata_and_old_assignment(target,revoke):
    async def scenario():
        async with app(target) as (h,sender):
            async with h.client() as customer,h.client() as cs:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,cs,person)
                before=await actor(h,cs);assert (await cap(cs,id,person['case_membership'])).status_code==200
                org,queue=row(target,'SELECT organization_id,queue_id FROM {s}.cases WHERE id=:id',id=UUID(id))
                sql,params={
                    'membership':('UPDATE {s}.staff_memberships SET active=false WHERE id=:id',{'id':person['case_membership']}),
                    'credential':('UPDATE {s}.staff_credentials SET active=false WHERE principal_id=:id',{'id':person['principal']}),
                    'queue':('UPDATE {s}.service_queues SET active=false WHERE id=:id',{'id':queue}),
                    'organization':('UPDATE {s}.organizations SET active=false WHERE id=:id',{'id':org}),
                    'role':("DELETE FROM {s}.membership_roles WHERE membership_id=:id",{'id':person['case_membership']}),
                    'contact':('UPDATE {s}.contacts SET verified_at=NULL WHERE id=:id',{'id':person['contact']}),
                }[revoke]
                try:
                    change(target,sql,**params)
                    # Exercise retained pre-revocation AccessIdentity as well as fresh HTTP dependency.
                    from airport_assistant.modules.identity.auth import AuthError
                    with pytest.raises(AuthError):await h.app.state.services.staff_capabilities.get(before,UUID(id),person['case_membership'])
                    assert (await cap(cs,id,person['case_membership'])).status_code in (401,404)
                    assert (await assign(h,cs,id,1,person['case_membership'])).status_code in (401,404)
                finally:
                    if revoke=='queue':change(target,'UPDATE {s}.service_queues SET active=true WHERE id=:id',id=queue)
                    if revoke=='organization':change(target,'UPDATE {s}.organizations SET active=true WHERE id=:id',id=org)
    run(scenario())


def test_supervisor_directory_pages_more_than_100_and_cursor_isolation(target):
    async def scenario():
        async with app(target) as (h,sender):
            async with h.client() as customer,h.client() as supervisor:
                id=await new_case(h,sender,customer);boss=operator(target,id,('supervisor',));await login(h,supervisor,boss)
                org,queue=row(target,'SELECT organization_id,queue_id FROM {s}.cases WHERE id=:id',id=UUID(id))
                # Bulk provision only disposable test-schema actors; reuse a valid test hash.
                with target['engine'].begin() as c:
                    s='"'+target['schema']+'"'
                    c.execute(text(f'''WITH p AS (INSERT INTO {s}.principals(tenant_id,display_name) SELECT :t,'Same display label' FROM generate_series(1,105) RETURNING id),
                      ct AS (INSERT INTO {s}.contacts(tenant_id,principal_id,email_address,email_lookup_key,verified_at) SELECT :t,id,id::text||'@example.com',id::text||'@example.com',clock_timestamp() FROM p RETURNING id,principal_id),
                      m AS (INSERT INTO {s}.staff_memberships(tenant_id,principal_id,organization_id) SELECT :t,id,:o FROM p RETURNING id),
                      r AS (INSERT INTO {s}.membership_roles(tenant_id,membership_id,role) SELECT :t,id,'cs' FROM m),
                      mq AS (INSERT INTO {s}.membership_queues(tenant_id,membership_id,organization_id,queue_id) SELECT :t,id,:o,:q FROM m)
                      INSERT INTO {s}.staff_credentials(tenant_id,principal_id,contact_id,password_hash)
                      SELECT :t,ct.principal_id,ct.id,sc.password_hash FROM ct CROSS JOIN {s}.staff_credentials sc WHERE sc.tenant_id=:t AND sc.principal_id=:p'''),{'t':target['a'],'o':org,'q':queue,'p':boss['principal']})
                first=await directory(supervisor,id,boss['case_membership'],limit=25);assert first.status_code==200,first.text
                data=first.json();cursor=data['next_cursor'];assert data['has_more'] and cursor
                seen=[v['membership_id'] for v in data['items']]
                while data['has_more']:
                    response=await directory(supervisor,id,boss['case_membership'],limit=25,cursor=data['next_cursor']);assert response.status_code==200,response.text
                    data=response.json();seen.extend(v['membership_id'] for v in data['items'])
                assert len(seen)>=106 and len(seen)==len(set(seen)) and seen==sorted(seen)
                assert data['next_cursor'] is None
                assert (await directory(supervisor,id,boss['case_membership'],limit=26,cursor=cursor)).status_code==400
                assert (await directory(supervisor,id,boss['case_membership'],limit=25,cursor=cursor+'x')).status_code==400
                # New session of same principal must not reuse cursor.
                async with h.client() as other:
                    await login(h,other,boss)
                    assert (await directory(other,id,boss['case_membership'],limit=25,cursor=cursor)).status_code==400
                # Case version changes invalidate an old directory page.
                assert (await assign(h,supervisor,id,1,boss['case_membership'])).status_code==200
                assert (await directory(supervisor,id,boss['case_membership'],limit=25,cursor=cursor)).status_code==400
    run(scenario())


def test_direct_runtime_rpc_is_constrained_and_definers_have_private_fixed_owners(target):
    async def scenario():
        async with app(target) as (h,sender):
            async with h.client() as customer,h.client() as cs:
                id=await new_case(h,sender,customer);person=operator(target,id);await login(h,cs,person)
                identity=await actor(h,cs)
                async with h.db.transaction(identity.database_context) as c:
                    s='"'+target['schema']+'"'
                    result=await(await c.execute(f'SELECT {s}.case_staff_workspace(%s,%s),{s}.case_staff_workspace(%s,%s),{s}.case_staff_assignees(%s,%s,NULL,25)',
                        (UUID(id),person['membership'],uuid4(),person['case_membership'],UUID(id),person['case_membership']))).fetchone()
                    assert result==(None,None,None)
                    # A raw staff read cannot turn directory metadata into contact/profile browsing.
                    target_person=operator(target,id)
                    result=await(await c.execute(f'SELECT display_name FROM {s}.principals WHERE tenant_id=%s AND id=%s',(target['a'],target_person['principal']))).fetchall()
                    assert result==[]
                with target['engine'].connect() as c:
                    functions=c.execute(text('''SELECT p.prosecdef,r.rolname,p.proconfig,
                        EXISTS(SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
                        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner
                        WHERE n.nspname=:s AND p.proname IN ('case_staff_workspace','case_staff_assignees')'''),{'s':target['schema']}).all()
                    assert len(functions)==2 and all(a and b==target['owner'] and d is False and config==['search_path=pg_catalog'] for a,b,config,d in functions)
                    assert c.execute(text('SELECT has_function_privilege(:r,:f,\'EXECUTE\')'),{'r':target['role'],'f':target['schema']+'.case_member_metadata(uuid,uuid,uuid)'}).scalar_one() is False
    run(scenario())


def test_active_chat_basis_claim_suppression_and_conversation_version_race(target):
    async def scenario():
        from airport_assistant.modules.conversations.handoff import ChatHandoff
        from airport_assistant.api.chat_handoff import router
        async with app(target) as (h,sender):
            h.app.state.services=replace(h.app.state.services,chat_handoff=ChatHandoff(PostgresCaseLifecycle(h.db,target['schema'])))
            h.app.include_router(router)
            async with h.client() as customer,h.client() as cs:
                draft=await prepared(h,sender,customer,anonymous=True);result=await confirm(h,customer,draft);assert result.status_code==201,result.text
                id=result.json()['case_id'];person=operator(target,id);await login(h,cs,person)
                waiting=await cap(cs,id,person['case_membership']);assert waiting.status_code==200,waiting.text
                basis=waiting.json();chat=basis['active_conversation'];assert chat['mode']=='waiting_staff'
                assert basis['self_claim_membership_id'] is None and 'claim' not in basis['allowed_actions']
                assert (await assign(h,cs,id,basis['case_version'],person['case_membership'])).status_code==422
                accepted=await cs.post('/api/v1/staff/conversations/'+chat['id']+'/accept',headers={**h.headers(cs),'Idempotency-Key':str(uuid4())},json={'expected_version':chat['version'],'expected_case_version':basis['case_version']})
                assert accepted.status_code==200,accepted.text
                human=(await cap(cs,id,person['case_membership'])).json();assert human['active_conversation']['mode']=='human' and human['case_version']>basis['case_version']
                assert human['assignee']['membership_id']==str(person['case_membership'])
                assert (await status(h,cs,id,human['case_version'],'awaiting_customer',expected_conversation_version=chat['version'])).status_code==409
                closed=await status(h,cs,id,human['case_version'],'closed',expected_conversation_version=human['active_conversation']['version'],close_reason='resolved',resolution='Synthetic closed chat')
                assert closed.status_code==200,closed.text
                final=(await cap(cs,id,person['case_membership'])).json();assert final['active_conversation'] is None and final['allowed_actions']==[]
    run(scenario())
