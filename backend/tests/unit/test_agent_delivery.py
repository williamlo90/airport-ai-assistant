"""Real graph -> delivery boundary; no provider/database requests."""
import asyncio
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
from test_agent_workflow import setup
from airport_assistant.modules.agent.delivery import AgentDelivery
from airport_assistant.integrations.agent.graph import AgentGraph
from airport_assistant.modules.agent.models import Decision,Clarification


def test_status_reads_receipt_without_loading_or_resuming_graph():
    async def scenario():
        run=uuid4();identity=SimpleNamespace(session=SimpleNamespace(tenant_id=uuid4(),id=uuid4()))
        saved=dict(state='published',message_id=uuid4(),sequence=2)
        publication=SimpleNamespace(receipt=AsyncMock(return_value=saved))
        workflow=SimpleNamespace(resume=AsyncMock(),engine=SimpleNamespace(load=AsyncMock()))
        result=await AgentDelivery(workflow,publication,b'test-only').status(identity,run)
        assert result==dict(run_id=run,**saved)
        workflow.resume.assert_not_awaited();workflow.engine.load.assert_not_awaited()
    asyncio.run(scenario())


def test_status_pending_and_draft_use_authorized_reads_without_model_or_mutation():
    async def scenario():
        run=uuid4();draft=SimpleNamespace(id=uuid4());identity=SimpleNamespace(session=SimpleNamespace(tenant_id=uuid4(),id=uuid4()))
        row=dict(phase='planning',result_draft_id=None)
        entered=[]
        @asynccontextmanager
        async def unit(who,id):
            entered.append((who,id));yield object(),object(),row
        drafts=SimpleNamespace(get=AsyncMock(return_value=draft))
        workflow=SimpleNamespace(resume=AsyncMock(),engine=SimpleNamespace(load=AsyncMock(),checkpoints=SimpleNamespace(unit=unit),foundation=SimpleNamespace(drafts=drafts)))
        delivery=AgentDelivery(workflow,SimpleNamespace(receipt=AsyncMock(return_value=None)),b'test-only')
        assert (await delivery.status(identity,run))['state']=='unresolved'
        assert entered==[(identity,run)]
        row.update(phase='done',result_draft_id=draft.id)
        assert await delivery.status(identity,run)==dict(run_id=run,state='draft',draft_id=draft.id)
        drafts.get.assert_awaited_once_with(identity,draft.id)
        workflow.resume.assert_not_awaited();workflow.engine.load.assert_not_awaited()
    asyncio.run(scenario())


class Publication:
    def __init__(self,f):self.f=f;self.tickets={};self.saved={};self.bodies=[]
    async def receipt(self,identity,ticket,*,discard=False):
        if ticket.id in self.saved:return self.saved[ticket.id]
        if discard and ticket.id in self.tickets:
            self.saved[ticket.id]=dict(state='discarded',reason='STALE_GENERATION')
            return self.saved[ticket.id]
    async def capture_bound(self,identity,cid,ticket,version,epoch):self.tickets.setdefault(ticket.id,(version,epoch))
    async def publish(self,identity,ticket,body):
        if self.tickets[ticket.id]!=(self.f.conv.version,self.f.conv.generation_epoch):
            return dict(state='discarded',reason='STALE_GENERATION')
        self.bodies.append(body);self.f.conv.version+=1
        self.saved[ticket.id]=dict(state='published',message_id=uuid4(),sequence=2)
        return self.saved[ticket.id]


def test_graph_clarification_publishes_once_and_restart_returns_receipt_without_model():
    async def scenario():
        f,store,model,engine,request=setup();publication=Publication(f)
        model.advance=AsyncMock(return_value=Decision(kind='clarify',clarification=Clarification(question='Which service?')))
        request=request.model_copy(update={'booking_references':()})
        delivery=AgentDelivery(AgentGraph(engine),publication,b'test-only')
        id=await delivery.start(f.identity,request)
        first=await delivery.resume(f.identity,id)
        again=await AgentDelivery(AgentGraph(engine),publication,b'test-only').resume(f.identity,id)
        assert first==again and first['state']=='published'
        assert publication.bodies==['Which service?'] and model.advance.await_count==1
        assert 'nonce' not in repr(delivery.ticket(f.identity,id))
    asyncio.run(scenario())


