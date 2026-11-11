"""Persist a lowered ``CREATE TASK`` as Nova metadata.

This is the I/O half of the lowering: :mod:`app.modules.task_orchestration.ddl`
parses and validates the surface, and this module writes the resulting task and
edges through :class:`~app.modules.task_orchestration.repository.TaskOrchestrationRepository`.

Two invariants from the design hold here:

* **``CREATE TASK`` never reaches the engine.** The raw statement is parsed and
  consumed; only ``CONFIG_TASK*`` rows are written. The engine statement for a
  node is produced later, at execution time, by
  :func:`app.modules.task_orchestration.execution.build_submit_task`.
* **No credential is stored or logged.** Only task names, the body text, and
  schedule/condition metadata are persisted; the body is opaque to this layer.

The graph is validated *after* merging the new edges with the stored ones, so a
cross-task cycle (``a AFTER b`` while ``b AFTER a``) is rejected here, not only a
self-cycle.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, replace
from typing import Any

from app.modules.task_orchestration.ddl import LoweredTask
from app.modules.task_orchestration.graph import Edge, Graph, GraphValidationError, validate_graph
from app.modules.task_orchestration.repository import task_orchestration_repository


@dataclass(frozen=True)
class PersistedTask:
    """Rows written for one lowered ``CREATE TASK``.

    Typed rather than a bare ``dict[str, object]`` so callers can read a field
    without an ``object`` index error: ``task`` is the ``CONFIG_TASKS`` row and
    ``edges`` are the ``CONFIG_TASK_EDGES`` rows.
    """

    task: dict[str, Any]
    edges: list[dict[str, Any]]


class TaskLoweringError(ValueError):
    """The statement lowers, but the resulting graph is invalid.

    Separate from :class:`~app.modules.task_orchestration.ddl.TaskDDLError`
    because this failure depends on state that already exists in
    ``CONFIG_TASK*``, not on the statement alone.
    """


async def persist_lowered_task(
    task: LoweredTask,
    *,
    created_by: str,
    owner_role: str | None = None,
    graph_id: str | None = None,
) -> PersistedTask:
    """Write ``task`` and its edges, then validate the merged graph.

    ``created_by`` is the Nova user who ran the statement; it becomes the task's
    ``created_by`` audit field. ``owner_role`` controls shared access and must
    match the connected graph. No password is accepted or stored.

    ``graph_id`` defaults to the predecessor's existing graph, or the first
    predecessor's qualified name when the graph has no edges yet. A standalone
    task uses its own qualified name. Sibling tasks therefore share one graph
    run instead of submitting their common root more than once. The new edges
    are persisted before the merged graph is validated.

    Raises :class:`TaskLoweringError` when a task with the same
    ``database.schema.name`` exists, or when the merged graph would contain a
    cycle, after removing the rows this call created so a rejected statement
    leaves no partial task behind.
    """
    if await task_orchestration_repository.find_task(
        task.name, task.database_name, task.schema_name
    ) is not None:
        # Two rows with one identity would make every later lookup, DROP TASK
        # and graph run pick one of them arbitrarily.
        raise TaskLoweringError(
            f"Task {task.qualified_name} already exists; drop it first with DROP TASK"
        )
    graph_key = graph_id or await _graph_key_for_task(task)
    if owner_role:
        await _validate_owner_role(task, graph_key, owner_role)
    when_ast = await _bind_streams(task, created_by, owner_role)
    task = await _route_stream_body(task)

    created_task = await task_orchestration_repository.create_task(
        {
            "name": task.name,
            "database_name": task.database_name,
            "schema_name": task.schema_name,
            "definition": task.body,
            "schedule_kind": task.schedule_kind,
            "schedule_expr": task.schedule_expr,
            "timezone": task.timezone,
            "when_expr": task.when_expr,
            "overlap_policy": task.overlap_policy,
            "owner_role": owner_role,
            "handler": task.handler,
            "handler_config": task.handler_config,
            # Snowflake: a new task does nothing on its own until RESUME.
            "lifecycle_state": "SUSPENDED",
            "when_ast": when_ast,
        },
        created_by,
    )

    if task.timezone is None:
        # ``timezone`` is NOT NULL in CONFIG_TASKS. The caller always supplies
        # the session zone; this guard turns a programming error into a clear
        # failure rather than a driver-level NOT NULL error.
        await task_orchestration_repository.delete_task(str(created_task["id"]))
        raise TaskLoweringError("CREATE TASK requires a timezone")

    created_edges: list[dict[str, Any]] = []
    try:
        for parent in task.after:
            created_edges.append(
                await task_orchestration_repository.create_edge(
                    graph_key,
                    {"parent_task": parent, "child_task": task.name, "edge_kind": "after"},
                )
            )
        if task.finalize:
            created_edges.append(
                await task_orchestration_repository.create_edge(
                    graph_key,
                    {
                        "parent_task": task.finalize,
                        "child_task": task.name,
                        "edge_kind": "finalize",
                    },
                )
            )

        await _validate_merged_graph(graph_key, task.name, (task.database_name, task.schema_name))
    except GraphValidationError as exc:
        await _rollback(created_task, created_edges)
        raise TaskLoweringError(str(exc)) from exc
    except Exception:
        await _rollback(created_task, created_edges)
        raise

    return PersistedTask(task=created_task, edges=created_edges)


async def _validate_owner_role(task: LoweredTask, graph_key: str, role: str) -> None:
    rows = [
        row
        for row in await task_orchestration_repository.list_tasks()
        if (row.get("database_name"), row.get("schema_name"))
        == (task.database_name, task.schema_name)
    ]
    by_name = {row["name"]: row for row in rows}
    parents = [*task.after, *([task.finalize] if task.finalize else [])]
    for name in parents:
        if name not in by_name or by_name[name].get("owner_role") != role:
            raise TaskLoweringError("Dependencies must exist and belong to the active owner role")
    graph_edges = await task_orchestration_repository.list_edges(graph_key)
    members = {
        endpoint for edge in graph_edges for endpoint in (edge["parent_task"], edge["child_task"])
    }
    if any(name not in by_name or by_name[name].get("owner_role") != role for name in members):
        raise TaskLoweringError("All graph nodes must have the same owner role")


async def _graph_key_for_task(task: LoweredTask) -> str:
    predecessors = [*task.after, *([task.finalize] if task.finalize else [])]
    if not predecessors:
        return task.qualified_name

    existing_graphs: set[str] = set()
    for edge in await task_orchestration_repository.list_all_edges():
        edge_graph_id = str(edge.get("graph_id") or "")
        if not _edge_in_scope(edge_graph_id, task.database_name, task.schema_name):
            continue
        if (
            str(edge.get("parent_task")) in predecessors
            or str(edge.get("child_task")) in predecessors
        ):
            existing_graphs.add(edge_graph_id)

    if len(existing_graphs) > 1:
        raise TaskLoweringError(
            "CREATE TASK joins predecessors from different stored graphs; "
            "reconcile their graph definitions before creating this task"
        )
    if existing_graphs:
        return next(iter(existing_graphs))

    parent = predecessors[0]
    parts = [part for part in (task.database_name, task.schema_name, parent) if part]
    return ".".join(parts)


async def _validate_merged_graph(
    graph_id: str, root: str, scope: tuple[str | None, str | None]
) -> None:
    """Validate the connected component that contains ``root``.

    Validating only ``root``'s own ``graph_id`` is not enough: edges are keyed by
    the graph that created them, so ``CREATE TASK b AFTER a`` stores ``a -> b``
    under ``b`` while a later ``CREATE TASK a AFTER b`` stores its edge under
    ``a``. Neither single key sees both edges, and a cycle would slip through.
    So all edges are read once and the component reachable from ``root`` (in
    either direction) is validated as a whole.

    Because a graph is single-schema and edge endpoints are bare names, the edge
    set is restricted to the task's own ``database.schema`` first — otherwise a
    same-named task in another schema would be pulled into this component and a
    cycle could be reported (or missed) across schemas.

    Finalizer edges are excluded from the dependency graph, exactly as
    ``dag.graph_from_task_rows`` does, so this matches what execution builds.
    """
    database_name, schema_name = scope
    all_edges = [
        Edge(parent=str(e["parent_task"]), child=str(e["child_task"]))
        for e in await task_orchestration_repository.list_all_edges()
        if str(e.get("edge_kind") or "after") == "after"
        and _edge_in_scope(str(e.get("graph_id") or ""), database_name, schema_name)
    ]

    component = _component(root, all_edges)
    component_edges = [
        edge for edge in all_edges if edge.parent in component and edge.child in component
    ]
    validate_graph(Graph.from_edges(sorted(component), component_edges))


def _edge_in_scope(edge_graph_id: str, database_name: str | None, schema_name: str | None) -> bool:
    """Whether an edge belongs to the same ``database.schema`` as the task.

    The edge's ``graph_id`` is the qualified root name, so the scope is its
    first two segments. A task with no ``database_name`` (legacy/unscoped) keeps
    the pre-scope behaviour of validating every edge by name — its schema
    default must not silently exclude the edges that would reveal a cycle.
    """
    if not database_name:
        return True
    parts = edge_graph_id.split(".")
    if len(parts) != 3:
        return False
    return parts[0] == database_name and parts[1] == schema_name


def _component(root: str, edges: list[Edge]) -> set[str]:
    """Every node connected to ``root``, following edges in both directions."""
    neighbours: dict[str, set[str]] = {}
    for edge in edges:
        neighbours.setdefault(edge.parent, set()).add(edge.child)
        neighbours.setdefault(edge.child, set()).add(edge.parent)

    seen = {root}
    stack = [root]
    while stack:
        node = stack.pop()
        for neighbour in neighbours.get(node, ()):
            if neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)
    return seen


async def _rollback(task_row: dict[str, Any], edge_rows: list[dict[str, Any]]) -> None:
    """Best-effort removal of a task and its edges after a validation failure.

    A rejected statement must not leave a half-created task that subsequent
    statements would then see as a graph node. Failures during rollback are
    swallowed: the original error is the one worth reporting.
    """
    for edge in edge_rows:
        with contextlib.suppress(Exception):
            await task_orchestration_repository.delete_edge(str(edge["id"]))
    with contextlib.suppress(Exception):
        await task_orchestration_repository.delete_task(str(task_row["id"]))


async def drop_nova_task(
    name: str, database_name: str | None, schema_name: str, user: dict[str, Any]
) -> dict[str, Any] | None:
    """Remove a Nova task and the edges that make it a child; ``None`` if absent.

    The caller's active role must own the task (the rule every task API uses).
    A task other tasks run after, or finalize, is refused with their names:
    dropping it would leave them waiting on a task that no longer exists. A
    task whose graph has a pending or running run is refused until the run
    settles. Run history stays, as it does for the engine's own tasks.
    """
    from app.modules.task_orchestration.access import owns_task
    from app.modules.task_orchestration.repository import task_graph_id

    task = await task_orchestration_repository.find_task(name, database_name, schema_name)
    if task is None:
        return None
    if not owns_task(task, user):
        raise TaskLoweringError(
            f"Task {task_graph_id(task)} is owned by role {task.get('owner_role')!r}; "
            "activate that role to drop it"
        )
    edges = [
        edge
        for edge in await task_orchestration_repository.list_all_edges()
        if _edge_in_scope(str(edge.get("graph_id") or ""), database_name, schema_name)
    ]
    dependents = sorted(
        {str(edge["child_task"]) for edge in edges if str(edge["parent_task"]) == name}
    )
    if dependents:
        raise TaskLoweringError(
            f"Tasks {', '.join(dependents)} run after or finalize {name}; drop them first"
        )
    own_edges = [edge for edge in edges if str(edge["child_task"]) == name]
    graph_ids = {str(edge["graph_id"]) for edge in own_edges} or {task_graph_id(task)}
    for graph_id in sorted(graph_ids):
        if await task_orchestration_repository.list_active_graph_runs(graph_id):
            raise TaskLoweringError(
                f"Task {task_graph_id(task)} has a run in progress; drop it after the run ends"
            )
    for edge in own_edges:
        await task_orchestration_repository.delete_edge(str(edge["id"]))
    await task_orchestration_repository.delete_task(str(task["id"]))
    return task


async def _bind_streams(task: LoweredTask, created_by: str, owner_role: str | None) -> str | None:
    """Resolve the ``WHEN``'s streams to their current ids, as the creating role.

    The task is bound to these stream objects: a stream dropped and created
    again under the same name is a different stream, and the task refuses to
    run against it until it is recreated.
    """
    if task.when_tree is None:
        return None
    from app.modules.streams.schemas import StreamError
    from app.modules.streams.service import StreamCaller, stream_service
    from app.modules.task_orchestration.stream_when import dump, stream_name

    caller = StreamCaller(created_by, owner_role)
    bound: dict[str, str] = {}
    for text in task.stream_names:
        try:
            record = await stream_service.current(stream_name(text))
            await stream_service.authorize_read(
                caller, record.source.database, record.source.name
            )
        except StreamError as exc:
            raise TaskLoweringError(f"WHEN reads stream {text}: {exc}") from None
        bound[text] = record.stream_id
    return dump(task.when_tree, bound)


async def _route_stream_body(task: LoweredTask) -> LoweredTask:
    """A single statement that reads a stream runs through Nova, not SUBMIT TASK.

    The engine does not know Nova streams, and only Nova can advance a
    stream's offset after the insert is verified.
    """
    if task.handler != "sql":
        return task
    from app.modules.streams.sql import stream_sql
    from app.sql_frontend.parser import parse_statement

    try:
        refs = await stream_sql._stream_refs(parse_statement(task.body), task.database_name)
    except ValueError:
        return task
    if not refs:
        return task
    return replace(task, handler="sql_script", statements=(task.body,))


async def set_task_lifecycle(
    name: str, database_name: str | None, schema_name: str, action: str, user: dict[str, Any]
) -> dict[str, Any] | None:
    """``ALTER TASK … RESUME | SUSPEND`` on a Nova task; ``None`` if absent.

    The root's state schedules its whole graph, so only a root (or standalone)
    task is resumed or suspended, as in Snowflake where the root controls the
    graph's schedule. Suspending stops new runs; a run in progress finishes,
    and a stream's backlog stays for the next RESUME.
    """
    from app.modules.task_orchestration.access import owns_task
    from app.modules.task_orchestration.repository import task_graph_id

    task = await task_orchestration_repository.find_task(name, database_name, schema_name)
    if task is None:
        return None
    if action not in {"resume", "suspend"}:
        raise TaskLoweringError("ALTER TASK on a Nova task supports RESUME and SUSPEND")
    if not owns_task(task, user):
        raise TaskLoweringError(
            f"Task {task_graph_id(task)} is owned by role {task.get('owner_role')!r}; "
            "activate that role to change it"
        )
    parents = [
        str(edge["parent_task"])
        for edge in await task_orchestration_repository.list_all_edges()
        if _edge_in_scope(str(edge.get("graph_id") or ""), database_name, schema_name)
        and str(edge["child_task"]) == name
    ]
    if parents:
        raise TaskLoweringError(
            f"Task {name} runs after {', '.join(sorted(parents))}; resume or suspend the "
            "graph's root task instead"
        )
    state = "STARTED" if action == "resume" else "SUSPENDED"
    await task_orchestration_repository.set_lifecycle(str(task["id"]), state)
    return {**task, "lifecycle_state": state}
