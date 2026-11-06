"""Transport diagnostic metadata contains fixed labels only; no real network."""
import asyncio
import json
import httpx
import pytest
import agent_selection as e
from agent_transport import Bridge
from airport_assistant.modules.agent.models import AgentError


@pytest.mark.parametrize('failure',['connect_timeout','read_timeout','body_timeout','invalid_json','cancel'])
def test_bridge_records_failure_stage_without_secret_or_exception_text(failure):
    record={}
    calls=[]
    payload={'synthetic':'canonical'}
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadTimeout('secret-marker provider body detail')
            yield b''
    async def handle(request):
        calls.append(request)
        if failure=='body_timeout':
            return httpx.Response(200,stream=Body())
        if failure=='invalid_json':
            return httpx.Response(200,content=b'not-json secret-marker')
        if failure=='cancel':
            raise asyncio.CancelledError()
        cls=httpx.ConnectTimeout if failure=='connect_timeout' else httpx.ReadTimeout
        raise cls('secret-marker request details',request=request)
    async def scenario():
        bridge=Bridge('openai',{'model':'gpt-5.4-mini-2026-03-17'},'secret-marker',e.digest(payload),e.digest,record,
                      network=httpx.MockTransport(handle))
        req=httpx.Request('POST','https://api.openai.com/v1/responses',json=payload)
        with pytest.raises(asyncio.CancelledError if failure=='cancel' else AgentError):
            await bridge.handle_async_request(req)
    asyncio.run(scenario())
    assert len(calls)==1
    expected={'body_timeout':'read_timeout','invalid_json':'invalid_provider_envelope','cancel':'cancelled'}.get(failure,failure)
    assert record['network_category']==expected
    assert record['network_phase']==('reading_response_body' if failure=='body_timeout' else 'parsing_response' if failure=='invalid_json' else 'awaiting_response_headers')
    assert 'secret-marker' not in json.dumps(record)