def test_private_output_is_not_published_or_replayed_on_restart():
    async def scenario():
        f,store,model,engine,request=setup();publication=Publication(f)
        delivery=AgentDelivery(AgentGraph(engine),publication,b'test-only')
        # Simulate an already persisted historical run; new starts are rejected
        # by the explicit activation admission before any source/model work.
        id=await engine.start(f.identity,request)
        result=await delivery.resume(f.identity,id)
        assert result['state']=='review' and result['reason']=='PRIVATE_PERSISTENCE_REQUIRED'
        assert not publication.bodies
        assert (await delivery.resume(f.identity,id))['state']=='discarded'
        assert not publication.bodies
    asyncio.run(scenario())


def test_context_change_during_real_graph_prevents_delivery():
    async def scenario():
        f,store,model,engine,request=setup();publication=Publication(f)
        async def change(*args,**kwargs):
            f.conv.generation_epoch+=1
            return Decision(kind='clarify',clarification=Clarification(question='Obsolete'))
        model.advance=AsyncMock(side_effect=change)
        request=request.model_copy(update={'booking_references':()})
        delivery=AgentDelivery(AgentGraph(engine),publication,b'test-only')
        id=await delivery.start(f.identity,request)
        assert (await delivery.resume(f.identity,id))['state']=='discarded'
        assert not publication.bodies
    asyncio.run(scenario())


def test_handoff_is_pending_draft_only_and_same_input_cannot_allocate_extra_runs():
    async def scenario():
        f,store,model,engine,request=setup();publication=Publication(f)
        delivery=AgentDelivery(AgentGraph(engine),publication,b'test-only')
        request=request.model_copy(update={'mode':'draft','action_type':'request_handoff','booking_references':()})
        id=await delivery.start(f.identity,request)
        assert await delivery.start(f.identity,request.model_copy(update={'request_key':uuid4()}))==id
        result=await delivery.resume(f.identity,id)
        assert result['state']=='draft' and store.drafts[0].state=='pending'
        assert not publication.tickets and not publication.bodies
    asyncio.run(scenario())


def test_private_start_disabled_stops_before_workflow_source_or_model():
    import pytest
    from airport_assistant.modules.agent.models import AgentError
    async def scenario():
        f,store,model,engine,request=setup()
        workflow=AgentGraph(engine)
        workflow.start=AsyncMock()
        delivery=AgentDelivery(workflow,Publication(f),b'test-only')
        with pytest.raises(AgentError) as error:await delivery.start(f.identity,request)
        assert error.value.code=='AGENT_PRIVATE_UNAVAILABLE'
        workflow.start.assert_not_awaited()
        assert not model.calls and not store.rows
    asyncio.run(scenario())


def test_private_fact_proof_is_collected_after_generation_binding_and_published_without_extra_load(monkeypatch):
    from airport_assistant.modules.agent.private_publication import PrivatePublicationPreparation
    async def scenario():
        identity=SimpleNamespace(session=SimpleNamespace(tenant_id=uuid4(),id=uuid4()))
        run=uuid4();labels=object();order=[]
        result=SimpleNamespace(status='waiting_user',labels=labels)
        work=SimpleNamespace(run=dict(mode='assist',conversation_id=uuid4(),conversation_version=1,generation_epoch=0),result=result)
        async def load(*args):
            assert 'proof' not in order;order.append('load');return work
        async def capture(*args,**kwargs):
            assert 'proof' not in order and kwargs['requirements'] is labels;order.append('capture')
        proof=object()
        async def reserve(*args):
            assert order[-1]=='capture';order.append('reserve');return SimpleNamespace(requirements=labels)
        async def collect(*args):
            assert order[-1]=='preflight';order.append('proof');return SimpleNamespace(body='Controlled private clarification',proof=proof)
        async def publish(*args,**kwargs):
            assert order[-1]=='reserve' and args[3] is labels;order.append('preflight')
            prepared=await args[2]()
            assert prepared.proof is proof;order.append('publish')
            return dict(state='published',message_id=uuid4(),sequence=2)
        engine=SimpleNamespace(load=load)
        workflow=SimpleNamespace(engine=engine,resume=AsyncMock(return_value=result))
        publication=SimpleNamespace(private_capability=object(),receipt=AsyncMock(return_value=None),capture_bound=capture,publish_prepared=publish)
        monkeypatch.setattr(PrivatePublicationPreparation,'reserve',reserve)
        monkeypatch.setattr(PrivatePublicationPreparation,'collect',collect)
        receipt=await AgentDelivery(workflow,publication,b'test-only').resume(identity,run)
        assert receipt['state']=='published' and order==['load','load','capture','reserve','preflight','proof','publish']
    asyncio.run(scenario())
