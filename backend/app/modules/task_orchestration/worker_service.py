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
from typing import Any

from app.core.config import settings
from app.modules.task_orchestration.consumer import GraphRunConsumer
from app.modules.task_orchestration.dag import GraphState
from app.modules.task_orchestration.execution import DelegateExecutor
from app.modules.task_orchestration.reconciler import Reconciler
from app.modules.task_orchestration.repository import TaskOrchestrationRepository
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker

logger = logging.getLogger(__name__)

#: A settled graph run no longer holds the QUEUE slot.
_TERMINAL_GRAPH_STATES = frozenset(
    {GraphState.SUCCESS.value, GraphState.FAILED.value, GraphState.CANCELLED.value}
)


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
        """Reconcile native state, settle stale nodes, then re-enqueue.

        Three independent recoveries, run in order:

        1. **Native state reconciliation** — advance nodes whose delegated
           ``SUBMIT TASK`` has settled in ``information_schema.task_runs`` and
           surface an auto-paused task, so a DAG never hangs silently (NOVA-37).
        2. **Lost trace by heartbeat** — a node whose worker died stops stamping
           ``heartbeat_at``; it is abandoned from durable ``NOVA_SYSTEM`` state,
           never inferred from the engine's (engine-wide, uninformative) archive
           failure (NOVA-46). Without this the row stays ``running`` and the DAG
           cannot re-evaluate it.
        3. **Lost-delivery re-enqueue** — covers a run persisted but never
           published (Redis flushed before the push, or the scheduler died
           between the two), and a run a worker abandoned mid-node.

        Re-processing an already-settled run is a no-op, so this is safe to run
        alongside live deliveries.
        """
        await self._reconcile_native()
        await self._abandon_stale_nodes()
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

    async def _abandon_stale_nodes(self) -> None:
        """Settle nodes whose worker heartbeat lapsed — the durable lost trace.

        Isolated like the native pass: a failure here must not stop re-enqueue.
        A node that could not be settled this pass stays a candidate for the
        next one, because the write is conditional.
        """
        try:
            abandoned = await self._reconciler.abandon_stale_nodes()
        except Exception:
            logger.exception("heartbeat reconciliation failed; continuing")
            return
        if abandoned:
            logger.info("abandoned %d stale node(s)", len(abandoned))

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        logger.info("nova-worker started; waiting for graph runs")
        await self._consumer.ensure_group()

        # Reconciliation runs as its own background task, not inline in the
        # consume loop. A node can block for the whole poll window (up to
        # WORKER_TASK_POLL_TIMEOUT_SECONDS, 4 h by default), and previously the
        # loop only reached `reconcile_once` after that delivery returned — so
        # every recovery path (lost delivery, abandoned node, deferred `queue`
        # run) was stalled for hours behind one slow node. Decoupling them keeps
        # recovery on its own cadence regardless of execution latency.
        reconcile_task = asyncio.create_task(
            self._reconcile_forever(stop_event), name="nova-worker-reconcile"
        )

        try:
            while not stop_event.is_set():
                try:
                    await self._process_available(block_ms=500)
                except Exception:
                    logger.exception("worker cycle failed; continuing")

                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=0.5)
        finally:
            reconcile_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reconcile_task
            logger.info("nova-worker stopped")

    async def _reconcile_forever(self, stop_event: asyncio.Event) -> None:
        """Run reconciliation on its own interval, independently of execution."""
        while not stop_event.is_set():
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("reconcile pass failed; continuing")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=self._reconcile_interval)

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
        # Only a *settled* run frees the QUEUE slot for a sibling. A run that
        # returned ``pending`` is itself the deferred one, and waking a sibling
        # here would risk a wake loop; the slot is still occupied.
        if state is not None and state.value in _TERMINAL_GRAPH_STATES:
            await self._wake_deferred_queue_runs(str(payload.get("graph_id") or ""))

    #: How many deferred siblings one settle may chain-wake. QUEUE is "one at a
    #: time", so each settle wakes exactly the next; the cap only guards against
    #: an unexpectedly pathological backlog turning one settle into an unbounded
    #: loop. The reconciler still catches up anything the cap leaves behind.
    MAX_QUEUE_WAKE_CHAIN = 100

    async def _wake_deferred_queue_runs(self, graph_id: str) -> None:
        """Start deferred ``queue`` runs now that a sibling has settled.

        A ``queue`` run defers itself (returns ``pending``) while another run for
        the same graph is active. Without this, the only thing that ever
        re-delivers it is the periodic reconciler, so a deferred run waits up to
        a full reconcile interval and — if the active-run scan is capped — can
        starve behind a backlog. Waking the next sibling on settle makes QUEUE
        "one after another" without depending on the reconcile cadence.

        The wake is **iterative and bounded**: each settled run wakes the oldest
        pending sibling, and that sibling's own settle wakes the next. The chain
        runs here (one graph at a time) rather than as unbounded async recursion,
        and the cap keeps one settle from monopolising the loop; anything left is
        picked up by the reconciler.
        """
        if not graph_id:
            return
        for _ in range(self.MAX_QUEUE_WAKE_CHAIN):
            try:
                active = await self._repository.list_active_graph_runs(graph_id)
            except Exception:
                logger.exception("could not list active runs for %s", graph_id)
                return
            # ``list_active_graph_runs`` orders by ``started_at``, so the first
            # pending row is the oldest deferred run.
            pending = next((run for run in active if str(run.get("state")) == "pending"), None)
            if pending is None:
                return
            state = await self._drive(pending)
            # Stop unless this run actually settled; a still-deferred run would
            # make this loop spin, and the reconciler will retry it later.
            if state is None or state.value not in _TERMINAL_GRAPH_STATES:
                return

    async def _drive(self, run: dict[str, Any]) -> GraphState | None:
        """Handle one graph run from durable state (no stream, no ack)."""
        graph_id = str(run["graph_id"])
        task_ids = [str(t["id"]) for t in await self._repository.list_tasks(graph_id)]
        payload = {
            "graph_run_id": str(run["id"]),
            "graph_id": graph_id,
            "trigger_type": "reconcile",
            "task_ids": ",".join(task_ids),
        }
        return await self._worker.handle(GraphRunJob.from_payload(payload))

    async def _requeue(self, run: dict[str, Any]) -> None:
        """Re-run a graph from durable state, without the stream.

        The graph's task ids are re-derived from the graph edges rather than the
        original payload, so a reconciler recovery needs nothing from Redis.

        This path deliberately does **not** call :meth:`_handle`: that method
        acks a stream id, and there is no stream entry for a reconciler-driven
        re-run — acking the graph-run id would be a no-op at best and could ack
        an unrelated pending delivery at worst. The handler is idempotent, so
        driving the worker directly is safe; a failure is simply retried on the
        next reconcile pass (the row stays non-terminal).
        """
        logger.info("reconciling graph run %s", run["id"])
        state = await self._drive(run)
        if state is not None:
            logger.info("graph run %s reconciled -> %s", run["id"], state)
        # A settled run frees the QUEUE slot; advance the deferred chain so it
        # does not wait for the reconcile cadence.
        if state is not None and state.value in _TERMINAL_GRAPH_STATES:
            await self._wake_deferred_queue_runs(str(run["graph_id"]))
