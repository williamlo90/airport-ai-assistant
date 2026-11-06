"""Operational staff translation and atomic recipient-gated approval, offline."""
import asyncio,copy
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest

from airport_assistant.modules.agent.translation import StaffTranslations,TranslationRequest,TranslationApproval
from airport_assistant.modules.conversations.publication import StaffMessages
from airport_assistant.modules.source.labels import SecurityLabels
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.agent.models import AgentError
from airport_assistant.modules.source.models import SourceFailure
from test_staff_disclosure import Store as SendStore,labels


class Store(SendStore):
    def __init__(self,identity,channel):
        super().__init__(identity,channel)
        self.rows={};self.events={};self.event_sequence=7;self.wall=datetime.now(timezone.utc)
        self.scope=True;self.fail_sent=False
        self.after_commit=None
    @asynccontextmanager
    async def transaction(self,context):
        before=copy.deepcopy((self.rows,self.events,self.event_sequence))
        try:
            async with super().transaction(context):yield self
        except BaseException:self.rows,self.events,self.event_sequence=before;raise
        else:
            if self.after_commit:self.after_commit()
    async def find_case(self,id,**kw):
        if not self.scope:return None
        return await super().find_case(id,**kw)
    async def now(self):return self.wall
    async def translation_context_clear(self,*args):return self.case['resource_security_labels']==[]
    async def translation_source_context(self,case,cid):
        if not self.scope:return None
        result=await self.staff_disclosure_context(case,cid,self.channel)
        result.pop('recipient');result['last_event_sequence']=self.event_sequence
        result['handler_principal_id']=self.identity.session.principal_id if self.membership else None
        result['supported_languages']=await self.languages()
        return result
    async def translation(self,id,**kw):return copy.deepcopy(self.rows.get(id))
    async def translation_request(self,key):
        return next((copy.deepcopy(row) for row in self.rows.values() if row['request_key']==key),None)
    async def create_translation(self,case,request,digest,*,requirements=None,source_context_hash=None):
        id=uuid4()
        row=dict(id=id,case_id=case,conversation_id=request.conversation_id,request_key=request.request_key,request_hash=digest,
            original_body=request.body,source_language=request.language,target_language=request.target_language,channel=request.channel,
            case_version=request.expected_version,conversation_version=request.expected_conversation_version,generation_epoch=request.expected_epoch,
            state='generating',revision=1,created_at=self.wall,expires_at=self.wall+timedelta(minutes=15),translated_body=None,
            reviewed_body=None,message_id=None,send_key=None,tenant_id=self.identity.session.tenant_id,
            principal_id=self.identity.session.principal_id,session_id=self.identity.session.id,
            resource_security_labels=requirements.to_records() if requirements else [],source_context_hash=source_context_hash)
        self.rows[id]=row;return copy.deepcopy(row)
    async def finish_translation(self,row,body):
        self.rows[row['id']].update(state='ready',translated_body=body,revision=row['revision']+1)
        return await self.translation(row['id'])
    async def fail_translation(self,row):self.rows[row['id']].update(state='failed',revision=row['revision']+1)
    async def sent_translation(self,row,body,message,key):
        if self.fail_sent:raise RuntimeError('Controlled participant write failure')
        self.rows[row['id']].update(state='sent',reviewed_body=body,message_id=message,send_key=key,revision=row['revision']+1)
        if self.corrupt=='translation':self.rows[row['id']]['reviewed_body']='Changed after write'
    async def staff_message(self,*args,**kwargs):
        result=await super().staff_message(*args,**kwargs)
        self.event_sequence+=1
        self.events[result['id']]=dict(tenant_id=self.identity.session.tenant_id,conversation_id=self.cid,message_id=result['id'],
            event_type='staff_message',language=args[2].language,sequence=self.event_sequence,last_event_sequence=self.event_sequence)
        return result
    async def translation_message_event(self,id):
        result=copy.deepcopy(self.events.get(id))
        if self.corrupt=='event':result['message_id']=uuid4()
        return result


def setup(channel='email',enabled=True):
    identity=SimpleNamespace(session=SimpleNamespace(kind='staff',tenant_id=uuid4(),principal_id=uuid4(),id=uuid4()),
        memberships=(object(),),database_context=None)
    store=Store(identity,channel)
    async def allowed(**kw):
        assert not store.in_tx and kw['principal_id']==store.customer and kw['principal_id']!=identity.session.principal_id
        return True
    access=SimpleNamespace(allows=AsyncMock(side_effect=allowed))
    messages=StaffMessages(store,access,private_disclosure=True);messages._disclosure.clock=lambda:store.clock
    async def translate(*args):assert not store.in_tx;return 'Booking PK-A-001 is valid until 20:30.'
    model=SimpleNamespace(translate=AsyncMock(side_effect=translate))
    service=StaffTranslations(store,messages,model,private_translation=enabled)
    if service.private is not None:
        service.private.clock=lambda:store.clock;service.private.gate.clock=lambda:store.clock
    request=TranslationRequest(conversation_id=store.cid,body='Booking PK-A-001 berlaku sampai 20:30.',language='id',target_language='en',
        channel=channel,expected_version=2,expected_conversation_version=3,expected_epoch=4,request_key=uuid4())
    return identity,store,access,messages,model,service,request


