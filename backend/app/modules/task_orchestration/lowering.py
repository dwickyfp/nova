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
from dataclasses import dataclass
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
    ``created_by`` and therefore the identity the worker submits ``SUBMIT TASK``
    as (delegate-first, design D9.4). No password is accepted or stored.

    ``graph_id`` defaults to the predecessor's existing graph, or the first
    predecessor's qualified name when the graph has no edges yet. A standalone
    task uses its own qualified name. Sibling tasks therefore share one graph
    run instead of submitting their common root more than once. The new edges
    are persisted before the merged graph is validated.

    Raises :class:`TaskLoweringError` when the merged graph would contain a
    cycle, after removing the rows this call created so a rejected statement
    leaves no partial task behind.
    """
    graph_key = graph_id or await _graph_key_for_task(task)

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
