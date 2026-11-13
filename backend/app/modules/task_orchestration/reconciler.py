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
* :meth:`Reconciler.settle_abandoned_runs` ends what an abandoned node holds.
  A native attempt whose trace shows ``SUCCESS``/``FAILED`` takes that verified
  outcome and the graph continues. Otherwise the run is held until no effect of
  the attempt can still be in flight (:func:`effect_deadline`), then the graph
  run is settled ``failed`` with the node left ``abandoned`` and an explanation.
  Nothing is re-run: an unknown outcome is reported, never replayed.

All are idempotent: a repeated pass on unchanged engine state performs no
write, because each transition is conditional on the row's current state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, Protocol
from uuid import UUID

from app.common.audit import write_audit_log
from app.core.config import settings
from app.modules.task_orchestration.dag import NodeState
from app.modules.task_orchestration.execution import native_attempt_name
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
from app.modules.task_orchestration.schedule import resolve_timezone

logger = logging.getLogger(__name__)


def _native_lookup_name(row: dict[str, Any], task: dict[str, Any]) -> str:
    """Find the attempt-specific native run for a durable UUID node row.

    Older test fixtures and legacy rows with non-UUID IDs retain the logical
    task name; repository-created node rows always use UUID IDs.
    """
    run_id = str(row["id"])
    try:
        UUID(run_id)
    except ValueError:
        return str(task["name"])
    return native_attempt_name(run_id)


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


#: Written on a node when its worker's heartbeat lapses, before the outcome is known.
STOPPED_REPORTING_MESSAGE = (
    "This step stopped reporting progress; Nova is confirming its outcome and will not repeat it"
)

#: Written on a node when its run is ended without a confirmed outcome.
UNKNOWN_OUTCOME_MESSAGE = (
    "This step stopped reporting progress (its worker or the metadata database was "
    "interrupted), so its outcome is unknown. Nova ended the run without repeating the "
    "step. Check whether its changes were applied before running the task again."
)

#: Seconds a Python task's sandbox and data token outlive the run's timeout
#: (``python_task._run_in_sandbox``): after that the attempt can write nothing.
_PYTHON_EFFECT_MARGIN_SECONDS = 600


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def effect_deadline(row: dict[str, Any], task: dict[str, Any] | None) -> datetime | None:
    """When no effect of an abandoned attempt can still be in flight.

    Counted from the node's last heartbeat (its start when it never stamped
    one), plus the heartbeat window, plus the longest the attempt's work may
    outlive its worker:

    * ``python`` — the sandbox lifetime and data-token TTL, the run timeout plus
      a margin; the sandbox is removed and its token stops working by then;
    * every other handler — the worker's native/statement poll window, which
      bounds how long Nova ever waits on the engine for one node.

    A node whose task is gone (``DROP TASK`` refuses while a step could still be
    in flight) needs only the heartbeat window. ``None`` when the row carries no
    timestamp, which keeps the run held.
    """
    base = _aware(row.get("heartbeat_at")) or _aware(row.get("started_at"))
    if base is None:
        return None
    window = float(settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS)
    if task is not None:
        if str(task.get("handler") or "sql") == "python":
            window += settings.TASK_PYTHON_TIMEOUT_SECONDS + _PYTHON_EFFECT_MARGIN_SECONDS
        else:
            window += settings.WORKER_TASK_POLL_TIMEOUT_SECONDS
    return base + timedelta(seconds=window)


@dataclass(frozen=True)
class RunHold:
    """Whether an active graph run can still change anything."""

    live: bool
    #: When a run held only by abandoned steps is settled; ``None`` otherwise.
    settles_at: datetime | None = None


async def active_run_hold(
    repository: TaskOrchestrationRepository,
    run: dict[str, Any],
    *,
    now: datetime | None = None,
) -> RunHold:
    """Classify an active run for callers that must wait for it (``DROP TASK``).

    A run is not live only when it is held by abandoned steps alone and every
    one of them is past its :func:`effect_deadline`: nothing of it can run or
    write any more, and the reconciler settles it ``failed``. Anything else —
    a pending run, a running step, a run with no abandoned step, or an
    abandoned step still inside its window — is live.
    """
    if str(run.get("state")) != "running":
        return RunHold(live=True)
    rows = await repository.list_node_runs(str(run["id"]))
    abandoned = [row for row in rows if row.get("state") == NodeState.ABANDONED.value]
    if not abandoned or any(row.get("state") == NodeState.RUNNING.value for row in rows):
        return RunHold(live=True)
    tasks = {str(t["id"]): t for t in await repository.list_tasks(str(run["graph_id"]))}
    deadlines = [effect_deadline(row, tasks.get(str(row.get("task_id")))) for row in abandoned]
    known = [deadline for deadline in deadlines if deadline is not None]
    if len(known) != len(deadlines):
        return RunHold(live=True)
    settles_at = max(known)
    if (now or datetime.now(UTC)) >= settles_at:
        return RunHold(live=False, settles_at=settles_at)
    return RunHold(live=True, settles_at=settles_at)


