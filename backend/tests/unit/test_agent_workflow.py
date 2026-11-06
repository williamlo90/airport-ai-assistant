"""Real LangGraph orchestration with transactional fake storage; no external IO."""
import asyncio,copy,json
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
from test_agent_foundation import Fixture
from airport_assistant.modules.agent.workflow import AgentWorkflow,RunRequest
from airport_assistant.modules.agent.models import AgentError,Decision,Clarification,DraftProposal,ToolCall
from airport_assistant.integrations.agent.graph import AgentGraph
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError


class Store:
    def __init__(self,f):self.f=f;self.rows={};self.drafts=[];self.clock=datetime.now(timezone.utc);self.lock=asyncio.Lock();self.active=True;self.fail_receipt=False;self.in_tx=False;self.commits=0
    @asynccontextmanager
    async def transaction(self,context):
        async with self.lock:
            self.in_tx=True
            before=copy.deepcopy((self.rows,self.drafts));self.context=context
            try:yield self
            except BaseException:self.rows,self.drafts=before;raise
            else:self.commits+=1
            finally:self.in_tx=False
    async def session_active(self):return self.active
    async def now(self):return self.clock
    def visible(self,r):return r['session_id']==self.context.session_id and r['tenant_id']==self.context.tenant_id
    async def locate_run(self,id):return self.rows[id]['conversation_id'] if id in self.rows and self.visible(self.rows[id]) else None
    async def run(self,id):return copy.deepcopy(self.rows[id]) if id in self.rows and self.visible(self.rows[id]) else None
    async def request_run(self,cid,key):
        return next((copy.deepcopy(r) for r in self.rows.values() if self.visible(r) and r['conversation_id']==cid and r['request_key']==key),None)
    async def message_run(self,cid,message,mode):
        return next((copy.deepcopy(r) for r in self.rows.values() if self.visible(r) and r['conversation_id']==cid and r['message_id']==message and r['mode']==mode),None)
    async def create_run(self,request,scope,digest,parameters):
        id=uuid4();r=dict(id=id,tenant_id=scope.identity.session.tenant_id,conversation_id=scope.conversation_id,message_id=scope.message_id,
            session_id=scope.identity.session.id,principal_id=scope.identity.session.principal_id,request_key=request.request_key,request_hash=digest,
            conversation_version=scope.conversation_version,generation_epoch=scope.generation_epoch,language=scope.language,mode=request.mode,
            parameters=parameters,phase='ready',revision=1,model_calls=0,read_calls=0,trace=[],pending_call=None,proposal=None,lease=None,lease_until=None,result_draft_id=None,reason='new',
            resource_security_labels=scope.labels.to_records() if scope.labels is not None else [])
        self.rows[id]=r;return copy.deepcopy(r)
    async def update_run(self,run,**changes):
        if self.fail_receipt and changes.get('phase')=='done':self.fail_receipt=False;raise RuntimeError('simulated receipt failure')
        if self.rows[run['id']]['revision']!=run['revision']:raise AgentError(409,'AGENT_RUN_BUSY')
        self.rows[run['id']].update(changes,revision=run['revision']+1)
        return copy.deepcopy(self.rows[run['id']])
    async def find_draft(self,id):return next((d for d in self.drafts if d.id==id),None)
    async def run_result_context(self,id):
        draft=await self.find_draft(id)
        if draft is None:return None
        return {key:getattr(draft,key) for key in ('id','tenant_id','conversation_id','actor_principal_id','actor_session_id',
            'created_session_id','payload_hash','version','resource_security_labels')}


class Drafts:
    def __init__(self,f,store):self.f,self.store=f,store
    async def _conversation(self,tx,identity,cid,*,candidate_only=False):
        if identity.session.id!=self.f.identity.session.id or cid!=self.f.conv.id:raise AuthError(404,'RESOURCE_NOT_FOUND')
        return self.f.conv
    async def create_in_transaction(self,tx,identity,cid,action,payload,**expected):
        if expected['expected_conversation_version']!=self.f.conv.version:raise DraftError(409,'VERSION_CONFLICT')
        draft=SimpleNamespace(id=uuid4(),state='pending',payload=payload,tenant_id=identity.session.tenant_id,
            conversation_id=cid,actor_principal_id=identity.session.principal_id,
            actor_session_id=identity.session.id if identity.session.kind=='anonymous' else None,created_session_id=identity.session.id,
            payload_hash='controlled-fixture-hash',version=1,resource_security_labels=list(self.f.conv.resource_security_labels))
        self.store.drafts.append(draft);return draft
    async def get(self,identity,id):return await self.store.find_draft(id)


