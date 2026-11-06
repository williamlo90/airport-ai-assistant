"""Independent diagnostic regression; entirely synthetic, executed only by Lead."""
import asyncio

import httpx
import pytest

from gemini_diagnostic import ClassifiedNetwork


def test_timeout_after_response_headers_is_classified_without_exception_text():
    marker = 'synthetic-private-network-detail'

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{'
            raise httpx.ReadTimeout(marker)

    async def scenario():
        record = {}
        network = httpx.MockTransport(lambda request: httpx.Response(200, stream=SlowBody()))
        async with httpx.AsyncClient(transport=ClassifiedNetwork(record, network)) as client:
            with pytest.raises(httpx.ReadTimeout):
                async with client.stream('POST', 'https://diagnostic.invalid/probe') as response:
                    async for _ in response.aiter_bytes():
                        pass
        assert record['network_category'] == 'read_timeout'
        assert marker not in str(record)

    asyncio.run(scenario())
