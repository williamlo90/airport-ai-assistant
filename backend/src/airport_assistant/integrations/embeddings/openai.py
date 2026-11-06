"""Small dedicated embedding adapter: fixed origin/model, bounded IO, no retries."""
import asyncio
import json
import httpx
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.knowledge.retrieval import MODEL, DIMENSIONS, normalized


class OpenAIEmbeddings:
    def __init__(self, key: str, *, transport=None):
        if not key or not key.strip():
            raise ValueError('Embedding credentials required.')
        self._key, self._transport = key, transport
        self._slot = asyncio.Semaphore(1)

    async def embed(self, texts):
        if not isinstance(texts, list) or not 1 <= len(texts) <= 8 or any(not isinstance(t, str) or not t or '\0' in t or len(t.encode('utf-8')) > 4000 for t in texts):
            raise DraftError(422, 'VALIDATION_ERROR')
        try:
            # Reject overload instead of allowing unbounded queued model calls.
            async with asyncio.timeout(0.1):
                await self._slot.acquire()
        except TimeoutError:
            raise DraftError(503, 'EMBEDDING_BUSY') from None
        try:
            async with asyncio.timeout(15):
                async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False, transport=self._transport) as client:
                    async with client.stream('POST', 'https://api.openai.com/v1/embeddings',
                        headers={'Authorization': 'Bearer '+self._key},
                        json={'model': MODEL, 'dimensions': DIMENSIONS, 'encoding_format': 'float', 'input': texts}) as response:
                        if response.status_code != 200:
                            raise DraftError(503, 'EMBEDDING_UNAVAILABLE')
                        buffer = bytearray()
                        async for part in response.aiter_bytes():
                            buffer.extend(part)
                            if len(buffer) > 1_000_000:
                                raise DraftError(503, 'EMBEDDING_UNAVAILABLE')
            data = json.loads(buffer)
            if data['model'] != MODEL or len(data['data']) != len(texts):
                raise ValueError
            rows = data['data']
            if any(type(r['index']) is not int for r in rows) or sorted(r['index'] for r in rows) != list(range(len(texts))):
                raise ValueError
            return [normalized(row['embedding']) for row in sorted(rows, key=lambda r: r['index'])]
        except (httpx.HTTPError, TimeoutError, KeyError, TypeError, ValueError, OverflowError):
            raise DraftError(503, 'EMBEDDING_UNAVAILABLE') from None
        finally:
            self._slot.release()
