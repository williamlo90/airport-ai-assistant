"""Failure containment, not provider uptime. Every request is in-process mocked."""
import asyncio
import httpx
import pytest
import airport_assistant.integrations.agent.openai as adapter
from airport_assistant.modules.agent.models import AgentError


def success():
    return httpx.Response(200,json={'model':'gpt-5.4-mini-2026-03-17','status':'completed','output':[
        {'type':'message','status':'completed','role':'assistant','content':[{'type':'output_text','text':'{"question":"What help do you need?"}'}]}]})


@pytest.mark.parametrize('failure',[401,429,503,'read_timeout','connect_error'])
def test_failure_does_not_retry_and_next_explicit_call_can_acquire_slot(failure):
    calls=[]
    def handle(request):
        calls.append(request)
        assert request.extensions['timeout']==dict(connect=15,read=15,write=15,pool=15)
        if len(calls)>1:
            return success()
        if isinstance(failure,int):
            return httpx.Response(failure,text='private-provider-secret')
        cls=httpx.ReadTimeout if failure=='read_timeout' else httpx.ConnectError
        raise cls('private-provider-secret',request=request)
    async def scenario():
        planner=adapter.OpenAIPlanner('private-provider-secret',transport=httpx.MockTransport(handle))
        with pytest.raises(AgentError) as error:
            await planner.decide('Help','en',())
        assert (error.value.status,error.value.code)==(503,'AGENT_MODEL_UNAVAILABLE')
        assert 'private-provider-secret' not in str(error.value)
        assert len(calls)==1
        assert (await planner.decide('Help','en',())).kind=='clarify'
        assert len(calls)==2  # Explicit new call, never replay of the failed call.
    asyncio.run(scenario())


def test_outer_deadline_is_bounded_and_releases_slot_without_retry(monkeypatch):
    requested=[]
    real_timeout=asyncio.timeout
    def fast_timeout(seconds):
        requested.append(seconds)
        return real_timeout(.02 if seconds==20 else seconds)
    monkeypatch.setattr(adapter.asyncio,'timeout',fast_timeout)
    calls=[]
    async def handle(request):
        calls.append(request)
        if len(calls)==1:
            await asyncio.Event().wait()
        return success()
    async def scenario():
        planner=adapter.OpenAIPlanner('offline-only',transport=httpx.MockTransport(handle))
        with pytest.raises(AgentError) as error:
            await planner.decide('Help','en',())
        assert error.value.code=='AGENT_MODEL_UNAVAILABLE' and len(calls)==1
        assert (await planner.decide('Help','en',())).kind=='clarify'
    asyncio.run(scenario())
    assert requested==[.1,20,.1,20]
