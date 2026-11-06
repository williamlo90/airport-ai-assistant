"""Controlled staff send transactions; no SQL/provider or staff source grants."""
import asyncio,copy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest

from airport_assistant.modules.conversations.publication import StaffMessages,StaffMessage
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure


def labels():
    return (dict(source='reference',resource_type='booking',reference='ROOT-PRIVATE',required_source_scopes=['booking:read']),
        dict(source='reference',resource_type='transaction',reference='TX-PRIVATE',required_source_scopes=['payment_details:read','payment_status:read']))


class Store:
    def __init__(self,identity,channel='chat',private=True):
        self.identity=identity;self.in_tx=False;self.active=True;self.clock=0.0;self.delay_last=False
        self.customer=uuid4();self.case_id=uuid4();self.cid=uuid4();self.member=uuid4()
        self.case=dict(id=self.case_id,status='in_progress',version=2,assigned_membership_id=self.member,
            resource_security_labels=list(labels()) if private else [])
        self.chat_row=dict(id=self.cid,case_id=self.case_id,mode='human',version=3,generation_epoch=4,handler_membership_id=self.member)
        self.recipient=dict(tenant_id=identity.session.tenant_id,principal_id=self.customer,tenant_active=True,
            principal_active=True,participant_active=True,owner_principal_id=self.customer,contact_id=uuid4(),
            verified=True,account_active=True,account_id=uuid4(),recipient='customer@example.com')
        self.sequence=5;self.messages={};self.jobs={};self.result_reads=0;self.channel=channel
        self.corrupt=None;self.membership=True
    @asynccontextmanager
    async def transaction(self,context):
        assert not self.in_tx
        self.in_tx=True
        before=copy.deepcopy((self.messages,self.jobs,self.case,self.chat_row,self.sequence))
        try:yield self
        except BaseException:
            self.messages,self.jobs,self.case,self.chat_row,self.sequence=before
            raise
        finally:self.in_tx=False
    async def session_active(self):return self.active
    async def find_case(self,id,**kw):return copy.deepcopy(self.case) if id==self.case_id else None
    async def chat(self,id,**kw):return copy.deepcopy(self.chat_row) if id==self.cid else None
    async def target_membership(self,id,case):return dict(principal_id=str(self.identity.session.principal_id)) if self.membership else None
    async def languages(self):return ['en','id']
    async def send_key(self,key):pass
    async def staff_receipt(self,key):
        row=next((m for m in self.messages.values() if m['staff_request_key']==key),None)
        return copy.deepcopy(row) if row else None
    @staticmethod
    def message_view(row,case_id):return dict(state='saved',message_id=row['id'],sequence=row['sequence'])
    async def staff_disclosure_context(self,case_id,cid,channel):
        return dict(tenant_id=self.identity.session.tenant_id,case_id=self.case_id,conversation_id=self.cid,
            case_version=self.case['version'],conversation_version=self.chat_row['version'],generation_epoch=self.chat_row['generation_epoch'],
            mode=self.chat_row['mode'],handler_membership_id=self.chat_row['handler_membership_id'],assigned_membership_id=self.case['assigned_membership_id'],
            last_sequence=self.sequence,case_labels=copy.deepcopy(self.case['resource_security_labels']),
            conversation_labels=copy.deepcopy(self.case['resource_security_labels']),recipient=copy.deepcopy(self.recipient))
    async def mail_available(self,case_id):return True
    async def staff_message(self,case,chat,command,key,digest,*,requirements=None):
        assert self.in_tx
        self.sequence+=1;self.chat_row['version']+=1;id=uuid4()
        row=dict(id=id,tenant_id=self.identity.session.tenant_id,conversation_id=self.cid,case_id=self.case_id,
            author_principal_id=self.identity.session.principal_id,created_session_id=self.identity.session.id,
            body=command.body,language=command.language,staff_request_key=key,request_hash=digest,sequence=self.sequence,
            basis_case_version=command.expected_version,basis_conversation_version=command.expected_conversation_version,
            basis_epoch=command.expected_epoch,resource_security_labels=requirements.to_records() if requirements else [],
            author_type='staff',source_kind='staff_reply',delivery_channel=command.channel,sender_membership_id=self.member)
        self.messages[id]=row
        if self.corrupt=='body':row['body']='Corrupted'
        if self.corrupt=='labels':row['resource_security_labels']=[]
        return copy.deepcopy(row)
    async def enqueue_email(self,case_id,message_id,*,requirements=None):
        id=uuid4();self.jobs[id]=dict(id=id,case_id=case_id,source_message_id=message_id,
            resource_security_labels=requirements.to_records() if requirements else [])
        if self.corrupt=='job':self.jobs[id]['resource_security_labels']=[]
        return id
    async def staff_disclosure_result(self,id):
        self.result_reads+=1
        if self.delay_last:self.clock=2.25
        if id not in self.messages:return None
        return dict(message=copy.deepcopy(self.messages[id]),job=next((copy.deepcopy(j) for j in self.jobs.values() if j['source_message_id']==id),None))


