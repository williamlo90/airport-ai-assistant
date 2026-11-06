"""Case-only email on isolated real SQL and controlled source/transport."""
import os
from uuid import uuid4
import pytest
from sqlalchemy import text

from test_private_mail_neon import target as mail_target,graph_target,schema_target,worker,sender,leased
from test_private_source_graph_neon import app,run,draft,staff,count
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.infrastructure.database.publication import PostgresPublication
from airport_assistant.modules.cases.lifecycle import CaseLifecycle,Assignment,StatusChange
from airport_assistant.modules.conversations.publication import StaffMessages
from airport_assistant.modules.conversations.case_email import CaseEmails,CaseEmail
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.source.labels import SecurityLabels
from airport_assistant.modules.conversations.correspondence import StaffCorrespondence

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_CASE_EMAIL_TESTS')!='1',reason='Opt-in isolated case email SQL.')


@pytest.fixture(scope='module')
def target(mail_target):
    t=mail_target
    assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0032_case_email')=='0032_case_email'
    return t


async def setup(t,h,private,*,public_case=None):
    if private:
        created,_=await draft(t,h)
        case=(await h.cases.confirm(t.identity,created.id,created.version,created.payload_hash,uuid4().hex,uuid4())).case_id
    else:case=public_case or t.public_case
    person=staff(t,case);member=person.memberships[0].id
    lifecycle=CaseLifecycle(PostgresCaseLifecycle(h.db,t.schema))
    current=await lifecycle.get(person,case,staff=True)
    if current['assigned_membership_id'] is None:
        current=await lifecycle.assign(person,case,Assignment(expected_version=current['version'],assignee_id=member))
    messages=StaffMessages(PostgresPublication(h.db,t.schema,mail_enabled=True),h.access,private_disclosure=True)
    command=CaseEmail(body='We received your request and will check it.',language='en',
        expected_version=current['version'],membership_id=member)
    return case,person,member,messages,CaseEmails(messages),command


@pytest.mark.parametrize('private',[False,True])
def test_case_email_without_live_chat_once_and_worker_provenance(target,private):
    async def scenario():
        async with app(target) as h,worker(target) as db:
            case,person,member,messages,service,command=await setup(target,h,private)
            with target.engine.connect() as c:
                origin=c.execute(text(f'SELECT v.id,v.case_id,v.mode,v.version,v.generation_epoch,v.last_sequence FROM "{target.schema}".cases c JOIN "{target.schema}".conversations v ON v.id=c.origin_conversation_id WHERE c.id=:case'),dict(case=case)).one()
                assert origin[1] is None and origin[2]=='ai'
            before=count(target,'messages');key=uuid4().hex
            result=await service.send(person,case,command,key)
            assert result['conversation_id'] is None and result['channel']=='email'
            assert await service.send(person,case,command,key)==result
            assert await messages.receipt(person,case,key,member)==result
            assert count(target,'messages')==before+1
            with target.engine.connect() as c:
                after=c.execute(text(f'SELECT id,case_id,mode,version,generation_epoch,last_sequence FROM "{target.schema}".conversations WHERE id=:id'),dict(id=origin[0])).one()
                assert after==origin
                job=c.execute(text(f'SELECT id FROM "{target.schema}".outbox_jobs WHERE source_message_id=:id'),dict(id=result['message_id'])).scalar_one()
            send,out=sender(target,db,h)
            lease=leased(target,job)
            snapshot=await send._repository.private_snapshot(lease)
            assert snapshot is not None and snapshot['app_message_id']==str(result['message_id'])
            assert (await send(lease)).state=='succeeded'
            out.send.assert_awaited_once()
            # Closure and conversation-free history do not invalidate the saved receipt.
            lifecycle=CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema))
            await lifecycle.status(person,case,StatusChange(expected_version=command.expected_version,status='closed',
                next_action='Complete.',close_reason='resolved',resolution='Checked by staff.'))
            assert await messages.receipt(person,case,key,member)==result
            with pytest.raises(DraftError):await service.send(person,case,command,uuid4().hex)
    run(scenario())


