"""Staff translation approval, durable attempt semantics, and strict API schemas."""
import asyncio,copy
from contextlib import asynccontextmanager
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from airport_assistant.modules.agent.translation import StaffTranslations,TranslationRequest,TranslationApproval,preserve_values
from airport_assistant.modules.agent.models import AgentError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError


class Store:
    def __init__(self):self.rows={};self.clock=datetime.now(timezone.utc);self.active=True;self.clear=True
    @asynccontextmanager
    async def transaction(self,context):
        before=copy.deepcopy(self.rows)
        try:yield self
        except BaseException:self.rows=before;raise
    async def session_active(self):return self.active
    async def now(self):return self.clock
    async def find_case(self,id,**kw):return {'id':id}
    async def chat(self,*a,**kw):return {}
    async def translation_context_clear(self,*a):return self.clear
    async def languages(self):return ['en','id']
    async def translation_request(self,key):return next((r for r in self.rows.values() if r['request_key']==key),None)
    async def translation(self,id,**kw):return self.rows.get(id)
    async def create_translation(self,case,request,digest):
        id=uuid4();self.rows[id]=dict(id=id,case_id=case,conversation_id=request.conversation_id,request_key=request.request_key,request_hash=digest,
            original_body=request.body,source_language=request.language,target_language=request.target_language,channel=request.channel,
            case_version=request.expected_version,conversation_version=request.expected_conversation_version,generation_epoch=request.expected_epoch,
            state='generating',revision=1,created_at=self.clock,expires_at=self.clock+timedelta(minutes=15),translated_body=None,reviewed_body=None,message_id=None,send_key=None)
        return self.rows[id]
    async def finish_translation(self,row,body):row.update(state='ready',translated_body=body,revision=2);return row
    async def fail_translation(self,row):row.update(state='failed',revision=2)
    async def sent_translation(self,row,body,message,key):row.update(state='sent',reviewed_body=body,message_id=message,send_key=key,revision=3)
    async def staff_receipt(self,key):return {'id':next(r['message_id'] for r in self.rows.values() if r['send_key']==key)}
    def message_view(self,row,case):return dict(state='saved',message_id=row['id'])


def setup():
    store=Store();model=SimpleNamespace(translate=AsyncMock(return_value='Booking PK-A-001 is valid until 20:30.'))
    messages=SimpleNamespace(target=AsyncMock(),send_in_transaction=AsyncMock(return_value=dict(state='saved',message_id=uuid4())))
    identity=SimpleNamespace(session=SimpleNamespace(kind='staff'),database_context=None)
    request=TranslationRequest(conversation_id=uuid4(),body='Booking PK-A-001 berlaku sampai 20:30.',language='id',target_language='en',channel='chat',expected_version=2,expected_conversation_version=3,expected_epoch=2,request_key=uuid4())
    return store,model,messages,identity,request,StaffTranslations(store,messages,model)


def test_preview_preserves_original_and_never_sends_until_explicit_reviewed_approval():
    async def scenario():
        store,model,messages,actor,request,service=setup();case=uuid4()
        row=await service.prepare(actor,case,request)
        assert row['original_body']==request.body and row['state']=='ready'
        messages.send_in_transaction.assert_not_awaited()
        assert (await service.prepare(actor,case,request))['id']==row['id'] and model.translate.await_count==1
        approval=TranslationApproval(text='Reviewed: Booking PK-A-001 is valid until 20:30.',approved=True,expected_revision=2)
        key=str(uuid4());receipt=await service.approve(actor,case,row['id'],approval,key)
        assert await service.approve(actor,case,row['id'],approval,key)==receipt
        assert messages.send_in_transaction.await_count==1
        assert row['original_body']==request.body and row['reviewed_body']==approval.text
    asyncio.run(scenario())


@pytest.mark.parametrize('failure',['revoke','stale','values','private'])
def test_model_await_cannot_bypass_current_authorization_or_value_fidelity(failure):
    async def scenario():
        store,model,messages,actor,request,service=setup()
        async def mutate(*args):
            if failure=='revoke':store.active=False
            if failure=='stale':messages.target.side_effect=DraftError(409,'STALE_MESSAGE')
            if failure=='private':store.clear=False
            return 'Booking PK-A-001 until 99:99.' if failure=='values' else model.translate.return_value
        model.translate.side_effect=mutate
        with pytest.raises((AuthError,DraftError,AgentError)):await service.prepare(actor,uuid4(),request)
        messages.send_in_transaction.assert_not_awaited()
        assert all(r['state']!='ready' for r in store.rows.values())
    asyncio.run(scenario())


def test_generating_checkpoint_never_repeats_unknown_model_request():
    async def scenario():
        store,model,messages,actor,request,service=setup();case=uuid4()
        # Cancellation leaves a generating reservation; a restart cannot spend again.
        async def cancel(*args):raise asyncio.CancelledError()
        model.translate.side_effect=cancel
        with pytest.raises(asyncio.CancelledError):await service.prepare(actor,case,request)
        with pytest.raises(AgentError):await service.prepare(actor,case,request)
        assert model.translate.await_count==1 and len(store.rows)==1
    asyncio.run(scenario())


def test_translation_schema_forbids_model_authority_and_requires_human_true():
    with pytest.raises(ValidationError):TranslationApproval(text='Send',approved=False,expected_revision=2)
    with pytest.raises(ValidationError):TranslationApproval(text='Send',approved=True,expected_revision=2,tenant_id=str(uuid4()))
    preserve_values('PK-A-001: 100.00 USD on 2026-11-05','PK-A-001: 100.00 USD pada 2026-11-05')
    with pytest.raises(AgentError):preserve_values('PK-A-001 costs 100.00 USD','PK-B-001 costs 100.00 USD')
