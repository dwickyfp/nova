"""Role-scoped task metadata and manual run API.

A caller can read a graph when every node belongs to their active role.
Active task administrators can inspect every graph, but running still requires
activating its common owner role. Role membership is checked on each request.
Legacy definitions without a role remain private to their creator and cannot
run until an administrator assigns ownership.

Manual runs persist the caller, role, and exact session reference before queue
publication. The worker authenticates from that session; scheduled runs use a
separate role-bound service identity. Creators are retained as audit metadata.
Unknown and unauthorized graphs both return 404. SQL and errors are redacted.
"""

from __future__ import annotations

import logging
from typing import Any, get_args

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query

from app.common.audit import write_audit_log
from app.common.sql_guard import redact_sql_credentials
from app.core.config import settings
from app.core.deps import get_current_user

from . import schemas
from .access import graph_role, is_task_admin, owns_task, verify_active_role
from .dag import finalizer_targets
from .repository import TaskOrchestrationRepository
from .repository import task_orchestration_repository as _repository
from .scheduler import should_enqueue
from .transport import RedisGraphRunTransport

router = APIRouter()
logger = logging.getLogger(__name__)

#: Roles that may read every graph. Mirrors `modules/users/router.py:ADMIN_ROLES`
#: — `ACCOUNTADMIN` is the Nova super user; the rest are the StarRocks security
#: roles. Kept as a local constant rather than importing the users module, so a
#: change there is a deliberate decision here too.
ADMIN_ROLES = ("ACCOUNTADMIN", "user_admin", "security_admin")

# Module-level dependency so route signatures avoid a `Depends()` call in an
# argument default (ruff B008).
current_user = Depends(get_current_user)


def _is_admin(user: dict[str, Any]) -> bool:
    return is_task_admin(user)


def _qualified_name(task: dict[str, Any]) -> str:
    """``database.schema.name`` for a task row, or its bare name if unscoped."""
    parts = [
        str(part)
        for part in (task.get("database_name"), task.get("schema_name"), task.get("name"))
        if part
    ]
    return ".".join(parts)


def _scope_of_graph(graph_id: str) -> tuple[str, ...]:
    """The ``(database, schema)`` scope encoded in a qualified graph id."""
    parts = graph_id.split(".")
    return tuple(parts[:2]) if len(parts) == 3 and all(parts) else ()


def _owned_by_caller(task: dict[str, Any], user: dict[str, Any]) -> bool:
    """Whether ``user`` owns ``task`` — the per-node predicate, not a graph rule.

    A graph is visible only when this is true for **every** node in it
    (``all(...)`` in :meth:`_GraphAccess.visible_graph_ids`); one unowned node
    makes the whole graph fail closed. See the module docstring for why.

    ``owner_role`` is compared with the active role. Legacy tasks without a
    role remain private to the creator; active administrators can inspect them.
    """
    if _is_admin(user):
        return True
    return owns_task(task, user)


