"""Prompt-adapter boundaries only; mocked outputs do not prove live quality."""
import asyncio
import json

import httpx
import pytest

from airport_assistant.integrations.agent.openai import OpenAIPlanner
from airport_assistant.modules.agent.models import AgentError


MODEL = 'gpt-5.4-mini-2026-03-17'


def completion(name, reference):
    return {'model': MODEL, 'status': 'completed', 'output': [{
        'type': 'function_call', 'status': 'completed', 'call_id': 'qa-call',
        'name': name, 'arguments': json.dumps({'reference': reference}),
    }]}


@pytest.mark.parametrize('unavailable', ['read_transaction', 'read_booking_policy'])
def test_tool_named_in_planning_instructions_still_requires_server_advertisement(unavailable):
    calls = []

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert [tool['name'] for tool in payload['tools']] == ['read_booking']
        return httpx.Response(200, json=completion(unavailable, 'Ab_7-x'))

    async def scenario():
        planner = OpenAIPlanner('synthetic-qa-key', transport=httpx.MockTransport(handle))
        with pytest.raises(AgentError) as error:
            await planner.advance('Check booking Ab_7-x.', 'en', ('read_booking',))
        assert error.value.code == 'AGENT_INVALID_RESPONSE'

    asyncio.run(scenario())
    assert len(calls) == 1


def test_booking_policy_keeps_customer_booking_reference_exact_without_prefix_restriction():
    calls = []

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload['input'][-1]['content'] == 'Correction: explain the policy for Ab_7-x.'
        assert payload['tools'][0]['name'] == 'read_booking_policy'
        context = json.loads(payload['input'][0]['content'])
        assert context['ordered_customer_history'] == ['Earlier booking PK-OLD-001.']
        return httpx.Response(200, json=completion('read_booking_policy', 'Ab_7-x'))

    async def scenario():
        planner = OpenAIPlanner('synthetic-qa-key', transport=httpx.MockTransport(handle))
        decision = await planner.advance(
            'Correction: explain the policy for Ab_7-x.', 'en', ('read_booking_policy',),
            memory=('Earlier booking PK-OLD-001.',),
        )
        assert decision.kind == 'tool'
        assert decision.tool.arguments == {'reference': 'Ab_7-x'}

    asyncio.run(scenario())
    assert len(calls) == 1