@dataclass
class AbandonedSettleReport:
    """What one pass over abandoned nodes verified and ended."""

    #: Abandoned nodes whose native trace proved their outcome.
    verified: list[str] = field(default_factory=list)
    #: Graph runs to drive again because a node now has a verified outcome.
    requeue: list[str] = field(default_factory=list)
    #: Graph runs ended ``failed`` with an unknown step outcome.
    settled: list[str] = field(default_factory=list)
    #: Graph runs still held because an attempt may still be in flight.
    held: list[str] = field(default_factory=list)


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

    async def _engine_zone(self) -> tzinfo | None:
        """The engine's session zone, read once; native trace times use it."""
        if not hasattr(self, "_zone_loaded"):
            self._zone: tzinfo | None = None
            try:
                name = await self._repository.get_engine_timezone()
                self._zone = resolve_timezone(name) if name else None
            except Exception:  # noqa: BLE001 - an unknown zone keeps the naive comparison
                logger.warning("engine timezone unavailable; comparing native times as read")
            self._zone_loaded = True
        return self._zone

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
        return [str(node["id"]) for node in stale if str(node.get("graph_run_id")) == graph_run_id]

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
            # Fenced on the generation read: a node retaken since is not ours to end.
            moved = await self._repository.abandon_task_run(
                str(node["id"]),
                generation=int(node.get("lease_generation") or 0),
                error_message=STOPPED_REPORTING_MESSAGE,
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

    async def settle_abandoned_runs(self, *, now: datetime | None = None) -> AbandonedSettleReport:
        """Resolve what abandoned nodes hold, by verification or by deadline.

        An abandoned node holds its graph run ``running`` (the worker will not
        repeat it, start a finalizer, or admit an overlapping run). Left alone
        that hold is permanent, and the task can never be dropped. Per node:

        1. A native attempt is verified from ``task_runs``. ``SUCCESS`` or
           ``FAILED`` (from this attempt, not an older one) is recorded and the
           graph run is driven again, continuing as for any settled node.
           ``RUNNING``/``PENDING`` keeps the run held.
        2. Otherwise the node waits for its :func:`effect_deadline`. Once every
           abandoned node of a run is past it and no node is running, the run
           is claimed under a new lease and settled ``failed``. Nodes stay
           ``abandoned`` with :data:`UNKNOWN_OUTCOME_MESSAGE`; dependents and
           finalizers do not start, and nothing is re-submitted.

        Every write is conditional (node state and lease generation, graph lease),
        so concurrent passes or deliveries cannot double-settle.
        """
        report = AbandonedSettleReport()
        rows = await self._repository.list_unsettled_abandoned_task_runs()
        if not rows:
            return report
        current = now or datetime.now(UTC)
        tasks = {str(t["id"]): t for t in await self._repository.list_tasks()}

        native_names: dict[str, str] = {}
        for row in rows:
            task = tasks.get(str(row.get("task_id")))
            native = row.get("delegated") and (task or {}).get("handler", "sql") in {None, "sql"}
            if task is not None and native:
                native_names[str(row["id"])] = _native_lookup_name(row, task)
        observed: dict[str, NativeRun] = {}
        if native_names:
            try:
                observed = await self._observer.read_runs(sorted(set(native_names.values())))
            except Exception:  # noqa: BLE001 - an unreadable trace verifies nothing
                logger.warning("native task trace unavailable; abandoned nodes stay held")
                observed = {}

        held: set[str] = set()
        expired: dict[str, list[tuple[dict[str, Any], dict[str, Any] | None]]] = {}
        for row in rows:
            graph_run_id = str(row.get("graph_run_id") or "")
            task = tasks.get(str(row.get("task_id")))
            native_name = native_names.get(str(row["id"]))
            if native_name is not None and task is not None:
                seen = observed.get(native_name)
                state = _NATIVE_TO_NODE[seen.state] if seen is not None else None
                if (
                    seen is not None
                    and state is not None
                    and not _is_stale_native_row(row, seen, await self._engine_zone())
                ):
                    await self._record_verified(report, row, task, state, seen)
                    continue
                if seen is not None and seen.state in {NativeState.RUNNING, NativeState.PENDING}:
                    held.add(graph_run_id)
                    continue
            deadline = effect_deadline(row, task)
            if deadline is None or current < deadline:
                held.add(graph_run_id)
                continue
            expired.setdefault(graph_run_id, []).append((row, task))

        for graph_run_id, nodes in expired.items():
            if graph_run_id in held or graph_run_id in report.requeue:
                continue
            if await self._settle_unknown_run(graph_run_id, nodes):
                report.settled.append(graph_run_id)
        report.held = sorted(held - set(report.requeue))
        return report

    async def _record_verified(
        self,
        report: AbandonedSettleReport,
        row: dict[str, Any],
        task: dict[str, Any],
        state: NodeState,
        observed: NativeRun,
    ) -> None:
        moved = await self._repository.settle_abandoned_task_run(
            str(row["id"]),
            state.value,
            generation=int(row.get("lease_generation") or 0),
            query_id=observed.query_id,
            error_message=observed.error_message,
        )
        if not moved:
            return
        report.verified.append(str(row["id"]))
        graph_run_id = str(row.get("graph_run_id") or "")
        if graph_run_id and graph_run_id not in report.requeue:
            report.requeue.append(graph_run_id)
        await self._audit(
            str(task["name"]),
            task,
            action="NODE_" + state.value.upper(),
            status="SUCCESS" if state is NodeState.SUCCESS else state.value.upper(),
            error=observed.error_message or "outcome verified from the native task trace",
            graph_run_id=graph_run_id,
        )
        if state is NodeState.SUCCESS:
            await self._repository.reset_consecutive_failures(str(task["id"]))

    async def _settle_unknown_run(
        self,
        graph_run_id: str,
        nodes: list[tuple[dict[str, Any], dict[str, Any] | None]],
    ) -> bool:
        """End one run held only by expired abandoned nodes as ``failed``."""
        run = await self._repository.get_graph_run(graph_run_id)
        if run is None or str(run.get("state")) not in {"pending", "running"}:
            return False
        latest = await self._repository.list_node_runs(graph_run_id)
        if any(row.get("state") == NodeState.RUNNING.value for row in latest):
            return False
        generation = await self._repository.claim_graph_run(
            graph_run_id, [str(run["state"])], "running"
        )
        if generation is None:
            return False
        for row, _task in nodes:
            existing = row.get("error_message")
            if not existing or existing == STOPPED_REPORTING_MESSAGE:
                await self._repository.settle_abandoned_task_run(
                    str(row["id"]),
                    NodeState.ABANDONED.value,
                    generation=int(row.get("lease_generation") or 0),
                    error_message=UNKNOWN_OUTCOME_MESSAGE,
                )
        moved = await self._repository.transition_graph_run(
            graph_run_id, ["running"], "failed", generation=generation
        )
        if not moved:
            return False
        names = sorted(str(task["name"]) for _row, task in nodes if task is not None)
        reason = (
            f"Step {', '.join(names) or 'of a dropped task'} stopped without a confirmed "
            "result; the run was ended without repeating it"
        )
        logger.info("graph run %s settled as failed: abandoned step outcome unknown", graph_run_id)
        await write_audit_log(
            event_type="task_graph_run",
            user_name="nova-reconciler",
            action="GRAPH_RUN_FAILED",
            object_type="TASK_GRAPH_RUN",
            object_name=graph_run_id,
            status="FAILED",
            error_message=reason,
            database_name=str(run.get("graph_id") or "") or None,
        )
        return True

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

        tasks = {str(t["id"]): t for t in await self._repository.list_tasks()}
        running = [row for row in running
                   if (tasks.get(str(row.get("task_id")), {}).get("handler") or "sql") == "sql"]
        names = sorted(
            {
                _native_lookup_name(row, tasks[str(row["task_id"])])
                for row in running
                if row.get("task_id") and str(row["task_id"]) in tasks
            }
        )
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
            native_name = _native_lookup_name(row, task)
            observed = runs.get(native_name)
            if observed is None:
                report.unknown.append(name)
                continue
            await self._apply(report, row, task, observed, schedules.get(native_name))

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

        if _is_stale_native_row(row, observed, await self._engine_zone()):
            # The newest native row predates this node's submission, so it is a
            # leftover from a previous attempt — not this run's outcome. Two
            # workers share the stream, so a reconciler must not settle a node
            # another worker is still waiting on. Skip; a later pass sees the
            # real row (NOVA-42 second finding).
            return

        # The native trace is the participant's verified outcome. Record it
        # against the lease generation observed with the row, so a node retaken
        # in between is never settled by a stale observation.
        moved = await self._repository.finish_task_run(
            str(row["id"]),
            node_state.value,
            generation=int(row.get("lease_generation") or 0),
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
            status=("SUCCESS" if node_state is NodeState.SUCCESS else node_state.value.upper()),
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
        ceiling = report.config.max_task_consecutive_fail_count or self._max_consecutive_fail_count
        if ceiling <= 0:
            return

        # The engine may embed the count in the error text; prefer that when
        # present, else use Nova's own persistent counter.
        engine_count = parse_consecutive_failures(observed.error_message)
        count = engine_count
        if count is None:
            count = await self._repository.increment_consecutive_failures(str(task["id"]))

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


def _is_stale_native_row(
    row: dict[str, Any], observed: NativeRun, engine_zone: tzinfo | None = None,
) -> bool:
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
    # Metadata stamps are timezone-aware; native times are engine-local naive.
    if started.tzinfo is not None and created.tzinfo is None and engine_zone is not None:
        started = started.astimezone(engine_zone)
    started_naive = started.replace(tzinfo=None) if started.tzinfo else started
    created_naive = created.replace(tzinfo=None) if created.tzinfo else created
    return created_naive < started_naive
