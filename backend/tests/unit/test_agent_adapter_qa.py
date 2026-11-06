"""Independent function-call completion checks; HTTP is fully mocked."""
import asyncio

import httpx
import pytest

from airport_assistant.integrations.agent.openai import OpenAIPlanner
from airport_assistant.modules.agent.models import AgentError


@pytest.mark.parametrize('call_status', ['in_progress', 'incomplete', 'failed'])
def test_unfinished_function_call_is_rejected_even_if_outer_response_is_completed(call_status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={
            'model': 'gpt-5.4-mini-2026-03-17',
            'status': 'completed',
            'output': [{
                'type': 'function_call', 'status': call_status,
                'call_id': 'call_qa', 'name': 'read_booking',
                'arguments': '{"reference":"PK-A-001"}',
            }],
        })

    async def scenario():
        planner = OpenAIPlanner('qa-placeholder', transport=httpx.MockTransport(respond))
        with pytest.raises(AgentError) as error:
            await planner.decide('Please inspect PK-A-001', 'en', ('read_booking',))
        assert (error.value.status, error.value.code) == (502, 'AGENT_INVALID_RESPONSE')

    asyncio.run(scenario())
    assert len(calls) == 1
