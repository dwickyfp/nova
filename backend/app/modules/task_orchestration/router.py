"""Read-only API for Nova task orchestration.

These endpoints expose the `CREATE TASK` surface — graphs, their edges, and their
runs — which the native `/tasks` API does not: `/tasks` reads StarRocks'
`information_schema.tasks` (native `SUBMIT TASK`), not Nova's `CONFIG_TASK*`
metadata. Without this, a task created by `CREATE TASK` is invisible to the UI.

**Read-only by construction.** Every route is `GET`, and nothing here calls a
repository write method. There is no mutation path through this router.

**Authorization.** The data lives in `NOVA_SYSTEM.CONFIG_TASK*` and is read
through the system pool, so StarRocks' own grant filter does **not** apply the
way it does on the caller-scoped `/tasks` path. Nova therefore enforces
ownership itself, in the backend, on every request:

* a caller sees a graph only if they own **every** task in it (`created_by`);
* a caller holding an admin role (`ACCOUNTADMIN` or a StarRocks security role)
  sees every graph, matching `/users`;
* an unknown **or** unauthorized graph id returns the same `404`, so the API does
  not reveal that someone else's graph exists.

**Why "every" and not "any".** A graph can contain nodes owned by different
users: `CREATE TASK x AFTER a` does not check who owns `a`, so a mixed-ownership
graph is reachable. Under an "any" rule, a user who owns one node would see the
definitions of the others — their `when_expr`, schedule and `created_by`. A graph
that contains a node owned by another user is therefore **fail-closed**: it is
invisible to every non-admin. That is the safe default; sharing a graph across
owners is a feature that would need its own permission model, not a looser
default.

This is deliberately not a second authorization system: it reuses the same
`created_by` the worker submits as, and the same admin-role list `/users` uses.

**Credential-invisible.** Responses carry ids, names, states, timings and
schedule metadata only. The task *body* is not exposed (it can name a `@stage`
whose credentials Nova injects at execution), and `error_message` is redacted
with the same helper the worker uses.
"""

from __future__ import annotations

from typing import Any, get_args

from fastapi import APIRouter, Depends, HTTPException

from app.common.sql_guard import redact_sql_credentials
from app.core.deps import get_current_user

from . import schemas
from .dag import finalizer_targets
from .repository import TaskOrchestrationRepository
from .repository import task_orchestration_repository as _repository

router = APIRouter()

#: Roles that may read every graph. Mirrors `modules/users/router.py:ADMIN_ROLES`
#: — `ACCOUNTADMIN` is the Nova super user; the rest are the StarRocks security
#: roles. Kept as a local constant rather than importing the users module, so a
#: change there is a deliberate decision here too.
ADMIN_ROLES = ("ACCOUNTADMIN", "user_admin", "security_admin")

# Module-level dependency so route signatures avoid a `Depends()` call in an
# argument default (ruff B008).
current_user = Depends(get_current_user)


def _is_admin(user: dict[str, Any]) -> bool:
    roles = user.get("roles") or []
    return any(role in roles for role in ADMIN_ROLES)


def _owned_by_caller(task: dict[str, Any], user: dict[str, Any]) -> bool:
    """Whether ``user`` owns ``task`` — the per-node predicate, not a graph rule.

    A graph is visible only when this is true for **every** node in it
    (``all(...)`` in :meth:`_GraphAccess.visible_graph_ids`); one unowned node
    makes the whole graph fail closed. See the module docstring for why.

    ``created_by`` is the Nova user who ran `CREATE TASK`, and is the same
    identity the worker submits the node as (delegate-first). An unset owner is
    not treated as world-readable: only an admin sees it.
    """
    if _is_admin(user):
        return True
    return str(task.get("created_by") or "") == str(user.get("username") or "")