async def ready(channel='email'):
    args=setup(channel);identity,store,access,messages,model,service,request=args
    row=await service.prepare(identity,store.case_id,request)
    return (*args,row)


@pytest.mark.parametrize('channel',['chat','email'])
def test_operational_prepare_get_without_personal_source_grants_then_human_send(channel):
    async def run():
        identity,store,access,messages,model,service,request,row=await ready(channel)
        assert row['state']=='ready' and row['resource_security_labels']==list(labels())
        assert (await service.get(identity,store.case_id,row['id']))==row
        access.allows.assert_not_awaited()
        assert not store.messages and not store.jobs
        approval=TranslationApproval(text=row['translated_body'],approved=True,expected_revision=row['revision'])
        result=await service.approve(identity,store.case_id,row['id'],approval,str(uuid4()))
        assert store.rows[row['id']]['state']=='sent' and store.rows[row['id']]['message_id']==result['message_id']
        assert len(store.messages)==len(store.events)==1 and len(store.jobs)==(1 if channel=='email' else 0)
        assert next(iter(store.messages.values()))['resource_security_labels']==list(labels())
        assert model.translate.await_count==1
        access.allows.assert_awaited_once()
    asyncio.run(run())


def test_generating_unknown_outcome_never_repeats_model_after_restart():
    async def run():
        identity,store,access,messages,model,service,request=setup()
        async def cancel(*args):raise asyncio.CancelledError()
        model.translate.side_effect=cancel
        with pytest.raises(asyncio.CancelledError):await service.prepare(identity,store.case_id,request)
        with pytest.raises(AgentError):await StaffTranslations(store,messages,model,private_translation=True).prepare(identity,store.case_id,request)
        assert len(store.rows)==1 and next(iter(store.rows.values()))['state']=='generating' and model.translate.await_count==1
    asyncio.run(run())


@pytest.mark.parametrize('change',['scope','session','handler','labels','version','languages'])
def test_model_await_rechecks_current_staff_scope_and_exact_context(change):
    async def run():
        identity,store,access,messages,model,service,request=setup()
        async def changed(*args):
            assert not store.in_tx
            if change=='scope':store.scope=False
            if change=='session':store.active=False
            if change=='handler':store.membership=False
            if change=='labels':store.case['resource_security_labels']=[]
            if change=='version':store.chat_row['version']+=1
            if change=='languages':store.languages=AsyncMock(return_value=['id'])
            return 'Booking PK-A-001 is valid until 20:30.'
        model.translate.side_effect=changed
        with pytest.raises((AuthError,DraftError,AgentError)):await service.prepare(identity,store.case_id,request)
        assert not store.messages and all(row['state']!='ready' for row in store.rows.values())
        access.allows.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('verdict',[False,None,'true',SourceFailure(503)])
def test_recipient_denial_or_outage_leaves_ready_preview_unchanged(verdict):
    async def run():
        identity,store,access,messages,model,service,request,row=await ready()
        async def denied(**kw):
            assert not store.in_tx
            if isinstance(verdict,Exception):raise verdict
            return verdict
        access.allows.side_effect=denied
        approval=TranslationApproval(text=row['translated_body'],approved=True,expected_revision=2)
        with pytest.raises((AuthError,SourceFailure)):await service.approve(identity,store.case_id,row['id'],approval,str(uuid4()))
        assert store.rows[row['id']]==row and not store.messages and not store.jobs and not store.events
    asyncio.run(run())


@pytest.mark.parametrize('failure',['participant_write','translation','event'])
def test_atomic_participant_failure_rolls_back_translation_message_event_job(failure):
    async def run():
        identity,store,access,messages,model,service,request,row=await ready()
        if failure=='participant_write':store.fail_sent=True
        else:store.corrupt=failure
        approval=TranslationApproval(text=row['translated_body'],approved=True,expected_revision=2)
        with pytest.raises((RuntimeError,DraftError)):await service.approve(identity,store.case_id,row['id'],approval,str(uuid4()))
        assert store.rows[row['id']]==row and not store.messages and not store.jobs and not store.events
        assert store.chat_row['version']==3 and store.sequence==5 and store.event_sequence==7
    asyncio.run(run())


def test_sent_replay_current_grants_and_reader_acl_ignore_old_pending_handler_versions():
    async def run():
        identity,store,access,messages,model,service,request,row=await ready()
        approval=TranslationApproval(text=row['translated_body'],approved=True,expected_revision=2);key=str(uuid4())
        first=await service.approve(identity,store.case_id,row['id'],approval,key)
        store.case.update(version=50,status='closed',assigned_membership_id=None)
        store.chat_row.update(version=60,generation_epoch=70,mode='ended',handler_membership_id=None)
        store.membership=False
        assert await service.approve(identity,store.case_id,row['id'],approval,key)==first
        assert len(store.messages)==len(store.jobs)==1 and model.translate.await_count==1
        access.allows.side_effect=None;access.allows.return_value=False
        with pytest.raises(AuthError):await service.approve(identity,store.case_id,row['id'],approval,key)
        store.scope=False
        with pytest.raises(AuthError):await service.get(identity,store.case_id,row['id'])
    asyncio.run(run())