def setup(channel='chat',private=True,enabled=True):
    identity=SimpleNamespace(session=SimpleNamespace(kind='staff',tenant_id=uuid4(),principal_id=uuid4(),id=uuid4()),
        memberships=(object(),),database_context=None)
    store=Store(identity,channel,private)
    async def allowed(**kw):
        assert not store.in_tx
        assert kw['principal_id']==store.customer and kw['principal_id']!=identity.session.principal_id
        return True
    access=SimpleNamespace(allows=AsyncMock(side_effect=allowed))
    service=StaffMessages(store,access,private_disclosure=enabled)
    if enabled:service._disclosure.clock=lambda:store.clock
    command=StaffMessage(conversation_id=store.cid,channel=channel,body='Reviewed staff reply',language='en',
        expected_version=2,expected_conversation_version=3,expected_epoch=4)
    return identity,store,access,service,command,str(uuid4())


@pytest.mark.parametrize('channel',['chat','email'])
def test_positive_customer_grants_staff_has_no_personal_source_grants(channel):
    async def run():
        identity,store,access,service,command,key=setup(channel)
        result=await service.send(identity,store.case_id,command,key)
        assert result['message_id'] in store.messages and len(store.messages)==1
        assert next(iter(store.messages.values()))['resource_security_labels']==list(labels())
        assert len(store.jobs)==(1 if channel=='email' else 0)
        access.allows.assert_awaited_once()
        if store.jobs:assert next(iter(store.jobs.values()))['resource_security_labels']==list(labels())
    asyncio.run(run())


@pytest.mark.parametrize('verdict',[False,None,'true',SourceFailure(503)])
def test_denied_invalid_or_unavailable_recipient_grants_cannot_send(verdict):
    async def run():
        identity,store,access,service,command,key=setup('email')
        async def denied(**kw):
            assert not store.in_tx
            if isinstance(verdict,Exception):raise verdict
            return verdict
        access.allows.side_effect=denied
        with pytest.raises((AuthError,SourceFailure)):await service.send(identity,store.case_id,command,key)
        assert not store.messages and not store.jobs and store.chat_row['version']==3
    asyncio.run(run())


@pytest.mark.parametrize('change',['recipient','participant','contact','membership','session','labels','version'])
def test_changes_during_source_await_fail_closed(change):
    async def run():
        identity,store,access,service,command,key=setup('email')
        async def changed(**kw):
            assert not store.in_tx
            if change=='recipient':store.recipient['principal_id']=uuid4()
            if change=='participant':store.recipient['participant_active']=False
            if change=='contact':store.recipient['contact_id']=uuid4()
            if change=='membership':store.membership=False
            if change=='session':store.active=False
            if change=='labels':store.case['resource_security_labels']=[]
            if change=='version':store.chat_row['version']+=1
            return True
        access.allows.side_effect=changed
        with pytest.raises((AuthError,DraftError)):await service.send(identity,store.case_id,command,key)
        assert not store.messages and not store.jobs
    asyncio.run(run())


@pytest.mark.parametrize('corruption',['body','labels','job'])
def test_actual_message_and_job_postloader_is_mandatory(corruption):
    async def run():
        identity,store,access,service,command,key=setup('email');store.corrupt=corruption
        with pytest.raises(DraftError):await service.send(identity,store.case_id,command,key)
        assert not store.messages and not store.jobs and store.chat_row['version']==3 and store.sequence==5
    asyncio.run(run())


def test_expiration_in_final_read_rolls_back_before_commit():
    async def run():
        identity,store,access,service,command,key=setup('email')
        async def allowed(**kw):store.clock=0.75;return True
        access.allows.side_effect=allowed;store.delay_last=True
        # Grant clock .75 then final DB read2.25 is still fresh1.5; use2.8 to
        # cross this operation's grants-based deadline specifically.
        original=store.staff_disclosure_result
        async def slow(id):
            result=await original(id);store.clock=2.8;return result
        store.staff_disclosure_result=slow
        with pytest.raises(SourceFailure):await service.send(identity,store.case_id,command,key)
        assert not store.messages and not store.jobs
    asyncio.run(run())


def test_historical_replay_checks_current_grants_without_old_handler_versions():
    async def run():
        identity,store,access,service,command,key=setup('email')
        first=await service.send(identity,store.case_id,command,key)
        store.chat_row.update(version=20,generation_epoch=21,mode='ended',handler_membership_id=None)
        store.case.update(version=10,status='closed',assigned_membership_id=None)
        store.membership=False  # no longer handler; current case RLS read remains allowed
        assert await service.send(identity,store.case_id,command,key)==first
        assert len(store.messages)==1 and len(store.jobs)==1 and access.allows.await_count==2
        access.allows.side_effect=None;access.allows.return_value=False
        with pytest.raises(AuthError):await service.send(identity,store.case_id,command,key)
        assert len(store.messages)==1
    asyncio.run(run())


