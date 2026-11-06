"""Controlled model, real case-email translation SQL and human approval."""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from sqlalchemy import text

from test_case_email_neon import target as email_target,mail_target,graph_target,schema_target,setup
from test_private_source_graph_neon import app,run,count
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.translations import PostgresTranslations
from airport_assistant.modules.conversations.publication import StaffMessages
from airport_assistant.modules.conversations.case_email import CaseEmails
from airport_assistant.modules.agent.translation import StaffTranslations,TranslationApproval
from airport_assistant.modules.agent.case_translation import CaseEmailTranslations,CaseTranslationRequest
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.drafts.models import ReportPayload
from airport_assistant.modules.agent.models import AgentError
from airport_assistant.modules.cases.lifecycle import CaseLifecycle,StatusChange
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle
from airport_assistant.modules.conversations.correspondence import StaffCorrespondence

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_CASE_TRANSLATION_TESTS')!='1',reason='Opt-in isolated translation SQL.')


@pytest.fixture(scope='module')
def target(email_target):
    t=email_target
    from airport_assistant.infrastructure.database import migrations as module
    original=module.command.upgrade
    def checked(*args,**kwargs):
        try:return original(*args,**kwargs)
        except Exception as error:
            cause=getattr(error,'orig',error);diag=getattr(cause,'diag',None)
            print('Case translation migration:',type(cause).__name__,getattr(cause,'sqlstate',None),getattr(diag,'message_primary',None))
            raise
    module.command.upgrade=checked
    try:
        assert migrate(t.direct,schema=t.schema,runtime_role=t.roles[0],policy_role=t.roles[1],auth_role=t.roles[2],target_revision='0033_case_email_translation')=='0033_case_email_translation'
    finally:module.command.upgrade=original
    return t


@pytest.mark.parametrize('private',[False,True])
def test_case_email_translation_get_only_recovery_then_human_approved_once(target,private):
    async def scenario():
        async with app(target) as h:
            case,person,member,_,_,command=await setup(target,h,private)
            repository=PostgresTranslations(h.db,target.schema,mail_enabled=True)
            messages=StaffMessages(repository,h.access,private_disclosure=True)
            model=SimpleNamespace(translate=AsyncMock())
            owner=StaffTranslations(repository,messages,model,private_translation=True)
            service=CaseEmailTranslations(owner)
            request=CaseTranslationRequest(body='Informasi layanan telah diterima.',language='id',target_language='en',
                expected_version=command.expected_version,membership_id=member,request_key=uuid4())
            before=count(target,'messages')
            async def translate(*args):
                assert h.db.active==0
                pending=await service.get(person,case,key=request.request_key)
                assert pending['state']=='generating'
                assert count(target,'messages')==before
                return 'We received your service information.'
            model.translate.side_effect=translate
            prepared=await service.prepare(person,case,request)
            assert prepared['state']=='ready' and prepared['conversation_id'] is None
            assert await service.get(person,case,key=request.request_key)==prepared
            assert await service.prepare(person,case,request)==prepared
            assert model.translate.await_count==1 and count(target,'messages')==before
            approval=TranslationApproval(text='We received the details. We will follow up by email.',approved=True,expected_revision=prepared['revision'])
            key=uuid4().hex
            saved=await service.approve(person,case,prepared['id'],approval,key)
            assert saved['conversation_id'] is None and saved['channel']=='email'
            assert await service.approve(person,case,prepared['id'],approval,key)==saved
            assert count(target,'messages')==before+1 and model.translate.await_count==1
            sent=await service.get(person,case,key=request.request_key)
            assert sent['state']=='sent' and sent['message_id']==saved['message_id']
            assert sent['original_body']==request.body and sent['translated_body']=='We received your service information.'
            assert sent['reviewed_body']==approval.text
            # A new draft must not adopt a previous ordinary-send receipt.
            next_request=request.model_copy(update={'request_key':uuid4()})
            async def next_translate(*args):
                assert h.db.active==0
                return 'We received your service information.'
            model.translate.side_effect=next_translate
            next_draft=await service.prepare(person,case,next_request)
            with pytest.raises(DraftError) as conflict:
                await service.approve(person,case,next_draft['id'],approval.model_copy(update={'expected_revision':next_draft['revision']}),key)
            assert conflict.value.code=='IDEMPOTENCY_CONFLICT'
            assert (await service.get(person,case,next_draft['id']))['state']=='ready'
            assert count(target,'messages')==before+1
            manual_key=uuid4().hex;manual_body='This reply was sent manually.'
            await CaseEmails(messages).send(person,case,command.model_copy(update={'body':manual_body}),manual_key)
            third=await service.prepare(person,case,request.model_copy(update={'request_key':uuid4()}))
            with pytest.raises(DraftError) as manual_conflict:
                await service.approve(person,case,third['id'],TranslationApproval(text=manual_body,approved=True,expected_revision=third['revision']),manual_key)
            assert manual_conflict.value.code=='IDEMPOTENCY_CONFLICT'
            assert (await service.get(person,case,third['id']))['state']=='ready'
            assert count(target,'messages')==before+2
            with target.engine.connect() as c:
                row=c.execute(text(f'SELECT body,language,case_id,conversation_id FROM "{target.schema}".messages WHERE id=:id'),dict(id=saved['message_id'])).one()
                assert row==(approval.text,'en',case,None)
                assert c.execute(text(f'SELECT count(*) FROM "{target.schema}".outbox_jobs WHERE source_message_id=:id'),dict(id=saved['message_id'])).scalar_one()==1
            with target.engine.begin() as c:c.execute(text(f'DELETE FROM "{target.schema}".membership_queues WHERE membership_id=:id'),dict(id=member))
            with pytest.raises(AuthError):await service.get(person,case,key=request.request_key)
    run(scenario())


