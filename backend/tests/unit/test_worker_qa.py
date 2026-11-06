"""Independent worker deadline and cancellation-boundary probes."""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from airport_assistant.modules.jobs.service import JobWorker, Lease, Outcome


def scheduler():
    lease = Lease(uuid4(), uuid4(), uuid4(), uuid4(), 'case_created', 1, 1, uuid4(), uuid4())
    scheduler = type('Scheduler', (), {})()
    scheduler.claim = AsyncMock(return_value=lease)
    scheduler.renew = AsyncMock(return_value=True)
    scheduler.finish = AsyncMock(return_value=True)
    return scheduler, lease


def test_handler_swallowing_timeout_cancellation_cannot_report_success():
    async def scenario():
        store, lease = scheduler()

        async def delayed_cleanup(_):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return Outcome('succeeded', 'OK')

        worker = JobWorker(store, {'case_created': delayed_cleanup}, lease_seconds=3, handler_timeout=0.01)
        assert await asyncio.wait_for(worker.run_once(), 2) == 'unknown'
        store.finish.assert_awaited_once_with(lease, Outcome('unknown', 'HANDLER_TIMEOUT'))
    asyncio.run(scenario())


def test_worker_cancellation_cancels_handler_without_finishing_job():
    async def scenario():
        store, _ = scheduler(); started = asyncio.Event(); cleaned = asyncio.Event()

        async def running(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        task = asyncio.create_task(JobWorker(store, {'case_created': running}, lease_seconds=3).run_once())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert cleaned.is_set()
        store.finish.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('renew_error', [False, True])
def test_renewal_loss_or_error_cancels_work_without_finishing(renew_error):
    async def scenario():
        store, _ = scheduler(); cleaned = asyncio.Event()
        store.renew.side_effect = RuntimeError('Synthetic scheduler failure') if renew_error else None
        store.renew.return_value = False

        async def running(_):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        worker = JobWorker(store, {'case_created': running}, lease_seconds=3)
        assert await asyncio.wait_for(worker.run_once(), 3) == 'lease_lost'
        assert cleaned.is_set()
        store.finish.assert_not_awaited()
    asyncio.run(scenario())
