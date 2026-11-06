"""Current organization admin SQL, safe feedback and partial-index admission."""
import asyncio
import os
import secrets
from datetime import datetime,timedelta,timezone
from dataclasses import replace
from uuid import uuid4
import pytest
from sqlalchemy import text
from test_customer_ratings_neon import target as rating_target,information_target,page_target,translation_target,email_target,mail_target,graph_target,schema_target,closed
from test_private_source_graph_neon import app,run,staff,count
from test_case_translation_neon import new_public_case
from test_staff_auth import insert
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.administration import PostgresAdministration,AdministrationSQL
from airport_assistant.infrastructure.database.ratings import PostgresRatings
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.infrastructure.database.publication import PostgresPublication
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.identity.administration import StaffAdministration,MembershipEdit
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.identity.auth import AuthError,Session
from airport_assistant.modules.cases.ratings import CustomerRatings,RatingInput
from airport_assistant.modules.cases.lifecycle import CaseLifecycle
from airport_assistant.modules.conversations.publication import AIPublication
from airport_assistant.modules.drafts.models import DraftError

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_ADMIN_TESTS')!='1',reason='Opt-in isolated admin/rating qualification.')


@pytest.fixture(scope='module')
def target(rating_target):
    t=rating_target
    from airport_assistant.infrastructure.database import migrations as module
    original=module.command.upgrade
    def checked(*args,**kwargs):
        try:return original(*args,**kwargs)
        except Exception as error:
            cause=getattr(error,'orig',error);diag=getattr(cause,'diag',None)
            print('Administration migration:',type(cause).__name__,getattr(cause,'sqlstate',None),getattr(diag,'message_primary',None));raise
    module.command.upgrade=checked
    try:assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0038_staff_administration')=='0038_staff_administration'
    finally:module.command.upgrade=original
    with t.engine.connect() as c:
        assert c.exec_driver_sql(f'SELECT "{t.schema}".administration_ready()').scalar_one() is True
        assert c.exec_driver_sql(f'SELECT "{t.schema}".ratings_ready()').scalar_one() is True
    return t


def administrator(t,case):
    person=staff(t,case);member=person.memberships[0]
    with t.engine.begin() as c:
        c.execute(text(f'DELETE FROM "{t.schema}".membership_roles WHERE tenant_id=:tenant AND membership_id=:id'),dict(tenant=t.a,id=member.id))
        insert(c,t.schema,'membership_roles',tenant_id=t.a,membership_id=member.id,role='admin')
    return replace(person,memberships=(replace(member,roles=frozenset({'admin'})),))


def command(actor,target,**values):
    return MembershipEdit(**(dict(actor_membership_id=actor.memberships[0].id,actor_version=actor.memberships[0].version,
        expected_version=1,active=True,roles=['supervisor'],queue_ids=list(target.memberships[0].queue_ids))|values))


def customer(t,tenant=None,anonymous=False):
    tenant=tenant or t.a;expires=datetime.now(timezone.utc)+timedelta(hours=1)
    with t.engine.begin() as c:
        principal=None if anonymous else insert(c,t.schema,'principals',tenant_id=tenant)
        if principal is not None:
            email=uuid4().hex+'@example.com';insert(c,t.schema,'contacts',tenant_id=tenant,principal_id=principal,
                email_address=email,email_lookup_key=email,verified_at=datetime.now(timezone.utc))
        sid=insert(c,t.schema,'sessions',tenant_id=tenant,principal_id=principal,session_kind='anonymous' if anonymous else 'customer',
            token_hash=secrets.token_hex(32),csrf_secret_hash='c'*64,expires_at=expires)
    return AccessIdentity(Session(sid,tenant,principal,'anonymous' if anonymous else 'customer',expires,'c'*64))