async def new_public_case(t,h,*,action='create_case'):
    conversation=await h.conversations.create(t.identity,'en')
    await h.conversations.append(t.identity,conversation.id,uuid4(),'Please help with airport facilities.')
    report=ReportPayload(category='information',service_type='general',summary='Airport facilities',customer_report='Please help.',contact_email='customer@example.com')
    draft=await h.drafts.create(t.identity,conversation.id,action,report)
    return (await h.cases.confirm(t.identity,draft.id,draft.version,draft.payload_hash,uuid4().hex,uuid4())).case_id


@pytest.mark.parametrize('change',['case_version','recipient','expired','invalid_values'])
def test_translation_cannot_send_after_basis_or_expiry_change_or_bad_model_values(target,change):
    async def scenario():
        async with app(target) as h:
            case,person,member,_,_,command=await setup(target,h,False,public_case=await new_public_case(target,h))
            repository=PostgresTranslations(h.db,target.schema,mail_enabled=True)
            messages=StaffMessages(repository,h.access,private_disclosure=True)
            model=SimpleNamespace(translate=AsyncMock(return_value='Reference PK-A-001 remains unchanged.'))
            service=CaseEmailTranslations(StaffTranslations(repository,messages,model,private_translation=True))
            request=CaseTranslationRequest(body='Referensi PK-A-001 tetap sama.',language='id',target_language='en',membership_id=member,
                expected_version=command.expected_version,request_key=uuid4())
            before=count(target,'messages')
            if change=='invalid_values':
                model.translate.return_value='Reference PK-A-002 remains unchanged.'
                with pytest.raises(AgentError):await service.prepare(person,case,request)
                failed=await service.get(person,case,key=request.request_key)
                assert failed['state']=='failed'
                assert (await service.prepare(person,case,request))['state']=='failed' and model.translate.await_count==1
            else:
                row=await service.prepare(person,case,request)
                if change=='case_version':
                    await CaseLifecycle(PostgresCaseLifecycle(h.db,target.schema)).status(person,case,
                        StatusChange(expected_version=command.expected_version,status='in_progress',next_action='Checking the request.'))
                elif change=='recipient':
                    with target.engine.begin() as c:c.execute(text(f'UPDATE "{target.schema}".case_participants SET revoked_at=clock_timestamp() WHERE case_id=:id'),dict(id=case))
                else:
                    with target.engine.begin() as c:
                        c.exec_driver_sql(f'ALTER TABLE "{target.schema}".case_email_translation_drafts DISABLE TRIGGER case_email_translation_guard')
                        c.execute(text(f'''UPDATE "{target.schema}".case_email_translation_drafts SET created_at=clock_timestamp()-interval '16 minutes',expires_at=clock_timestamp()-interval '1 minute' WHERE id=:id'''),dict(id=row['id']))
                        c.exec_driver_sql(f'ALTER TABLE "{target.schema}".case_email_translation_drafts ENABLE TRIGGER case_email_translation_guard')
                with pytest.raises((DraftError,AuthError,AgentError)):
                    await service.approve(person,case,row['id'],TranslationApproval(text='Reviewed reference PK-A-001.',approved=True,expected_revision=row['revision']),uuid4().hex)
                assert (await service.get(person,case,row['id']))['state']=='ready'
            assert count(target,'messages')==before
    run(scenario())


