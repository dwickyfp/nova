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
from app.modules.task_orchestration.repository import (
    TaskOrchestrationRepository,
    scope_from_graph_id,
)
from app.modules.task_orchestration.schedule import (
    ScheduleError,
    due_occurrences,
    resolve_timezone,
)
from app.modules.task_orchestration.transport import GraphRunTransport

logger = logging.getLogger(__name__)

SCHEDULED_KINDS = frozenset({"cron", "interval"})

#: The Nova overlap policies, enforced at graph-run enqueue. ``skip`` is the
#: default so an unset or unknown value never starts an overlapping run.
OVERLAP_POLICIES = frozenset({"skip", "queue", "allow"})


def should_enqueue(overlap_policy: str, active_runs: int) -> bool:
    """Whether a new graph run may be created, given active runs for the graph.

    ``skip``  — no new run while one is active (the occurrence is dropped).
    ``queue`` — a new run is created; the worker defers it until the active one
                settles, so occurrences are honoured in order, one at a time.
    ``allow`` — a new run is created and may execute concurrently with the
                active one.

    Pure, so the three policies are unit-testable without a database.
    """
    if active_runs <= 0:
        return True
    # Only an explicit queue/allow may overlap. `skip` and any unknown value
    # refuse, so a bad value never starts an overlap the caller did not ask for.
    return overlap_policy in {"queue", "allow"}


def deterministic_run_id(graph_id: str, due_at: datetime, root_task: str = "") -> str:
    """The idempotency key for a ``(graph, root occurrence)`` pair.

    A UUID v5 keeps it within the 64-char primary-key column and makes the key
    reproducible across processes and restarts, which is what turns a double
    tick into a single graph run. ``root_task`` is part of the key because one
    graph may have several scheduled roots; excluding it would make two distinct
    roots due at the same instant collide on one run.
    """
    return str(uuid5(NAMESPACE_URL, f"nova:graph_run:{graph_id}:{root_task}:{due_at.isoformat()}"))


@dataclass(frozen=True)
class DueGraph:
    """A graph whose root task has a due occurrence that is not yet enqueued."""

    graph_id: str
    due_at: datetime
    task_ids: list[str]
    task_names: list[str]
    #: The enqueue-time overlap policy, taken from the graph's root task.
    overlap_policy: str = "skip"
    #: The root task whose schedule anchored this occurrence.
    root_task: str = ""

    @property
    def run_id(self) -> str:
        return deterministic_run_id(self.graph_id, self.due_at, self.root_task)


@dataclass
class SchedulerPlan:
    """The graphs a tick found due, as pure data (no side effects)."""

    due: list[DueGraph]
    #: Tasks whose schedule expression was unusable and was skipped. Distinct
    #: from an overlap skip, which is a deliberate policy outcome, not a defect.
    skipped: int = 0
    #: Due graphs not enqueued because their overlap policy refused to overlap
    #: an active run (``skip``). Reported separately so the two kinds of skip
    #: are never conflated in logs or tests.
    overlap_skipped: int = 0


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

    logger.warning("engine reported no session timezone; falling back to UTC for schedule anchors")
    return "UTC"


def task_qualified_name(task: dict[str, Any]) -> str:
    """``database.schema.name`` for a task row, or its bare name if unscoped."""
    parts = [
        str(part)
        for part in (task.get("database_name"), task.get("schema_name"), task.get("name"))
        if part
    ]
    return ".".join(parts)