class _GraphAccess:
    """Per-request view of which graphs the caller may read.

    Built once per request from the repository rather than per endpoint, so the
    list and the detail endpoints cannot disagree about ownership.
    """

    def __init__(self, repository: TaskOrchestrationRepository, user: dict[str, Any]):
        self._repository = repository
        self._user = user
        self._graph_ids: list[str] | None = None
        # Keyed by (database, schema, name): a task name alone is ambiguous
        # because two schemas may each hold a task of the same name.
        self._tasks_by_qualified: dict[str, dict[str, Any]] = {}
        self._tasks_by_id: dict[str, dict[str, Any]] = {}
        # Bare-name fallback; see `_task_by_name` for when it is the only match.
        self._tasks_by_name_only: dict[str, dict[str, Any]] = {}
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
        await verify_active_role(self._user)
        graph_ids = await self._repository.list_graph_ids()
        for task in await self._repository.list_tasks():
            self._tasks_by_qualified[_qualified_name(task)] = task
            self._tasks_by_id[str(task["id"])] = task
            # Last write wins on a duplicate bare name; the scoped lookup is
            # consulted first, so this fallback only ever serves a row the
            # qualified map could not resolve.
            self._tasks_by_name_only[str(task["name"])] = task
        for graph_id in graph_ids:
            self._edges_by_graph[graph_id] = await self._repository.list_edges(graph_id)
        self._graph_ids = graph_ids

    def _tasks_for(self, graph_id: str) -> list[dict[str, Any]]:
        edges = self._edges_by_graph.get(graph_id, [])
        if edges:
            scope = _scope_of_graph(graph_id)
            names = {
                str(endpoint)
                for edge in edges
                for endpoint in (edge["parent_task"], edge["child_task"])
            }
            found: list[dict[str, Any]] = []
            for name in sorted(names):
                task = self._task_by_name(name, scope)
                if task is not None:
                    found.append(task)
            return found
        # Standalone graph: its id is the task's qualified name (or, for a
        # legacy row, the task id).
        task = (
            self._tasks_by_qualified.get(graph_id)
            or self._tasks_by_id.get(graph_id)
            or self._task_by_name(graph_id.rpartition(".")[2], _scope_of_graph(graph_id))
        )
        return [task] if task else []

    def _task_by_name(self, name: str, scope: tuple[str, ...]) -> dict[str, Any] | None:
        """Resolve an edge endpoint to its task row.

        An edge stores a **bare** task name, but the task row is scoped. The
        qualified lookup is the precise one; the bare-name fallback covers a row
        whose scope is incomplete (``schema_name`` NULL) while the edge's graph
        id still carries both parts — a state the data holds today (legacy and
        test rows), where the strict lookup would silently render zero nodes.
        """
        if scope:
            scoped = self._tasks_by_qualified.get(".".join((*scope, name)))
            if scoped is not None:
                return scoped
        return self._tasks_by_name_only.get(name)

    async def visible_graph_ids(self) -> list[str]:
        await self._load()
        if self._visible is not None:
            return self._visible
        assert self._graph_ids is not None
        visible: list[str] = []
        for graph_id in self._graph_ids:
            tasks = self._tasks_for(graph_id)
            # A graph with no resolvable task is not a graph the UI can render:
            # it is an orphan (its task rows were dropped while edges remained),
            # and showing it as an empty flow is worse than hiding it. This holds
            # for admins too — "admin sees everything" means every graph that
            # exists, not every dangling edge.
            if not tasks:
                continue
            if all(_owned_by_caller(task, self._user) for task in tasks):
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


@router.get("/tasks", response_model=schemas.TaskListResponse)
async def list_tasks(
    database: str | None = None,
    schema: str | None = None,
    user: dict = current_user,
) -> schemas.TaskListResponse:
    """List Nova tasks, optionally scoped to one ``database.schema``.

    Tasks are owned by a ``database.schema`` like a stage. Without the query
    parameters the caller sees every task they own; with both, only that
    schema's tasks. Ownership is the same rule the graph endpoints use: a caller
    sees a task they created, and an admin sees everything. The task **body** is
    never returned — it can name a stage whose credentials Nova injects at
    execution time.
    """
    access = _GraphAccess(_repository, user)
    visible = set(await access.visible_graph_ids())
    tallies = await _repository.count_graph_runs_by_graph()
    tasks: list[schemas.TaskSummary] = []
    for task in await _repository.list_tasks():
        if database is not None and str(task.get("database_name") or "") != database:
            continue
        if schema is not None and str(task.get("schema_name") or "") != schema:
            continue
        graph_id = _task_graph_id(task)
        # A task is visible when its graph is visible (same ownership rule the
        # graph endpoints enforce), so the list cannot leak a task the graph
        # view would hide.
        if graph_id not in visible and not _owned_by_caller(task, user):
            continue
        tasks.append(
            schemas.TaskSummary(
                id=str(task["id"]),
                name=str(task["name"]),
                database_name=task.get("database_name"),
                schema_name=task.get("schema_name"),
                schedule_kind=_narrow(
                    task.get("schedule_kind"), get_args(schemas.ScheduleKind), "manual"
                ),
                schedule_expr=task.get("schedule_expr"),
                timezone=task.get("timezone"),
                overlap_policy=_narrow(task.get("overlap_policy"), _OVERLAP_POLICIES, "skip"),
                created_by=task.get("created_by"),
                graph_id=graph_id,
                created_at=task.get("created_at"),
                run_counts=_run_counts(tallies.get(graph_id)),
            )
        )
    tasks.sort(key=lambda task: (task.database_name or "", task.schema_name or "", task.name))
    return schemas.TaskListResponse(tasks=tasks, count=len(tasks))


