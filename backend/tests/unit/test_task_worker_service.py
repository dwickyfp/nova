"""Unit tests for the ``WorkerService`` loop (NOVA-54 hardening).

Two production behaviours are pinned here, both with in-memory fakes so they do
not need Redis or StarRocks:

* a settled graph run **wakes the next deferred ``queue`` run** immediately,
  instead of waiting for the periodic reconciler (which is what let a deferred
  run starve behind a cap); and
* reconciliation runs on its **own background cadence**, decoupled from the
  consume loop, so one node blocking for the poll window cannot stall recovery.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.modules.task_orchestration.dag import GraphState
from app.modules.task_orchestration.worker_service import WorkerService


class FakeRepository:
    """The slice ``WorkerService`` uses."""

    def __init__(self, runs: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> None:
        self.runs = {str(r["id"]): r for r in runs}
        self.tasks = tasks
        self.requeued: list[str] = []

    async def list_active_graph_runs(self, graph_id: str) -> list[dict[str, Any]]:
        return [
            r
            for r in self.runs.values()
            if r["graph_id"] == graph_id and r["state"] in ("pending", "running")
        ]

    async def list_tasks(self, graph_id: str | None = None) -> list[dict[str, Any]]:
        return self.tasks

    async def get_graph_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)


class FakeConsumer:
    def __init__(self, jobs: list[tuple[str, dict[str, str]]] | None = None) -> None:
        self.jobs = jobs or []
        self.acked: list[str] = []

    async def ensure_group(self) -> None:
        return None

    async def read(self, *, count=None, block_ms=5000):
        jobs, self.jobs = self.jobs, []
        return jobs

    async def claim_stale(self, **kwargs):
        return []

    async def ack(self, stream_id: str) -> None:
        self.acked.append(stream_id)


class FakeWorker:
    """Settles runs by transitioning them, recording what was driven."""

    def __init__(self, repo: FakeRepository, settle_to: str = "success") -> None:
        self._repo = repo
        self._settle_to = settle_to
        self.driven: list[str] = []

    async def handle(self, job) -> GraphState:
        self.driven.append(job.graph_run_id)
        row = self._repo.runs[job.graph_run_id]
        if row["state"] == "pending":
            row["state"] = self._settle_to
        return GraphState(row["state"])


class FakeReconciler:
    """Stands in for the real reconciler; its methods are never reached here."""

    async def reconcile_native(self):
        return None

    async def abandon_stale_nodes(self):
        return []

    async def scan(self):
        class _Report:
            pending_graph_runs: list[str] = []
            abandoned_graph_runs: list[str] = []

        return _Report()


def _service(repo, worker, consumer, reconciler, interval=0.01) -> WorkerService:
    service = WorkerService(repo, executor=None, consumer=consumer, reconciler=reconciler)
    service._worker = worker  # type: ignore[assignment]
    service._reconcile_interval = interval
    return service


def _run(run_id: str, graph_id: str, state: str) -> dict[str, Any]:
    return {"id": run_id, "graph_id": graph_id, "state": state, "trigger_type": "schedule"}


class TestQueueWakeChain:
    async def test_settle_wakes_the_next_deferred_run(self):
        repo = FakeRepository(
            runs=[
                _run("r_active", "g1", "running"),
                _run("r_queued", "g1", "pending"),
            ],
            tasks=[{"id": "t1"}],
        )
        worker = FakeWorker(repo)
        service = _service(repo, worker, FakeConsumer(), FakeReconciler())

        # The active run settles; the deferred one must be driven immediately.
        await service._wake_deferred_queue_runs("g1")

        assert worker.driven == ["r_queued"]
        assert repo.runs["r_queued"]["state"] == "success"

    async def test_wake_chain_advances_one_run_at_a_time(self):
        repo = FakeRepository(
            runs=[
                _run("r1", "g1", "pending"),
                _run("r2", "g1", "pending"),
                _run("r3", "g1", "pending"),
            ],
            tasks=[{"id": "t1"}],
        )
        worker = FakeWorker(repo)
        service = _service(repo, worker, FakeConsumer(), FakeReconciler())

        await service._wake_deferred_queue_runs("g1")

        # Oldest first (list_active_graph_runs orders by started_at; the fake
        # returns insertion order), and the chain runs to completion.
        assert worker.driven == ["r1", "r2", "r3"]

    async def test_no_pending_run_does_nothing(self):
        repo = FakeRepository(runs=[_run("r1", "g1", "running")], tasks=[{"id": "t1"}])
        worker = FakeWorker(repo)
        service = _service(repo, worker, FakeConsumer(), FakeReconciler())
        await service._wake_deferred_queue_runs("g1")
        assert worker.driven == []


class TestReconcileCadence:
    async def test_reconcile_runs_while_the_consume_loop_is_blocked(self):
        """A blocked delivery must not stop reconciliation (NOVA-54 finding 5).

        The consume loop processes one job for the whole test; the reconcile
        background task must still tick on its own cadence.
        """
        repo = FakeRepository(runs=[], tasks=[])
        reconciler = FakeReconciler()

        class BlockingConsumer(FakeConsumer):
            async def read(self, *, count=None, block_ms=5000):
                await asyncio.sleep(0.2)
                return []

        service = _service(
            repo, FakeWorker(repo), BlockingConsumer(), reconciler, interval=0.01
        )
        passes = {"n": 0}

        async def counting_reconcile_once() -> None:
            passes["n"] += 1

        service.reconcile_once = counting_reconcile_once  # type: ignore[method-assign]

        stop = asyncio.Event()
        task = asyncio.create_task(service.run_forever(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

        assert passes["n"] >= 1, "reconcile must run independently of the drain loop"


class TestBoundedGraphConcurrency:
    async def test_slow_run_does_not_block_other_runs_or_exceed_capacity(self):
        class WaitingWorker:
            def __init__(self) -> None:
                self.started: list[str] = []
                self.release = asyncio.Event()
                self.two_started = asyncio.Event()

            async def handle(self, job):
                self.started.append(job.graph_run_id)
                if len(self.started) >= 2:
                    self.two_started.set()
                await self.release.wait()
                return GraphState.SUCCESS

        class BoundedConsumer(FakeConsumer):
            async def read(self, *, count=None, block_ms=5000):
                count = count or len(self.jobs)
                jobs, self.jobs = self.jobs[:count], self.jobs[count:]
                if not jobs:
                    await asyncio.sleep(0.01)
                return jobs

        jobs = [
            (f"s{i}", {"graph_run_id": f"r{i}", "graph_id": f"g{i}"})
            for i in range(3)
        ]
        repo = FakeRepository([], [])
        consumer = BoundedConsumer(jobs)
        worker = WaitingWorker()
        service = WorkerService(
            repo,
            executor=None,
            consumer=consumer,
            reconciler=FakeReconciler(),
            reconcile_interval=1,
            max_concurrent_graph_runs=2,
        )
        service._worker = worker  # type: ignore[assignment]
        stop = asyncio.Event()
        loop = asyncio.create_task(service.run_forever(stop))
        try:
            await asyncio.wait_for(worker.two_started.wait(), timeout=1)
            assert worker.started == ["r0", "r1"]
            assert len(consumer.jobs) == 1
            worker.release.set()
            for _ in range(100):
                if len(consumer.acked) == 3:
                    break
                await asyncio.sleep(0.01)
            assert len(consumer.acked) == 3
            assert worker.started == ["r0", "r1", "r2"]
        finally:
            worker.release.set()
            stop.set()
            await asyncio.wait_for(loop, timeout=2)
