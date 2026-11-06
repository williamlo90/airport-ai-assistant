"""Actual rating migration, isolation, classification and atomic audit on disposable Neon."""
import asyncio
import os
from dataclasses import replace
from uuid import uuid4
import pytest
from sqlalchemy import text
from test_customer_information_neon import target as information_target,page_target,translation_target,email_target,mail_target,graph_target,schema_target
from test_private_source_graph_neon import app,run,draft,staff,count
from test_case_translation_neon import new_public_case
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.ratings import PostgresRatings,RatingSQL
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.cases.ratings import CustomerRatings,RatingInput
from airport_assistant.modules.cases.lifecycle import CaseLifecycle,StatusChange
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_RATINGS_TESTS')!='1',reason='Opt-in isolated customer rating SQL.')


@pytest.fixture(scope='module')
def target(information_target):
    t=information_target
    from airport_assistant.infrastructure.database import migrations as module
    original=module.command.upgrade
    def checked(*args,**kwargs):
        try:return original(*args,**kwargs)
        except Exception as error:
            cause=getattr(error,'orig',error);diag=getattr(cause,'diag',None)
            print('Rating migration:',type(cause).__name__,getattr(cause,'sqlstate',None),getattr(diag,'message_primary',None));raise
    module.command.upgrade=checked
    try:assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0037_customer_ratings')=='0037_customer_ratings'
    finally:module.command.upgrade=original
    with t.engine.connect() as c:assert c.exec_driver_sql(f'SELECT "{t.schema}".ratings_ready()').scalar_one() is True
    return t


async def closed(t,h,private=False):
    if private:
        created,_=await draft(t,h)
        cid=(await h.cases.confirm(t.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())).case_id
    else:cid=await new_public_case(t,h)
    person=staff(t,cid);lifecycle=CaseLifecycle(PostgresCaseLifecycle(h.db,t.schema),h.access)
    before=await lifecycle.get(person,cid,staff=True)
    await lifecycle.status(person,cid,StatusChange(expected_version=before['version'],status='closed',next_action='No further action pending.',close_reason='resolved',resolution='The procedure was explained.'))
    return cid,person


@pytest.mark.parametrize('private',[False,True])
def test_actual_rating_upsert_owner_source_isolation_and_no_case_or_outbox_changes(target,private):
    async def scenario():
        async with app(target) as h:
            cid,operator=await closed(target,h,private)
            ratings=CustomerRatings(PostgresRatings(h.db,target.schema),h.access)
            life=CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema),h.access)
            before=await life.get(target.identity,cid)
            jobs=count(target,'outbox_jobs');events=count(target,'rating_events')
            first=await ratings.save(target.identity,'case',cid,RatingInput(expected_version=0,score=4,comment='Clear original advice.'))
            assert first['rating']['version']==1 and first['eligible'] is True
            assert await ratings.save(target.identity,'case',cid,RatingInput(expected_version=0,score=4,comment='Clear original advice.'))==first
            assert count(target,'rating_events')==events+1 and count(target,'outbox_jobs')==jobs
            assert await life.get(target.identity,cid)==before
            assert await ratings.get(target.identity,'case',cid)==first
            changed=await ratings.save(target.identity,'case',cid,RatingInput(expected_version=1,score=2))
            assert changed['rating']['version']==2 and changed['rating']['comment'] is None
            with pytest.raises(DraftError):await ratings.save(target.identity,'case',cid,RatingInput(expected_version=0,score=1))
            with pytest.raises(AuthError):await ratings.get(operator,'case',cid)
            outsider=replace(target.identity,session=replace(target.identity.session,tenant_id=target.b))
            with pytest.raises(AuthError):await ratings.get(outsider,'case',cid)
            if private:
                h.grants.denied={'booking:read'}
                with pytest.raises(AuthError):await ratings.get(target.identity,'case',cid)
                with pytest.raises(AuthError):await ratings.save(target.identity,'case',cid,RatingInput(expected_version=2,score=1))
                h.grants.denied.clear()
                with target.engine.connect() as c:
                    labels=c.execute(text(f'SELECT resource_security_labels FROM "{target.schema}".ratings WHERE case_id=:id'),dict(id=cid)).scalar_one()
                    assert labels and len(labels)>=2
            assert count(target,'rating_events')==events+2
    run(scenario())


def test_audit_failure_rolls_back_and_changed_readiness_cannot_write(target):
    class FaultSQL(RatingSQL):
        async def save_rating(self,*args):await super().save_rating(*args);raise RuntimeError('Controlled after-audit rollback')
    class FaultRepo(PostgresRatings):
        def unit(self,c,context):return FaultSQL(c,self._schema,context)
    async def scenario():
        async with app(target) as h:
            cid,_=await closed(target,h);rows=count(target,'ratings');events=count(target,'rating_events')
            faulty=CustomerRatings(FaultRepo(h.db,target.schema),h.access)
            with pytest.raises(RuntimeError):await faulty.save(target.identity,'case',cid,RatingInput(expected_version=0,score=1))
            assert count(target,'ratings')==rows and count(target,'rating_events')==events
            ratings=CustomerRatings(PostgresRatings(h.db,target.schema),h.access)
            with pytest.raises(DatabaseError):
                async with PostgresRatings(h.db,target.schema).transaction(target.identity.database_context) as tx:
                    await tx._c.execute(tx.query('UPDATE {s}.ratings SET resource_security_labels=\'[]\'::jsonb WHERE tenant_id=%s'),(target.a,))
            with target.engine.begin() as c:
                c.exec_driver_sql(f'ALTER TABLE "{target.schema}".ratings DISABLE TRIGGER rating_audit')
            try:
                with pytest.raises(DraftError) as e:await ratings.save(target.identity,'case',cid,RatingInput(expected_version=0,score=1))
                assert e.value.code=='CASE_UNAVAILABLE'
            finally:
                with target.engine.begin() as c:c.exec_driver_sql(f'ALTER TABLE "{target.schema}".ratings ENABLE TRIGGER rating_audit')
            assert (await ratings.save(target.identity,'case',cid,RatingInput(expected_version=0,score=3)))['rating']['version']==1
    run(scenario())


def test_two_independent_connections_cannot_overwrite_a_new_rating_with_the_same_old_version(target):
    async def scenario():
        async with app(target) as a,app(target) as b:
            cid,_=await closed(target,a)
            first=CustomerRatings(PostgresRatings(a.db,target.schema),a.access)
            second=CustomerRatings(PostgresRatings(b.db,target.schema),b.access)
            results=await asyncio.gather(first.save(target.identity,'case',cid,RatingInput(expected_version=0,score=1)),
                second.save(target.identity,'case',cid,RatingInput(expected_version=0,score=5)),return_exceptions=True)
            assert sum(isinstance(value,dict) for value in results)==1
            errors=[value for value in results if isinstance(value,DraftError)]
            assert len(errors)==1 and errors[0].status==409
            saved=await first.get(target.identity,'case',cid)
            assert saved['rating']['version']==1 and saved['rating']['score'] in(1,5)
    run(scenario())