def test_case_email_current_recipient_grants_and_membership(target):
    async def scenario():
        async with app(target) as h:
            case,person,member,messages,service,command=await setup(target,h,True)
            before=count(target,'messages')
            h.grants.denied={'booking:read'}
            with pytest.raises(AuthError):await service.send(person,case,command,uuid4().hex)
            assert count(target,'messages')==before
            h.grants.denied=set()
            other=staff(target,case)
            with pytest.raises((DraftError,AuthError)):await service.send(other,case,command,uuid4().hex)
            with pytest.raises(DraftError):await service.send(person,case,command.model_copy(update={'expected_version':999}),uuid4().hex)
            assert count(target,'messages')==before
    run(scenario())


def test_case_email_sql_requires_job_and_valid_current_sender_basis(target):
    async def scenario():
        async with app(target) as h:
            case,person,member,messages,service,command=await setup(target,h,True)
            repository=messages._repository
            before=count(target,'messages')
            for tamper in ('missing_job','wrong_version','wrong_member','wrong_language'):
                inserted=False
                if tamper=='wrong_language':
                    with target.engine.begin() as c:c.execute(text(f"UPDATE \"{target.schema}\".tenants SET supported_languages=ARRAY['en'] WHERE id=:id"),dict(id=target.a))
                try:
                    with pytest.raises(DatabaseError) as rejected:
                        async with repository.transaction(person.database_context) as tx:
                            current=await tx.find_case(case,lock=True)
                            requirements=SecurityLabels.from_records(target.a,current['resource_security_labels'])
                            candidate=command
                            if tamper=='wrong_version':current=dict(current,version=999)
                            if tamper=='wrong_member':candidate=command.model_copy(update={'membership_id':uuid4()})
                            if tamper=='wrong_language':candidate=command.model_copy(update={'language':'id'})
                            saved=await tx.case_email_message(current,candidate,uuid4().hex,'a'*64,requirements=requirements)
                            inserted=True
                            if tamper!='missing_job':await tx.enqueue_email(case,saved['id'],requirements=requirements)
                    assert rejected.value.sqlstate in ('23514','42501')
                    assert inserted is (tamper=='missing_job')
                finally:
                    if tamper=='wrong_language':
                        with target.engine.begin() as c:c.execute(text(f"UPDATE \"{target.schema}\".tenants SET supported_languages=ARRAY['en','id'] WHERE id=:id"),dict(id=target.a))
                assert count(target,'messages')==before
    run(scenario())


def test_saved_receipt_cannot_be_rebound_to_other_case_or_actor(target):
    async def scenario():
        async with app(target) as h:
            case,person,member,messages,service,command=await setup(target,h,True)
            key=uuid4().hex;await service.send(person,case,command,key)
            other_draft,_=await draft(target,h)
            other_case=(await h.cases.confirm(target.identity,other_draft.id,other_draft.version,other_draft.payload_hash,uuid4().hex,uuid4())).case_id
            with pytest.raises(AuthError):await messages.receipt(person,other_case,key,member)
            other=staff(target,case)
            with pytest.raises(AuthError):await messages.receipt(other,case,key,other.memberships[0].id)
            with target.engine.begin() as c:
                c.execute(text(f'DELETE FROM "{target.schema}".membership_queues WHERE membership_id=:id'),dict(id=member))
            with pytest.raises(AuthError):await messages.receipt(person,case,key,member)
    run(scenario())


def test_existing_private_live_chat_email_remains_supported(target):
    from test_private_mail_neon import test_private_staff_approved_email_and_scoped_history
    test_private_staff_approved_email_and_scoped_history(target)


