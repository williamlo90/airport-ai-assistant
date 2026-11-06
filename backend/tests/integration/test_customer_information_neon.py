"""Actual once-only customer message/revision graph, no model or email."""
import os
import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_customer_cases_neon import target as page_target,translation_target,email_target,mail_target,graph_target,schema_target
from test_case_translation_neon import new_public_case
from test_private_source_graph_neon import app,run,draft,human,staff,count
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.customer_information import PostgresCustomerInformation,CustomerInformationSQL
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.cases.information import CustomerInformation
from airport_assistant.modules.cases.information_preview import InformationInput,InformationPreviewCodec
from airport_assistant.modules.cases.lifecycle import CaseLifecycle,StatusChange,Assignment
from airport_assistant.modules.conversations.handoff import ChatHandoff
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_CUSTOMER_INFORMATION_TESTS')!='1',reason='Opt-in isolated customer information SQL.')


@pytest.fixture(scope='module')
def target(page_target):
    t=page_target
    from airport_assistant.infrastructure.database import migrations as module
    original=module.command.upgrade
    def checked(*args,**kwargs):
        try:return original(*args,**kwargs)
        except Exception as error:
            cause=getattr(error,'orig',error);diag=getattr(cause,'diag',None)
            print('Customer information migration:',type(cause).__name__,getattr(cause,'sqlstate',None),getattr(diag,'message_primary',None))
            raise
    module.command.upgrade=checked
    try:
        assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0035_customer_information')=='0035_customer_information'
    finally:module.command.upgrade=original
    with t.engine.connect() as c:
        assert c.exec_driver_sql(f'SELECT "{t.schema}".customer_information_ready()').scalar_one() is True
        assert c.exec_driver_sql(f'SELECT "{t.schema}".case_email_translation_ready()').scalar_one() is True
    return t


def service(t,h,*,repository=None,codec=None):
    return CustomerInformation(repository or PostgresCustomerInformation(h.db,t.schema),codec or InformationPreviewCodec(b'i'*32),h.access)


@pytest.mark.parametrize('private',[False,True])
def test_original_information_once_with_revision_history_stale_close_and_saved_recovery(target,private):
    async def scenario():
        async with app(target) as h:
            if private:
                created,_=await draft(target,h)
                cid=(await h.cases.confirm(target.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())).case_id
            else:cid=await new_public_case(target,h)
            person=staff(target,cid)
            lifecycle=CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema),h.access)
            before=await lifecycle.get(person,cid,staff=True)
            before=await lifecycle.assign(person,cid,Assignment(expected_version=before['version'],assignee_id=person.memberships[0].id))
            info=service(target,h)
            preview=await info.preview(target.identity,cid,InformationInput(expected_version=before['version'],message='This is my original additional information.',language='en'))
            key=uuid4().hex;baseline=(count(target,'messages'),count(target,'outbox_jobs'))
            saved=await info.confirm(target.identity,cid,preview['confirmation_token'],key)
            after=await lifecycle.get(target.identity,cid)
            assert after['version']==before['version']+1 and after['history'][-1]['event_type']=='customer_information'
            assert after['history'][-1]['customer_message']==preview['message']
            assert all(after[k]==before[k] for k in ('status','assigned_membership_id','next_actor','next_action','customer_report'))
            assert (count(target,'messages'),count(target,'outbox_jobs'))==(baseline[0]+1,baseline[1])
            assert await info.confirm(target.identity,cid,preview['confirmation_token'],key)==saved
            with pytest.raises(DraftError) as e:await info.confirm(target.identity,cid,preview['confirmation_token'],uuid4().hex)
            assert e.value.code=='IDEMPOTENCY_CONFLICT'
            with pytest.raises(DraftError) as e:
                await lifecycle.status(person,cid,StatusChange(expected_version=before['version'],status='closed',next_action='Finished',close_reason='no_response',resolution='No reply',followup_evidence='Earlier request'))
            assert e.value.code=='VERSION_CONFLICT'
            await lifecycle.status(person,cid,StatusChange(expected_version=after['version'],status='closed',next_action='Finished',close_reason='resolved',resolution='Reviewed new information'))
            info._codec._clock=lambda:preview['expires_at']+1
            assert await info.receipt(target.identity,cid,key)==saved
            if private:
                h.grants.denied={'booking:read'}
                with pytest.raises(AuthError):await info.receipt(target.identity,cid,key)
    run(scenario())


def test_actual_active_private_chat_preserves_mode_version_epoch_but_stales_case_basis(target):
    async def scenario():
        async with app(target) as h:
            conv,cid,person,before=await human(target,h)
            info=service(target,h)
            preview=await info.preview(target.identity,cid,InformationInput(expected_version=before['case_version'],message='More details for the assigned staff member.',language='en'))
            await info.confirm(target.identity,cid,preview['confirmation_token'],uuid4().hex)
            after=await ChatHandoff(PostgresCaseLifecycle(h.db,target.schema)).get(person,conv)
            assert after['case_version']==before['case_version']+1
            assert all(after[k]==before[k] for k in ('mode','version','generation_epoch','handler_membership_id'))
    run(scenario())


@pytest.mark.parametrize('table,trigger',[('cases','guard_case_lifecycle'),('cases','validate_case_lifecycle'),('cases','case_chat_pair_check'),('case_history','guard_lifecycle_history')])
def test_admission_detects_disabled_critical_dependency_trigger_then_restores(target,table,trigger):
    q='"'+target.schema+'"'
    with target.engine.connect() as c:
        assert c.exec_driver_sql(f'SELECT {q}.customer_information_ready()').scalar_one() is True
        c.rollback();transaction=c.begin()
        try:
            c.exec_driver_sql(f'ALTER TABLE {q}.{table} DISABLE TRIGGER {trigger}')
            assert c.exec_driver_sql(f'SELECT {q}.customer_information_ready()').scalar_one() is False
        finally:transaction.rollback()
        assert c.exec_driver_sql(f'SELECT {q}.customer_information_ready()').scalar_one() is True


