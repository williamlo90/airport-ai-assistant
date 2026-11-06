"""API-FE-05 isolated Neon reads over constraint-valid synthetic case graphs."""
import os
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from test_staff_capabilities_neon import target as capabilities_target,app as capabilities_app
from test_staff_capabilities_neon import lifecycle_target,commit_target,adoption_target,draft_target,conversation_target,auth_target
from test_staff_auth import run,row,change,insert
from test_drafts import actor,create_draft,payload
from test_conversations import create as create_conversation
from test_case_commit import confirm
from test_case_lifecycle import new_case,operator,login,assign,status
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.modules.cases.queue import StaffQueue,QueueCursor

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in isolated server queue pagination tests.')


@pytest.fixture(scope='module')
def target(capabilities_target):
    t=capabilities_target
    direct=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):assert migrate(direct.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0022_staff_queue')=='0022_staff_queue'
    return t


@asynccontextmanager
async def app(target):
    async with capabilities_app(target) as (h,sender):
        h.app.state.services=replace(h.app.state.services,staff_queue=StaffQueue(PostgresCaseLifecycle(h.db,target['schema']),QueueCursor(target['key'].encode())))
        yield h,sender


async def page(c,mid,**params):
    return await c.get('/api/v1/staff/queues/cases',params=dict(membership_id=str(mid),**params))


def bulk_graphs(t,template,identity,other_queue,count=105):
    """Operator seed only disposable test schema; leave every FK/trigger enabled.

    Clone a real confirmed case graph with new identities, then validate all
    deferred constraints. This is a pagination fixture, not a confirm E2E claim.
    """
    s='"'+t['schema']+'"';cid=str(UUID(template));qid=str(UUID(str(other_queue)))
    with t['engine'].begin() as c:
        for key,value in [('tenant_id',identity.session.tenant_id),('principal_id',identity.session.principal_id),('session_id',identity.session.id),('actor_kind','customer')]:
            c.execute(text('SELECT set_config(:k,:v,true)'),{'k':'app.'+key,'v':str(value)})
        c.exec_driver_sql(f'''DO $fixture$
          DECLARE oldcase jsonb; olddraft jsonb; oldmessage jsonb; oldconsent jsonb; oldhistory jsonb; oldaudit jsonb; oldjob jsonb; oldreceipt jsonb;
            nc uuid; nd uuid; nm uuid; ref text; dest uuid; i integer; BEGIN
          SELECT to_jsonb(v) INTO oldcase FROM {s}.cases v WHERE id='{cid}'::uuid;
          SELECT to_jsonb(v) INTO olddraft FROM {s}.action_drafts v WHERE id=(oldcase->>'origin_draft_id')::uuid;
          SELECT to_jsonb(v) INTO oldmessage FROM {s}.messages v WHERE id=(oldcase->>'initial_message_id')::uuid;
          SELECT to_jsonb(v) INTO oldconsent FROM {s}.action_consents v WHERE case_id='{cid}'::uuid;
          SELECT to_jsonb(v) INTO oldhistory FROM {s}.case_history v WHERE case_id='{cid}'::uuid AND event_type='created';
          SELECT to_jsonb(v) INTO oldaudit FROM {s}.audit_events v WHERE case_id='{cid}'::uuid AND event_type='case_confirmed';
          SELECT to_jsonb(v) INTO oldjob FROM {s}.outbox_jobs v WHERE case_id='{cid}'::uuid AND kind='case_created';
          SELECT to_jsonb(v) INTO oldreceipt FROM {s}.idempotency_records v WHERE result_case_id='{cid}'::uuid;
          IF oldreceipt IS NULL OR oldjob IS NULL THEN RAISE EXCEPTION 'Fixture template missing'; END IF;
          FOR i IN 1..{int(count)} LOOP
            nc:=gen_random_uuid();nd:=gen_random_uuid();nm:=gen_random_uuid();ref:='CASE-'||replace(nc::text,'-','');
            dest:=CASE WHEN mod(i,2)=0 THEN '{qid}'::uuid ELSE (oldcase->>'queue_id')::uuid END;
            INSERT INTO {s}.action_drafts SELECT (jsonb_populate_record(NULL::{s}.action_drafts,olddraft||jsonb_build_object('id',nd,'state','pending','result_case_id',NULL,'result_message_id',NULL,'result_ref',NULL,'committed_at',NULL,'destination_queue_id',dest,'destination',(olddraft->'destination')||jsonb_build_object('queue_id',dest)))).*;
            INSERT INTO {s}.cases SELECT (jsonb_populate_record(NULL::{s}.cases,oldcase||jsonb_build_object('id',nc,'public_reference',ref,'origin_draft_id',nd,'initial_message_id',nm,'queue_id',dest,'created_at','2026-01-01T00:00:00.123456+00:00'))).*;
            INSERT INTO {s}.messages SELECT (jsonb_populate_record(NULL::{s}.messages,oldmessage||jsonb_build_object('id',nm,'case_id',nc,'client_message_id',gen_random_uuid()))).*;
            UPDATE {s}.action_drafts SET state='committed',result_case_id=nc,result_message_id=nm,result_ref=ref,committed_at=(olddraft->>'committed_at')::timestamptz WHERE id=nd;
            INSERT INTO {s}.action_consents SELECT (jsonb_populate_record(NULL::{s}.action_consents,oldconsent||jsonb_build_object('id',gen_random_uuid(),'draft_id',nd,'case_id',nc))).*;
            INSERT INTO {s}.case_participants SELECT (jsonb_populate_record(NULL::{s}.case_participants,to_jsonb(v)||jsonb_build_object('id',gen_random_uuid(),'case_id',nc))).* FROM {s}.case_participants v WHERE v.case_id='{cid}'::uuid;
            INSERT INTO {s}.case_history SELECT (jsonb_populate_record(NULL::{s}.case_history,oldhistory||jsonb_build_object('id',gen_random_uuid(),'case_id',nc))).*;
            INSERT INTO {s}.audit_events SELECT (jsonb_populate_record(NULL::{s}.audit_events,oldaudit||jsonb_build_object('id',gen_random_uuid(),'case_id',nc,'action_draft_id',nd))).*;
            INSERT INTO {s}.outbox_jobs SELECT (jsonb_populate_record(NULL::{s}.outbox_jobs,oldjob||jsonb_build_object('id',gen_random_uuid(),'case_id',nc,'action_draft_id',nd,'dedupe_key','case_created:'||nd::text,'payload',jsonb_build_object('case_id',nc,'draft_id',nd)))).*;
            INSERT INTO {s}.idempotency_records SELECT (jsonb_populate_record(NULL::{s}.idempotency_records,oldreceipt||jsonb_build_object('id',gen_random_uuid(),'key',gen_random_uuid()::text,'draft_id',nd,'result_case_id',nc,'result_message_id',nm,'result_ref',ref))).*;
          END LOOP;
          END $fixture$''')
        c.exec_driver_sql('SET CONSTRAINTS ALL IMMEDIATE')


def test_more_than_100_ties_multiple_queues_views_and_live_updates(target):
    async def scenario():
        async with app(target) as (h,sender):
            async with h.client() as customer,h.client() as staff:
                id=await new_case(h,sender,customer);boss=operator(target,id,('supervisor',));await login(h,staff,boss)
                org,q1=row(target,'SELECT organization_id,queue_id FROM {s}.cases WHERE id=:id',id=UUID(id))
                with target['engine'].begin() as c:
                    q2=insert(c,target['schema'],'service_queues',tenant_id=target['a'],organization_id=org,service_type='parking',name='Second permitted queue')
                    insert(c,target['schema'],'membership_queues',tenant_id=target['a'],membership_id=boss['case_membership'],organization_id=org,queue_id=q2)
                bulk_graphs(target,id,await actor(h,customer),q2)
                with target['engine'].connect() as c:
                    expected=[str(v[0]) for v in c.execute(text('SELECT id FROM "'+target['schema']+'".cases WHERE tenant_id=:t AND organization_id=:o ORDER BY created_at,id'),{'t':target['a'],'o':org}).all()]
                first=await page(staff,boss['case_membership'],limit=20);assert first.status_code==200,first.text
                data=first.json();assert data['consistency']=='live_keyset' and len(data['queues'])==2 and data['queue_id'] is None
                assert 'total' not in data and all('customer_report' not in v for v in data['items'])
                seen=[v['id'] for v in data['items']];watermark=data['watermark'];token=data['next_cursor']
                contact=row(target,'SELECT ct.email_address FROM {s}.cases c JOIN {s}.contacts ct ON(ct.tenant_id,ct.id)=(c.tenant_id,c.owner_contact_id) WHERE c.id=:id',id=UUID(id))[0]
                # Isolate routing to the two queues under test for this one insert.
                with target['engine'].begin() as c:
                    changed=c.execute(text('UPDATE "'+target['schema']+'".service_queues SET active=false WHERE tenant_id=:t AND active AND id NOT IN(:a,:b) RETURNING id'),{'t':target['a'],'a':q1,'b':q2}).all()
                try:
                    preview=await create_draft(h,customer,await create_conversation(h,customer),payload(contact_email=contact))
                    inserted=await confirm(h,customer,preview);assert inserted.status_code==201,inserted.text
                finally:
                    with target['engine'].begin() as c:
                        for changed_queue in changed:c.execute(text('UPDATE "'+target['schema']+'".service_queues SET active=true WHERE tenant_id=:t AND id=:q'),{'t':target['a'],'q':changed_queue[0]})
                new_id=inserted.json()['case_id']
                assert row(target,'SELECT organization_id FROM {s}.cases WHERE id=:id',id=UUID(new_id))[0]==org
                # Updating an already visited case must not reorder it or cause duplicate pages.
                assert (await assign(h,staff,seen[0],1,boss['case_membership'])).status_code==200
                assert (await status(h,staff,seen[0],2,'in_progress')).status_code==200
                while data['has_more']:
                    response=await page(staff,boss['case_membership'],limit=20,cursor=data['next_cursor']);assert response.status_code==200,response.text
                    data=response.json();assert data['watermark']==watermark
                    seen.extend(v['id'] for v in data['items'])
                assert seen==expected and len(seen)>100 and len(seen)==len(set(seen))
                assert new_id not in seen  # Actual runtime insert after initial watermark.
                assert data['next_cursor'] is None
                mine=(await page(staff,boss['case_membership'],view='mine')).json();assert [v['id'] for v in mine['items']]==[seen[0]]
                unassigned=(await page(staff,boss['case_membership'],view='unassigned',limit=100)).json();assert all(v['assigned_membership_id'] is None for v in unassigned['items'])
                single=(await page(staff,boss['case_membership'],queue_id=str(q2),limit=100)).json();assert all(v['queue_id']==str(q2) for v in single['items']) and len(single['queues'])==2
                active=(await page(staff,boss['case_membership'],status='in_progress')).json();assert [v['id'] for v in active['items']]==[seen[0]]
                live=(await page(staff,boss['case_membership'],view='unassigned',limit=1)).json();old=live['items'][0]
                assert (await assign(h,staff,old['id'],old['version'],boss['case_membership'])).status_code==200
                assert (await assign(h,staff,old['id'],old['version']+1,None)).status_code==200
                next_live=(await page(staff,boss['case_membership'],view='unassigned',limit=1,cursor=live['next_cursor'])).json()
                assert next_live['items'][0]['id']!=old['id']
                assert (await page(staff,boss['case_membership'],view='unassigned',limit=1)).json()['items'][0]['id']==old['id']
                assert (await page(staff,boss['case_membership'],limit=21,cursor=token)).status_code==400
                assert (await page(staff,boss['case_membership'],view='mine',limit=20,cursor=token)).status_code==400
                # Scope changes without a member-version bump still invalidate the cursor.
                version=row(target,'SELECT version FROM {s}.staff_memberships WHERE id=:id',id=boss['case_membership'])[0]
                change(target,'DELETE FROM {s}.membership_queues WHERE membership_id=:m AND queue_id=:q',m=boss['case_membership'],q=q2)
                assert row(target,'SELECT version FROM {s}.staff_memberships WHERE id=:id',id=boss['case_membership'])[0]==version
                assert (await page(staff,boss['case_membership'],limit=20,cursor=token)).status_code==400
                assert (await page(staff,boss['case_membership'],queue_id=str(q2))).status_code==404
    run(scenario())


def test_scope_empty_legacy_query_guards_and_direct_rpc(target):
    async def scenario():
        async with app(target) as (h,sender):
            async with h.client() as customer,h.client() as cs,h.client() as admin:
                id=await new_case(h,sender,customer);person=operator(target,id);administrator=operator(target,id,('admin',));await login(h,cs,person);await login(h,admin,administrator)
                empty=await page(cs,person['membership']);assert empty.status_code==200,empty.text
                assert empty.json()['items']==[] and empty.json()['organization']['name'] and empty.json()['next_cursor'] is None
                legacy=await cs.get('/api/v1/staff/queues/cases?status=new');assert legacy.status_code==200 and set(legacy.json())=={'items'} and len(legacy.json()['items'])<=100
                assert (await page(admin,administrator['case_membership'])).status_code==404
                assert (await page(cs,administrator['case_membership'])).status_code==404
                assert (await page(cs,person['case_membership'],queue_id=str(person['queue']))).status_code==404
                for suffix,code in [('view=mine',400),('limit=2',400),('cursor=x',400),('sort=created_asc',400),('membership_id=bad',422),('membership_id='+str(person['case_membership'])+'&limit=0',422),('membership_id='+str(person['case_membership'])+'&unknown=x',400),('membership_id='+str(person['case_membership'])+'&view=all&view=mine',400)]:
                    response=await cs.get('/api/v1/staff/queues/cases?'+suffix);assert response.status_code==code and response.headers['cache-control']=='no-store'
                identity=await actor(h,cs)
                async with h.db.transaction(identity.database_context) as c:
                    s='"'+target['schema']+'"'
                    result=await(await c.execute(f'SELECT {s}.case_staff_queue_workspace(%s,NULL),{s}.case_staff_queue_workspace(%s,%s)',(administrator['case_membership'],person['case_membership'],person['queue']))).fetchone()
                    assert result==(None,None)
                with target['engine'].connect() as c:
                    acl=c.execute(text('''SELECT p.prosecdef,r.rolname,r.rolcanlogin,p.proconfig,EXISTS(SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE') FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname=:s AND p.proname='case_staff_queue_workspace' ''') ,{'s':target['schema']}).one()
                    assert acl==(True,target['owner'],False,['search_path=pg_catalog'],False)
                # Reusing a retained actor snapshot cannot read after selected queue access is revoked.
                change(target,'DELETE FROM {s}.membership_queues WHERE membership_id=:m',m=person['case_membership'])
                from airport_assistant.modules.identity.auth import AuthError
                with pytest.raises(AuthError):await h.app.state.services.staff_queue.page(identity,person['case_membership'],cursor='bad')
                assert (await page(cs,person['case_membership'])).status_code==404
    run(scenario())