@pytest.mark.parametrize('private',[False,True])
def test_correspondence_reads_published_messages_with_real_keyset_and_revocation(target,private):
    async def scenario():
        async with app(target) as h:
            case,person,member,messages,service,command=await setup(target,h,private)
            sent=[]
            for n in range(3):
                saved=await service.send(person,case,command.model_copy(update={'body':f'Published staff reply {n}.'}),uuid4().hex)
                sent.append(saved['message_id'])
            history=StaffCorrespondence(messages)
            # Staff history relies on live operational ACL, not personal source grants.
            h.grants.denied={'booking:read','payment_status:read','payment_details:read'}
            newest=await history.read(person,case,member,limit=2)
            assert newest['has_more'] and [r['id'] for r in newest['items']]==sent[-2:]
            older=await history.read(person,case,member,limit=2,before_time=newest['before_time'],before_id=newest['before_id'])
            assert not older['has_more'] and len(older['items'])==2
            assert older['items'][0]['author_type']=='customer' and older['items'][1]['id']==sent[0]
            assert len({r['id'] for r in older['items']+newest['items']})==4
            assert all(r['case_id']==case and r['conversation_id'] is None for r in newest['items'])
            assert all('recipient' not in r and 'resource_security_labels' not in r for r in newest['items'])
            with target.engine.begin() as c:c.execute(text(f'DELETE FROM "{target.schema}".membership_queues WHERE membership_id=:id'),dict(id=member))
            with pytest.raises(AuthError):await history.read(person,case,member,limit=2)
    run(scenario())


@pytest.mark.parametrize('tamper',['guard','constraint','policy','trigger','worker_snapshot','ancestry_acl','ancestry_missing','job_trigger'])
def test_case_email_readiness_rejects_semantic_drift(target,tamper):
    q='"'+target.schema+'"'
    with target.engine.connect() as c:
        assert c.exec_driver_sql(f'SELECT {q}.case_email_ready()').scalar_one() is True
        c.rollback();transaction=c.begin()
        try:
            if tamper=='guard':
                c.exec_driver_sql(f"CREATE OR REPLACE FUNCTION {q}.guard_case_email() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN RETURN NEW;END $$")
            elif tamper=='constraint':
                c.exec_driver_sql(f'ALTER TABLE {q}.messages DROP CONSTRAINT message_author_shape,ADD CONSTRAINT message_author_shape CHECK(true)')
            elif tamper=='policy':
                c.exec_driver_sql(f'ALTER POLICY runtime_case_email ON {q}.messages WITH CHECK(true)')
            elif tamper=='trigger':
                c.exec_driver_sql(f'ALTER TABLE {q}.messages DISABLE TRIGGER case_email_guard')
            elif tamper=='job_trigger':
                c.exec_driver_sql(f'ALTER TABLE {q}.outbox_jobs DISABLE TRIGGER email_job_graph')
            elif tamper=='ancestry_acl':
                c.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION {q}.case_correspondence_ancestry(uuid) TO PUBLIC')
            elif tamper=='ancestry_missing':
                c.exec_driver_sql(f'ALTER FUNCTION {q}.case_correspondence_ancestry(uuid) RENAME TO case_correspondence_hidden')
            else:
                definition=c.exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(target.schema+'.mail_private_snapshot(uuid,uuid,uuid,uuid,bigint)',)).scalar_one()
                original="AND(mv.case_id=c.id OR(m.conversation_id IS NULL AND m.case_id=c.id AND m.source_kind='staff_reply' AND m.basis_conversation_version IS NULL AND m.basis_epoch IS NULL))"
                assert definition.count(original)==1
                c.exec_driver_sql(definition.replace(original,'AND mv.case_id=c.id'),execution_options={'no_parameters':True})
            assert c.exec_driver_sql(f'SELECT {q}.case_email_ready()').scalar_one() is False
        finally:transaction.rollback()
        assert c.exec_driver_sql(f'SELECT {q}.case_email_ready()').scalar_one() is True