@pytest.mark.parametrize('enabled',[False,True])
def test_public_send_retains_single_transaction_and_no_source_call(enabled):
    async def run():
        identity,store,access,service,command,key=setup(private=False,enabled=enabled)
        await service.send(identity,store.case_id,command,key)
        assert len(store.messages)==1
        access.allows.assert_not_awaited()
    asyncio.run(run())


def test_private_default_off_cannot_send():
    async def run():
        identity,store,access,service,command,key=setup(enabled=False)
        with pytest.raises(DraftError):await service.send(identity,store.case_id,command,key)
        assert not store.messages and not store.jobs
        access.allows.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('private',[False,True])
def test_sql_writers_use_explicit_private_columns_and_public_original_defaults(private):
    async def run():
        from airport_assistant.infrastructure.database.publication import PublicationSQL
        from airport_assistant.modules.source.labels import SecurityLabels
        identity,store,access,service,command,key=setup('email')
        requirements=SecurityLabels.from_records(identity.session.tenant_id,list(labels())) if private else None
        message_id=uuid4();job_id=uuid4()
        cursor=SimpleNamespace(fetchone=AsyncMock(side_effect=[(6,),
            (message_id,store.cid,6,'email',requirements.to_records()) if private else (message_id,store.cid,6,'email'),(job_id,)]))
        connection=SimpleNamespace(execute=AsyncMock(return_value=cursor))
        context=SimpleNamespace(tenant_id=identity.session.tenant_id,principal_id=identity.session.principal_id,session_id=identity.session.id)
        tx=PublicationSQL(connection,'app',context);tx.mail_enabled=True
        saved=await tx.staff_message(store.case,store.chat_row,command,key,'a'*64,requirements=requirements)
        assert saved['id']==message_id
        assert await tx.enqueue_email(store.case_id,message_id,requirements=requirements)==job_id
        _,message_call,job_call=connection.execute.await_args_list
        for call in (message_call,job_call):
            query=call.args[0].as_string()
            assert ('resource_security_labels' in query) is private
            assert query.count('%s')==len(call.args[1])
            if private:assert any(getattr(value,'obj',None)==requirements.to_records() for value in call.args[1])
    asyncio.run(run())


@pytest.mark.parametrize('records',[None,{},'[]'])
def test_default_off_malformed_labels_do_not_become_public(records):
    async def run():
        identity,store,access,service,command,key=setup(private=False,enabled=False)
        store.case['resource_security_labels']=records
        with pytest.raises(AuthError):await service.send(identity,store.case_id,command,key)
        assert not store.messages
    asyncio.run(run())


@pytest.mark.parametrize('denied_scope',['booking:read','payment_status:read','payment_details:read'])
def test_each_recipient_source_scope_is_required_independently(denied_scope):
    async def run():
        from airport_assistant.modules.source.labels import PrivateSourceAccess
        identity,store,access,service,command,key=setup('email')
        async def grants(**kw):
            assert not store.in_tx and kw['principal_id']==store.customer
            return denied_scope not in kw['required_scopes']
        service._disclosure.source_access=PrivateSourceAccess(SimpleNamespace(has_grants=AsyncMock(side_effect=grants)))
        with pytest.raises(AuthError):await service.send(identity,store.case_id,command,key)
        assert not store.messages and not store.jobs
    asyncio.run(run())


@pytest.mark.parametrize('change',['participant_active','contact_id','account_active'])
@pytest.mark.parametrize('read',['receipt','result'])
def test_recipient_change_in_last_stored_read_rolls_back_without_clock_advance(change,read):
    async def run():
        identity,store,access,service,command,key=setup('email')
        def mutate():store.recipient[change]=uuid4() if change=='contact_id' else False
        if read=='receipt':
            original=store.staff_receipt
            async def altered(key):
                result=await original(key)
                if store.messages:mutate()
                return result
            store.staff_receipt=altered
        else:
            original=store.staff_disclosure_result
            async def altered(id):
                result=await original(id);mutate();return result
            store.staff_disclosure_result=altered
        with pytest.raises((AuthError,DraftError)):await service.send(identity,store.case_id,command,key)
        assert store.clock==0 and not store.messages and not store.jobs
        assert await store.staff_receipt(key) is None
        assert store.chat_row['version']==3 and store.sequence==5
    asyncio.run(run())


@pytest.mark.parametrize('change',['participant_active','contact_id','account_active'])
def test_historical_replay_rechecks_recipient_after_stored_result_await(change):
    async def run():
        identity,store,access,service,command,key=setup('email')
        await service.send(identity,store.case_id,command,key)
        before=copy.deepcopy((store.messages,store.jobs,store.chat_row,store.sequence))
        original=store.staff_disclosure_result
        async def changed(id):
            result=await original(id)
            store.recipient[change]=uuid4() if change=='contact_id' else False
            return result
        store.staff_disclosure_result=changed
        access.allows.reset_mock()
        with pytest.raises((AuthError,DraftError)):await service.send(identity,store.case_id,command,key)
        assert store.clock==0 and (store.messages,store.jobs,store.chat_row,store.sequence)==before
        access.allows.assert_not_awaited()
    asyncio.run(run())