def _task_graph_id(task: dict[str, Any]) -> str:
    """The graph id a task belongs to: its qualified name (or bare name)."""
    parts = [
        str(part)
        for part in (task.get("database_name"), task.get("schema_name"), task.get("name"))
        if part
    ]
    return ".".join(parts)


@router.get("/graphs", response_model=schemas.GraphListResponse)
async def list_graphs(user: dict = current_user) -> schemas.GraphListResponse:
    """List the graphs the caller may see, with each graph's last run and tallies."""
    access = _GraphAccess(_repository, user)
    tallies = await _repository.count_graph_runs_by_graph()
    summaries: list[schemas.GraphSummary] = []
    for graph_id in await access.visible_graph_ids():
        graph_tasks = await access.require(graph_id)
        root = _root_task(access.edges_for(graph_id), graph_tasks)
        last_run = await _repository.get_latest_graph_run(graph_id)
        scope_task = root or (graph_tasks[0] if graph_tasks else None)
        summaries.append(
            schemas.GraphSummary(
                graph_id=graph_id,
                root_task=root["name"] if root else None,
                owner_role=graph_role(graph_tasks),
                can_run=bool(graph_role(graph_tasks))
                and graph_role(graph_tasks) == user.get("active_role"),
                database_name=(scope_task or {}).get("database_name"),
                schema_name=(scope_task or {}).get("schema_name"),
                node_count=len(graph_tasks),
                schedule_kind=(root or {}).get("schedule_kind"),
                schedule_expr=(root or {}).get("schedule_expr"),
                timezone=(root or {}).get("timezone"),
                overlap_policy=(root or {}).get("overlap_policy") or "skip",
                last_run=_run_summary(last_run),
                run_counts=_run_counts(tallies.get(graph_id)),
                # The anchor node's creation time represents the graph's; a
                # graph has no row of its own.
                created_at=(root or scope_task or {}).get("created_at"),
            )
        )
    return schemas.GraphListResponse(graphs=summaries, count=len(summaries))


@router.get("/graphs/{graph_id}", response_model=schemas.GraphDetailResponse)
async def get_graph(graph_id: str, user: dict = current_user) -> schemas.GraphDetailResponse:
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
            database_name=task.get("database_name"),
            schema_name=task.get("schema_name"),
            schedule_kind=_narrow(
                task.get("schedule_kind"), get_args(schemas.ScheduleKind), "manual"
            ),
            schedule_expr=task.get("schedule_expr"),
            timezone=task.get("timezone"),
            overlap_policy=_narrow(task.get("overlap_policy"), _OVERLAP_POLICIES, "skip"),
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