class Model:
    def __init__(self):self.calls=[];self.hook=None;self.always_read=False
    async def decide(self,report,language,tools,*,draft=False):
        self.calls.append(('draft',report,language,tools))
        if self.hook:await self.hook()
        return Decision(kind='draft',proposal=DraftProposal(category='booking_issue',service_type='parking',summary='Please help'))
    async def advance(self,report,language,tools,*,memory,observations):
        self.calls.append(('assist',report,language,memory,observations))
        if self.hook:await self.hook()
        if not observations or self.always_read:return Decision(kind='tool',tool=ToolCall(call_id='call_'+str(len(self.calls)),name='read_booking',arguments={'reference':'PK-A-001'}))
        return Decision(kind='clarify',clarification=Clarification(question='Please clarify your booking issue.'))


def setup():
    f=Fixture();f.identity.database_context=SimpleNamespace(session_id=f.identity.session.id,tenant_id=f.identity.session.tenant_id)
    f.conversations.customer_history=AsyncMock(side_effect=lambda *a:(f.msg,))
    store=Store(f);f.service.drafts=Drafts(f,store);model=Model();f.service.model=model
    engine=AgentWorkflow(f.service,store)
    request=RunRequest(conversation_id=f.conv.id,message_id=f.msg.id,request_key=uuid4(),booking_references=('PK-A-001',))
    return f,store,model,engine,request


def test_langgraph_read_then_clarify_refreshes_source_and_does_not_checkpoint_private_output():
    async def scenario():
        f,store,model,engine,request=setup();graph=AgentGraph(engine)
        id=await graph.start(f.identity,request);result=await graph.resume(f.identity,id)
        assert result.status=='waiting_user' and result.labels is not None
        assert len(model.calls)==2 and model.calls[-1][-1][0]['data']['reference']=='PK-A-001'
        checkpoint=json.dumps(store.rows[id],default=str)
        assert 'confirmed' not in checkpoint and 'Please clarify' not in checkpoint and 'T2' not in checkpoint
        # A fresh process resumes only metadata; generated private wording is not replayed.
        again=await AgentGraph(AgentWorkflow(f.service,store)).resume(f.identity,id)
        assert again.status=='waiting_user' and again.observations==() and len(model.calls)==2
    asyncio.run(scenario())


def test_unknown_model_execution_is_not_replayed_after_restart():
    async def scenario():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        work=await engine.load(f.identity,id);await engine._claim(work,'planning')
        with pytest.raises(AgentError):await AgentGraph(engine).resume(f.identity,id)
        store.clock+=timedelta(seconds=121)
        outcome=await AgentGraph(AgentWorkflow(f.service,store)).resume(f.identity,id)
        assert outcome.status=='review' and outcome.reason=='model_outcome_unknown'
        assert model.calls==[] and store.drafts==[]
    asyncio.run(scenario())


def test_crash_before_draft_receipt_rolls_back_write_and_resume_does_not_repeat_model():
    async def scenario():
        f,store,model,engine,request=setup();request=request.model_copy(update={'mode':'draft'})
        id=await engine.start(f.identity,request);store.fail_receipt=True
        with pytest.raises(RuntimeError):await AgentGraph(engine).resume(f.identity,id)
        assert store.rows[id]['phase']=='draft' and not store.drafts and len(model.calls)==1
        # Use a new graph/controller over the same durable records.
        result=await AgentGraph(AgentWorkflow(f.service,store)).resume(f.identity,id)
        assert result.status=='done' and len(store.drafts)==1 and len(model.calls)==1
        again=await AgentGraph(engine).resume(f.identity,id)
        assert again.draft.id==result.draft.id and len(store.drafts)==1 and len(model.calls)==1
    asyncio.run(scenario())


def test_request_key_cannot_be_rebound_and_checkpoint_cannot_grant_cross_session_access():
    async def scenario():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        assert await engine.start(f.identity,request)==id
        with pytest.raises(AgentError):await engine.start(f.identity,request.model_copy(update={'mode':'draft'}))
        wrong=SimpleNamespace(session=SimpleNamespace(kind='customer',id=uuid4()),database_context=SimpleNamespace(tenant_id=f.identity.session.tenant_id,session_id=uuid4()))
        with pytest.raises(AuthError):await engine.load(wrong,id)
        assert not model.calls
    asyncio.run(scenario())


