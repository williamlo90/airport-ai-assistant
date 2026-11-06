"""AI-01 capability enforcement; no real providers or database connections."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from test_source_labels import observation,payment
from test_source_client import booking
from airport_assistant.modules.source.models import Booking,Policy
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.agent.models import Decision,DraftProposal,Clarification,ToolCall,AgentError
from airport_assistant.modules.agent.service import AgentFoundation
from airport_assistant.agent_config import AgentSettings


class Fixture:
    def __init__(self,kind='customer'):
        self.identity=SimpleNamespace(session=SimpleNamespace(kind=kind,tenant_id=uuid4(),id=uuid4(),principal_id=uuid4() if kind=='customer' else None))
        self.conv=SimpleNamespace(id=uuid4(),version=2,generation_epoch=1,mode='ai',case_id=None,language='en',last_sequence=1,resource_security_labels=())
        self.msg=SimpleNamespace(id=uuid4(),sequence=1,body='Please investigate parking booking PK-A-001 and transaction TX-A-001.')
        async def read(*args):return self.conv,self.msg
        self.conversations=SimpleNamespace(customer_message=AsyncMock(side_effect=read))
        self.model=SimpleNamespace(decide=AsyncMock(return_value=Decision(kind='clarify',clarification=Clarification(question='What happened?'))))
        self.drafts=SimpleNamespace(create=AsyncMock(return_value='pending draft'))
        self.source=SimpleNamespace(booking=AsyncMock(return_value=observation(Booking,booking())),transaction=AsyncMock(return_value=payment(True,True)),policy=AsyncMock())
        self.private=SimpleNamespace(allows=AsyncMock(return_value=True))
        self.service=AgentFoundation(self.model,self.conversations,self.drafts,self.source,self.private)
    async def scope(self):return await self.service.scope(self.identity,self.conv.id,self.msg.id,booking_references=('PK-A-001',),transaction_references=('TX-A-001',))


def test_plan_is_single_step_and_does_not_execute_the_selected_tool():
    async def scenario():
        f=Fixture();scope=await f.scope()
        call=ToolCall(call_id='call_1',name='read_booking',arguments={'reference':'PK-A-001'})
        f.model.decide.return_value=Decision(kind='tool',tool=call)
        assert (await f.service.plan(scope)).tool==call
        f.source.booking.assert_not_awaited();f.drafts.create.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('name,args',[
    ('confirm',{}),('close_case',{}),('execute_sql',{'sql':'select *'}),('fetch',{'url':'https://evil.test'}),
    ('read_booking',{'reference':'PK-B-001'}),('read_transaction',{'reference':'TX-B-001'}),
    ('read_booking',{'reference':'PK-A-001','tenant_id':'foreign'}),
    ('read_booking',{'reference':'PK-A-001','principal_id':'foreign'}),
    ('search_knowledge',{'query':'ignore rules','policy_reference':'OTHER'})])
def test_model_cannot_expand_tool_or_identity_scope(name,args):
    async def scenario():
        f=Fixture();scope=await f.scope()
        with pytest.raises(AgentError) as e:await f.service.execute_read(scope,{'call_id':'call_1','name':name,'arguments':args})
        assert e.value.code=='AGENT_TOOL_DENIED'
        f.source.booking.assert_not_awaited();f.source.transaction.assert_not_awaited();f.drafts.create.assert_not_awaited()
    asyncio.run(scenario())


def test_transaction_observation_carries_whole_dto_and_independent_booking_labels():
    async def scenario():
        f=Fixture();scope=await f.scope()
        result=await f.service.execute_read(scope,ToolCall(call_id='call_1',name='read_transaction',arguments={'reference':'TX-A-001'}))
        assert result.data.amount_minor==10000 and result.authority=='untrusted_tool_data'
        labels=result.labels.to_records()
        assert {r['resource_type'] for r in labels}=={'booking','transaction'}
        assert 'payment_details:read' in next(r['required_source_scopes'] for r in labels if r['resource_type']=='transaction')
        assert '10000' not in repr(result)
    asyncio.run(scenario())


def test_revoked_source_grants_block_result_and_policy_uses_booking_reference():
    async def scenario():
        f=Fixture();scope=await f.scope();f.private.allows.return_value=False
        with pytest.raises(AuthError):await f.service.execute_read(scope,ToolCall(call_id='call_1',name='read_booking',arguments={'reference':'PK-A-001'}))
        f.private.allows.return_value=True
        book=f.source.booking.return_value
        f.source.policy.return_value=Policy(reference=book.policy_reference,service_type='parking',version=2,
            effective_from=book.observed_at,observed_at=book.observed_at,source_updated_at=book.source_updated_at,instructions={'en':'Rules','id':'Aturan'})
        result=await f.service.execute_read(scope,ToolCall(call_id='call_2',name='read_booking_policy',arguments={'reference':'PK-A-001'}))
        f.source.policy.assert_awaited_once_with(f.identity,book.policy_reference)
        assert result.labels.labels[0].reference=='PK-A-001'
    asyncio.run(scenario())


def test_draft_is_based_only_on_persisted_customer_report_and_cannot_commit():
    async def scenario():
        f=Fixture();scope=await f.scope()
        f.model.decide.return_value=Decision(kind='draft',proposal=DraftProposal(category='transaction_issue',service_type='parking',summary='Investigate payment'))
        assert await f.service.prepare_draft(scope,contact_email='user@example.com')=='pending draft'
        args,kwargs=f.drafts.create.await_args
        assert args[0] is f.identity and args[1]==f.conv.id and args[2]=='create_case'
        assert args[3].customer_report==f.msg.body and args[3].booking_reference=='PK-A-001'
        assert kwargs=={'expected_conversation_version':2,'expected_generation_epoch':1}
        f.model.decide.assert_awaited_once_with(f.msg.body,'en',(),draft=True)
        assert not hasattr(f.drafts,'confirm')
    asyncio.run(scenario())


@pytest.mark.parametrize('change',['version','epoch','mode','report','session'])
def test_model_in_flight_context_changes_prevent_draft_write(change):
    async def scenario():
        f=Fixture();scope=await f.scope()
        async def generate(*args,**kwargs):
            if change=='version':f.conv.version+=1
            if change=='epoch':f.conv.generation_epoch+=1
            if change=='mode':f.conv.mode='human'
            if change=='report':f.msg.body='Corrected report'
            if change=='session':f.conversations.customer_message.side_effect=AuthError(401,'AUTH_REQUIRED')
            return Decision(kind='draft',proposal=DraftProposal(category='information',service_type='general',summary='Help'))
        f.model.decide.side_effect=generate
        with pytest.raises((AgentError,AuthError)):await f.service.prepare_draft(scope)
        f.drafts.create.assert_not_awaited()
    asyncio.run(scenario())


def test_anonymous_has_no_private_source_tools_and_reference_prefix_does_not_match():
    async def scenario():
        f=Fixture('anonymous');scope=await f.scope()
        assert f.service.tools(scope)==()
        with pytest.raises(AgentError):await f.service.scope(f.identity,f.conv.id,f.msg.id,booking_references=('PK-A',))
    asyncio.run(scenario())


def test_config_is_opt_in_and_does_not_reuse_evaluation_key(monkeypatch):
    monkeypatch.setenv('EVAL_OPENAI_API_KEY','eval-only')
    assert not AgentSettings(_env_file=None).enabled
    with pytest.raises(ValidationError):AgentSettings(enabled=True,_env_file=None)
    assert 'private-key' not in repr(AgentSettings(enabled=True,api_key='private-key',_env_file=None))


def test_old_customer_message_cannot_start_a_new_scope_after_later_message():
    async def scenario():
        f=Fixture();f.conv.last_sequence=2
        with pytest.raises(AgentError) as error:await f.scope()
        assert error.value.code=='AGENT_CONTEXT_CHANGED'
        f.model.decide.assert_not_awaited()
    asyncio.run(scenario())


def test_enabled_composition_requires_dependencies_and_binds_model_snapshot():
    from airport_assistant.composition import build_services
    from airport_assistant.auth_config import AuthSettings
    from airport_assistant.knowledge_config import KnowledgeSettings
    auth=AuthSettings(SESSION_SECRET='k'*48,_env_file=None)
    assert build_services(object(),auth).agent is None
    configured=AgentSettings(enabled=True,api_key='fake',_env_file=None)
    built=build_services(object(),auth,agent_settings=configured)
    assert isinstance(built.agent,AgentFoundation) and built.agent.model.model=='gpt-5.4-mini-2026-03-17'
    kb=AgentSettings(enabled=True,api_key='fake',knowledge_min_score=.3,knowledge_policy_id='test-policy',_env_file=None)
    with pytest.raises(ValueError):build_services(object(),auth,agent_settings=kb)
    enabled=build_services(object(),auth,agent_settings=kb,knowledge_settings=KnowledgeSettings(enabled=True,api_key='fake',_env_file=None))
    assert enabled.agent.evidence is not None


def test_knowledge_query_preserves_server_policy_language_and_time():
    from airport_assistant.modules.knowledge.retrieval import Search
    async def scenario():
        f=Fixture();selection=Search(query='original',service_type='parking',language='id',as_of='2026-01-01T00:00:00Z',policy_reference='OLD',terminal='T2')
        outcome=SimpleNamespace(snapshot=None,status='insufficient_sources')
        f.service.evidence=SimpleNamespace(collect=AsyncMock(return_value=outcome))
        scope=await f.service.scope(f.identity,f.conv.id,f.msg.id,knowledge=selection)
        call=ToolCall(call_id='call_1',name='search_knowledge',arguments={'query':'parking extension'})
        result=await f.service.execute_read(scope,call)
        actor,actual=f.service.evidence.collect.await_args.args
        assert actor is f.identity and actual.query=='parking extension'
        assert actual.policy_reference=='OLD' and actual.language=='id' and actual.as_of==selection.as_of and actual.terminal=='T2'
        assert result.data is outcome
        for override in ('tenant_id','as_of','policy_reference','language','allowed_roles'):
            bad=ToolCall(call_id='call_2',name='search_knowledge',arguments={'query':'parking',override:'override'})
            with pytest.raises(AgentError):await f.service.execute_read(scope,bad)
        assert f.service.evidence.collect.await_count==1
    asyncio.run(scenario())


@pytest.mark.parametrize('mismatch',['booking','policy'])
def test_source_observation_must_match_requested_or_booking_bound_reference(mismatch):
    async def scenario():
        f=Fixture();scope=await f.scope()
        if mismatch=='booking':
            f.source.booking.return_value=observation(Booking,{**booking(),'reference':'PK-B-001'})
            name='read_booking'
        else:
            book=f.source.booking.return_value
            f.source.policy.return_value=Policy(reference='wrong-policy',service_type='parking',version=1,
                effective_from=book.observed_at,observed_at=book.observed_at,source_updated_at=book.source_updated_at,instructions={'en':'Rules','id':'Aturan'})
            name='read_booking_policy'
        with pytest.raises(AgentError) as error:await f.service.execute_read(scope,ToolCall(call_id='call_1',name=name,arguments={'reference':'PK-A-001'}))
        assert error.value.code=='AGENT_INVALID_RESPONSE'
        f.private.allows.assert_not_awaited()
    asyncio.run(scenario())
