"""Short scheduler transactions surround, never contain, async business work."""
import asyncio
from dataclasses import dataclass,field
from typing import Literal,Protocol
from uuid import UUID,uuid4


@dataclass(frozen=True)
class Lease:
    tenant_id:UUID
    id:UUID
    case_id:UUID|None
    draft_id:UUID|None
    kind:str
    attempts:int
    generation:int
    worker_id:UUID
    token:UUID=field(repr=False)
    replay_policy:str='reconcile'


@dataclass(frozen=True)
class Outcome:
    state:Literal['succeeded','retry','dead','blocked','unknown']
    code:Literal['OK','RETRYABLE','PERMANENT','BLOCKED','UNKNOWN','HANDLER_ERROR','HANDLER_TIMEOUT','BAD_RESULT']

    def __post_init__(self):
        codes={'succeeded':{'OK'},'retry':{'RETRYABLE'},'dead':{'PERMANENT'},'blocked':{'BLOCKED'},'unknown':{'UNKNOWN','HANDLER_ERROR','HANDLER_TIMEOUT','BAD_RESULT'}}
        if self.state not in codes or self.code not in codes[self.state]:raise ValueError('Invalid job outcome.')


class Scheduler(Protocol):
    async def claim(self,worker_id:UUID,kinds:tuple[str,...],seconds:int)->Lease|None:...
    async def renew(self,lease:Lease,seconds:int)->bool:...
    async def finish(self,lease:Lease,outcome:Outcome)->bool:...


class JobWorker:
    def __init__(self,scheduler:Scheduler,handlers,*,lease_seconds=30,handler_timeout=60,worker_id=None):
        if type(lease_seconds) is not int or not 3<=lease_seconds<=300:raise ValueError('Invalid lease duration.')
        if not 0<handler_timeout<=600:raise ValueError('Invalid handler timeout.')
        self._scheduler,self._handlers=scheduler,dict(handlers)
        self._lease_seconds,self._timeout,self.id=lease_seconds,handler_timeout,worker_id or uuid4()

    async def _heartbeat(self,lease):
        while True:
            await asyncio.sleep(self._lease_seconds/3)
            try:
                if await self._scheduler.renew(lease,self._lease_seconds) is not True:return
            except Exception:return

    async def run_once(self):
        if not self._handlers:return 'idle'
        lease=await self._scheduler.claim(self.id,tuple(sorted(self._handlers)),self._lease_seconds)
        if lease is None:return 'idle'
        async def handle():
            try:
                deadline=asyncio.timeout(self._timeout)
                async with deadline:
                    result=await self._handlers[lease.kind](lease)
                if deadline.expired():return Outcome('unknown','HANDLER_TIMEOUT')
                if type(result) is not Outcome:return Outcome('unknown','BAD_RESULT')
                return result
            except TimeoutError:return Outcome('unknown','HANDLER_TIMEOUT')
            except Exception:return Outcome('unknown','HANDLER_ERROR')
        heartbeat=asyncio.create_task(self._heartbeat(lease));work=asyncio.create_task(handle())
        try:
            done,_=await asyncio.wait((heartbeat,work),return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                return 'lease_lost'
            result=await work
        finally:
            heartbeat.cancel();work.cancel()
            await asyncio.gather(heartbeat,work,return_exceptions=True)
        accepted=await self._scheduler.finish(lease,result)
        return result.state if accepted else 'lease_lost'
