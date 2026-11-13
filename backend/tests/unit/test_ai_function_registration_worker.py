import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_db import MetadataUnavailable
from app.modules.llm_functions import registration_worker as module


@pytest.fixture
def queue(monkeypatch):
    repository = AsyncMock()
    repository.claim.return_value = []
    monkeypatch.setattr(module, "JobRepository", lambda connection: repository)

    @asynccontextmanager
    async def transaction():
        yield object()

    return repository, transaction


async def test_claim_is_bounded_and_stops_admission_after_stop(queue):
    repository, transaction = queue
    stop = asyncio.Event()
    job = {"id": "fixture"}
    repository.claim.return_value = [job]

    async def handle(claimed):
        assert claimed is job
        stop.set()

    participant = AsyncMock(handle=AsyncMock(side_effect=handle))
    worker = module.AIFunctionRegistrationWorker(participant=participant, transaction=transaction)
    await worker.run_forever(stop)
    repository.claim.assert_awaited_once_with(
        "maintenance",
        worker._holder,
        seconds=300,
        limit=1,
        kind=module.JOB_KIND,
    )
    assert worker.OPERATION_SECONDS < worker.LEASE_SECONDS
    participant.handle.assert_awaited_once_with(job)


async def test_idle_worker_stops_without_redis(queue):
    repository, transaction = queue
    stop = asyncio.Event()
    worker = module.AIFunctionRegistrationWorker(transaction=transaction)
    task = asyncio.create_task(worker.run_forever(stop))
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, 1)
    repository.claim.assert_awaited_once()


@pytest.mark.parametrize("deadline", [False, True])
async def test_cancellation_drains_the_participant_without_requeue(queue, deadline):
    repository, transaction = queue
    repository.claim.return_value = [{"id": "fixture"}]
    entered, ended, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def handle(job):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()
            stop.set()

    participant = AsyncMock(handle=AsyncMock(side_effect=handle))
    worker = module.AIFunctionRegistrationWorker(participant=participant, transaction=transaction)
    if deadline:
        worker.OPERATION_SECONDS = 0.01
    task = asyncio.create_task(worker.run_forever(stop))
    await entered.wait()
    if not deadline:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await asyncio.wait_for(task, 1)
    assert ended.is_set()
    participant.handle.assert_awaited_once()
    repository.claim.assert_awaited_once()
    repository.enqueue.assert_not_awaited()


async def test_pg_failure_terminates_the_loop_for_supervision(queue):
    repository, transaction = queue
    repository.claim.side_effect = MetadataUnavailable()
    with pytest.raises(MetadataUnavailable):
        await module.AIFunctionRegistrationWorker(transaction=transaction).run_forever(
            asyncio.Event()
        )