@pytest.mark.parametrize('path',['get','prepare','sent_replay'])
@pytest.mark.parametrize('records',[None,{},list(labels())])
def test_default_off_denies_stored_private_or_malformed_preview_on_all_paths(path,records):
    async def run():
        identity,store,access,messages,model,service,request,row=await ready()
        legacy=StaffTranslations(store,messages,model)
        store.case['resource_security_labels']=[]
        store.rows[row['id']]['resource_security_labels']=records
        if path=='sent_replay':store.rows[row['id']].update(state='sent',reviewed_body=row['translated_body'],send_key='test-command-12345',message_id=uuid4())
        with pytest.raises(AgentError):
            if path=='get':await legacy.get(identity,store.case_id,row['id'])
            elif path=='prepare':await legacy.prepare(identity,store.case_id,request)
            else:
                approval=TranslationApproval(text=row['translated_body'],approved=True,expected_revision=2)
                await legacy.approve(identity,store.case_id,row['id'],approval,'test-command-12345')
        assert model.translate.await_count==1 and not store.messages and not store.jobs
    asyncio.run(run())


@pytest.mark.parametrize('private',[False,True])
def test_sql_translation_columns_keep_public_legacy_insert_compatible(private):
    async def run():
        from airport_assistant.infrastructure.database.translations import TranslationSQL
        identity,store,access,messages,model,service,request=setup()
        connection=SimpleNamespace(execute=AsyncMock())
        context=SimpleNamespace(tenant_id=identity.session.tenant_id,principal_id=identity.session.principal_id,session_id=identity.session.id)
        tx=TranslationSQL(connection,'app',context);tx.translation=AsyncMock(return_value={})
        req=SecurityLabels.from_records(identity.session.tenant_id,list(labels())) if private else None
        await tx.create_translation(store.case_id,request,'a'*64,requirements=req,source_context_hash='b'*64 if private else None)
        call=connection.execute.await_args;sql=call.args[0].as_string()
        assert sql.count('%s')==len(call.args[1])
        assert ('resource_security_labels' in sql) is private and ('source_context_hash' in sql) is private
    asyncio.run(run())


def test_prepare_finish_deadline_crossed_in_exit_session_never_commits_ready():
    async def run():
        identity,store,access,messages,model,service,request=setup()
        start=store.wall
        async def translate(*args):
            assert not store.in_tx
            store.wall=start+timedelta(seconds=119.9);store.clock=119.9
            return 'Booking PK-A-001 is valid until 20:30.'
        model.translate.side_effect=translate
        original=store.session_active
        async def exit_session():
            result=await original()
            if any(row['state']=='ready' for row in store.rows.values()):
                store.wall=start+timedelta(seconds=120.1);store.clock=120.1
            return result
        store.session_active=exit_session
        with pytest.raises(SourceFailure):await service.prepare(identity,store.case_id,request)
        assert all(row['state']!='ready' for row in store.rows.values()) and not store.messages
        assert model.translate.await_count==1
    asyncio.run(run())


def test_approval_deadline_in_exit_session_rolls_back_entire_send_graph():
    async def run():
        identity,store,access,messages,model,service,request,row=await ready()
        store.rows[row['id']]['expires_at']=store.wall+timedelta(seconds=.5)
        original=store.session_active
        async def exit_session():
            result=await original()
            if store.rows[row['id']]['state']=='sent':store.clock=.75
            return result
        store.session_active=exit_session
        approval=TranslationApproval(text=row['translated_body'],approved=True,expected_revision=2)
        with pytest.raises(AgentError):await service.approve(identity,store.case_id,row['id'],approval,str(uuid4()))
        assert store.rows[row['id']]['state']=='ready' and not store.messages and not store.events and not store.jobs
    asyncio.run(run())


def test_slow_commit_approval_unknown_recovers_original_sent_receipt_only_once():
    async def run():
        identity,store,access,messages,model,service,request,row=await ready()
        store.rows[row['id']]['expires_at']=store.wall+timedelta(seconds=.5)
        def after_commit():
            if store.rows[row['id']]['state']=='sent':store.clock=.75
        store.after_commit=after_commit
        approval=TranslationApproval(text=row['translated_body'],approved=True,expected_revision=2);key=str(uuid4())
        with pytest.raises(SourceFailure):await service.approve(identity,store.case_id,row['id'],approval,key)
        assert store.rows[row['id']]['state']=='sent' and len(store.messages)==1
        recovered=await service.approve(identity,store.case_id,row['id'],approval,key)
        assert recovered['message_id']==store.rows[row['id']]['message_id'] and len(store.messages)==len(store.jobs)==len(store.events)==1
        assert model.translate.await_count==1
    asyncio.run(run())