def test_actual_translation_sent_failure_rolls_back_message_job_and_draft_together(target):
    class FaultingTranslations(PostgresTranslations):
        fail_sent=True
        def unit(self,connection,context):
            tx=super().unit(connection,context);original=tx.finish_case_translation
            async def finish(row,state,**kwargs):
                result=await original(row,state,**kwargs)
                if state=='sent' and self.fail_sent:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
                return result
            tx.finish_case_translation=finish
            return tx
    async def scenario():
        async with app(target) as h:
            case,person,member,_,_,command=await setup(target,h,False,public_case=await new_public_case(target,h))
            repository=FaultingTranslations(h.db,target.schema,mail_enabled=True)
            messages=StaffMessages(repository,h.access,private_disclosure=True)
            model=SimpleNamespace(translate=AsyncMock(return_value='Please wait for our email.'))
            service=CaseEmailTranslations(StaffTranslations(repository,messages,model,private_translation=True))
            request=CaseTranslationRequest(body='Mohon tunggu email kami.',language='id',target_language='en',membership_id=member,
                expected_version=command.expected_version,request_key=uuid4())
            ready=await service.prepare(person,case,request);before=(count(target,'messages'),count(target,'outbox_jobs'))
            approval=TranslationApproval(text='Please wait for our email.',approved=True,expected_revision=ready['revision']);key=uuid4().hex
            with pytest.raises(DraftError):await service.approve(person,case,ready['id'],approval,key)
            assert (count(target,'messages'),count(target,'outbox_jobs'))==before
            assert await service.get(person,case,ready['id'])==ready
            assert model.translate.await_count==1
            repository.fail_sent=False
            saved=await service.approve(person,case,ready['id'],approval,key)
            assert (count(target,'messages'),count(target,'outbox_jobs'))==(before[0]+1,before[1]+1)
            assert await service.approve(person,case,ready['id'],approval,key)==saved
            history=await StaffCorrespondence(messages).read(person,case,member)
            assert not history['has_more'] and len(history['items'])==2
            assert history['items'][-1]['id']==saved['message_id'] and history['items'][-1]['body']==approval.text
            assert model.translate.await_count==1
    run(scenario())


def test_correspondence_union_reads_linked_conversation_and_case_owned_report(target):
    from test_private_source_graph_neon import staff
    from airport_assistant.modules.conversations.handoff import ChatHandoff,AcceptChat
    from airport_assistant.modules.conversations.publication import StaffMessage
    async def scenario():
        async with app(target) as h:
            case=await new_public_case(target,h,action='request_handoff');person=staff(target,case);member=person.memberships[0].id
            with target.engine.connect() as c:
                conversation=c.execute(text(f'SELECT origin_conversation_id FROM "{target.schema}".cases WHERE id=:id'),dict(id=case)).scalar_one()
            handoff=ChatHandoff(PostgresCaseLifecycle(h.db,target.schema));chat=await handoff.get(person,conversation)
            await handoff.accept(person,conversation,AcceptChat(expected_version=chat['version'],expected_case_version=chat['case_version']),uuid4().hex)
            chat=await handoff.get(person,conversation)
            messages=StaffMessages(PostgresTranslations(h.db,target.schema,mail_enabled=True),h.access,private_disclosure=True)
            saved=await messages.send(person,case,StaffMessage(conversation_id=conversation,channel='chat',body='Published chat reply.',language='en',
                expected_version=chat['case_version'],expected_conversation_version=chat['version'],expected_epoch=chat['generation_epoch']),uuid4().hex)
            history=await StaffCorrespondence(messages).read(person,case,member)
            assert not history['has_more']
            assert any(r['id']==saved['message_id'] and r['conversation_id']==conversation for r in history['items'])
            assert any(r['author_type']=='customer' and r['conversation_id'] is None for r in history['items'])
            assert any(r['author_type']=='customer' and r['conversation_id']==conversation for r in history['items'])
    run(scenario())


@pytest.mark.parametrize('tamper',['read_grant','write_grant','policy_kind','worker_column'])
def test_translation_readiness_detects_runtime_capability_drift(target,tamper):
    q='"'+target.schema+'"';runtime='"'+target.roles[0]+'"'
    with target.engine.connect() as c:
        assert c.exec_driver_sql(f'SELECT {q}.case_email_translation_ready()').scalar_one() is True
        c.rollback();transaction=c.begin()
        try:
            if tamper=='read_grant':c.exec_driver_sql(f'REVOKE SELECT ON {q}.case_email_translation_drafts FROM {runtime}')
            elif tamper=='write_grant':c.exec_driver_sql(f'REVOKE UPDATE(reviewed_body) ON {q}.case_email_translation_drafts FROM {runtime}')
            elif tamper=='worker_column':c.exec_driver_sql(f'GRANT SELECT(original_body) ON {q}.case_email_translation_drafts TO "{target.roles[3]}"')
            else:
                policy=c.exec_driver_sql("SELECT pg_get_expr(polqual,polrelid) FROM pg_policy WHERE polrelid=%s::regclass AND polname='case_translation_read'",(target.schema+'.case_email_translation_drafts',)).scalar_one()
                c.exec_driver_sql(f'DROP POLICY case_translation_read ON {q}.case_email_translation_drafts')
                c.exec_driver_sql(f'CREATE POLICY case_translation_read ON {q}.case_email_translation_drafts AS RESTRICTIVE FOR SELECT TO {runtime} USING({policy})')
            assert c.exec_driver_sql(f'SELECT {q}.case_email_translation_ready()').scalar_one() is False
        finally:transaction.rollback()
        assert c.exec_driver_sql(f'SELECT {q}.case_email_translation_ready()').scalar_one() is True
