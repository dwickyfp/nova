"""Nova scheduler tick — decide which graphs are due, persist, then publish.

The scheduler **never executes SQL**. It computes time and creates graph runs;
the worker executes. That separation is what lets the scheduler be a singleton
(design §2, rule 2).

Ordering is non-negotiable and is enforced here: the graph run is written to
``NOVA_SYSTEM`` *first*, and only then is the job pushed to Redis (design §2,
rule 1). If the push fails or Redis is flushed, the row survives and the
reconciler re-derives the work.

Idempotency uses a **deterministic graph-run id** of ``graph_id`` + due instant:
two ticks for the same due time resolve to the same primary key, so the second
tick sees the run already exists and does not publish a duplicate job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.core.config import settings
from app.modules.task_orchestration.graph import Edge, Graph
from app.modules.task_orchestration.repository import TaskOrchestrationRepository
from app.modules.task_orchestration.schedule import (
    ScheduleError,
    latest_occurrence,
    resolve_timezone,
)
from app.modules.task_orchestration.transport import GraphRunTransport

logger = logging.getLogger(__name__)

SCHEDULED_KINDS = frozenset({"cron", "interval"})


def deterministic_run_id(graph_id: str, due_at: datetime) -> str:
    """The idempotency key for a (graph, due-time) pair.

    A UUID v5 keeps it within the 64-char primary-key column and makes the key
    reproducible across processes and restarts, which is what turns a double
    tick into a single graph run.
    """
    return str(uuid5(NAMESPACE_URL, f"nova:graph_run:{graph_id}:{due_at.isoformat()}"))


@dataclass(frozen=True)
class DueGraph:
    """A graph whose root task has a due occurrence that is not yet enqueued."""

    graph_id: str
    due_at: datetime
    task_ids: list[str]
    task_names: list[str]

    @property
    def run_id(self) -> str:
        return deterministic_run_id(self.graph_id, self.due_at)


@dataclass
class SchedulerPlan:
    """The graphs a tick found due, as pure data (no side effects)."""

    due: list[DueGraph]
    skipped: int = 0


def naive_engine_time_to_utc(value: datetime, engine_timezone: str) -> datetime:
    """Interpret a naive StarRocks ``DATETIME`` in the engine's session timezone.

    ``NOW()`` is written in the engine session timezone (the design records that
    this is ``Asia/Jakarta`` on the probed FE), so reading it back as UTC would
    silently offset every schedule anchor. The zone is resolved from the engine
    (or an explicit override), never assumed.
    """
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return value.replace(tzinfo=resolve_timezone(engine_timezone)).astimezone(UTC)


async def resolve_engine_timezone(repository: TaskOrchestrationRepository) -> str:
    """The session zone to interpret naive engine timestamps in.

    An explicit ``SCHEDULER_ENGINE_TIMEZONE`` wins (useful for tests and
    unusual deployments). Otherwise the zone is read from the engine itself via
    ``SELECT @@time_zone``, which is the only value that cannot be wrong: the
    engine writes ``NOW()`` in that zone, and a static default drifts from it
    the moment the deployment differs (the defect that made interval tasks
    never fire).
    """
    configured = (settings.SCHEDULER_ENGINE_TIMEZONE or "").strip()
    if configured:
        return configured

    detected = await repository.get_engine_timezone()
    if detected:
        resolve_timezone(detected)
        return detected

    logger.warning(
        "engine reported no session timezone; falling back to UTC for schedule anchors"
    )
    return "UTC"


def build_graphs(tasks: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Graph]:
    """Group tasks and edges by ``graph_id``.

    Graph membership is explicit: an edge's ``graph_id`` names its graph. Tasks
    that appear in no edge are single-node graphs keyed by their own id, so a
    standalone scheduled task still gets a graph run.

    Edge endpoints are task **names** (the repository stores names), so the
    per-graph task list is resolved by name.
    """
    by_graph: dict[str, list[Edge]] = {}
    for edge in edges:
        by_graph.setdefault(str(edge["graph_id"]), []).append(
            Edge(parent=str(edge["parent_task"]), child=str(edge["child_task"]))
        )

    graphs: dict[str, Graph] = {}
    referenced: set[str] = set()
    for graph_id, graph_edges in by_graph.items():
        names = [
            name
            for name in {
                endpoint for edge in graph_edges for endpoint in (edge.parent, edge.child)
            }
        ]
        graphs[graph_id] = Graph.from_edges(names, graph_edges)
        referenced.update(names)

    for task in tasks:
        if task["name"] not in referenced:
            graphs[task["id"]] = Graph.from_edges([task["name"]], [])

    return graphs


def _graph_task_ids(
    graph: Graph, tasks_by_name: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    names = sorted(graph.nodes)
    ids = [tasks_by_name[name]["id"] for name in names if name in tasks_by_name]
    return ids, names


def plan_tick(
    tasks: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    now: datetime,
    engine_timezone: str = "UTC",
) -> SchedulerPlan:
    """Compute the due graphs for this tick. Pure — no I/O, no side effects.

    Only **root** tasks (no incoming edge) are schedule anchors: a graph run is
    triggered once per due root, and a single run covers every node reachable
    from it. Enqueueing per task would multiply one DAG into one run per node,
    which design §2 explicitly rejects.

    ``engine_timezone`` is the StarRocks session timezone that ``created_at``
    was written in; callers obtain it from the engine (see
    ``resolve_engine_timezone``) rather than a config default.
    """
    tasks_by_name = {t["name"]: t for t in tasks}
    graphs = build_graphs(tasks, edges)
    due: list[DueGraph] = []
    skipped = 0

    for graph_id, graph in graphs.items():
        children = {child for kids in graph.adjacency.values() for child in kids}
        roots = [name for name in graph.nodes if name not in children]

        graph_due_at: datetime | None = None
        root_task: dict[str, Any] | None = None
        for name in sorted(roots):
            task = tasks_by_name.get(name)
            if task is None:
                continue
            kind = (task.get("schedule_kind") or "").lower()
            if kind not in SCHEDULED_KINDS:
                continue
            created_at = task.get("created_at")
            try:
                if isinstance(created_at, datetime):
                    anchor = naive_engine_time_to_utc(created_at, engine_timezone)
                else:
                    anchor = now
                occurrence = latest_occurrence(
                    kind,
                    task.get("schedule_expr") or "",
                    task.get("timezone") or "",
                    anchor,
                    now,
                )
            except ScheduleError as exc:
                logger.warning("task %r has an unusable schedule: %s", name, exc)
                skipped += 1
                continue
            if occurrence is None:
                continue
            if graph_due_at is None or occurrence < graph_due_at:
                graph_due_at = occurrence
                root_task = task

        if graph_due_at is None or root_task is None:
            continue

        task_ids, task_names = _graph_task_ids(graph, tasks_by_name)
        due.append(
            DueGraph(
                graph_id=graph_id,
                due_at=graph_due_at,
                task_ids=task_ids,
                task_names=task_names,
            )
        )

    return SchedulerPlan(due=due, skipped=skipped)


class SchedulerTick:
    """Orchestrates one tick: plan (pure) then persist-then-publish (side effects)."""

    def __init__(
        self,
        repository: TaskOrchestrationRepository,
        transport: GraphRunTransport,
    ) -> None:
        self._repository = repository
        self._transport = transport

    async def tick(self, now: datetime | None = None) -> SchedulerPlan:
        moment = now or datetime.now(UTC)
        engine_timezone = await resolve_engine_timezone(self._repository)
        tasks = await self._repository.list_tasks()
        edges = await self._repository.list_all_edges()
        plan = plan_tick(tasks, edges, moment, engine_timezone)

        for due in plan.due:
            existing = await self._repository.get_graph_run(due.run_id)
            if existing is not None:
                continue

            created = await self._repository.create_graph_run(
                {
                    "id": due.run_id,
                    "graph_id": due.graph_id,
                    "trigger_type": "schedule",
                    "state": "pending",
                }
            )
            await self._transport.publish_graph_run(created, due.task_ids)

        return plan
