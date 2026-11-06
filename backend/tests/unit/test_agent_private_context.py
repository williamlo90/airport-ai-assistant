"""Classification at model boundaries; controlled graph, NOT private SQL proof."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from airport_assistant.modules.agent.models import AgentError,ToolCall
from airport_assistant.modules.agent.workflow import AgentWorkflow
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.evidence import SourceEvidence,ObservedSource
from test_agent_foundation import Fixture
from test_agent_workflow import setup as workflow_setup
from test_agent_answers import setup as answer_setup
from test_source_evidence import items


def labels():
    return (dict(source='reference',resource_type='booking',reference='ROOT-PRIVATE',required_source_scopes=['booking:read']),)


def test_scope_carries_current_context_requirements_without_printing_them():
    async def run():
        f=Fixture();f.conv.resource_security_labels=labels();scope=await f.scope()
        assert scope.labels.to_records()==list(labels())
        assert 'ROOT-PRIVATE' not in repr(scope)
    asyncio.run(run())


@pytest.mark.parametrize('stored',[None,'[]',({'invalid':True},)])
def test_missing_or_invalid_classification_is_not_a_public_scope(stored):
    async def run():
        f=Fixture();f.conv.resource_security_labels=stored
        with pytest.raises(AgentError):await f.scope()
        f.model.decide.assert_not_awaited()
    asyncio.run(run())


def test_classification_change_without_version_change_stops_a_model_request():
    async def run():
        f=Fixture();scope=await f.scope();f.conv.resource_security_labels=labels()
        with pytest.raises(AgentError) as error:await f.service.plan(scope)
        assert error.value.code=='AGENT_CONTEXT_CHANGED'
        f.model.decide.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('verdict',[False,None,1,'true',{'allowed':True}])
def test_tool_grants_require_literal_true_not_truthiness(verdict):
    async def run():
        f=Fixture();scope=await f.scope();f.private.allows.return_value=verdict
        with pytest.raises(AuthError):
            await f.service.execute_read(scope,ToolCall(call_id='read',name='read_booking',arguments={'reference':'PK-A-001'}))
    asyncio.run(run())


def test_source_observation_inherits_existing_private_memory_requirements():
    async def run():
        f=Fixture();f.conv.resource_security_labels=labels();scope=await f.scope()
        result=await f.service.execute_read(scope,ToolCall(call_id='read',name='read_transaction',arguments={'reference':'TX-A-001'}))
        assert len(result.labels.labels)==3
        assert result.labels==f.private.allows.await_args.kwargs['labels']
        assert list(labels())[0] in result.labels.to_records()
    asyncio.run(run())


def test_policy_tool_keeps_its_full_checked_booking_for_later_human_consent():
    async def run():
        f=Fixture();book,_,policy=items()
        f.source.booking.return_value=book;f.source.policy.return_value=policy
        scope=await f.scope()
        result=await f.service.execute_read(scope,ToolCall(call_id='read',name='read_booking_policy',arguments={'reference':book.reference}))
        assert result.booking==book
        evidence=SourceEvidence.capture(f.identity.session.tenant_id,(ObservedSource(result.data,result.booking),))
        assert book.location in evidence.display('en')
        assert 'booking=' not in repr(result)
    asyncio.run(run())


def test_policy_refresh_detects_booking_changes_even_when_policy_is_unchanged():
    from dataclasses import replace
    from airport_assistant.modules.agent.models import ToolObservation
    from airport_assistant.modules.source.labels import labels_for_policy
    f=Fixture();book,_,policy=items();bundle=labels_for_policy(f.identity.session.tenant_id,book,policy)
    before=ToolObservation('read_booking_policy',policy,bundle,booking=book)
    after=replace(before,booking=book.model_copy(update={'status':'cancelled'}))
    assert AgentWorkflow.observation_key((before,))!=AgentWorkflow.observation_key((after,))


def test_private_context_knowledge_tool_uses_knowledge_wire_shape_and_preserves_labels():
    async def run():
        f,store,model,engine,request,k=answer_setup();f.conv.resource_security_labels=labels()
        scope=await f.service.scope(f.identity,f.conv.id,f.msg.id,knowledge=k.selection,include_history=True)
        result=await f.service.execute_read(scope,ToolCall(call_id='read',name='search_knowledge',arguments={'query':'Waiting area'}))
        assert result.labels==scope.labels
        wire=AgentWorkflow._wire((result,))
        assert wire[0]['data']['sources'] and 'required_source_scopes' not in json.dumps(wire)
        assert len(AgentWorkflow.observation_key((result,)))==64
    asyncio.run(run())


def test_public_knowledge_answer_based_on_private_memory_is_still_classified():
    from airport_assistant.integrations.agent.graph import AgentGraph
    async def run():
        f,store,model,engine,request,k=answer_setup();f.conv.resource_security_labels=labels()
        engine=AgentWorkflow(f.service,store,private_persistence=True)
        id=await engine.start(f.identity,request)
        result=await AgentGraph(engine).resume(f.identity,id)
        assert result.status=='answer' and result.labels.to_records()==list(labels())
        assert store.rows[id]['resource_security_labels']==list(labels())
        assert 'records_json' not in json.dumps(store.rows[id],default=str)
    asyncio.run(run())


@pytest.mark.parametrize('mode',['assist','draft','answer'])
def test_revocation_after_claim_is_checked_before_any_model_spend(mode):
    async def run():
        if mode=='answer':f,store,model,engine,request,_=answer_setup()
        else:f,store,model,engine,request=workflow_setup();request=request.model_copy(update={'mode':mode})
        id=await engine.start(f.identity,request);work=await engine.load(f.identity,id)
        claim=engine._claim
        async def revoked(*args):
            result=await claim(*args)
            f.conversations.customer_message.side_effect=AuthError(404,'RESOURCE_NOT_FOUND')
            return result
        engine._claim=revoked
        with pytest.raises(AuthError):await engine.plan(work)
        assert model.calls==[]
        if mode=='answer':model.answer.assert_not_awaited()
    asyncio.run(run())


def test_recheck_after_refresh_prevents_sending_a_revoked_context_to_model():
    async def run():
        f,store,model,engine,request=workflow_setup()
        id=await engine.start(f.identity,request);work=await engine.load(f.identity,id)
        refresh=engine._refresh
        async def revoked(*args):
            result=await refresh(*args)
            f.conversations.customer_message.side_effect=AuthError(404,'RESOURCE_NOT_FOUND')
            return result
        engine._refresh=revoked
        with pytest.raises(AuthError):await engine.plan(work)
        assert model.calls==[]
    asyncio.run(run())
