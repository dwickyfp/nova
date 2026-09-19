"""Reconciliation for worker restarts and lost native task traces (NOVA-37).

Redis is ephemeral transport; ``NOVA_SYSTEM`` is the source of truth (design
§2). Three failure modes need a re-derivation path that does **not** depend on
the stream:

* **Redis flushed / job never delivered.** A graph run exists in ``pending``
  but no worker ever saw it.
* **Worker died mid-node.** A node row is left ``running`` with a heartbeat
  that has stopped advancing. It is **abandoned**, not trusted: the graph is
  re-evaluated from the last confirmed node states, and the node is re-run.
* **Auto-paused.** The node's task auto-paused after
  ``max_task_consecutive_fail_count`` consecutive failures. It may not hang a
  DAG silently and may not be reported as success.

A lost native trace is **not** inferred from a failed ``task_runs`` read: the
archive failure is engine-wide, so it cannot identify one task's absent trace
(NOVA-46). The durable signal is the worker heartbeat — the "worker died
mid-node" row above is exactly the lost-trace case.

State mutation is explicit:

* :meth:`Reconciler.scan` performs **pure reads** and returns a report; the
  existing worker service uses it to re-enqueue lost deliveries.
* :meth:`Reconciler.reconcile_native` reads ``information_schema.task_runs`` for
  the nodes of running graph runs, advances their persisted state, and surfaces
  an auto-pause to the audit log; a failed read is ``UNKNOWN`` and writes nothing.
* :meth:`Reconciler.abandon_stale_nodes` abandons a ``running`` row whose
  heartbeat lapsed — the lost trace, settled from durable state and audited
  ``NODE_ABANDONED``.

All are idempotent: a repeated pass on unchanged engine state performs no
write, because each transition is conditional on the row's current state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.common.audit import write_audit_log
from app.core.config import settings
from app.modules.task_orchestration.dag import NodeState
from app.modules.task_orchestration.native import (
    NativeConfig,
    NativeRun,
    NativeState,
    fetch_native_runs,
    fetch_native_schedules,
    parse_consecutive_failures,
    read_native_config,
    schedule_is_paused,
)
from app.modules.task_orchestration.repository import TaskOrchestrationRepository

logger = logging.getLogger(__name__)


class NativeObserver(Protocol):
    """The engine reads reconciliation needs, injectable for tests."""

    async def read_native_config(self) -> NativeConfig: ...

    async def read_runs(self, task_names: list[str]) -> dict[str, NativeRun]: ...

    async def read_schedules(self, task_names: list[str]) -> dict[str, str]: ...


class EngineNativeObserver:
    """Default observer: reads the engine on the system connection.

    Configuration is read via ``ADMIN SHOW FRONTEND CONFIG LIKE '%task%'`` — the
    FE-config surface, not ``SHOW VARIABLES``. Every read tolerates an
    unavailable engine by returning an ``UNKNOWN``/unavailable result rather
    than raising, so reconciliation degrades instead of crashing. Connection
    *acquisition* is inside the guard too: when the pool cannot reach the FE,
    ``db.system_conn()`` is what raises (NOVA-44).
    """

    async def read_native_config(self) -> NativeConfig:
        return await read_native_config()

    async def read_runs(self, task_names: list[str]) -> dict[str, NativeRun]:
        return await fetch_native_runs(task_names)

    async def read_schedules(self, task_names: list[str]) -> dict[str, str]:
        return await fetch_native_schedules(task_names)


@dataclass
class ReconcileReport:
    """What one reconciliation pass found and re-enqueued."""

    pending_graph_runs: list[str] = field(default_factory=list)
    abandoned_task_runs: list[str] = field(default_factory=list)
    abandoned_graph_runs: list[str] = field(default_factory=list)


@dataclass
class NativeReconcileReport:
    """What one native-state reconciliation pass observed and changed."""

    observed: int = 0
    advanced: list[str] = field(default_factory=list)
    #: Node rows whose native trace disappeared — explicit, never success.
    lost_traces: list[str] = field(default_factory=list)
    #: Tasks whose native state could not be read this pass.
    unknown: list[str] = field(default_factory=list)
    #: Tasks the engine auto-paused after consecutive failures.
    auto_paused: list[str] = field(default_factory=list)
    config: NativeConfig = field(default_factory=NativeConfig)


#: Native observation -> persisted node state. Only a *settled* native row
#: (``SUCCESS``/``FAILED``) advances a node. ``MISSING``, ``PENDING``,
#: ``RUNNING`` and ``UNKNOWN`` all map to ``None``, meaning "leave the row
#: unchanged": the engine's archive is engine-wide and cannot prove that a
#: particular task's trace is absent, so a read with no row must never settle a
#: node on its own. The lost trace is settled by :meth:`Reconciler.abandon_stale_nodes`
#: once the worker's heartbeat lapses (NOVA-46 / NOVA-52).
_NATIVE_TO_NODE: dict[NativeState, NodeState | None] = {
    NativeState.SUCCESS: NodeState.SUCCESS,
    NativeState.FAILED: NodeState.FAILED,
    NativeState.PENDING: None,
    NativeState.RUNNING: None,
    NativeState.MISSING: None,
    NativeState.UNKNOWN: None,
}

#: States a running node row may be advanced from.
_RUNNING = (NodeState.RUNNING.value,)


class Reconciler:
    """Re-derives unfinished work from durable state alone."""

    def __init__(
        self,
        repository: TaskOrchestrationRepository,
        *,
        heartbeat_timeout_seconds: int | None = None,
        observer: NativeObserver | None = None,
        max_consecutive_fail_count: int | None = None,
    ) -> None:
        self._repository = repository
        self._heartbeat_timeout = (
            heartbeat_timeout_seconds
            if heartbeat_timeout_seconds is not None
            else settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS
        )
        self._observer = observer or EngineNativeObserver()
        self._max_consecutive_fail_count = (
            max_consecutive_fail_count
            if max_consecutive_fail_count is not None
            else settings.WORKER_MAX_CONSECUTIVE_FAIL_COUNT
        )

    async def scan(self) -> ReconcileReport:
        """Find work the stream may have lost. Pure reads, no writes.

        Only genuinely **pending** runs are re-enqueued: a ``running`` run is
        being executed by a live worker (or is covered by the heartbeat path
        below), so re-enqueueing it would just create delivery churn. Splitting
        the two also removes a starvation window — the previous shared
        ``["pending", "running"]`` query was ordered oldest-first and capped, so
        a long-lived ``running`` run could occupy the cap and hide newer
        ``pending`` runs from recovery.
        """
        report = ReconcileReport()

        pending = await self._repository.list_graph_runs_by_state(["pending"])
        for run in pending:
            report.pending_graph_runs.append(str(run["id"]))

        stale_nodes = await self._repository.list_stale_task_runs(self._heartbeat_timeout)
        for node in stale_nodes:
            graph_run_id = node.get("graph_run_id")
            if graph_run_id:
                report.abandoned_task_runs.append(str(node["id"]))
                if str(graph_run_id) not in report.abandoned_graph_runs:
                    report.abandoned_graph_runs.append(str(graph_run_id))

        stale_graphs = await self._repository.list_stale_graph_runs(self._heartbeat_timeout)
        for run in stale_graphs:
            if str(run["id"]) not in report.abandoned_graph_runs:
                report.abandoned_graph_runs.append(str(run["id"]))

        return report

    async def abandoned_node_ids(self, graph_run_id: str) -> list[str]:
        """Node ids in a graph run that are ``running`` with a stale heartbeat.

        The worker moves these to ``abandoned`` before re-evaluating, so a
        redelivery cannot mistake a dead worker's row for live progress.
        """
        stale = await self._repository.list_stale_task_runs(self._heartbeat_timeout)
        return [
            str(node["id"])
            for node in stale
            if str(node.get("graph_run_id")) == graph_run_id
        ]

    async def abandon_stale_nodes(self) -> list[dict[str, Any]]:
        """Settle ``RUNNING`` rows whose worker heartbeat lapsed — the lost trace.

        This is the durable lost-trace signal (design §3): a worker that dies
        mid-node stops stamping ``heartbeat_at``, so the row is abandoned and the
        graph is re-evaluated with the node runnable again. It replaces any
        inference from the engine's archive, which is engine-wide and cannot
        identify a per-task lost trace (NOVA-46).

        ``scan`` remains a pure read that only reports candidates; this method
        performs the conditional write (``running`` -> ``abandoned``) so a
        redelivery or a concurrent pass cannot double-settle. Only the caller
        that moved the row writes ``NODE_ABANDONED``. Returns the abandoned rows.
        """
        stale = await self._repository.list_stale_task_runs(self._heartbeat_timeout)
        abandoned: list[dict[str, Any]] = []
        tasks: dict[str, dict[str, Any]] | None = None
        for node in stale:
            moved = await self._repository.transition_task_run(
                str(node["id"]), list(_RUNNING), NodeState.ABANDONED.value
            )
            if not moved:
                continue
            abandoned.append(node)
            if tasks is None:
                tasks = {str(t["id"]): t for t in await self._repository.list_tasks()}
            task = tasks.get(str(node.get("task_id")))
            if task is None:
                continue
            await self._audit(
                str(task["name"]),
                task,
                action="NODE_ABANDONED",
                status="ABANDONED",
                error="worker heartbeat lapsed; the node is abandoned and re-evaluated",
                graph_run_id=str(node.get("graph_run_id") or ""),
            )
        return abandoned

    async def reconcile_native(self) -> NativeReconcileReport:
        """Advance running nodes from the engine's ``task_runs``; surface pauses.

        Only node rows that are ``running`` are observed: a node is running only
        because a worker submitted its ``SUBMIT TASK``, so those are the only
        rows whose native trace can settle them. Nothing here executes SQL —
        node execution remains the worker's (design §2).

        A failed read is ``UNKNOWN`` and leaves the row untouched (design §1: an
        engine-side archive failure can never masquerade as a node's outcome).
        A read with no row (``MISSING``) is likewise not a verdict: the archive
        is engine-wide and cannot prove a per-task trace is gone, so it must not
        settle the node (NOVA-52). The lost trace is settled by
        :meth:`abandon_stale_nodes` (NOVA-46) — it is the only writer of
        ``NODE_ABANDONED``.
        """
        report = NativeReconcileReport()

        running = await self._repository.list_running_task_runs()
        if not running:
            return report

        task_ids = sorted({str(row["task_id"]) for row in running if row.get("task_id")})
        tasks = {str(t["id"]): t for t in await self._repository.list_tasks()}
        names = [
            str(tasks[tid]["name"]) for tid in task_ids if tid in tasks and tasks[tid].get("name")
        ]
        if not names:
            return report

        report.config = await self._observer.read_native_config()
        runs = await self._observer.read_runs(names)
        schedules = await self._observer.read_schedules(names)
        report.observed = len(runs)

        for row in running:
            task_id = row.get("task_id")
            if not task_id:
                continue
            task = tasks.get(str(task_id))
            if task is None:
                continue
            name = str(task["name"])
            observed = runs.get(name)
            if observed is None:
                report.unknown.append(name)
                continue
            await self._apply(report, row, task, observed, schedules.get(name))

        return report

    async def _apply(
        self,
        report: NativeReconcileReport,
        row: dict[str, Any],
        task: dict[str, Any],
        observed: NativeRun,
        schedule: str | None,
    ) -> None:
        name = str(task["name"])
        node_state = _NATIVE_TO_NODE[observed.state]

        if node_state is None:
            # RUNNING/PENDING: still in flight. MISSING: no native row, which
            # proves nothing per-task. UNKNOWN: a failed read — no state may be
            # inferred. All leave the row untouched.
            if observed.state is NativeState.UNKNOWN:
                report.unknown.append(name)
            return

        if _is_stale_native_row(row, observed):
            # The newest native row predates this node's submission, so it is a
            # leftover from a previous attempt — not this run's outcome. Two
            # workers share the stream, so a reconciler must not settle a node
            # another worker is still waiting on. Skip; a later pass sees the
            # real row (NOVA-42 second finding).
            return

        moved = await self._repository.transition_task_run(
            str(row["id"]),
            list(_RUNNING),
            node_state.value,
            query_id=observed.query_id,
            error_message=observed.error_message,
        )
        if not moved:
            # Another pass or delivery already settled this row.
            return
        report.advanced.append(str(row["id"]))

        await self._audit(
            name,
            task,
            action="NODE_" + node_state.value.upper(),
            status=(
                "SUCCESS"
                if node_state is NodeState.SUCCESS
                else node_state.value.upper()
            ),
            error=observed.error_message,
            graph_run_id=str(row.get("graph_run_id") or ""),
        )

        if node_state is NodeState.SUCCESS:
            # A success breaks the run: the engine's consecutive-failure
            # counter resets, and so does Nova's (NOVA-37 AC #3).
            await self._repository.reset_consecutive_failures(str(task["id"]))
            return
        if node_state is NodeState.FAILED:
            await self._check_auto_pause(report, task, observed, schedule)

    async def _check_auto_pause(
        self,
        report: NativeReconcileReport,
        task: dict[str, Any],
        observed: NativeRun,
        schedule: str | None,
    ) -> None:
        """Surface an auto-paused task, but only at the real threshold.

        The engine pauses a task after ``max_task_consecutive_fail_count``
        consecutive failures and exposes neither a count nor a pause flag. Nova
        therefore keeps its own counter (`CONFIG_TASKS.consecutive_fail_count`,
        incremented here, reset on success) and compares it against the ceiling
        read live from the engine. A single failure is just a failure — only
        reaching the ceiling, or the native schedule already showing a
        pause/suspend marker, raises the auto-pause alarm. Alarming on every
        failure would drown the real signal (the defect NOVA-42 reports).
        """
        name = str(task["name"])
        ceiling = (
            report.config.max_task_consecutive_fail_count
            or self._max_consecutive_fail_count
        )
        if ceiling <= 0:
            return

        # The engine may embed the count in the error text; prefer that when
        # present, else use Nova's own persistent counter.
        engine_count = parse_consecutive_failures(observed.error_message)
        count = engine_count
        if count is None:
            count = await self._repository.increment_consecutive_failures(
                str(task["id"])
            )

        paused_by_schedule = schedule_is_paused(schedule)
        if count < ceiling and not paused_by_schedule:
            return

        reason = (
            f"native SCHEDULE shows a pause/suspend marker ({schedule!r})"
            if paused_by_schedule
            else f"{count} consecutive failures reached the ceiling of {ceiling}"
        )
        report.auto_paused.append(name)
        await self._audit(
            name,
            task,
            action="TASK_AUTO_PAUSE_SUSPECTED",
            status="FAILED",
            error=(
                f"task {name!r} is auto-paused or about to be: {reason}; "
                "the graph cannot advance on its own"
            ),
            graph_run_id="",
        )

    async def _audit(
        self,
        task_name: str,
        task: dict[str, Any],
        *,
        action: str,
        status: str,
        error: str | None,
        graph_run_id: str,
    ) -> None:
        await write_audit_log(
            event_type="task_reconcile",
            user_name=str(task.get("created_by") or "nova-reconciler"),
            action=action,
            object_type="TASK",
            object_name=task_name,
            status=status,
            error_message=error,
            database_name=graph_run_id or None,
        )


def _is_stale_native_row(row: dict[str, Any], observed: NativeRun) -> bool:
    """Whether ``observed`` is a leftover from a previous attempt.

    The engine's newest row for a task can predate this node's submission when
    a prior attempt failed and the current one is still in flight. Settling the
    node on that row would fail a live run (and could raise a false auto-pause),
    so the write is skipped until a row at or after the node's start appears.

    The comparison is done only when both timestamps are known. ``CREATE_TIME``
    has second granularity, so a row in the same second as ``started_at`` is
    accepted — the strict "newer than" guard is reserved for rows that are
    unambiguously older.
    """
    started = row.get("started_at")
    created = observed.create_time
    if not isinstance(started, datetime) or not isinstance(created, datetime):
        return False
    started_naive = started.replace(tzinfo=None) if started.tzinfo else started
    created_naive = created.replace(tzinfo=None) if created.tzinfo else created
    return created_naive < started_naive
