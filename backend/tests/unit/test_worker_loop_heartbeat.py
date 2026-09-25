"""A worker heartbeat means the consume loop is still progressing."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.modules.task_orchestration import worker_service as module
from app.modules.task_orchestration.dag import GraphState


@pytest.mark.asyncio
@pytest.mark.parametrize(("poll_fails", "expected"), [(False, ["worker"]), (True, [])])
async def test_worker_heartbeat_requires_a_successful_consumer_poll(
    monkeypatch, poll_fails: bool, expected: list[str]
) -> None:
    stop = asyncio.Event()
    pulses: list[str] = []
    monkeypatch.setattr(module, "heartbeat", pulses.append)

    class Consumer:
        async def ensure_group(self) -> None:
            pass

        async def queue_depth(self) -> int:
            return 0

        async def claim_stale(self, *, count: int) -> list:
            if poll_fails:
                stop.set()
                raise RuntimeError("queue unavailable")
            return []

        async def read(self, *, count: int, block_ms: int) -> list:
            stop.set()
            return []

    service = module.WorkerService(None, None, Consumer(), None)
    service.reconcile_once = AsyncMock(return_value=True)
    await service.run_forever(stop)

    assert pulses == expected


@pytest.mark.asyncio
async def test_worker_heartbeat_continues_while_a_long_run_fills_capacity(monkeypatch) -> None:
    stop = asyncio.Event()
    release = asyncio.Event()
    started = asyncio.Event()
    pulses: list[str] = []

    def pulse(service: str) -> None:
        pulses.append(service)
        if len(pulses) == 2:
            release.set()
            stop.set()

    monkeypatch.setattr(module, "heartbeat", pulse)

    class Consumer:
        first = True

        async def ensure_group(self) -> None:
            pass

        async def queue_depth(self) -> int:
            return 0

        async def claim_stale(self, *, count: int) -> list:
            return []

        async def read(self, *, count: int, block_ms: int) -> list:
            if self.first:
                self.first = False
                return [("1-0", {"graph_run_id": "run", "graph_id": "graph"})]
            return []

        async def ack(self, stream_id: str) -> None:
            pass

    class SlowWorker:
        async def handle(self, job) -> GraphState:
            started.set()
            await release.wait()
            return GraphState.RUNNING

    service = module.WorkerService(None, None, Consumer(), None, max_concurrent_graph_runs=1)
    service._worker = SlowWorker()
    service.reconcile_once = AsyncMock(return_value=True)

    await asyncio.wait_for(service.run_forever(stop), timeout=2)

    assert started.is_set()
    assert pulses == ["worker", "worker"]