def test_context_change_prevents_resume_and_draft_creation():
    async def scenario():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        f.conv.version+=1
        result=await AgentGraph(engine).resume(f.identity,id)
        assert result.status=='superseded' and not store.drafts and not model.calls
    asyncio.run(scenario())


def test_memory_and_language_survive_and_draft_model_never_receives_private_observations():
    async def scenario():
        f,store,model,engine,request=setup()
        earlier=SimpleNamespace(id=uuid4(),sequence=0,body='Parking booking PK-A-001 needs help.')
        f.msg.body='Bukan besok, maksud saya hari Jumat.';f.conv.language='id'
        f.conversations.customer_history=AsyncMock(return_value=(earlier,f.msg))
        request=request.model_copy(update={'mode':'draft'})
        id=await engine.start(f.identity,request)
        result=await AgentGraph(engine).resume(f.identity,id)
        assert result.draft.payload.booking_reference=='PK-A-001'
        assert earlier.body in result.draft.payload.customer_report and f.msg.body in result.draft.payload.customer_report
        assert model.calls[0][2]=='id' and model.calls[0][3]==()
    asyncio.run(scenario())


def test_changed_source_after_model_produces_review_not_stale_question():
    async def scenario():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        async def change():
            if len(model.calls)==2:
                book=f.source.booking.return_value
                f.source.booking.return_value=book.model_copy(update={'status':'cancelled'})
        model.hook=change
        result=await AgentGraph(engine).resume(f.identity,id)
        assert result.status=='review' and result.reason=='source_context_changed' and result.question is None
    asyncio.run(scenario())


def test_model_budget_is_bounded_and_tracing_disabled_even_if_environment_enables_it(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING','true')
    async def scenario():
        from langsmith import get_tracing_context
        f,store,model,engine,request=setup();model.always_read=True
        async def check():assert get_tracing_context()['enabled'] is False
        model.hook=check
        id=await engine.start(f.identity,request);outcome=await AgentGraph(engine).resume(f.identity,id)
        assert outcome.status=='review' and len(model.calls)==3 and not store.drafts
    asyncio.run(scenario())


def test_private_grant_revoked_during_model_blocks_generated_question():
    async def scenario():
        f,store,model,engine,request=setup()
        async def revoke():
            if len(model.calls)==2:f.private.allows.return_value=False
        model.hook=revoke
        id=await engine.start(f.identity,request)
        with pytest.raises(AuthError):await AgentGraph(engine).resume(f.identity,id)
        assert store.rows[id]['phase']=='review' and not store.drafts
        assert 'Please clarify' not in json.dumps(store.rows[id],default=str)
    asyncio.run(scenario())


def test_expired_read_lease_reexecutes_only_safe_read_and_counts_both_attempts():
    async def scenario():
        f,store,model,engine,request=setup();id=await engine.start(f.identity,request)
        work=await engine.load(f.identity,id);await engine.plan(work)
        work=await engine.load(f.identity,id);await engine._claim(work,'reading')
        store.clock+=timedelta(seconds=121)
        result=await AgentGraph(AgentWorkflow(f.service,store)).resume(f.identity,id)
        assert result.status=='waiting_user' and len(model.calls)==2
        assert store.rows[id]['read_calls']==f.source.booking.await_count+1
        assert not store.drafts
    asyncio.run(scenario())


def test_latest_explicit_reference_correction_removes_old_reference_from_tools():
    async def scenario():
        f,store,model,engine,request=setup()
        earlier=SimpleNamespace(id=uuid4(),body='Please check PK-A-001.')
        f.msg.body='Correction: use PK-B-002 instead.'
        f.conversations.customer_history=AsyncMock(return_value=(earlier,f.msg))
        request=request.model_copy(update={'booking_references':('PK-A-001','PK-B-002')})
        id=await engine.start(f.identity,request);work=await engine.load(f.identity,id)
        assert work.scope.booking_references==frozenset({'PK-B-002'})
        with pytest.raises(AgentError):f.service.validate_call(work.scope,ToolCall(call_id='stale',name='read_booking',arguments={'reference':'PK-A-001'}))
    asyncio.run(scenario())
