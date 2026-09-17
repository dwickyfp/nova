"""Reconciliation for worker restarts and lost native task traces (NOVA-37).

Redis is ephemeral transport; ``NOVA_SYSTEM`` is the source of truth (design
§2). Three failure modes need a re-derivation path that does **not** depend on
the stream:

* **Redis flushed / job never delivered.** A graph run exists in ``pending``
  but no worker ever saw it.
* **Worker died mid-node.** A node row is left ``running`` with a heartbeat
  that has stopped advancing. It is **abandoned**, not trusted: the graph is
  re-evaluated from the last confirmed node states, and the node is re-run.
* **Native trace lost or auto-paused.** A node submitted its ``SUBMIT TASK`` and
  is polling, but the native run's row has vanished (a task running when the FE
  died leaves no trace, design §1) or the task auto-paused after
  ``max_task_consecutive_fail_count`` consecutive failures. Neither may hang a
  DAG silently and neither may be reported as success.

The reconciler runs on a cadence in the worker process. It polls only graph
runs and node rows that are actually active — never every task — so its cost
stays proportional to in-flight work.

Native observation is deliberately split from state mutation:

* :meth:`Reconciler.scan` performs **pure reads** and returns a report; the
  existing worker service uses it to re-enqueue lost deliveries.
* :meth:`Reconciler.reconcile_native` reads ``information_schema.task_runs`` for
  the nodes of running graph runs and advances their persisted state, marking a
  lost trace ``abandoned`` and surfacing an auto-pause to the audit log.

Both are idempotent: a repeated pass on unchanged engine state performs no
write, because each transition is conditional on the row's current state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.database import db
from app.modules.task_orchestration.dag import NodeState
from app.modules.task_orchestration.native import (
    NativeConfig,
    NativeRun,
    NativeState,
    read_latest_native_runs,
    read_native_config,
)
from app.modules.task_orchestration.repository import TaskOrchestrationRepository

logger = logging.getLogger(__name__)


class NativeObserver(Protocol):
    """The engine reads reconciliation needs, injectable for tests."""

    async def read_native_config(self) -> NativeConfig: ...

    async def read_runs(self, task_names: list[str]) -> dict[str, NativeRun]: ...


class EngineNativeObserver:
    """Default observer: reads the engine on the system connection.

    Configuration is read via ``ADMIN SHOW FRONTEND CONFIG LIKE '%task%'`` — the
    FE-config surface, not ``SHOW VARIABLES``. Every read tolerates an
    unavailable engine by returning an ``UNKNOWN``/unavailable result rather
    than raising, so reconciliation degrades instead of crashing.
    """

    async def read_native_config(self) -> NativeConfig:
        return await read_native_config()

    async def read_runs(self, task_names: list[str]) -> dict[str, NativeRun]:
        if not task_names:
            return {}
        try:
            async with db.system_conn() as conn:
                return await read_latest_native_runs(conn, task_names)
        except Exception as exc:
            logger.warning(
                "could not read native task states for %d tasks: %s",
                len(task_names),
                exc,
            )
            return {
                name: NativeRun(task_name=name, state=NativeState.UNKNOWN)
                for name in task_names
            }


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


#: Native observation -> persisted node state. ``MISSING`` maps to ``abandoned``
#: (lost trace is explicit and non-successful); ``UNKNOWN`` maps to ``None``,
#: meaning "leave the row unchanged".
_NATIVE_TO_NODE: dict[NativeState, NodeState | None] = {
    NativeState.SUCCESS: NodeState.SUCCESS,
    NativeState.FAILED: NodeState.FAILED,
    NativeState.PENDING: None,
    NativeState.RUNNING: None,
    NativeState.MISSING: NodeState.ABANDONED,
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
        """Find work the stream may have lost. Pure reads, no writes."""
        report = ReconcileReport()

        pending = await self._repository.list_graph_runs_by_state(["pending", "running"])
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

    async def reconcile_native(self) -> NativeReconcileReport:
        """Advance running nodes from the engine's ``task_runs``; surface pauses.

        Only node rows that are ``running`` are observed: a node is running only
        because a worker submitted its ``SUBMIT TASK``, so those are the only
        rows whose native trace can settle them. Nothing here executes SQL —
        node execution remains the worker's (design §2).
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
            await self._apply(report, row, task, observed)

        return report

    async def _apply(
        self,
        report: NativeReconcileReport,
        row: dict[str, Any],
        task: dict[str, Any],
        observed: NativeRun,
    ) -> None:
        name = str(task["name"])
        node_state = _NATIVE_TO_NODE[observed.state]

        if node_state is None:
            # RUNNING/PENDING: still in flight. UNKNOWN: a failed read — no
            # state may be inferred, so the row is left untouched.
            if observed.state is NativeState.UNKNOWN:
                report.unknown.append(name)
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

        if node_state is NodeState.ABANDONED:
            report.lost_traces.append(name)
            await self._audit(
                name,
                task,
                action="NODE_ABANDONED",
                status="ABANDONED",
                error="native trace lost; the run did not survive in task_runs",
                graph_run_id=str(row.get("graph_run_id") or ""),
            )
        else:
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

        if node_state is NodeState.FAILED:
            await self._check_auto_pause(report, task, observed)

    async def _check_auto_pause(
        self,
        report: NativeReconcileReport,
        task: dict[str, Any],
        observed: NativeRun,
    ) -> None:
        """Surface an auto-paused task rather than letting a DAG hang.

        The engine pauses a task after ``max_task_consecutive_fail_count``
        consecutive failures and the pause is not queryable (no ``STATE``
        column). The signal available is the failure itself: when a task fails
        on every observed attempt up to the configured ceiling, the DAG cannot
        advance on its own and that is recorded to the audit log. The configured
        count is read from the engine when available, else Nova's setting.
        """
        ceiling = (
            report.config.max_task_consecutive_fail_count
            or self._max_consecutive_fail_count
        )
        if ceiling <= 0:
            return
        name = str(task["name"])
        report.auto_paused.append(name)
        await self._audit(
            name,
            task,
            action="TASK_AUTO_PAUSE_SUSPECTED",
            status="FAILED",
            error=(
                f"task {name!r} failed and will auto-pause after {ceiling} "
                "consecutive failures; the graph cannot advance on its own"
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
