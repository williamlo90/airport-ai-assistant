"""Read-only human-operation recovery; no SQL, model, or email provider."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.agent.translation import StaffTranslations
from airport_assistant.modules.conversations.publication import StaffMessages
from airport_assistant.modules.identity.auth import AuthError


class Store:
    def __init__(self):
        self.actor=SimpleNamespace(session=SimpleNamespace(kind='staff',id=uuid4(),
            tenant_id=uuid4(),principal_id=uuid4()),memberships=(object(),),database_context=None)
        self.case_id=uuid4();self.cid=uuid4();self.mid=uuid4();self.member=uuid4();self.key=str(uuid4())
        self.active=True;self.visible=True;self.reads=0
        self.case=dict(id=self.case_id,status='closed',version=99,resource_security_labels=[])
        self.message=dict(id=self.mid,tenant_id=self.actor.session.tenant_id,case_id=self.case_id,
            conversation_id=self.cid,author_principal_id=self.actor.session.principal_id,
            staff_request_key=self.key,resource_security_labels=[],sequence=5,delivery_channel='email')
        self.ancestry=dict(case_labels=[],conversation_labels=[],origin_labels=[])
        self.row=dict(id=uuid4(),case_id=self.case_id,principal_id=self.actor.session.principal_id,
            session_id=self.actor.session.id,state='generating')

    @asynccontextmanager
    async def transaction(self,context):yield self
    async def session_active(self):return self.active
    async def find_case(self,id):return dict(self.case) if id==self.case_id else None
    async def staff_workspace(self,id,member):return {} if self.visible and member==self.member else None
    async def staff_receipt(self,key):return {'id':self.mid} if key==self.key else None
    async def staff_disclosure_result(self,id):return dict(message=dict(self.message),job=None)
    async def chat(self,id):return dict(id=self.cid,case_id=self.case_id,mode='ended',version=999)
    async def staff_receipt_ancestry(self,*args):self.reads+=1;return self.ancestry
    @staticmethod
    def message_view(row,case):return dict(state='saved',message_id=row['id'],case_id=case)
    async def translation_request(self,key):return dict(self.row,request_key=key)


def test_send_receipt_uses_history_acl_after_close_and_never_replays_send():
    async def run():
        db=Store();service=StaffMessages(db)
        service.target=AsyncMock(side_effect=AssertionError('No write target check'))
        assert await service.receipt(db.actor,db.case_id,db.key,db.member)==dict(
            state='saved',message_id=db.mid,case_id=db.case_id)
        service.target.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('change',['case','actor','key','tenant','membership','session','ancestry'])
def test_receipt_rejects_wrong_parent_owner_or_current_scope(change):
    async def run():
        db=Store();service=StaffMessages(db)
        if change=='case':db.message['case_id']=uuid4()
        if change=='actor':db.message['author_principal_id']=uuid4()
        if change=='key':db.message['staff_request_key']=str(uuid4())
        if change=='tenant':db.message['tenant_id']=uuid4()
        if change=='membership':db.visible=False
        if change=='session':db.active=False
        if change=='ancestry':db.ancestry=None
        with pytest.raises(AuthError):await service.receipt(db.actor,db.case_id,db.key,db.member)
    asyncio.run(run())


def test_private_ancestry_receipt_rechecks_metadata_without_customer_grants():
    async def run():
        db=Store()
        db.ancestry['conversation_labels']=[dict(source='reference',resource_type='booking',
            reference='BK-01',required_source_scopes=['booking:read'])]
        grants=SimpleNamespace(allows=AsyncMock(side_effect=AssertionError('Staff uses operational ACL')))
        await StaffMessages(db,grants).receipt(db.actor,db.case_id,db.key,db.member)
        assert db.reads==2
        grants.allows.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('state',['generating','ready','failed','sent'])
def test_translation_discovery_reuses_existing_get_and_never_calls_model(state):
    async def run():
        db=Store();db.row['state']=state
        model=SimpleNamespace(translate=AsyncMock(side_effect=AssertionError('GET must not generate')))
        service=StaffTranslations(db,None,model)
        service.get=AsyncMock(return_value=db.row)
        assert await service.by_request(db.actor,db.case_id,uuid4())==db.row
        service.get.assert_awaited_once_with(db.actor,db.case_id,db.row['id'])
        model.translate.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('field',['principal_id','session_id','case_id'])
def test_translation_discovery_rejects_other_owner_session_or_parent(field):
    async def run():
        db=Store();db.row[field]=uuid4();service=StaffTranslations(db,None,None)
        service.get=AsyncMock()
        with pytest.raises(AuthError):await service.by_request(db.actor,db.case_id,uuid4())
        service.get.assert_not_awaited()
    asyncio.run(run())


def test_http_receipt_recovery_is_get_no_store_and_validates_selectors():
    async def run():
        import httpx
        from fastapi import FastAPI
        from airport_assistant.api.staff_messages import router
        from airport_assistant.api.dependencies import get_staff_identity
        from airport_assistant.api.errors import install_error_handlers
        db=Store();app=FastAPI();app.include_router(router)
        @app.middleware('http')
        async def request_context(request,call_next):
            request.state.request_id=str(uuid4())
            return await call_next(request)
        service=SimpleNamespace(receipt=AsyncMock(return_value=dict(state='saved',message_id=db.mid,
            conversation_id=None,case_id=db.case_id,sequence=2,channel='email')))
        app.state.services=SimpleNamespace(staff_messages=service)
        app.dependency_overrides[get_staff_identity]=lambda:db.actor
        # Use the application's real error boundary for selector rejection.
        install_error_handlers(app)
        path=f'/api/v1/staff/cases/{db.case_id}/message-receipts/{db.key}'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
            response=await client.get(path,params={'membership_id':str(db.member)})
            assert response.status_code==200 and response.headers['Cache-Control']=='no-store'
            assert response.json()['conversation_id'] is None
            service.receipt.assert_awaited_once_with(db.actor,db.case_id,db.key,db.member)
            for suffix in (f'?membership_id={db.member}&membership_id={db.member}',f'?membership_id={db.member}&tenant_id={uuid4()}'):
                response=await client.get(path+suffix)
                assert response.status_code==400
            assert service.receipt.await_count==1
    asyncio.run(run())


@pytest.mark.parametrize('extra',[{'recipient':'other@example.com'},{'channel':'chat'},
    {'conversation_id':str(uuid4())},{'expected_epoch':1},{'body':'   '},{'language':'xx'}])
def test_case_email_command_rejects_client_routing_and_fake_chat_basis(extra):
    from pydantic import ValidationError
    from airport_assistant.modules.conversations.case_email import CaseEmail
    value=dict(body='Please check your email for the next update.',language='en',expected_version=2,membership_id=uuid4())
    with pytest.raises(ValidationError):CaseEmail.model_validate(dict(value,**extra))