def test_admin_directory_scope_no_self_no_case_access_and_atomic_versioned_changes(target):
    async def scenario():
        async with app(target) as h:
            cid=await new_public_case(target,h);actor=administrator(target,cid);person=staff(target,cid)
            service=StaffAdministration(PostgresAdministration(h.db,target.schema));mid=actor.memberships[0].id
            scope=await service.workspace(actor,mid);assert scope['membership_version']==1
            page=await service.directory(actor,mid,limit=1);assert page['has_more'] and len(page['items'])==1
            nextpage=await service.directory(actor,mid,after=page['next_after'],limit=100)
            assert person.session.principal_id in {__import__('uuid').UUID(row['principal_id']) for row in [*page['items'],*nextpage['items']]}
            with pytest.raises(AuthError):await service.workspace(person,person.memberships[0].id)
            with pytest.raises(AuthError):await service.workspace(actor,person.memberships[0].id)
            with pytest.raises(AuthError):await service.update(actor,actor.session.principal_id,command(actor,person))
            with pytest.raises(AuthError):await CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema),h.access).get(actor,cid,staff=True)
            with target.engine.begin() as c:
                org=insert(c,target.schema,'organizations',tenant_id=target.a,name='Another organization')
                queue=insert(c,target.schema,'service_queues',tenant_id=target.a,organization_id=org,service_type='general',name='Other queue')
                foreign=insert(c,target.schema,'principals',tenant_id=target.a)
                insert(c,target.schema,'staff_memberships',tenant_id=target.a,principal_id=foreign,organization_id=org)
            with pytest.raises(AuthError):await service.get(actor,foreign,mid)
            before=count(target,'membership_events');jobs=count(target,'outbox_jobs')
            with pytest.raises(DraftError) as e:await service.update(actor,person.session.principal_id,command(actor,person,queue_ids=[queue]))
            assert e.value.code=='VALIDATION_ERROR' and count(target,'membership_events')==before
            saved=await service.update(actor,person.session.principal_id,command(actor,person))
            assert saved['member']['roles']==['supervisor'] and saved['member']['version']==2
            assert await service.update(actor,person.session.principal_id,command(actor,person))==saved
            assert count(target,'membership_events')==before+1
            with pytest.raises(DraftError):await service.update(actor,person.session.principal_id,command(actor,person,roles=['admin']))
            await service.update(actor,person.session.principal_id,command(actor,person,expected_version=2,active=False))
            with pytest.raises(AuthError):await CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema),h.access).get(person,cid,staff=True)
            assert count(target,'membership_events')==before+2 and count(target,'outbox_jobs')==jobs
            assert not(await service.get(actor,person.session.principal_id,mid))['member']['active']
    run(scenario())


def test_admin_fault_rolls_back_audit_roles_and_queue_bindings_and_raw_dml_is_denied(target):
    class FaultSQL(AdministrationSQL):
        async def admin_change(self,*args):await super().admin_change(*args);raise RuntimeError('Controlled post-audit rollback')
    class FaultRepo(PostgresAdministration):
        def unit(self,c,context):return FaultSQL(c,self._schema,context)
    async def scenario():
        async with app(target) as h:
            cid=await new_public_case(target,h);actor=administrator(target,cid);person=staff(target,cid)
            repo=PostgresAdministration(h.db,target.schema);service=StaffAdministration(repo);mid=actor.memberships[0].id
            before=await service.get(actor,person.session.principal_id,mid);events=count(target,'membership_events')
            with pytest.raises(RuntimeError):await StaffAdministration(FaultRepo(h.db,target.schema)).update(actor,person.session.principal_id,command(actor,person))
            assert await service.get(actor,person.session.principal_id,mid)==before and count(target,'membership_events')==events
            with pytest.raises(DatabaseError):
                async with repo.transaction(actor.database_context) as tx:
                    await tx._c.execute(tx.query('UPDATE {s}.staff_memberships SET active=false WHERE id=%s'),(person.memberships[0].id,))
            with target.engine.begin() as c:
                c.exec_driver_sql(f'GRANT UPDATE(active) ON "{target.schema}".staff_memberships TO "{target.roles[0]}"')
            try:
                with pytest.raises(DraftError) as e:await service.workspace(actor,mid)
                assert e.value.code=='STAFF_UNAVAILABLE'
            finally:
                with target.engine.begin() as c:c.exec_driver_sql(f'REVOKE UPDATE(active) ON "{target.schema}".staff_memberships FROM "{target.roles[0]}"')
            assert await service.workspace(actor,mid)
    run(scenario())


