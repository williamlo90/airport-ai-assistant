"""Outcome/cancellation semantics without provider calls or database mocks of SQL."""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from airport_assistant.modules.jobs.service import Lease,Outcome,JobWorker


def setup():
    lease=Lease(uuid4(),uuid4(),uuid4(),uuid4(),'case_created',1,1,uuid4(),uuid4())
    scheduler=AsyncMock();scheduler.claim.return_value=lease;scheduler.finish.return_value=True;scheduler.renew.return_value=True
    return lease,scheduler


def test_empty_registry_does_not_claim_or_complete():
    _,scheduler=setup()
    assert asyncio.run(JobWorker(scheduler,{}).run_once())=='idle'
    scheduler.claim.assert_not_awaited();scheduler.finish.assert_not_awaited()


@pytest.mark.parametrize('state,code',[('succeeded','OK'),('retry','RETRYABLE'),('dead','PERMANENT'),('blocked','BLOCKED'),('unknown','UNKNOWN')])
def test_explicit_handler_outcome_is_fenced(state,code):
    lease,scheduler=setup()
    handler=AsyncMock(return_value=Outcome(state,code))
    assert asyncio.run(JobWorker(scheduler,{'case_created':handler}).run_once())==state
    scheduler.finish.assert_awaited_once_with(lease,Outcome(state,code))


def test_exception_or_timeout_is_unknown_never_automatic_retry(caplog):
    async def scenario():
        for timed in (False,True):
            _,scheduler=setup()
            async def handler(lease):
                if timed:await asyncio.sleep(1)
                raise RuntimeError('private-provider-response')
            assert await JobWorker(scheduler,{'case_created':handler},handler_timeout=.02).run_once()=='unknown'
            assert scheduler.finish.call_args.args[1].code==('HANDLER_TIMEOUT' if timed else 'HANDLER_ERROR')
    asyncio.run(scenario());assert 'private-provider-response' not in caplog.text


def test_heartbeat_loss_cancels_handler_and_never_finishes_stale_lease():
    async def scenario():
        _,scheduler=setup();scheduler.renew.return_value=False;cancelled=asyncio.Event()
        async def handler(lease):
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        assert await JobWorker(scheduler,{'case_created':handler},lease_seconds=3).run_once()=='lease_lost'
        assert cancelled.is_set();scheduler.finish.assert_not_awaited()
    asyncio.run(scenario())


def test_process_cancellation_leaves_lease_for_policy_based_recovery():
    async def scenario():
        _,scheduler=setup();started=asyncio.Event()
        async def handler(lease):started.set();await asyncio.Event().wait()
        task=asyncio.create_task(JobWorker(scheduler,{'case_created':handler}).run_once())
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        scheduler.finish.assert_not_awaited()
    asyncio.run(scenario())


def test_lease_token_is_not_in_repr_and_outcome_text_is_bounded():
    lease,_=setup();assert str(lease.token) not in repr(lease)
    for state,code in [('succeeded','UNKNOWN'),('retry','private response'),('unknown','OK')]:
        with pytest.raises(ValueError):Outcome(state,code)
