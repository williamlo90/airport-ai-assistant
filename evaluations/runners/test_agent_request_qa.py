"""Malformed-envelope classification; synthetic HTTP only, executed by Lead."""
import asyncio
import json

import httpx
import pytest

import agent_selection as evaluator
from agent_transport import Bridge
from airport_assistant.modules.agent.models import AgentError


@pytest.mark.parametrize('body', [b'[]', b'null', b'42', b'"unexpected scalar"'])
def test_valid_json_nonobject_response_is_safe_envelope_failure(body):
    record = {}
    canonical = {'synthetic': 'request'}

    async def scenario():
        bridge = Bridge(
            'openai', {'model': 'gpt-5.4-mini-2026-03-17'}, 'synthetic-private-key',
            evaluator.digest(canonical), evaluator.digest, record,
            network=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
        )
        request = httpx.Request('POST', 'https://api.openai.com/v1/responses', json=canonical)
        with pytest.raises(AgentError) as error:
            await bridge.handle_async_request(request)
        assert error.value.code == 'AGENT_MODEL_UNAVAILABLE'

    asyncio.run(scenario())
    assert record['http_status'] == 200
    assert record['network_phase'] == 'parsing_response'
    assert record['network_category'] == 'invalid_provider_envelope'
    assert record['transport_error'] == 'provider_or_envelope_failure'
    assert 'synthetic-private-key' not in json.dumps(record)
