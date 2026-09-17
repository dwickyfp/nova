"""The graph-run worker: consume a job, execute ready nodes, advance state.

One delivery of a graph run is processed to a stable point:

1. Load the graph's tasks and edges from ``NOVA_SYSTEM`` — the source of truth,
   never the stream payload, which carries ids only.
2. Evaluate the DAG state machine (:mod:`dag`) against the node rows already
   persisted.
3. Execute every **ready** node delegate-first, then persist its outcome with a
   conditional write.
4. Re-evaluate; repeat until nothing is ready. If the graph has settled, write
   the terminal graph state and the audit entry.

Because every transition is a conditional write on the current state and the
graph-id/attempt pair is the idempotency key, re-delivering the same job is a
no-op: ready nodes are already terminal, so nothing re-executes. The stream is
transport, not truth.

A worker that dies mid-run leaves a ``RUNNING`` node row. The reconciler
(:mod:`reconciler`) finds it after the heartbeat window and re-evaluates the
graph — the abandoned row is not trusted as progress.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.common.audit import write_audit_log
from app.modules.task_orchestration.dag import (
    GraphState,
    NodeState,
    evaluate,
    graph_from_task_rows,
)
from app.modules.task_orchestration.execution import (
    DelegateExecutor,
    NodeExecutionError,
    TaskSpec,
)
from app.modules.task_orchestration.repository import TaskOrchestrationRepository

logger = logging.getLogger(__name__)

#: Node states that mean "this node must be executed now".
_RUNNABLE = frozenset({NodeState.PENDING, NodeState.ABANDONED})


class TaskLookup(Protocol):
    """Resolves a task id to its definition row."""

    async def get_task(self, task_id: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class GraphRunJob:
    """A stream delivery: ids only, never a credential or a body."""

    graph_run_id: str
    graph_id: str
    trigger_type: str = "schedule"
    task_ids: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, str]) -> GraphRunJob:
        raw_ids = payload.get("task_ids") or ""
        return cls(
            graph_run_id=str(payload["graph_run_id"]),
            graph_id=str(payload["graph_id"]),
            trigger_type=str(payload.get("trigger_type") or "schedule"),
            task_ids=tuple(i for i in raw_ids.split(",") if i),
        )


class GraphRunWorker:
    """Drives one graph run to completion, idempotently."""

    def __init__(
        self,
        repository: TaskOrchestrationRepository,
        executor: DelegateExecutor,
    ) -> None:
        self._repository = repository
        self._executor = executor

    async def handle(self, job: GraphRunJob) -> GraphState | None:
        """Process a graph run. Safe to call repeatedly for the same job.

        Returns the graph's terminal state when this call settled it, or the
        state it was already in.
        """
        graph_run = await self._repository.get_graph_run(job.graph_run_id)
        if graph_run is None:
            logger.warning(
                "graph run %s not found; the stream job is stale", job.graph_run_id
            )
            return None
        # ``NOVA_SYSTEM`` is the source of truth: the graph id comes from the
        # persisted row, not the stream payload, which is transport only.
        job = GraphRunJob(
            graph_run_id=job.graph_run_id,
            graph_id=str(graph_run["graph_id"]),
            trigger_type=job.trigger_type,
            task_ids=job.task_ids,
        )
        if graph_run.get("state") in {
            GraphState.SUCCESS.value,
            GraphState.FAILED.value,
            GraphState.CANCELLED.value,
        }:
            return GraphState(str(graph_run["state"]))

        # Claim the run by moving pending -> running. If another delivery got
        # here first, the conditional write affects no rows and we stand down.
        already = graph_run.get("state") == GraphState.RUNNING.value
        if not already:
            claimed = await self._repository.transition_graph_run(
                job.graph_run_id,
                [GraphState.PENDING.value, GraphState.RUNNING.value, "success"],
                GraphState.RUNNING.value,
            )
            if not claimed:
                latest = await self._repository.get_graph_run(job.graph_run_id)
                return GraphState(str((latest or {}).get("state", "")))

        return await self._drive(job)

    async def _drive(self, job: GraphRunJob) -> GraphState | None:
        """Evaluate and execute until the graph settles."""
        tasks = await self._repository.list_tasks()
        edges = await self._repository.list_edges(job.graph_id)
        by_id = {task["id"]: task for task in tasks}
        by_name = {task["name"]: task for task in tasks}

        node_names = [
            name for name in {t["name"] for t in tasks} if name in {
                str(edge["parent_task"]) for edge in edges
            } | {str(edge["child_task"]) for edge in edges}
        ]
        if not node_names:
            # A standalone task is a one-node graph keyed by its own id.
            standalone = by_id.get(job.graph_id)
            if standalone is not None:
                node_names = [standalone["name"]]

        graph = graph_from_task_rows(node_names, edges)
        if not graph.nodes:
            await self._settle(job, GraphState.FAILED, reason="graph has no nodes")
            return GraphState.FAILED

        while True:
            states, rows = await self._load_states(job.graph_run_id, graph.nodes, by_name)
            decision = evaluate(graph, states)

            if decision.ready:
                for name in decision.ready:
                    task = by_name.get(name)
                    if task is None:
                        logger.error("node %r has no task row; skipping", name)
                        await self._settle(job, GraphState.FAILED, reason=f"missing task {name}")
                        return GraphState.FAILED
                await self._execute_ready(job, graph, decision.ready, by_name, rows)
                continue

            if decision.skipped:
                # Persist skipped nodes so the graph's state is complete and
                # auditable — a node that never ran is visible as skipped, not
                # absent. Then re-evaluate: a skip may unlock a join.
                recorded = await self._persist_skips(job, decision.skipped, by_name, rows)
                if recorded:
                    continue

            if decision.graph_state is None:
                # Nothing ready, nothing terminal: nodes are running natively
                # and this delivery has done all it can. The reconciler will
                # pick the run up when the native run settles.
                return GraphState.RUNNING

            await self._settle(job, decision.graph_state)
            return decision.graph_state

    async def _load_states(
        self,
        graph_run_id: str,
        node_names: list[str],
        by_name: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, NodeState], dict[str, dict[str, Any]]]:
        rows = {
            row["task_id"]: row
            for row in await self._repository.list_node_runs(graph_run_id)
        }
        states: dict[str, NodeState] = {}
        for name in node_names:
            task = by_name.get(name)
            if task is None or task["id"] not in rows:
                states[name] = NodeState.PENDING
                continue
            raw = str(rows[task["id"]]["state"] or "pending")
            states[name] = _to_node_state(raw)
        return states, rows

    async def _persist_skips(
        self,
        job: GraphRunJob,
        skipped: tuple[str, ...],
        by_name: dict[str, dict[str, Any]],
        existing: dict[str, dict[str, Any]],
    ) -> bool:
        """Record skipped nodes as rows. Returns True when it changed anything.

        A skipped node never opens a connection; it is persisted as
        ``skipped`` so the graph's state is explicit rather than a node that
        simply has no row.
        """
        changed = False
        for name in skipped:
            task = by_name.get(name)
            if task is None:
                continue
            row = existing.get(task["id"])
            if row is None:
                row = await self._repository.create_task_run_once(
                    job.graph_run_id, task["id"]
                )
            moved = await self._repository.transition_task_run(
                row["id"],
                [NodeState.PENDING.value, NodeState.ABANDONED.value],
                NodeState.SKIPPED.value,
            )
            if moved:
                changed = True
                await self._audit(
                    job, task, task.get("created_by"), NodeState.SKIPPED.value, None
                )
        return changed

    async def _execute_ready(
        self,
        job: GraphRunJob,
        graph: Any,
        ready: tuple[str, ...],
        by_name: dict[str, dict[str, Any]],
        existing: dict[str, dict[str, Any]],
    ) -> None:
        """Execute ready nodes, persisting each outcome as a conditional write.

        Nodes are **claimed** one at a time (a fast conditional DB write, which
        is where idempotency lives) but **executed concurrently**: siblings in an
        ``A -> B -> [C, D]`` graph must run in parallel, not one after the other
        (design: "C dan D jalan paralel"). The engine's own
        ``task_runs_concurrency`` bounds how many run natively at once.
        """
        claimed: list[tuple[dict[str, Any], str]] = []
        for name in ready:
            task = by_name[name]
            row = existing.get(task["id"])
            if row is None:
                row = await self._repository.create_task_run_once(
                    job.graph_run_id, task["id"]
                )
            current = _to_node_state(str(row["state"] or "pending"))
            if current not in _RUNNABLE:
                # Another delivery already handled this node.
                continue
            moved = await self._repository.transition_task_run(
                row["id"], [current.value], NodeState.RUNNING.value
            )
            if moved:
                claimed.append((task, row["id"]))

        if not claimed:
            return
        await asyncio.gather(
            *(self._when_then_execute(job, task, run_id) for task, run_id in claimed)
        )

    async def _when_then_execute(
        self, job: GraphRunJob, task: dict[str, Any], run_id: str
    ) -> None:
        """Evaluate ``WHEN`` then execute, as one schedulable unit."""
        if await self._when_allows(job, task, run_id):
            await self._execute_one(job, task, run_id)

    async def _when_allows(
        self, job: GraphRunJob, task: dict[str, Any], run_id: str
    ) -> bool:
        """Evaluate the node's ``WHEN``. False skips the node (and descendants).

        The expression is evaluated on the owner's connection through the same
        credential path as execution. An evaluation **error** fails the node
        rather than silently skipping it — an error is not "no data" (design §6).
        """
        expression = (task.get("when_expr") or "").strip()
        if not expression:
            return True
        owner = task.get("created_by") or ""
        spec = TaskSpec(
            name=task["name"],
            body=task.get("definition") or "",
            database=task.get("database_name"),
        )
        try:
            allowed = await self._executor.evaluate_when(
                expression, spec=spec, owner=owner
            )
        except Exception as exc:  # noqa: BLE001 - an error is a failure, not a skip
            message = _safe_message(exc)
            await self._repository.transition_task_run(
                run_id,
                [NodeState.RUNNING.value],
                NodeState.FAILED.value,
                error_message=message,
            )
            await self._audit(job, task, owner, NodeState.FAILED.value, message)
            return False
        if allowed:
            return True
        await self._repository.transition_task_run(
            run_id,
            [NodeState.RUNNING.value],
            NodeState.SKIPPED.value,
        )
        await self._audit(job, task, owner, NodeState.SKIPPED.value, None)
        return False

    async def _execute_one(
        self, job: GraphRunJob, task: dict[str, Any], run_id: str
    ) -> None:
        owner = task.get("created_by")
        if not owner:
            await self._repository.transition_task_run(
                run_id,
                [NodeState.RUNNING.value],
                NodeState.FAILED.value,
                error_message="task has no owner; delegate-first cannot run",
            )
            await self._audit(job, task, owner, NodeState.FAILED.value, "task owner unknown")
            return

        body = task.get("definition") or ""
        if not body.strip():
            await self._repository.transition_task_run(
                run_id,
                [NodeState.RUNNING.value],
                NodeState.SKIPPED.value,
                error_message=None,
            )
            await self._audit(job, task, owner, NodeState.SKIPPED.value, "empty body")
            return

        spec = TaskSpec(
            name=task["name"],
            body=body,
            database=task.get("database_name"),
        )

        async def heartbeat() -> None:
            await self._repository.mark_task_run_heartbeat(run_id)
            await self._repository.mark_graph_run_heartbeat(job.graph_run_id)

        try:
            result = await self._executor.execute(spec, owner, heartbeat=heartbeat)
        except NodeExecutionError as exc:
            await self._repository.transition_task_run(
                run_id,
                [NodeState.RUNNING.value],
                NodeState.FAILED.value,
                query_id=exc.query_id,
                error_message=str(exc),
            )
            await self._audit(job, task, owner, NodeState.FAILED.value, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - any failure fails the node
            # CredentialUnavailable and connection errors land here. Never
            # include the exception's repr if it could carry a secret; the
            # executor already redacts engine messages.
            message = _safe_message(exc)
            await self._repository.transition_task_run(
                run_id,
                [NodeState.RUNNING.value],
                NodeState.FAILED.value,
                error_message=message,
            )
            await self._audit(job, task, owner, NodeState.FAILED.value, message)
            return

        state = (
            NodeState.SUCCESS
            if result.state.upper() in {"FINISHED", "SUCCESS"}
            else NodeState.SUSPENDED
            if result.state.upper() == "SUSPENDED"
            else NodeState.FAILED
        )
        await self._repository.transition_task_run(
            run_id,
            [NodeState.RUNNING.value],
            state.value,
            query_id=result.query_id,
            error_message=result.error_message,
        )
        await self._audit(job, task, owner, state.value, result.error_message)

    async def _settle(
        self, job: GraphRunJob, state: GraphState, *, reason: str | None = None
    ) -> None:
        moved = await self._repository.transition_graph_run(
            job.graph_run_id,
            [GraphState.PENDING.value, GraphState.RUNNING.value],
            state.value,
        )
        if moved:
            logger.info(
                "graph run %s settled as %s", job.graph_run_id, state.value
            )
            await write_audit_log(
                event_type="task_graph_run",
                user_name="nova-worker",
                action="GRAPH_RUN_" + state.value.upper(),
                object_type="TASK_GRAPH_RUN",
                object_name=job.graph_run_id,
                status="SUCCESS" if state is GraphState.SUCCESS else state.value.upper(),
                error_message=reason,
                database_name=job.graph_id,
            )

    async def _audit(
        self,
        job: GraphRunJob,
        task: dict[str, Any],
        owner: str | None,
        state: str,
        error: str | None,
    ) -> None:
        await write_audit_log(
            event_type="task_node_run",
            user_name=owner or "nova-worker",
            action="NODE_" + state.upper(),
            object_type="TASK",
            object_name=task["name"],
            status="SUCCESS" if state == NodeState.SUCCESS.value else state.upper(),
            error_message=error,
            session_id=None,
            database_name=job.graph_id,
        )


def _to_node_state(raw: str) -> NodeState:
    try:
        return NodeState(raw.lower())
    except ValueError:
        return NodeState.PENDING


def _safe_message(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    for marker in ("password", "identified by", "secret", "token"):
        if marker in lowered:
            return "node execution failed; details suppressed to avoid a credential"
    return text


async def process_job(
    repository: TaskOrchestrationRepository,
    executor: DelegateExecutor,
    payload: dict[str, str],
) -> GraphState | None:
    """Convenience entry point used by the consumer and tests."""
    return await GraphRunWorker(repository, executor).handle(GraphRunJob.from_payload(payload))