class _GraphAccess:
    """Per-request view of which graphs the caller may read.

    Built once per request from the repository rather than per endpoint, so the
    list and the detail endpoints cannot disagree about ownership.
    """

    def __init__(self, repository: TaskOrchestrationRepository, user: dict[str, Any]):
        self._repository = repository
        self._user = user
        self._graph_ids: list[str] | None = None
        self._tasks_by_name: dict[str, dict[str, Any]] = {}
        self._edges_by_graph: dict[str, list[dict[str, Any]]] = {}
        self._visible: list[str] | None = None

    async def _load(self) -> None:
        """Read the whole read model once per request.

        All edges and all tasks in a bounded number of queries, not one query per
        graph. The orchestration tables are small (config-plane, not fact-plane),
        so one sweep is the right shape and it keeps the list and detail
        endpoints consistent.
        """
        if self._graph_ids is not None:
            return
        graph_ids = await self._repository.list_graph_ids()
        for task in await self._repository.list_tasks():
            self._tasks_by_name[str(task["name"])] = task
        for graph_id in graph_ids:
            self._edges_by_graph[graph_id] = await self._repository.list_edges(graph_id)
        self._graph_ids = graph_ids

    def _tasks_for(self, graph_id: str) -> list[dict[str, Any]]:
        edges = self._edges_by_graph.get(graph_id, [])
        if edges:
            names = {
                str(endpoint)
                for edge in edges
                for endpoint in (edge["parent_task"], edge["child_task"])
            }
            return [
                self._tasks_by_name[name]
                for name in names
                if name in self._tasks_by_name
            ]
        # Standalone graph: keyed by task id, not name.
        task = next(
            (row for row in self._tasks_by_name.values() if str(row["id"]) == graph_id),
            None,
        )
        return [task] if task else []

    async def visible_graph_ids(self) -> list[str]:
        await self._load()
        if self._visible is not None:
            return self._visible
        assert self._graph_ids is not None
        visible: list[str] = []
        for graph_id in self._graph_ids:
            tasks = self._tasks_for(graph_id)
            if tasks and all(_owned_by_caller(task, self._user) for task in tasks):
                visible.append(graph_id)
            elif not tasks and _is_admin(self._user):
                # A standalone task whose row is missing: only an admin sees it.
                visible.append(graph_id)
        self._visible = visible
        return visible

    def edges_for(self, graph_id: str) -> list[dict[str, Any]]:
        """Edges for a graph, from the per-request read model."""
        return self._edges_by_graph.get(graph_id, [])

    async def require(self, graph_id: str) -> list[dict[str, Any]]:
        """The graph's tasks, or 404 when absent **or** not the caller's.

        A single 404 for both cases is deliberate: distinguishing them would tell
        an unauthorized caller that a graph id exists.
        """
        if graph_id not in await self.visible_graph_ids():
            raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
        return self._tasks_for(graph_id)


@router.get("/graphs", response_model=schemas.GraphListResponse)
async def list_graphs(user: dict = current_user) -> schemas.GraphListResponse:
    """List the graphs the caller may see, with each graph's last run."""
    access = _GraphAccess(_repository, user)
    summaries: list[schemas.GraphSummary] = []
    for graph_id in await access.visible_graph_ids():
        graph_tasks = await access.require(graph_id)
        root = _root_task(access.edges_for(graph_id), graph_tasks)
        last_run = await _repository.get_latest_graph_run(graph_id)
        summaries.append(
            schemas.GraphSummary(
                graph_id=graph_id,
                root_task=root["name"] if root else None,
                node_count=len(graph_tasks),
                schedule_kind=(root or {}).get("schedule_kind"),
                schedule_expr=(root or {}).get("schedule_expr"),
                timezone=(root or {}).get("timezone"),
                overlap_policy=(root or {}).get("overlap_policy") or "skip",
                last_run=_run_summary(last_run),
            )
        )
    return schemas.GraphListResponse(graphs=summaries, count=len(summaries))


@router.get("/graphs/{graph_id}", response_model=schemas.GraphDetailResponse)
async def get_graph(
    graph_id: str, user: dict = current_user
) -> schemas.GraphDetailResponse:
    """A graph's definition: every node (including finalizers) and every edge."""
    access = _GraphAccess(_repository, user)
    graph_tasks = await access.require(graph_id)
    edges = access.edges_for(graph_id)
    finalizers = finalizer_targets(edges)
    last_states = await _last_node_states(graph_id)

    nodes = [
        schemas.GraphNode(
            name=str(task["name"]),
            task_id=str(task["id"]),
            schedule_kind=_narrow(
                task.get("schedule_kind"), get_args(schemas.ScheduleKind), "manual"
            ),
            schedule_expr=task.get("schedule_expr"),
            timezone=task.get("timezone"),
            overlap_policy=_narrow(
                task.get("overlap_policy"), _OVERLAP_POLICIES, "skip"
            ),
            when_expr=task.get("when_expr"),
            created_by=task.get("created_by"),
            is_finalizer=str(task["name"]) in finalizers,
            last_state=(
                _narrow(last_states.get(str(task["id"])), _TASK_RUN_STATES, "pending")
                if last_states.get(str(task["id"])) is not None
                else None
            ),
        )
        for task in graph_tasks
    ]
    # Deterministic ordering so the UI and tests see a stable list.
    nodes.sort(key=lambda node: node.name)
    return schemas.GraphDetailResponse(
        graph_id=graph_id,
        nodes=nodes,
        edges=[
            schemas.GraphEdge(
                parent_task=str(edge["parent_task"]),
                child_task=str(edge["child_task"]),
                edge_kind=_narrow(edge.get("edge_kind"), _EDGE_KINDS, "after"),
            )
            for edge in edges
        ],
        node_count=len(nodes),
    )


@router.get("/graphs/{graph_id}/runs", response_model=schemas.GraphRunListResponse)
async def list_graph_runs(
    graph_id: str, user: dict = current_user
) -> schemas.GraphRunListResponse:
    """Run history for a graph, newest first."""
    access = _GraphAccess(_repository, user)
    await access.require(graph_id)
    runs = await _repository.list_graph_runs(graph_id)
    return schemas.GraphRunListResponse(
        runs=[_run_response(run) for run in runs], count=len(runs)
    )


