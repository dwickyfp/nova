"""The long-running worker loop: consume jobs, reconcile lost work.

``nova-worker`` scales horizontally — N instances share the scheduler's stream
through a consumer group. The loop does three things on each cycle:

1. **Drain** new stream deliveries; process each to a stable point.
2. **Claim** deliveries whose consumer died before acking, so a crashed worker's
   job is finished by another.
3. **Reconcile** on a slower cadence, re-deriving graph runs the stream lost
   (Redis flush, worker death mid-node) from ``NOVA_SYSTEM`` alone.

Every handler is idempotent, so steps 2 and 3 can re-process a job without
executing a node twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.core.config import settings
from app.modules.task_orchestration.consumer import GraphRunConsumer
from app.modules.task_orchestration.execution import DelegateExecutor
from app.modules.task_orchestration.reconciler import Reconciler
from app.modules.task_orchestration.repository import TaskOrchestrationRepository
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker

logger = logging.getLogger(__name__)


class WorkerService:
    """Consumes graph-run jobs and reconciles unfinished work."""

    def __init__(
        self,
        repository: TaskOrchestrationRepository,
        executor: DelegateExecutor,
        consumer: GraphRunConsumer,
        reconciler: Reconciler,
        *,
        reconcile_interval: float | None = None,
    ) -> None:
        self._repository = repository
        self._worker = GraphRunWorker(repository, executor)
        self._consumer = consumer
        self._reconciler = reconciler
        self._reconcile_interval = (
            reconcile_interval
            if reconcile_interval is not None
            else settings.WORKER_RECONCILE_INTERVAL_SECONDS
        )

    async def run_once(self, *, block_ms: int = 100) -> int:
        """One cycle: drain new + claimed deliveries. Returns jobs processed."""
        processed = await self._process_available(block_ms=block_ms)
        return processed

    async def reconcile_once(self) -> None:
        """Reconcile native state, then re-enqueue graph runs the stream lost.

        Two independent recoveries, run in order:

        1. **Native trace reconciliation** — advance nodes whose delegated
           ``SUBMIT TASK`` has settled in ``information_schema.task_runs``,
           mark a lost trace ``abandoned``, and surface an auto-paused task, so
           a DAG never hangs silently (NOVA-37).
        2. **Lost-delivery re-enqueue** — covers a run persisted but never
           published (Redis flushed before the push, or the scheduler died
           between the two), and a run a worker abandoned mid-node.

        Re-processing an already-settled run is a no-op, so this is safe to run
        alongside live deliveries.
        """
        await self._reconcile_native()
        report = await self._reconciler.scan()
        for graph_run_id in [*report.pending_graph_runs, *report.abandoned_graph_runs]:
            run = await self._repository.get_graph_run(graph_run_id)
            if run is None:
                continue
            if run.get("state") in {"success", "failed", "cancelled"}:
                continue
            await self._requeue(run)

    async def _reconcile_native(self) -> None:
        """Advance running nodes from the engine's native task state.

        A NATIVE reconciliation failure must not stop lost-delivery recovery,
        so it is caught and logged: the two paths are independent.
        """
        try:
            report = await self._reconciler.reconcile_native()
        except Exception:
            logger.exception("native-state reconciliation failed; continuing")
            return
        if report.advanced or report.lost_traces or report.auto_paused:
            logger.info(
                "native reconcile: %d observed, %d advanced, %d lost traces, "
                "%d auto-pause suspects",
                report.observed,
                len(report.advanced),
                len(report.lost_traces),
                len(report.auto_paused),
            )

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        logger.info("nova-worker started; waiting for graph runs")
        await self._consumer.ensure_group()
        next_reconcile = asyncio.get_running_loop().time()

        while not stop_event.is_set():
            try:
                await self._process_available(block_ms=500)
            except Exception:
                logger.exception("worker cycle failed; continuing")

            now = asyncio.get_running_loop().time()
            if now >= next_reconcile:
                try:
                    await self.reconcile_once()
                except Exception:
                    logger.exception("reconcile pass failed; continuing")
                next_reconcile = now + self._reconcile_interval

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=0.5)

        logger.info("nova-worker stopped")

    async def _process_available(self, *, block_ms: int) -> int:
        processed = 0
        for stream_id, payload in await self._consumer.read(block_ms=block_ms):
            processed += 1
            await self._handle(stream_id, payload)

        for stream_id, payload in await self._consumer.claim_stale():
            processed += 1
            await self._handle(stream_id, payload)
        return processed

    async def _handle(self, stream_id: str, payload: dict[str, str]) -> None:
        try:
            state = await self._worker.handle(GraphRunJob.from_payload(payload))
        except Exception:
            # Leave the job unacked so it is redelivered; the handler is
            # idempotent, so a retry cannot double-execute a node.
            logger.exception("graph run %s failed; leaving for redelivery", payload)
            return
        await self._consumer.ack(stream_id)
        if state is not None:
            logger.info("graph run %s handled -> %s", payload.get("graph_run_id"), state)

    async def _requeue(self, run: dict) -> None:
        """Re-run a graph from durable state, without the stream.

        The graph's task ids are re-derived from the graph edges rather than the
        original payload, so a reconciler recovery needs nothing from Redis.
        """
        graph_id = str(run["graph_id"])
        task_ids = [str(t["id"]) for t in await self._repository.list_tasks(graph_id)]
        payload = {
            "graph_run_id": str(run["id"]),
            "graph_id": graph_id,
            "trigger_type": "reconcile",
            "task_ids": ",".join(task_ids),
        }
        logger.info("reconciling graph run %s", run["id"])
        await self._handle(str(run["id"]), payload)
