"""Current-source operation deadline, measured before any awaited grant check."""
import asyncio
from airport_assistant.modules.source.models import SourceFailure


class SourceReadOperation:
    def __init__(self,source,clock):
        self._source,self._clock=source,clock
        self._started=None

    def fresh(self):
        if self._started is None:return 2
        elapsed=self._clock()-self._started
        if not 0<=elapsed<2:raise SourceFailure(503)
        return 2-elapsed

    async def allows(self,**kwargs):
        if self._started is None:self._started=self._clock()
        try:
            async with asyncio.timeout(self.fresh()):
                allowed=self._source is not None and await self._source.allows(**kwargs)
        except TimeoutError:raise SourceFailure(503) from None
        self.fresh()
        return allowed