@router.get("/runs/{graph_run_id}", response_model=schemas.GraphRunDetailResponse)
async def get_graph_run(
    graph_run_id: str, user: dict = current_user
) -> schemas.GraphRunDetailResponse:
    """One graph run with its node runs.

    A run is reachable only through a graph the caller may see, so this endpoint
    cannot be used to enumerate someone else's runs by id.
    """
    run = await _repository.get_graph_run(graph_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{graph_run_id}' not found")
    access = _GraphAccess(_repository, user)
    await access.require(str(run["graph_id"]))

    node_runs = await _repository.list_node_runs(graph_run_id)
    return schemas.GraphRunDetailResponse(
        run=_run_response(run),
        node_runs=[_node_run_response(node) for node in node_runs],
    )


# ── Helpers ────────────────────────────────────────────────────────────────────


def _root_task(
    edges: list[dict[str, Any]], graph_tasks: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The graph's schedule anchor: a node with no incoming edge.

    Matches ``scheduler.plan_tick``, which anchors a run on roots only. Every
    edge kind counts here: a finalizer is still not a root, because the run it
    belongs to is anchored on its dependency graph. When several roots exist the
    first by name is reported, so the list is stable.
    """
    children = {str(edge["child_task"]) for edge in edges}
    roots = [task for task in graph_tasks if str(task["name"]) not in children]
    roots.sort(key=lambda task: str(task["name"]))
    return roots[0] if roots else None


async def _last_node_states(graph_id: str) -> dict[str, str]:
    """Latest state per task id across every run of ``graph_id``."""
    latest: dict[str, tuple[int, str]] = {}
    for run in await _repository.list_task_runs_for_graph(graph_id):
        task_id = run.get("task_id")
        if not task_id:
            continue
        attempt = int(run.get("attempt") or 1)
        state = str(run.get("state") or "")
        if task_id not in latest or attempt >= latest[task_id][0]:
            latest[str(task_id)] = (attempt, state)
    return {task_id: state for task_id, (_, state) in latest.items()}


def _narrow(value: Any, allowed: tuple[str, ...], fallback: str) -> Any:
    """Coerce a database string to one of a `Literal`'s members.

    The columns are constrained when written, but a row could predate a value
    or hold something hand-edited. Returning a known-good member keeps the
    response model valid instead of raising a validation error at serialisation
    time; an unexpected value falls back to the most conservative member rather
    than being reported as something it is not.
    """
    candidate = str(value or "")
    return candidate if candidate in allowed else fallback


_GRAPH_RUN_STATES = get_args(schemas.GraphRunState)
_TRIGGER_TYPES = get_args(schemas.TriggerType)
_OVERLAP_POLICIES = get_args(schemas.OverlapPolicy)
_TASK_RUN_STATES = get_args(schemas.TaskRunState)
_EDGE_KINDS = get_args(schemas.EdgeKind)


def _run_summary(run: dict[str, Any] | None) -> schemas.GraphRunSummary | None:
    if run is None:
        return None
    return schemas.GraphRunSummary(
        id=str(run["id"]),
        state=_narrow(run.get("state"), _GRAPH_RUN_STATES, "pending"),
        trigger_type=_narrow(run.get("trigger_type"), _TRIGGER_TYPES, "manual"),
        overlap_policy=_narrow(run.get("overlap_policy"), _OVERLAP_POLICIES, "skip"),
        started_at=run.get("started_at"),
        finished_at=run.get("finished_at"),
    )


def _run_response(run: dict[str, Any]) -> schemas.GraphRunResponse:
    return schemas.GraphRunResponse(
        id=str(run["id"]),
        graph_id=str(run["graph_id"]),
        trigger_type=_narrow(run.get("trigger_type"), _TRIGGER_TYPES, "manual"),
        state=_narrow(run.get("state"), _GRAPH_RUN_STATES, "pending"),
        overlap_policy=_narrow(run.get("overlap_policy"), _OVERLAP_POLICIES, "skip"),
        started_at=run.get("started_at"),
        heartbeat_at=run.get("heartbeat_at"),
        finished_at=run.get("finished_at"),
    )


def _node_run_response(node: dict[str, Any]) -> schemas.NodeRunResponse:
    error = node.get("error_message")
    return schemas.NodeRunResponse(
        id=str(node["id"]),
        task_id=str(node["task_id"]) if node.get("task_id") else None,
        attempt=int(node.get("attempt") or 1),
        state=_narrow(node.get("state"), _TASK_RUN_STATES, "pending"),
        delegated=bool(node.get("delegated", True)),
        starrocks_query_id=node.get("starrocks_query_id"),
        # Redacted with the worker's helper: an engine error can echo a rewritten
        # `@stage` statement, credentials included.
        error_message=redact_sql_credentials(str(error)) if error else None,
        started_at=node.get("started_at"),
        heartbeat_at=node.get("heartbeat_at"),
        finished_at=node.get("finished_at"),
    )