def test_atomic_rollback_after_actual_message_case_and_history_then_same_key_retry(target):
    seen=[]
    class FaultSQL(CustomerInformationSQL):
        async def publish_information(self,record,key,digest,message_id,labels):
            result=await super().publish_information(record,key,digest,message_id,labels)
            seen.append((result['version'],(await self.information_receipt(key=key))['message_id']))
            raise RuntimeError('Controlled post-write rollback')
    class FaultRepository(PostgresCustomerInformation):
        def unit(self,c,context):return FaultSQL(c,self._schema,context)
    async def scenario():
        async with app(target) as h:
            cid=await new_public_case(target,h)
            original=service(target,h);preview=await original.preview(target.identity,cid,InformationInput(expected_version=1,message='Atomic original.',language='en'))
            key=uuid4().hex;baseline={table:count(target,table) for table in ('messages','customer_case_updates','case_history','outbox_jobs')}
            faulty=service(target,h,repository=FaultRepository(h.db,target.schema))
            with pytest.raises(RuntimeError):await faulty.confirm(target.identity,cid,preview['confirmation_token'],key)
            assert len(seen)==1 and seen[0][0]==2
            assert {table:count(target,table) for table in baseline}==baseline
            with pytest.raises(AuthError):await original.receipt(target.identity,cid,key)
            saved=await original.confirm(target.identity,cid,preview['confirmation_token'],key)
            assert saved['case_version']==2 and saved['message_id']!=seen[0][1]
            assert await original.confirm(target.identity,cid,preview['confirmation_token'],key)==saved
    run(scenario())


def test_deferred_database_expiry_rolls_back_complete_graph_despite_app_clock_skew(target):
    stages=[]
    class SlowSQL(CustomerInformationSQL):
        async def publish_information(self,*args,**kwargs):
            result=await super().publish_information(*args,**kwargs)
            stages.append('complete_graph_before_expiry')
            await self._c.execute('SELECT pg_sleep(6)')
            stages.append('after_database_expiry')
            return result
    class SlowRepository(PostgresCustomerInformation):
        def unit(self,c,context):return SlowSQL(c,self._schema,context)
    async def scenario():
        async with app(target) as h:
            cid=await new_public_case(target,h)
            # Token's signed expiry is ~5 seconds ahead of the actual database;
            # deliberately skew application time so ONLY deferred SQL catches it.
            codec=InformationPreviewCodec(b'i'*32,clock=lambda:int(time.time())-895)
            info=service(target,h,repository=SlowRepository(h.db,target.schema),codec=codec)
            preview=await info.preview(target.identity,cid,InformationInput(expected_version=1,message='Must expire at commit.',language='en'))
            baseline={table:count(target,table) for table in ('messages','customer_case_updates','case_history')}
            with pytest.raises(DatabaseError) as e:await info.confirm(target.identity,cid,preview['confirmation_token'],uuid4().hex)
            assert e.value.sqlstate=='23514' and stages==['complete_graph_before_expiry','after_database_expiry']
            assert {table:count(target,table) for table in baseline}==baseline
            view=await CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema)).get(target.identity,cid)
            assert view['version']==1
    run(scenario())


async def insert_ledger(tx,record,key,digest,message_id,labels):
    from datetime import datetime,timezone
    from psycopg.types.json import Jsonb
    await tx._c.execute(tx.query('''INSERT INTO {s}.customer_case_updates(tenant_id,id,case_id,principal_id,session_id,
        request_key,request_hash,message_id,message,language,expected_version,preview_expires_at,resource_security_labels)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'''),
        (record.tenant_id,record.preview_id,record.case_id,record.principal_id,record.session_id,key,digest,message_id,
         record.message,record.language,record.expected_version,datetime.fromtimestamp(record.expires_at,timezone.utc),Jsonb(labels)))


@pytest.mark.parametrize('malformed',['orphan','body'])
def test_malformed_direct_sql_customer_information_graph_is_rejected(target,malformed):
    async def scenario():
        async with app(target) as h:
            cid=await new_public_case(target,h);repo=PostgresCustomerInformation(h.db,target.schema)
            codec=InformationPreviewCodec(b'i'*32)
            record,_=codec.issue(target.identity,cid,InformationInput(expected_version=1,message='Canonical original.',language='en'))
            key=uuid4().hex;mid=uuid4();inserted=False;reserved=False
            with pytest.raises(DatabaseError) as e:
                async with repo.transaction(target.identity.database_context) as tx:
                    await tx.find_case(cid,lock=True)
                    await insert_ledger(tx,record,key,codec.digest(record),mid,[]);reserved=True
                    if malformed=='orphan':
                        await tx._c.execute(tx.query('SET CONSTRAINTS {s}.customer_information_graph IMMEDIATE'))
                    else:
                        await tx._c.execute(tx.query('''INSERT INTO {s}.messages(tenant_id,id,case_id,author_principal_id,author_type,body,
                            language,client_message_id,sequence,source_kind,created_session_id,request_hash,basis_case_version,resource_security_labels)
                            VALUES(%s,%s,%s,%s,'customer','CHANGED BODY','en',%s,2,'customer_update',%s,%s,1,'[]') RETURNING id'''),
                            (target.a,mid,cid,target.principal,record.preview_id,target.identity.session.id,codec.digest(record)))
                        inserted=True
            assert e.value.sqlstate=='23514' and reserved and not inserted
            async with repo.transaction(target.identity.database_context) as tx:assert await tx.information_receipt(key=key) is None
    run(scenario())
