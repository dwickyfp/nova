"""Task-orchestration metadata, read and written in PostgreSQL.

Every call runs in an actor-bound metadata transaction: the caller's own
context for requests, the captured graph owner for scheduled reservations, or
an explicit SERVICE actor for orchestration work. :meth:`unit` groups several
calls in one transaction; otherwise each call is its own short transaction.
There is no StarRocks metadata path and no fallback.

Runs are fenced: a claim returns a lease generation and every later heartbeat
or transition of that run or node names it, so a worker that lost its lease
cannot overwrite the current holder's progress. A graph run is created
together with the durable ``task.graph`` job (and its outbox wakeup) that
delivers it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.common.durable_jobs import JobRepository
from app.common.metadata_actors import Connection, metadata_scope, service_actor
from app.core.database import db
from app.core.metadata_db import MetadataConflict
from app.modules.access_control.security_context import SecurityContext
from app.modules.task_orchestration.graph_jobs import graph_job_definition
from app.modules.task_orchestration.metadata_repository import TaskMetadataRepository
from app.modules.task_orchestration.metadata_scheduler import (
    ScheduledOccurrence,
    SchedulerLease,
    SchedulerRepository,
)

#: Columns a definition change may name (handler and owner identity are fixed).
_TASK_CHANGES = frozenset(
    {
        "name", "database_name", "schema_name", "definition", "schedule_kind",
        "schedule_expr", "timezone", "when_expr", "overlap_policy", "owner_role",
        "handler_config", "when_ast",
    }
)


class UnknownUpdateColumnError(ValueError):
    """Raised when an update payload names a column outside the whitelist."""


def _node_run_id(graph_run_id: str, task_id: str, attempt: int) -> str:
    """The deterministic primary key of one node attempt.

    Reproducible across processes and restarts, and within the 64-char column,
    so two concurrent creators collide on the same row instead of each inserting
    a duplicate (the double-execution defect).
    """
    key = f"nova:task_run:{graph_run_id}:{task_id}:{attempt}"
    return str(uuid5(NAMESPACE_URL, key))


def scope_from_graph_id(graph_id: str) -> tuple[str, str] | None:
    """Split a qualified graph id into ``(database_name, schema_name)``.

    A graph id is the root task's qualified name, ``database.schema.name`` (or a
    legacy bare task id / bare name). Returns ``None`` when the id is not
    three-part qualified, so callers can fall back to name-only resolution for
    rows written before tasks were schema-scoped.
    """
    parts = graph_id.split(".")
    if len(parts) == 3 and all(parts):
        return parts[0], parts[1]
    return None


def task_graph_id(task: dict[str, Any]) -> str:
    """The graph id of a task row: its qualified ``database.schema.name``.

    An unscoped legacy row falls back to its bare name, matching the pre-scope
    convention where a task's own name identified its graph.
    """
    parts = [
        str(part)
        for part in (task.get("database_name"), task.get("schema_name"), task.get("name"))
        if part
    ]
    return ".".join(parts)


def _task_scope_key(task: dict[str, Any]) -> tuple[str, str] | None:
    database = task.get("database_name")
    schema = task.get("schema_name")
    if database and schema:
        return str(database), str(schema)
    return None



def graph_ids_from(edges: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> list[str]:
    """Graph ids from already-loaded edges and tasks (see ``list_graph_ids``).

    Pure, so a caller that already holds every edge and task (the graph list
    read model) derives the ids without reading both tables again.
    """
    graph_ids = sorted({str(edge["graph_id"]) for edge in edges})

    # Endpoint names by scope, so a task is only "referenced" by an edge in
    # its own scope (a same-named task in another schema is its own graph).
    referenced: dict[tuple[str, ...], set[str]] = {}
    for edge in edges:
        scope = scope_from_graph_id(str(edge["graph_id"]))
        referenced.setdefault(scope or (), set()).update(
            (str(edge["parent_task"]), str(edge["child_task"]))
        )

    standalone: list[str] = []
    for task in tasks:
        scope = _task_scope_key(task)
        name = str(task["name"])
        if name in referenced.get(scope or (), set()):
            continue
        if scope and name in referenced.get((), set()):
            continue
        qualified = task_graph_id(task)
        if qualified not in graph_ids:
            standalone.append(qualified)
    return [*graph_ids, *sorted(set(standalone) - set(graph_ids))]


class TaskOrchestrationRepository:
    """Task definitions, edges, runs and role bindings in ``nova_meta``."""

    def __init__(
        self, actor: SecurityContext | None = None, *, connection: Connection | None = None,
    ) -> None:
        self.actor = actor or service_actor("task_orchestration")
        self._connection = connection

    def for_actor(self, actor: SecurityContext) -> TaskOrchestrationRepository:
        """The same repository acting for ``actor`` (its own transactions)."""
        return TaskOrchestrationRepository(actor)

    @asynccontextmanager
    async def unit(self) -> AsyncIterator[TaskOrchestrationRepository]:
        """Several calls in one transaction that commits or rolls back together."""
        if self._connection is not None:
            yield self
            return
        async with metadata_scope(self.actor) as connection:
            yield TaskOrchestrationRepository(self.actor, connection=connection)

    @asynccontextmanager
    async def _store(self, *, read_only: bool = False) -> AsyncIterator[TaskMetadataRepository]:
        async with metadata_scope(
            self.actor, connection=self._connection, read_only=read_only,
        ) as connection:
            yield TaskMetadataRepository(connection, self.actor)

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _decode_wal_marks(raw: Any) -> dict[str, int] | None:
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _run(cls, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        run = dict(row)
        run["wal_marks"] = cls._decode_wal_marks(run.get("wal_marks"))
        return run

    @classmethod
    def _runs(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [cls._run(row) or {} for row in rows]

    @staticmethod
    def _task(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        task = dict(row)
        config = task.get("handler_config")
        if config is not None and not isinstance(config, str):
            # Callers parse the stored JSON text, as they did before the port.
            task["handler_config"] = json.dumps(config)
        return task

    async def get_engine_timezone(self) -> str | None:
        """The StarRocks session timezone, e.g. ``Asia/Jakarta``.

        Native task traces report engine-local naive times; reconciliation
        reads this to compare them with PG's timezone-aware run stamps.
        """
        result = await db.execute_system("SELECT @@time_zone AS time_zone")
        if not result["rows"]:
            return None
        value = result["rows"][0][0]
        return str(value) if value else None

    # ── Role bindings ──────────────────────────────────────────

    async def get_role_execution_user(self, role: str) -> str | None:
        async with self._store(read_only=True) as store:
            return await store.get_role_execution_user(role)

    async def bind_role_execution_user(
        self, role: str, user: str, configured_by: str | None = None,
    ) -> None:
        """``configured_by`` is the transaction's actor; the argument is ignored."""
        async with self._store() as store:
            await store.bind_role_execution_user(role, user)

    # ── Tasks ──────────────────────────────────────────────────

    async def create_task(self, data: dict[str, Any], created_by: str | None) -> dict[str, Any]:
        payload = dict(data)
        if created_by:
            payload["created_by"] = created_by
        if isinstance(payload.get("handler_config"), str):
            payload["handler_config"] = json.loads(payload["handler_config"])
        async with self._store() as store:
            return self._task(await store.create_task(payload)) or {}

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self._store(read_only=True) as store:
            return self._task(await store.get_task(task_id))

    async def find_task(
        self, name: str, database_name: str | None, schema_name: str | None
    ) -> dict[str, Any] | None:
        """The task with this exact ``(database, schema, name)``; 409 when ambiguous.

        ``NULL`` scopes compare as values. Imported duplicates are never
        resolved by a silent pick.
        """
        async with self._store(read_only=True) as store:
            rows = await store.find_task(name, database_name, schema_name)
        if len(rows) > 1:
            raise MetadataConflict()
        return self._task(rows[0]) if rows else None

    async def list_tasks(self, graph_id: str | None = None) -> list[dict[str, Any]]:
        async with self._store(read_only=True) as store:
            return [self._task(row) or {} for row in await store.list_tasks(graph_id)]

    async def update_task(self, task_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Apply a definition change at the version read in the same transaction."""
        unknown = sorted(set(data) - _TASK_CHANGES - {"lifecycle_state"})
        if unknown or not data:
            raise UnknownUpdateColumnError(f"cannot update task column(s): {', '.join(unknown)}")
        changes = dict(data)
        lifecycle = changes.pop("lifecycle_state", None)
        if isinstance(changes.get("handler_config"), str):
            changes["handler_config"] = json.loads(changes["handler_config"])
        async with self._store() as store:
            current = await store.get_task(task_id)
            if current is None:
                return None
            row = current
            if changes:
                row = await store.update_task(
                    task_id, changes, expected_version=int(current.get("version") or 1),
                )
            if lifecycle is not None:
                row = await store.set_lifecycle(task_id, str(lifecycle)) or row
            return self._task(row)

    async def set_lifecycle(self, task_id: str, state: str) -> None:
        """``STARTED`` or ``SUSPENDED``: whether the scheduler may start the task."""
        if state not in {"STARTED", "SUSPENDED"}:
            raise ValueError("Unknown task lifecycle state")
        async with self._store() as store:
            await store.set_lifecycle(task_id, state)

    async def delete_task(self, task_id: str) -> bool:
        async with self._store() as store:
            return await store.delete_task(task_id)

    async def increment_consecutive_failures(self, task_id: str) -> int:
        """Atomic counter bookkeeping; it is not a definition change."""
        async with self._store() as store:
            return await store.increment_consecutive_failures(task_id)

    async def reset_consecutive_failures(self, task_id: str) -> None:
        async with self._store() as store:
            await store.reset_consecutive_failures(task_id)

    # ── Edges ──────────────────────────────────────────────────

    async def create_edge(self, graph_id: str, data: dict[str, Any]) -> dict[str, Any]:
        async with self._store() as store:
            return await store.create_edge(graph_id, data)

    async def list_edges(self, graph_id: str) -> list[dict[str, Any]]:
        async with self._store(read_only=True) as store:
            return await store.list_edges(graph_id)

    async def list_all_edges(self) -> list[dict[str, Any]]:
        """Every edge, in one query — the scheduler plans all graphs per tick."""
        async with self._store(read_only=True) as store:
            return await store.list_all_edges()

    async def delete_edge(self, edge_id: str) -> bool:
        async with self._store() as store:
            return await store.delete_edge(edge_id)

    # ── Read model for the orchestration API ───────────────────

    async def get_latest_graph_run(self, graph_id: str) -> dict[str, Any] | None:
        async with self._store(read_only=True) as store:
            return self._run(await store.latest_graph_run(graph_id))

    async def latest_graph_runs(self) -> dict[str, dict[str, Any]]:
        """Each graph's most recent run, keyed by graph id, in one query."""
        async with self._store(read_only=True) as store:
            rows = await store.latest_graph_runs()
        return {str(row["graph_id"]): row for row in self._runs(rows)}

    async def latest_stream_runs(self, graph_id: str, limit: int = 20) -> list[dict[str, Any]]:
        async with self._store(read_only=True) as store:
            rows = await store.list_graph_runs(graph_id, trigger_type="stream", limit=limit)
        return self._runs(rows)

    async def list_task_runs_for_graph(self, graph_id: str) -> list[dict[str, Any]]:
        async with self._store(read_only=True) as store:
            return await store.list_task_runs_for_graph(graph_id)

    async def count_graph_runs(self, graph_id: str) -> int:
        async with self._store(read_only=True) as store:
            return await store.count_graph_runs(graph_id)

    async def list_graph_runs_page(
        self, graph_id: str, *, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        async with self._store(read_only=True) as store:
            rows = await store.list_graph_runs(graph_id, limit=limit, offset=offset)
        return self._runs(rows)

    async def count_graph_runs_by_graph(self) -> dict[str, dict[str, int]]:
        """Per-graph ``{total, success, failed}``; ``cancelled`` counts only in total."""
        async with self._store(read_only=True) as store:
            rows = await store.count_graph_runs_by_graph()
        return {
            str(row["graph_id"]): {
                "total": int(row["total"] or 0),
                "success": int(row["success"] or 0),
                "failed": int(row["failed"] or 0),
            }
            for row in rows
        }

    # ── Graph runs ─────────────────────────────────────────────

    async def create_graph_run(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a pending run with its delivery job, never resetting an existing run."""
        run, _created = await self.create_graph_run_once(data)
        return run

    async def create_graph_run_once(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create-if-absent, with its ``task.graph`` job and wakeup in the same transaction.

        ``created`` is True only for the call whose insert won; a racing
        creator receives the existing run and creates no second job.
        """
        if data.get("state", "pending") != "pending":
            raise ValueError("A graph run starts pending")
        run_id = str(data.get("id") or uuid4())
        async with self._store() as store:
            existing = await store.get_graph_run(run_id)
            if existing is not None:
                if existing["graph_id"] != data["graph_id"]:
                    raise MetadataConflict()
                return self._run(existing) or {}, False
            job = await JobRepository(store.connection).enqueue(
                graph_job_definition(run_id, str(data["graph_id"])),
            )
            row, created = await store.create_graph_run_once(
                {**data, "id": run_id, "wal_marks": data.get("wal_marks"), "job_id": job["id"]},
            )
        return self._run(row) or {}, created

    async def create_scheduled_graph_run(
        self,
        lease: SchedulerLease,
        root_task: dict[str, Any],
        scheduled_at: datetime,
        data: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Reserve a due occurrence, its job and wakeup, and create its run, atomically.

        The reservation locks the live leader lease in this transaction, so an
        expired leader cannot publish. A repeated reservation returns the
        original run without another job.
        """
        run_id = str(data["id"])
        async with self._store() as store:
            existing = await store.get_graph_run(run_id)
            if existing is not None:
                if existing["graph_id"] != data["graph_id"]:
                    raise MetadataConflict()
                return self._run(existing) or {}, False
            occurrence = ScheduledOccurrence(
                root_task_id=str(root_task["id"]),
                scheduled_at=scheduled_at,
                graph_run_id=run_id,
                definition=graph_job_definition(
                    run_id, str(data["graph_id"]),
                    definition_version=int(root_task.get("version") or 1),
                ),
            )
            reservation = await SchedulerRepository(store.connection).reserve(lease, occurrence)
            row, created = await store.create_graph_run_once(
                {**data, "id": run_id, "job_id": reservation["job_id"]},
            )
        return self._run(row) or {}, created

    async def get_graph_run(self, run_id: str) -> dict[str, Any] | None:
        async with self._store(read_only=True) as store:
            return self._run(await store.get_graph_run(run_id))

    async def existing_graph_run_ids(self, run_ids: list[str]) -> set[str]:
        if not run_ids:
            return set()
        async with self._store(read_only=True) as store:
            return await store.existing_graph_run_ids(run_ids)

    async def graph_run_for_job(self, job_id: Any) -> dict[str, Any] | None:
        async with self._store(read_only=True) as store:
            return self._run(await store.graph_run_for_job(job_id))

    async def list_active_graph_runs(self, graph_id: str) -> list[dict[str, Any]]:
        """Pending and running runs, oldest first — the overlap decision's input."""
        async with self._store(read_only=True) as store:
            return self._runs(await store.list_active_graph_runs(graph_id))

    async def list_graph_runs_by_state(
        self, states: list[str], *, limit: int = 1000
    ) -> list[dict[str, Any]]:
        if not states:
            return []
        async with self._store(read_only=True) as store:
            return self._runs(await store.list_graph_runs_by_state(states, limit=limit))

    async def claim_graph_run(
        self, run_id: str, from_states: list[str], to_state: str,
    ) -> int | None:
        """Move the run and take a new lease; its generation, or None when lost."""
        async with self._store() as store:
            return await store.claim_graph_run(run_id, from_states, to_state)

    async def transition_graph_run(
        self, run_id: str, from_states: list[str], to_state: str, *, generation: int,
    ) -> bool:
        """A state change by the current lease holder only."""
        if not from_states:
            return False
        async with self._store() as store:
            return await store.transition_graph_run(
                run_id, from_states, to_state, generation=generation,
            )

    async def heartbeat_graph_run(self, run_id: str, *, generation: int) -> bool:
        async with self._store() as store:
            return await store.heartbeat_graph_run(run_id, generation=generation)

    async def list_stale_graph_runs(
        self, older_than_seconds: int, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        async with self._store(read_only=True) as store:
            return self._runs(await store.list_stale_graph_runs(
                older_than_seconds=older_than_seconds, limit=limit,
            ))

    # ── Node runs ──────────────────────────────────────────────

    async def create_task_run_once(
        self, graph_run_id: str, task_id: str, attempt: int = 1, *, delegated: bool = True
    ) -> dict[str, Any]:
        """Create a node attempt by its deterministic id, or return the raced row."""
        async with self._store() as store:
            row, _created = await store.create_task_run_once(
                graph_run_id, task_id, attempt, delegated=delegated,
            )
        return row

    async def get_task_run(self, run_id: str) -> dict[str, Any] | None:
        async with self._store(read_only=True) as store:
            return await store.get_task_run(run_id)

    async def list_task_runs(self, graph_run_id: str) -> list[dict[str, Any]]:
        async with self._store(read_only=True) as store:
            return await store.list_task_runs(graph_run_id)

    async def list_node_runs(self, graph_run_id: str) -> list[dict[str, Any]]:
        """Node rows for a graph run keyed by task id (latest attempt wins)."""
        latest: dict[str, dict[str, Any]] = {}
        for run in await self.list_task_runs(graph_run_id):
            task_id = run.get("task_id")
            if task_id and (task_id not in latest or run["attempt"] >= latest[task_id]["attempt"]):
                latest[task_id] = run
        return list(latest.values())

    async def list_running_task_runs(self, *, limit: int = 500) -> list[dict[str, Any]]:
        async with self._store(read_only=True) as store:
            return await store.list_running_task_runs(limit=limit)

    async def list_stale_task_runs(
        self, older_than_seconds: int, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        async with self._store(read_only=True) as store:
            return await store.list_stale_task_runs(
                older_than_seconds=older_than_seconds, limit=limit,
            )

    async def claim_task_run(self, run_id: str, from_states: list[str]) -> int | None:
        """Start (or retake an abandoned) node under a new lease generation."""
        async with self._store() as store:
            return await store.claim_task_run(run_id, from_states)

    async def finish_task_run(
        self,
        run_id: str,
        to_state: str,
        *,
        generation: int,
        query_id: str | None = None,
        error_message: str | None = None,
        output: str | None = None,
    ) -> bool:
        """Record a running node's outcome, only for the current lease holder."""
        async with self._store() as store:
            return await store.finish_task_run(
                run_id, to_state, generation=generation, query_id=query_id,
                error_message=error_message, output=output,
            )

    async def skip_pending_task_run(self, run_id: str) -> bool:
        async with self._store() as store:
            return await store.skip_pending_task_run(run_id)

    async def abandon_task_run(
        self, run_id: str, *, generation: int, query_id: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        async with self._store() as store:
            return await store.abandon_task_run(
                run_id, generation=generation, query_id=query_id, error_message=error_message,
            )

    async def heartbeat_task_run(self, run_id: str, *, generation: int) -> bool:
        async with self._store() as store:
            return await store.heartbeat_task_run(run_id, generation=generation)


task_orchestration_repository = TaskOrchestrationRepository()