def test_reciprocal_admin_deactivation_serializes_by_organization_without_deadlock(target):
    async def scenario():
        async with app(target) as a,app(target) as b:
            cid=await new_public_case(target,a);first=administrator(target,cid);second=administrator(target,cid)
            left=StaffAdministration(PostgresAdministration(a.db,target.schema));right=StaffAdministration(PostgresAdministration(b.db,target.schema))
            results=await asyncio.gather(left.update(first,second.session.principal_id,command(first,second,active=False,roles=['admin'],queue_ids=[])),
                right.update(second,first.session.principal_id,command(second,first,active=False,roles=['admin'],queue_ids=[])),return_exceptions=True)
            assert sum(isinstance(value,dict) for value in results)==1
            assert sum(isinstance(value,AuthError) for value in results)==1, [type(value).__name__ for value in results]
            with target.engine.connect() as c:
                active=c.execute(text(f'SELECT count(*) FROM "{target.schema}".staff_memberships WHERE id=ANY(:ids) AND active'),dict(ids=[first.memberships[0].id,second.memberships[0].id])).scalar_one()
                assert active==1
    run(scenario())


def test_private_rating_current_source_owner_and_real_foreign_sessions(target):
    async def scenario():
        async with app(target) as h:
            cid,_=await closed(target,h,True);ratings=CustomerRatings(PostgresRatings(h.db,target.schema),h.access)
            saved=await ratings.save(target.identity,'case',cid,RatingInput(expected_version=0,score=5,comment='Original private feedback.'))
            assert saved['rating']['version']==1
            for stranger in (customer(target),customer(target,target.b)):
                with pytest.raises(AuthError) as e:await ratings.get(stranger,'case',cid)
                assert e.value.status==404
            h.grants.denied={'booking:read'}
            with pytest.raises(AuthError):await ratings.get(target.identity,'case',cid)
            with pytest.raises(AuthError):await ratings.save(target.identity,'case',cid,RatingInput(expected_version=1,score=1))
            h.grants.denied.clear();assert await ratings.get(target.identity,'case',cid)==saved
            with target.engine.connect() as c:
                labels=c.execute(text(f'SELECT resource_security_labels FROM "{target.schema}".ratings WHERE case_id=:id'),dict(id=cid)).scalar_one()
                assert labels
    run(scenario())


def test_anonymous_conversation_requires_actual_published_assistance_and_has_one_owned_rating(target):
    async def scenario():
        async with app(target) as h:
            owner=customer(target,anonymous=True);conversation=await h.conversations.create(owner,'en')
            await h.conversations.append(owner,conversation.id,uuid4(),'Please help with airport information.')
            ratings=CustomerRatings(PostgresRatings(h.db,target.schema),h.access)
            assert (await ratings.get(owner,'conversation',conversation.id))['eligible'] is False
            with pytest.raises(DraftError):await ratings.save(owner,'conversation',conversation.id,RatingInput(expected_version=0,score=5))
            publication=AIPublication(PostgresPublication(h.db,target.schema))
            ticket=await publication.capture(owner,conversation.id)
            assert(await publication.publish(owner,ticket,'Controlled public help; not a live model call.'))['state']=='published'
            saved=await ratings.save(owner,'conversation',conversation.id,RatingInput(expected_version=0,score=3))
            assert saved['eligible'] and saved['rating']['version']==1
            assert await ratings.save(owner,'conversation',conversation.id,RatingInput(expected_version=0,score=3))==saved
            with pytest.raises(AuthError):await ratings.get(customer(target,anonymous=True),'conversation',conversation.id)
    run(scenario())


@pytest.mark.parametrize('index',['rating_one_case','rating_one_conversation'])
def test_dropped_partial_unique_index_fails_admission_and_rollback_restores_it(target,index):
    with target.engine.connect() as c:
        transaction=c.begin()
        try:
            c.exec_driver_sql(f'DROP INDEX "{target.schema}"."{index}"')
            assert c.exec_driver_sql(f'SELECT "{target.schema}".ratings_ready()').scalar_one() is False
        finally:transaction.rollback()
        assert c.exec_driver_sql(f'SELECT "{target.schema}".ratings_ready()').scalar_one() is True