@router.get("/graphs/{graph_id}/nodes/{task_id}/sql", response_model=schemas.TaskSQLResponse)
async def get_task_sql(
    graph_id: str, task_id: str, user: dict = current_user
) -> schemas.TaskSQLResponse:
    tasks = await _GraphAccess(_repository, user).require(graph_id)
    task = next((task for task in tasks if str(task["id"]) == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    body = task.get("definition")
    return schemas.TaskSQLResponse(
        task_id=task_id,
        name=str(task["name"]),
        sql=redact_sql_credentials(str(body)) if body else None,
    )


async def _publish_manual_run(run: dict[str, Any], task_ids: list[str]) -> None:
    async with aioredis.from_url(settings.REDIS_URL, decode_responses=True) as client:
        await RedisGraphRunTransport(client).publish_graph_run(run, task_ids)


@router.post("/graphs/{graph_id}/runs", response_model=schemas.GraphRunResponse, status_code=202)
async def run_graph(graph_id: str, user: dict = current_user) -> schemas.GraphRunResponse:
    access = _GraphAccess(_repository, user)
    tasks = await access.require(graph_id)
    root = _root_task(access.edges_for(graph_id), tasks)
    execution_role = user.get("active_role")
    if not execution_role or execution_role not in user.get(
        "assigned_roles", user.get("roles", [])
    ):
        raise HTTPException(status_code=403, detail="Select an assigned role before running a task")
    if root is None:
        raise HTTPException(status_code=409, detail="Task graph has no root to run")
    owner_role = graph_role(tasks)
    if owner_role is None:
        raise HTTPException(
            409, "Every node must have the same owner role before this task can run"
        )
    if execution_role != owner_role:
        raise HTTPException(403, f"Activate the task owner role {owner_role!r} before running")
    policy = _narrow(root.get("overlap_policy"), _OVERLAP_POLICIES, "skip")
    active = await _repository.list_active_graph_runs(graph_id)
    if not should_enqueue(policy, len(active)):
        raise HTTPException(status_code=409, detail="This task already has an active run")

    audit = {
        "event_type": "task",
        "user_name": user["username"],
        "action": "RUN_TASK",
        "object_type": "TASK_GRAPH",
        "object_name": graph_id,
        "database_name": root.get("database_name"),
        "schema_name": root.get("schema_name"),
        "session_id": user.get("session_id"),
        "active_role": user.get("active_role"),
    }
    await write_audit_log(**audit, status="ATTEMPTED")
    try:
        run = await _repository.create_graph_run(
            {
                "graph_id": graph_id,
                "trigger_type": "manual",
                "state": "pending",
                "overlap_policy": policy,
                "execution_user": user["username"],
                "execution_role": execution_role,
                "execution_session_id": user["session_id"],
            }
        )
    except Exception:
        logger.warning("Could not persist manual task run")
        raise HTTPException(
            status_code=503, detail="Could not queue task run. Retry later."
        ) from None
    try:
        await _publish_manual_run(run, [str(task["id"]) for task in tasks])
    except Exception:
        # The reconciler delivers persisted pending runs if Redis is unavailable.
        logger.warning("Manual task run persisted; awaiting transport recovery")
    try:
        await write_audit_log(**audit, status="SUCCESS", query_id=str(run["id"]))
    except Exception:
        logger.warning("Manual task run audit outcome unavailable; attempt recorded")
    return _run_response(run)


@router.get("/graphs/{graph_id}/runs", response_model=schemas.GraphRunListResponse)
async def list_graph_runs(
    graph_id: str,
    limit: int = Query(10, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = current_user,
) -> schemas.GraphRunListResponse:
    """One page of a graph's run history, newest first.

    Pagination is in the engine, not the client: a graph with thousands of runs
    must not serialise all of them to render ten. The response carries the
    unpaginated ``count`` so the UI can build its page controls without a second
    call.
    """
    access = _GraphAccess(_repository, user)
    await access.require(graph_id)
    total = await _repository.count_graph_runs(graph_id)
    runs = await _repository.list_graph_runs_page(graph_id, limit=limit, offset=offset)
    return schemas.GraphRunListResponse(runs=[_run_response(run) for run in runs], count=total)


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


def _run_counts(tally: dict[str, int] | None) -> schemas.RunCounts:
    """Fold a repository tally (or its absence) into the response model.

    A graph with no runs has no row in the tally map, so ``None`` is a valid
    input and means ``{0, 0, 0}`` rather than an error.
    """
    return schemas.RunCounts(
        total=int((tally or {}).get("total") or 0),
        success=int((tally or {}).get("success") or 0),
        failed=int((tally or {}).get("failed") or 0),
    )


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
        execution_user=run.get("execution_user"),
        execution_role=run.get("execution_role"),
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
