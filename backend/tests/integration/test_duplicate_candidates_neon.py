"""Exact duplicate lookup on isolated Neon schema/roles, no external providers."""
import os
from uuid import UUID, uuid4

import pytest

from test_staff_queue_neon import target as queue_target, capabilities_target, lifecycle_target, commit_target, adoption_target, draft_target, conversation_target, auth_target, app, bulk_graphs
from test_staff_auth import run, row, change, insert
from test_case_lifecycle import new_case, operator, login, status
from test_case_commit import prepared, confirm
from test_conversations import create as create_conversation
from test_drafts import actor, create_draft, payload
from test_anonymous_adoption import issue, verify

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in isolated duplicate candidate Neon tests.')


@pytest.fixture(scope='module')
def target(queue_target):
    from pathlib import Path
    from airport_assistant.database_config import MigrationDatabaseSettings,load_database_settings
    from airport_assistant.infrastructure.database.migrations import migrate
    t=queue_target
    direct=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2): assert migrate(direct.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0023_adoption_guard')=='0023_adoption_guard'
    return t


async def search(h,c,id,mid,reference,version=1,**kwargs):
    return await c.post('/api/v1/staff/cases/'+id+'/duplicate-candidates/search',headers=kwargs.pop('headers',h.headers(c)),json=kwargs.pop('body',dict(membership_id=str(mid),expected_version=version,public_reference=reference)),**kwargs)


async def same_owner(h,c,contact=None):
    report=payload(contact_email=contact) if contact else payload(category='information',service_type='general',booking_reference=None,contact_email=None)
    draft=await create_draft(h,c,await create_conversation(h,c),report,action='create_case' if contact else 'request_handoff')
    result=await confirm(h,c,draft);assert result.status_code==201,result.text
    return result.json()['case_id'],result.json()['result_ref']


def business_counts(t):
    tables=('cases','action_drafts','messages','action_consents','case_history','audit_events','outbox_jobs','idempotency_records')
    return tuple(row(t,'SELECT count(*) FROM {s}.'+name)[0] for name in tables)


def test_scoped_exact_lookup_http_privacy_no_writes_and_revalidation(target):
    async def scenario():
        async with app(target) as (h,sender):
            async with h.client() as customer,h.client() as other,h.client('b.example.test') as foreign,h.client() as staff,h.client() as admin:
                source=await new_case(h,sender,customer)
                contact=row(target,'SELECT ct.email_address FROM {s}.cases c JOIN {s}.contacts ct ON(ct.tenant_id,ct.id)=(c.tenant_id,c.owner_contact_id) WHERE c.id=:id',id=UUID(source))[0]
                # A second verified contact on the same owner is not another owner.
                owner=row(target,'SELECT owner_principal_id FROM {s}.cases WHERE id=:id',id=UUID(source))[0]
                alternate='alternate'+uuid4().hex+'@example.com'
                with target['engine'].begin() as c:
                    c.execute(__import__('sqlalchemy').text('INSERT INTO "'+target['schema']+'".contacts(tenant_id,principal_id,email_address,email_lookup_key,verified_at) VALUES(:t,:p,:e,:e,clock_timestamp())'),{'t':target['a'],'p':owner,'e':alternate})
                candidate,reference=await same_owner(h,customer,alternate)
                stranger=await new_case(h,sender,other);foreign_id=await new_case(h,sender,foreign)
                principal=operator(target,source);administrator=operator(target,source,('admin',))
                await login(h,staff,principal);await login(h,admin,administrator)
                mid=principal['case_membership'];source_ref='CASE-'+source.replace('-','')
                # operator() creates another valid membership in another organization.
                # Route one real confirmed graph there; never disable graph constraints.
                disabled=[]
                from sqlalchemy import text
                with target['engine'].begin() as c:
                    disabled=c.execute(text('UPDATE "'+target['schema']+'".service_queues SET active=false WHERE tenant_id=:t AND service_type=\'parking\' AND active AND id<>:q RETURNING id'),{'t':target['a'],'q':principal['queue']}).all()
                try:
                    other_org,other_org_ref=await same_owner(h,customer,alternate)
                finally:
                    for changed in disabled: change(target,'UPDATE {s}.service_queues SET active=true WHERE id=:id',id=changed[0])
                assert row(target,'SELECT organization_id FROM {s}.cases WHERE id=:id',id=UUID(other_org))[0]==principal['org']
                assert (await staff.get('/api/v1/staff/cases/'+other_org)).status_code==200
                before=business_counts(target)
                good=await search(h,staff,source,mid,reference);assert good.status_code==200,good.text
                assert good.headers['cache-control']=='no-store'
                assert good.json()==dict(case_id=source,case_version=1,membership_id=str(mid),membership_version=1,items=[dict(case_id=candidate,public_reference=reference)])
                for ref in (source_ref,'CASE-'+uuid4().hex,'CASE-'+stranger.replace('-',''),'CASE-'+foreign_id.replace('-',''),other_org_ref):
                    response=await search(h,staff,source,mid,ref);assert response.status_code==200 and response.json()['items']==[],response.text
                assert business_counts(target)==before
                assert (await search(h,staff,source,principal['membership'],reference,version=99)).status_code==404
                assert (await search(h,admin,source,administrator['case_membership'],reference)).status_code==404
                assert (await search(h,customer,source,mid,reference)).status_code==401
                assert (await search(h,staff,source,mid,reference,version=99)).status_code==409
                no_csrf=await search(h,staff,source,mid,reference,headers={'Origin':'https://a.example.test'});assert no_csrf.status_code==403
                bad_origin=await search(h,staff,source,mid,reference,headers={**h.headers(staff),'Origin':'https://elsewhere.example.test'});assert bad_origin.status_code==403
                base=dict(membership_id=str(mid),expected_version=1,public_reference=reference)
                for body in ({**base,'public_reference':'CASE-%'},{**base,'expected_version':True},{**base,'tenant_id':str(target['b'])}):
                    invalid=await search(h,staff,source,mid,reference,body=body);assert invalid.status_code==422 and invalid.headers['cache-control']=='no-store'
                query=await search(h,staff,source,mid,reference,params={'membership_id':str(mid)});assert query.status_code==400
                spoof=await search(h,staff,source,mid,reference,headers={**h.headers(staff),'x-tenant-id':str(target['b'])});assert spoof.status_code==400
                # Closed targets remain valid: lookup must agree with existing close policy.
                closed=await status(h,staff,candidate,1,'closed',close_reason='resolved',resolution='Synthetic target result');assert closed.status_code==200,closed.text
                assert (await search(h,staff,source,mid,reference)).json()['items'][0]['case_id']==candidate
                # Another queue in the same organization requires its own grant.
                org,queue=row(target,'SELECT organization_id,queue_id FROM {s}.cases WHERE id=:id',id=UUID(source))
                with target['engine'].begin() as c:
                    q2=insert(c,target['schema'],'service_queues',tenant_id=target['a'],organization_id=org,service_type='parking',name='Other permitted queue')
                bulk_graphs(target,source,await actor(h,customer),q2,count=2)
                other_target,other_ref=row(target,'SELECT id,public_reference FROM {s}.cases WHERE tenant_id=:t AND queue_id=:q',t=target['a'],q=q2)
                assert (await staff.get('/api/v1/staff/cases/'+str(other_target))).status_code==404
                assert (await search(h,staff,source,mid,other_ref)).json()['items']==[]
                with target['engine'].begin() as c: insert(c,target['schema'],'membership_queues',tenant_id=target['a'],membership_id=mid,organization_id=org,queue_id=q2)
                assert (await search(h,staff,source,mid,other_ref)).json()['items']==[dict(case_id=str(other_target),public_reference=other_ref)]
                # Revoke all actor grants for target queue after a positive lookup.
                change(target,'DELETE FROM {s}.membership_queues WHERE membership_id=:m AND queue_id=:q',m=mid,q=q2)
                assert (await search(h,staff,source,mid,other_ref)).json()['items']==[]
                denied=await status(h,staff,source,1,'closed',close_reason='duplicate',resolution='Synthetic duplicate',related_case_id=str(other_target))
                assert denied.status_code==404 and row(target,'SELECT status,version FROM {s}.cases WHERE id=:id',id=UUID(source))==('new',1)
                final=await status(h,staff,source,1,'closed',close_reason='duplicate',resolution='Synthetic duplicate',related_case_id=candidate);assert final.status_code==200,final.text
                assert final.json()['related_case_ref']==reference
                assert (await search(h,staff,source,mid,reference,version=2)).json()['error']['code']=='CASE_CLOSED'
    run(scenario())


def test_anonymous_owner_pair_and_real_adoption_invalidate_old_basis(target):
    async def scenario():
        async with app(target) as (h,sender):
            async with h.client() as anon,h.client() as other,h.client() as staff:
                original_verify=h.auth.verify_challenge
                async def diagnostic_verify(*args,**kwargs):
                    try: return await original_verify(*args,**kwargs)
                    except Exception as error:
                        import json,re
                        details=[];current=error
                        while current is not None and len(details)<4:
                            diag=getattr(current,'diag',None)
                            primary=getattr(diag,'message_primary',None) or ''
                            safe_primary=primary if re.fullmatch(r'permission denied for (?:table|column|schema) [\w.]+',primary) else None
                            details.append(dict(type=type(current).__name__,sqlstate=getattr(current,'sqlstate',None),table=getattr(diag,'table_name',None),constraint=getattr(diag,'constraint_name',None),permission=safe_primary,functions=re.findall(r'PL/pgSQL function ([\w.() ,]+) line (\d+)',getattr(diag,'context',None) or '')))
                            current=current.__context__
                        print('SANITIZED_ADOPTION_DIAGNOSTIC '+json.dumps(details));raise
                h.auth.verify_challenge=diagnostic_verify
                draft=await prepared(h,sender,anon,anonymous=True);r=await confirm(h,anon,draft);assert r.status_code==201,r.text
                source=r.json()['case_id'];candidate,reference=await same_owner(h,anon)
                other_draft=await prepared(h,sender,other,anonymous=True);other_result=await confirm(h,other,other_draft);assert other_result.status_code==201
                principal=operator(target,source);await login(h,staff,principal);mid=principal['case_membership']
                assert (await search(h,staff,source,mid,reference)).json()['items']==[dict(case_id=candidate,public_reference=reference)]
                negative=await search(h,staff,source,mid,other_result.json()['result_ref']);assert negative.status_code==200 and negative.json()['items']==[]
                # Normal OTP adoption changes both same-session cases, no operator owner rewrite.
                # The guard fix must not grant this private auth role email-column access.
                assert row(target,"SELECT has_column_privilege(:role,'\""+target['schema']+"\".case_history','email_source_id','SELECT')",role=target['owner'])[0] is False
                assert row(target,"SELECT has_column_privilege(:role,'\""+target['schema']+"\".cases','owner_principal_id','UPDATE')",role=target['role'])[0] is False
                challenge,code=await issue(h,sender,anon);upgraded=await verify(h,anon,challenge,code);assert upgraded.status_code==200,upgraded.text
                stale=await search(h,staff,source,mid,reference);assert stale.status_code==409
                stale_close=await status(h,staff,source,1,'closed',close_reason='duplicate',resolution='Stale lookup must not authorize closure.',related_case_id=candidate)
                assert stale_close.status_code==409 and row(target,'SELECT status,version FROM {s}.cases WHERE id=:id',id=UUID(source))==('new',2)
                assert (await search(h,staff,source,mid,reference,version=2)).json()['items']==[dict(case_id=candidate,public_reference=reference)]
    run(scenario())