def build_graphs(tasks: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Graph]:
    """Group tasks and edges by ``graph_id``.

    Graph membership is explicit: an edge's ``graph_id`` names its graph. Tasks
    that appear in no edge are single-node graphs keyed by their own **qualified**
    name, so a standalone scheduled task still gets a graph run. A graph id is
    itself qualified (``database.schema.name``), so a graph's scope is recovered
    from it and its node names resolve within that scope — two same-named tasks
    in different schemas are two graphs, not one.
    """
    by_graph: dict[str, list[Edge]] = {}
    for edge in edges:
        by_graph.setdefault(str(edge["graph_id"]), []).append(
            Edge(parent=str(edge["parent_task"]), child=str(edge["child_task"]))
        )

    graph_ids_with_edges = set(by_graph)
    # Endpoint names by scope, plus an unscoped bucket for legacy edges whose
    # graph id is not qualified. A task is "referenced" if its own scope (or the
    # unscoped bucket) contains its name.
    referenced: dict[tuple[str, str] | None, set[str]] = {}
    for graph_id, graph_edges in by_graph.items():
        bucket = scope_from_graph_id(graph_id)
        endpoints = referenced.setdefault(bucket, set())
        endpoints.update(endpoint for edge in graph_edges for endpoint in (edge.parent, edge.child))

    graphs: dict[str, Graph] = {}
    for graph_id, graph_edges in by_graph.items():
        names = [
            name
            for name in {endpoint for edge in graph_edges for endpoint in (edge.parent, edge.child)}
        ]
        graphs[graph_id] = Graph.from_edges(names, graph_edges)

    for task in tasks:
        qualified = task_qualified_name(task)
        # A task belongs to a graph if a graph with its qualified name exists,
        # or it is an endpoint of an edge in its own scope (or an unscoped edge).
        if qualified in graph_ids_with_edges:
            continue
        scope = _task_scope(task)
        if str(task["name"]) in referenced.get(scope, set()):
            continue
        if scope is not None and str(task["name"]) in referenced.get(None, set()):
            continue
        graphs[qualified] = Graph.from_edges([str(task["name"])], [])

    return graphs


def _task_scope(task: dict[str, Any]) -> tuple[str, str] | None:
    database = task.get("database_name")
    schema = task.get("schema_name")
    if database and schema:
        return str(database), str(schema)
    return None


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

    Only **root** tasks (no incoming edge) are schedule anchors: a due root
    creates one graph run covering every node reachable from it. Enqueueing per
    task would multiply one DAG into one run per node, which design §2
    explicitly rejects.

    A graph may have **several scheduled roots** (``A`` at 12:00, ``B`` at
    12:05 under one ``graph_id``). Each due root is its own occurrence and gets
    its own run: previously only the earliest occurrence across a graph's roots
    was ever enqueued, so every other root's cadence was silently dropped once
    the earliest run existed. The occurrence is part of the run id, so two
    roots due at different instants resolve to different (idempotent) runs.

    A tick that arrives late **catches up**: every occurrence missed since the
    anchor is emitted, each with its own idempotent run id, so a scheduler
    outage does not silently swallow the fires in between. The catch-up is
    bounded (``schedule.MAX_CATCH_UP_OCCURRENCES``) so a very long outage
    converges to the present instead of replaying unbounded history.

    ``engine_timezone`` is the StarRocks session timezone that ``created_at``
    was written in; callers obtain it from the engine (see
    ``resolve_engine_timezone``) rather than a config default.
    """
    graphs = build_graphs(tasks, edges)
    due: list[DueGraph] = []
    skipped = 0

    for graph_id, graph in graphs.items():
        # Resolve node rows **within this graph's scope**. A single global
        # ``{name: task}`` map would collide two same-named tasks in different
        # schemas, and a root in one schema could anchor a run using another
        # schema's task row.
        scope = scope_from_graph_id(graph_id)
        scoped = _tasks_by_name(tasks, scope)
        children = {child for kids in graph.adjacency.values() for child in kids}
        roots = [name for name in graph.nodes if name not in children]

        for name in sorted(roots):
            task = scoped.get(name)
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
                occurrences = due_occurrences(
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
            if not occurrences:
                continue

            task_ids, task_names = _graph_task_ids(graph, scoped)
            policy = _normalise_overlap(task.get("overlap_policy"))
            for occurrence in occurrences:
                due.append(
                    DueGraph(
                        graph_id=graph_id,
                        due_at=occurrence,
                        task_ids=task_ids,
                        task_names=task_names,
                        overlap_policy=policy,
                        root_task=name,
                    )
                )

    return SchedulerPlan(due=due, skipped=skipped)


def _tasks_by_name(
    tasks: list[dict[str, Any]], scope: tuple[str, str] | None
) -> dict[str, dict[str, Any]]:
    """Task rows keyed by bare name, restricted to ``scope`` when given.

    An unscoped graph (a legacy graph id) falls back to every task, preserving
    the pre-scope behaviour for rows written before the scope columns existed.
    """
    if scope is None:
        return {str(task["name"]): task for task in tasks}
    database_name, schema_name = scope
    return {
        str(task["name"]): task
        for task in tasks
        if str(task.get("database_name") or "") == database_name
        and str(task.get("schema_name") or "") == schema_name
    }


def _normalise_overlap(value: Any) -> str:
    """The root task's overlap policy, defaulting to ``skip``.

    An unknown or missing value falls back to the strictest policy rather than
    the most permissive: a bad value must never silently start overlapping runs.
    """
    candidate = str(value or "").strip().lower()
    return candidate if candidate in OVERLAP_POLICIES else "skip"


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
            # One graph's failure must not abandon the rest of the batch. Before
            # this guard a single transport error (or a DB blip) escaped
            # ``tick`` and every later due graph in the same batch was silently
            # dropped until the next tick.
            try:
                await self._enqueue(due, plan)
            except Exception:
                logger.exception(
                    "failed to enqueue due graph %s (root %s); continuing",
                    due.graph_id,
                    due.root_task or "-",
                )

        return plan

    async def _enqueue(self, due: DueGraph, plan: SchedulerPlan) -> None:
        """Persist then publish one due occurrence, idempotently.

        The overlap policy is evaluated against the runs that already exist —
        **before** this occurrence's row is created — so a policy refusal never
        leaves a stray ``pending`` row behind (a stray row would count as active
        forever and block every future occurrence under ``skip``).

        The create is **create-if-absent**: ``CONFIG_TASK_GRAPH_RUNS`` is a
        StarRocks Primary-Key table where a plain ``INSERT`` is a destructive
        upsert, so a re-tick (or a second leader during a lock-TTL window) must
        not reset a run that is already ``running``/``success``. Only a
        genuinely new row is published; an existing one means the occurrence is
        already owned by the stream or the reconciler.
        """
        # Idempotency short-circuit first: on a re-tick for an occurrence that
        # already exists, there is nothing to decide — the stream or the
        # reconciler already owns it. Checking this before the overlap policy
        # keeps a re-tick from being miscounted as an overlap skip.
        existing = await self._repository.get_graph_run(due.run_id)
        if existing is not None:
            return

        # `skip` refuses to create a run while one is active; `queue` creates it
        # (the worker defers it behind the active run); `allow` creates it and
        # lets it run concurrently. The decision reads the active runs once, so
        # a long list cannot change mid-decision.
        active = await self._repository.list_active_graph_runs(due.graph_id)
        if not should_enqueue(due.overlap_policy, len(active)):
            logger.info(
                "dropping due graph %s (root %s): %s overlap policy with %d active run(s)",
                due.graph_id,
                due.root_task or "-",
                due.overlap_policy,
                len(active),
            )
            plan.overlap_skipped += 1
            return

        created, is_new = await self._repository.create_graph_run_once(
            {
                "id": due.run_id,
                "graph_id": due.graph_id,
                "trigger_type": "schedule",
                "state": "pending",
                "overlap_policy": due.overlap_policy,
            }
        )
        if not is_new:
            # A concurrent tick created it between our read and our write. It
            # owns the publish; we must not double-publish.
            return
        await self._transport.publish_graph_run(created, due.task_ids)
