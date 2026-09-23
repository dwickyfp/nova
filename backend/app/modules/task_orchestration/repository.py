"""Repository for Phase 9 task-orchestration state in NOVA_SYSTEM.

Every read and write goes through ``db.execute_system`` — there is no ad-hoc
root connection here, unlike the legacy ``modules/tasks/service.py``. No method
accepts or stores a credential value; only object names are persisted.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.core.database import db

_TASKS = "NOVA_SYSTEM.CONFIG_TASKS"
_EDGES = "NOVA_SYSTEM.CONFIG_TASK_EDGES"
_GRAPH_RUNS = "NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS"
_TASK_RUNS = "NOVA_SYSTEM.CONFIG_TASK_RUNS"

_TASK_COLUMNS = (
    "id, name, database_name, schema_name, definition, schedule_kind, schedule_expr, "
    "timezone, when_expr, overlap_policy, owner_role, created_by, "
    "consecutive_fail_count, version, created_at, updated_at"
)
_EDGE_COLUMNS = "id, graph_id, parent_task, child_task, edge_kind, created_at"
_GRAPH_RUN_COLUMNS = (
    "id, graph_id, trigger_type, state, overlap_policy, wal_marks, started_at, "
    "heartbeat_at, finished_at"
)
_TASK_RUN_COLUMNS = (
    "id, graph_run_id, task_id, attempt, state, delegated, starrocks_query_id, "
    "error_message, started_at, heartbeat_at, finished_at"
)

# Edges store task *names* (parent_task/child_task), not ids, so graph membership
# is resolved by name.
_UPDATABLE_COLUMNS: dict[str, frozenset[str]] = {
    "task": frozenset(
        {
            "name",
            "database_name",
            "schema_name",
            "definition",
            "schedule_kind",
            "schedule_expr",
            "timezone",
            "when_expr",
            "overlap_policy",
            "owner_role",
        }
    ),
    "edge": frozenset({"parent_task", "child_task"}),
    "graph_run": frozenset({"trigger_type", "state", "wal_marks", "heartbeat_at", "finished_at"}),
    "task_run": frozenset(
        {
            "attempt",
            "state",
            "delegated",
            "starrocks_query_id",
            "error_message",
            "heartbeat_at",
            "finished_at",
        }
    ),
}


class UnknownUpdateColumnError(ValueError):
    """Raised when an update payload names a column outside the whitelist."""


def _assignments(entity: str, data: dict[str, Any]) -> tuple[str, list[Any]]:
    """Build a parameterized SET clause from whitelisted columns only.

    The keys are validated against ``_UPDATABLE_COLUMNS`` before interpolation —
    values were already parameterized, but raw keys in a SET clause are an
    injection surface the moment an API exposes these methods.
    """
    if not data:
        raise ValueError(f"no fields to update for {entity}")

    allowed = _UPDATABLE_COLUMNS[entity]
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise UnknownUpdateColumnError(
            f"cannot update {entity} column(s): {', '.join(unknown)}; "
            f"allowed: {', '.join(sorted(allowed))}"
        )

    clause = ", ".join(f"{key} = %s" for key in data)
    return clause, list(data.values())


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


class TaskOrchestrationRepository:
    """CRUD over the four ``CONFIG_TASK*`` tables."""

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _to_dict(columns: str, row: list[Any]) -> dict[str, Any]:
        return dict(zip([c.strip() for c in columns.split(",")], row, strict=True))

    @staticmethod
    def _encode_wal_marks(marks: dict[str, int] | None) -> str | None:
        if marks is None:
            return None
        return json.dumps(marks)

    @staticmethod
    def _decode_wal_marks(raw: str | None) -> dict[str, int] | None:
        if raw is None:
            return None
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    async def get_engine_timezone(self) -> str | None:
        """The StarRocks session timezone of this connection, e.g. ``Asia/Jakarta``.

        ``NOW()`` values are written in this zone and read back naive, so the
        scheduler needs it to interpret schedule anchors. Reading it from the
        engine removes the guess a config default would encode.
        """
        result = await db.execute_system("SELECT @@time_zone AS time_zone")
        if not result["rows"]:
            return None
        value = result["rows"][0][0]
        return str(value) if value else None

    # ── Tasks ──────────────────────────────────────────────────

    async def create_task(self, data: dict[str, Any], created_by: str | None) -> dict[str, Any]:
        task_id = str(uuid4())
        await db.execute_system(
            f"""
            INSERT INTO {_TASKS}
            (id, name, database_name, schema_name, definition, schedule_kind,
             schedule_expr, timezone, when_expr, overlap_policy, owner_role,
             created_by, version, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, NOW(), NOW())
            """,
            [
                task_id,
                data["name"],
                data.get("database_name"),
                data.get("schema_name"),
                data.get("definition"),
                data.get("schedule_kind", "manual"),
                data.get("schedule_expr"),
                data["timezone"],
                data.get("when_expr"),
                data.get("overlap_policy", "skip"),
                data.get("owner_role"),
                created_by,
            ],
        )
        created = await self.get_task(task_id)
        assert created is not None
        return created

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        result = await db.execute_system(
            f"SELECT {_TASK_COLUMNS} FROM {_TASKS} WHERE id = %s",
            [task_id],
        )
        if not result["rows"]:
            return None
        return self._to_dict(_TASK_COLUMNS, result["rows"][0])

    async def find_task(
        self, name: str, database_name: str | None, schema_name: str | None
    ) -> dict[str, Any] | None:
        """The task with this exact ``(database, schema, name)`` identity.

        Tasks are schema-scoped, so ``name`` alone is ambiguous. ``NULL`` scope
        columns are matched with ``<=>`` (NULL-safe equality) so a legacy row
        created before the scope column existed resolves the same way it was
        written.
        """
        result = await db.execute_system(
            f"SELECT {_TASK_COLUMNS} FROM {_TASKS} "
            "WHERE name = %s AND database_name <=> %s AND schema_name <=> %s "
            "LIMIT 1",
            [name, database_name, schema_name],
        )
        if not result["rows"]:
            return None
        return self._to_dict(_TASK_COLUMNS, result["rows"][0])

    async def list_tasks_for_schema(
        self, database_name: str, schema_name: str
    ) -> list[dict[str, Any]]:
        """Every task scoped to one ``database.schema``, for the explorer."""
        result = await db.execute_system(
            f"SELECT {_TASK_COLUMNS} FROM {_TASKS} "
            "WHERE database_name = %s AND schema_name = %s ORDER BY name",
            [database_name, schema_name],
        )
        return [self._to_dict(_TASK_COLUMNS, row) for row in result["rows"]]

    async def list_tasks(self, graph_id: str | None = None) -> list[dict[str, Any]]:
        """Task rows, either all or the members of one graph.

        A graph is single-schema, and its id is the root task's **qualified**
        name (``database.schema.name``). Membership is therefore resolved by the
        graph's scope plus the edge endpoints (bare names): filtering by name
        alone would merge two same-named tasks in different schemas.
        """
        if graph_id is None:
            sql = f"SELECT {_TASK_COLUMNS} FROM {_TASKS} ORDER BY name"
            params: list[Any] = []
        else:
            scope = scope_from_graph_id(graph_id)
            names_sql = (
                "name IN ("
                f"  SELECT parent_task FROM {_EDGES} WHERE graph_id = %s "
                "   UNION "
                f"  SELECT child_task FROM {_EDGES} WHERE graph_id = %s"
                ")"
            )
            if scope is None:
                # Legacy/unscoped graph id: the graph id is the task's own bare
                # id, so resolve by name only.
                sql = (
                    f"SELECT {_TASK_COLUMNS} FROM {_TASKS} "
                    "WHERE id = %s OR "
                    f"({names_sql}) ORDER BY name"
                )
                params = [graph_id, graph_id, graph_id]
            else:
                database_name, schema_name = scope
                root_name = graph_id.rsplit(".", 1)[-1]
                sql = (
                    f"SELECT {_TASK_COLUMNS} FROM {_TASKS} "
                    "WHERE database_name = %s AND schema_name = %s AND "
                    f"(name = %s OR {names_sql}) ORDER BY name"
                )
                params = [database_name, schema_name, root_name, graph_id, graph_id]
        result = await db.execute_system(sql, params)
        return [self._to_dict(_TASK_COLUMNS, row) for row in result["rows"]]

    async def update_task(self, task_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        assignments, values = _assignments("task", data)
        await db.execute_system(
            f"UPDATE {_TASKS} SET {assignments}, version = version + 1, updated_at = NOW() "
            "WHERE id = %s",
            [*values, task_id],
        )
        return await self.get_task(task_id)

    async def delete_task(self, task_id: str) -> bool:
        result = await db.execute_system(f"DELETE FROM {_TASKS} WHERE id = %s", [task_id])
        return bool(result.get("affected"))

    async def increment_consecutive_failures(self, task_id: str) -> int:
        """Increment a task's consecutive-failure counter, returning the new value.

        The engine auto-pauses a task after a run of consecutive failures but
        exposes no count of its own, so Nova keeps one. Incrementing is atomic
        in the engine (``SET col = col + 1``), so two reconcilers cannot lose
        an increment. ``version``/``updated_at`` are deliberately untouched: a
        counter is bookkeeping, not a definition change.
        """
        await db.execute_system(
            f"UPDATE {_TASKS} SET consecutive_fail_count = "
            "COALESCE(consecutive_fail_count, 0) + 1 WHERE id = %s",
            [task_id],
        )
        result = await db.execute_system(
            f"SELECT consecutive_fail_count FROM {_TASKS} WHERE id = %s", [task_id]
        )
        if not result["rows"]:
            return 0
        return int(result["rows"][0][0] or 0)

    async def reset_consecutive_failures(self, task_id: str) -> None:
        """Clear a task's consecutive-failure run after a successful node."""
        await db.execute_system(
            f"UPDATE {_TASKS} SET consecutive_fail_count = 0 WHERE id = %s",
            [task_id],
        )

    # ── Edges ──────────────────────────────────────────────────

    async def create_edge(self, graph_id: str, data: dict[str, Any]) -> dict[str, Any]:
        edge_id = str(uuid4())
        await db.execute_system(
            f"""
            INSERT INTO {_EDGES}
            (id, graph_id, parent_task, child_task, edge_kind, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            """,
            [
                edge_id,
                graph_id,
                data["parent_task"],
                data["child_task"],
                data.get("edge_kind", "after"),
            ],
        )
        created = await self.get_edge(edge_id)
        assert created is not None
        return created

    async def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        result = await db.execute_system(
            f"SELECT {_EDGE_COLUMNS} FROM {_EDGES} WHERE id = %s", [edge_id]
        )
        if not result["rows"]:
            return None
        return self._to_dict(_EDGE_COLUMNS, result["rows"][0])

    async def list_edges(self, graph_id: str) -> list[dict[str, Any]]:
        result = await db.execute_system(
            f"SELECT {_EDGE_COLUMNS} FROM {_EDGES} WHERE graph_id = %s ORDER BY id",
            [graph_id],
        )
        return [self._to_dict(_EDGE_COLUMNS, row) for row in result["rows"]]

    async def list_all_edges(self) -> list[dict[str, Any]]:
        """Every edge, in one query — the scheduler plans all graphs per tick."""
        result = await db.execute_system(
            f"SELECT {_EDGE_COLUMNS} FROM {_EDGES} ORDER BY graph_id, id"
        )
        return [self._to_dict(_EDGE_COLUMNS, row) for row in result["rows"]]

    async def update_edge(self, edge_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        assignments, values = _assignments("edge", data)
        await db.execute_system(
            f"UPDATE {_EDGES} SET {assignments} WHERE id = %s",
            [*values, edge_id],
        )
        return await self.get_edge(edge_id)

    async def delete_edge(self, edge_id: str) -> bool:
        result = await db.execute_system(f"DELETE FROM {_EDGES} WHERE id = %s", [edge_id])
        return bool(result.get("affected"))

    # ── Read model for the orchestration API ───────────────────
    #
    # These serve the read-only `/api/v1/task-orchestration` endpoints. They are
    # deliberately in the repository, not the router: the router must not carry
    # SQL, and the graph-assembly rules (names as endpoints, standalone tasks as
    # single-node graphs) belong next to the write path that produced them.

    async def list_graph_ids(self) -> list[str]:
        """Every graph id that has at least one edge, plus standalone task graphs.

        A graph is identified by its edges' ``graph_id``. A task with no edges is
        a single-node graph keyed by its **qualified** name — the same convention
        ``scheduler.build_graphs`` uses, so the API and the scheduler agree on
        what a graph is. A legacy row with no scope keeps its bare name / task id.
        """
        edges = await self.list_all_edges()
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
        for task in await self.list_tasks():
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

    async def get_tasks_by_names(self, names: list[str]) -> list[dict[str, Any]]:
        """Task rows for ``names``, in one query.

        Name-keyed rather than id-keyed because edges store task names (design
        §5). An empty list returns no rows without touching the database.
        """
        if not names:
            return []
        placeholders = ", ".join(["%s"] * len(names))
        result = await db.execute_system(
            f"SELECT {_TASK_COLUMNS} FROM {_TASKS} WHERE name IN ({placeholders}) ORDER BY name",
            list(names),
        )
        return [self._to_dict(_TASK_COLUMNS, row) for row in result["rows"]]

    async def get_latest_graph_run(self, graph_id: str) -> dict[str, Any] | None:
        """The most recent graph run for a graph, or ``None``.

        Used by the graph list so a row can show its last run without loading the
        whole history.
        """
        result = await db.execute_system(
            f"SELECT {_GRAPH_RUN_COLUMNS} FROM {_GRAPH_RUNS} "
            "WHERE graph_id = %s ORDER BY started_at DESC LIMIT 1",
            [graph_id],
        )
        if not result["rows"]:
            return None
        row = self._to_dict(_GRAPH_RUN_COLUMNS, result["rows"][0])
        row["wal_marks"] = self._decode_wal_marks(row["wal_marks"])
        return row

    async def list_task_runs_for_graph(self, graph_id: str) -> list[dict[str, Any]]:
        """Every node-run row belonging to any run of ``graph_id``.

        Joined through ``CONFIG_TASK_GRAPH_RUNS`` rather than filtering in the
        handler, so "the last state of each node in this graph" is one query plus
        a fold in the caller.
        """
        columns = ", ".join(f"r.{column}" for column in _TASK_RUN_COLUMNS.split(", "))
        result = await db.execute_system(
            f"SELECT {columns} "
            f"FROM {_TASK_RUNS} r "
            f"JOIN {_GRAPH_RUNS} g ON g.id = r.graph_run_id "
            "WHERE g.graph_id = %s ORDER BY r.started_at",
            [graph_id],
        )
        return [self._to_dict(_TASK_RUN_COLUMNS, row) for row in result["rows"]]

    async def count_graph_runs(self, graph_id: str) -> int:
        """Total number of runs for a graph — the pagination denominator."""
        result = await db.execute_system(
            f"SELECT COUNT(*) FROM {_GRAPH_RUNS} WHERE graph_id = %s",
            [graph_id],
        )
        if not result["rows"] or not result["rows"][0]:
            return 0
        return int(result["rows"][0][0] or 0)

    async def list_graph_runs_page(
        self, graph_id: str, *, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        """One page of a graph's runs, newest first.

        Ordered and bounded in the engine rather than sliced in the router, so a
        graph with a long history never materialises every row to show ten of
        them. ``id`` is the tiebreaker after ``started_at`` so two runs that
        started in the same second paginate deterministically.
        """
        result = await db.execute_system(
            f"SELECT {_GRAPH_RUN_COLUMNS} FROM {_GRAPH_RUNS} "
            "WHERE graph_id = %s ORDER BY started_at DESC, id DESC LIMIT %s OFFSET %s",
            [graph_id, max(1, limit), max(0, offset)],
        )
        runs = [self._to_dict(_GRAPH_RUN_COLUMNS, row) for row in result["rows"]]
        for run in runs:
            run["wal_marks"] = self._decode_wal_marks(run["wal_marks"])
        return runs

    async def count_graph_runs_by_graph(self) -> dict[str, dict[str, int]]:
        """Per-graph ``{total, success, failed}`` tallies, in one query.

        The list endpoint needs a tally for every visible graph, so a per-graph
        count would be N queries. This folds the whole ``CONFIG_TASK_GRAPH_RUNS``
        table instead — the table is config-plane (one row per graph run), so one
        sweep is the right shape, exactly like the read model behind
        ``/graphs``.

        ``cancelled`` is deliberately in ``total`` but in neither bucket; see
        ``schemas.RunCounts`` for why.
        """
        result = await db.execute_system(
            "SELECT graph_id, "
            "COUNT(*) AS total, "
            "SUM(CASE WHEN state = 'success' THEN 1 ELSE 0 END) AS success, "
            "SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) AS failed "
            f"FROM {_GRAPH_RUNS} GROUP BY graph_id"
        )
        tallies: dict[str, dict[str, int]] = {}
        for row in result["rows"]:
            graph_id = str(row[0])
            tallies[graph_id] = {
                "total": int(row[1] or 0),
                "success": int(row[2] or 0),
                "failed": int(row[3] or 0),
            }
        return tallies

    # ── Graph runs ─────────────────────────────────────────────

    async def create_graph_run(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a graph run, **never overwriting an existing row**.

        ``NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS`` is a StarRocks Primary-Key table,
        so a plain ``INSERT`` on an existing primary key is a **destructive
        full-row upsert**: it would reset a run that is already ``running`` or
        ``success`` back to ``pending`` (verified against 4.1.x on 2026-09-19).
        The scheduler keys runs by a deterministic ``(graph_id, due_at)`` id, so
        a second tick — or a second leader during a lock-TTL window — must treat
        the existing row as the run, not clobber it.

        The guarded ``INSERT … SELECT … WHERE NOT EXISTS`` is the primitive that
        makes this safe: it inserts only when the id is absent and leaves an
        existing row untouched (verified: ``affected = 0`` and the row keeps its
        state).
        """
        run_id = data.get("id") or str(uuid4())
        await db.execute_system(
            f"""
            INSERT INTO {_GRAPH_RUNS}
            (id, graph_id, trigger_type, state, overlap_policy, wal_marks,
             started_at, finished_at)
            SELECT %s, %s, %s, %s, %s, %s, NOW(), NULL
            WHERE NOT EXISTS (SELECT 1 FROM {_GRAPH_RUNS} WHERE id = %s)
            """,
            [
                run_id,
                data["graph_id"],
                data.get("trigger_type", "manual"),
                data.get("state", "pending"),
                data.get("overlap_policy", "skip"),
                self._encode_wal_marks(data.get("wal_marks")),
                run_id,
            ],
        )
        created = await self.get_graph_run(run_id)
        assert created is not None
        return created

    async def create_graph_run_once(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create-if-absent. Returns ``(row, created)``.

        ``created`` is authoritative: it comes from the guarded insert's own
        affected-row count, not from a read-then-write check (which two
        concurrent ticks could both pass). ``affected == 1`` means **this** call
        inserted the row and therefore owns the publish; ``affected == 0`` means
        a concurrent creator got there first and owns it.
        """
        run_id = data.get("id") or str(uuid4())
        result = await db.execute_system(
            f"""
            INSERT INTO {_GRAPH_RUNS}
            (id, graph_id, trigger_type, state, overlap_policy, wal_marks,
             started_at, finished_at)
            SELECT %s, %s, %s, %s, %s, %s, NOW(), NULL
            WHERE NOT EXISTS (SELECT 1 FROM {_GRAPH_RUNS} WHERE id = %s)
            """,
            [
                run_id,
                data["graph_id"],
                data.get("trigger_type", "manual"),
                data.get("state", "pending"),
                data.get("overlap_policy", "skip"),
                self._encode_wal_marks(data.get("wal_marks")),
                run_id,
            ],
        )
        inserted = int(result.get("affected") or 0) == 1
        created = await self.get_graph_run(run_id)
        assert created is not None
        return created, inserted

    async def get_graph_run(self, run_id: str) -> dict[str, Any] | None:
        result = await db.execute_system(
            f"SELECT {_GRAPH_RUN_COLUMNS} FROM {_GRAPH_RUNS} WHERE id = %s", [run_id]
        )
        if not result["rows"]:
            return None
        row = self._to_dict(_GRAPH_RUN_COLUMNS, result["rows"][0])
        row["wal_marks"] = self._decode_wal_marks(row["wal_marks"])
        return row

    async def existing_graph_run_ids(self, run_ids: list[str]) -> set[str]:
        """Batch idempotency checks for a scheduler tick's bounded due set."""
        existing: set[str] = set()
        ids = list(dict.fromkeys(run_ids))
        for offset in range(0, len(ids), 500):
            batch = ids[offset : offset + 500]
            placeholders = ", ".join("%s" for _ in batch)
            result = await db.execute_system(
                f"SELECT id FROM {_GRAPH_RUNS} WHERE id IN ({placeholders})",
                batch,
            )
            existing.update(str(row[0]) for row in result["rows"])
        return existing

    async def list_graph_runs(self, graph_id: str) -> list[dict[str, Any]]:
        result = await db.execute_system(
            f"SELECT {_GRAPH_RUN_COLUMNS} FROM {_GRAPH_RUNS} "
            "WHERE graph_id = %s ORDER BY started_at DESC",
            [graph_id],
        )
        runs = [self._to_dict(_GRAPH_RUN_COLUMNS, row) for row in result["rows"]]
        for run in runs:
            run["wal_marks"] = self._decode_wal_marks(run["wal_marks"])
        return runs

    async def list_active_graph_runs(self, graph_id: str) -> list[dict[str, Any]]:
        """Graph runs for ``graph_id`` that have not reached a terminal state.

        The overlap decision reads this before enqueueing: an active run is what
        ``skip`` refuses to overlap and ``queue`` defers behind. ``pending`` and
        ``running`` are the active set; every other state is terminal.
        """
        result = await db.execute_system(
            f"SELECT {_GRAPH_RUN_COLUMNS} FROM {_GRAPH_RUNS} "
            "WHERE graph_id = %s AND state IN ('pending', 'running') "
            "ORDER BY started_at, id",
            [graph_id],
        )
        runs = [self._to_dict(_GRAPH_RUN_COLUMNS, row) for row in result["rows"]]
        for run in runs:
            run["wal_marks"] = self._decode_wal_marks(run["wal_marks"])
        return runs

    async def update_graph_run(self, run_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        payload = dict(data)
        if "wal_marks" in payload:
            payload["wal_marks"] = self._encode_wal_marks(payload["wal_marks"])
        assignments, values = _assignments("graph_run", payload)
        await db.execute_system(
            f"UPDATE {_GRAPH_RUNS} SET {assignments} WHERE id = %s",
            [*values, run_id],
        )
        return await self.get_graph_run(run_id)

    async def delete_graph_run(self, run_id: str) -> bool:
        result = await db.execute_system(f"DELETE FROM {_GRAPH_RUNS} WHERE id = %s", [run_id])
        return bool(result.get("affected"))

    async def list_graph_runs_by_state(
        self, states: list[str], *, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Graph runs in any of ``states`` — the reconciler's work list.

        The reconciler polls only graph runs that are actually active, never
        every task (design §2, resource note). The cap is generous because the
        list is the recovery work-set: a ``pending`` run beyond the cap would not
        be re-enqueued, so the bound is set well above a realistic in-flight
        backlog rather than tuned for the common case.
        """
        if not states:
            return []
        placeholders = ", ".join(["%s"] * len(states))
        result = await db.execute_system(
            f"SELECT {_GRAPH_RUN_COLUMNS} FROM {_GRAPH_RUNS} "
            f"WHERE state IN ({placeholders}) ORDER BY started_at LIMIT %s",
            [*states, limit],
        )
        runs = [self._to_dict(_GRAPH_RUN_COLUMNS, row) for row in result["rows"]]
        for run in runs:
            run["wal_marks"] = self._decode_wal_marks(run["wal_marks"])
        return runs

    async def transition_graph_run(
        self, run_id: str, from_states: list[str], to_state: str
    ) -> bool:
        """Conditional state write. Returns True only if this caller moved it.

        The ``WHERE state IN (...)`` guard is what makes at-least-once delivery
        safe: a duplicate delivery finds the row already in ``to_state`` and its
        update affects no rows, so it cannot re-run the graph.
        """
        if not from_states:
            return False
        placeholders = ", ".join(["%s"] * len(from_states))
        finished = ", finished_at = NOW()" if to_state in {"success", "failed", "cancelled"} else ""
        result = await db.execute_system(
            f"UPDATE {_GRAPH_RUNS} SET state = %s{finished} "
            f"WHERE id = %s AND state IN ({placeholders})",
            [to_state, run_id, *from_states],
        )
        return bool(result.get("affected"))

    # ── Task runs ──────────────────────────────────────────────

    async def create_task_run(self, data: dict[str, Any]) -> dict[str, Any]:
        run_id = str(uuid4())
        await db.execute_system(
            f"""
            INSERT INTO {_TASK_RUNS}
            (id, graph_run_id, task_id, attempt, state, delegated,
             starrocks_query_id, error_message, started_at, finished_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NULL)
            """,
            [
                run_id,
                data.get("graph_run_id"),
                data.get("task_id"),
                data.get("attempt", 1),
                data.get("state", "pending"),
                data.get("delegated", True),
                data.get("starrocks_query_id"),
                data.get("error_message"),
            ],
        )
        created = await self.get_task_run(run_id)
        assert created is not None
        return created

    async def get_task_run(self, run_id: str) -> dict[str, Any] | None:
        result = await db.execute_system(
            f"SELECT {_TASK_RUN_COLUMNS} FROM {_TASK_RUNS} WHERE id = %s", [run_id]
        )
        if not result["rows"]:
            return None
        return self._to_dict(_TASK_RUN_COLUMNS, result["rows"][0])

    async def list_task_runs(self, graph_run_id: str) -> list[dict[str, Any]]:
        result = await db.execute_system(
            f"SELECT {_TASK_RUN_COLUMNS} FROM {_TASK_RUNS} "
            "WHERE graph_run_id = %s ORDER BY started_at",
            [graph_run_id],
        )
        return [self._to_dict(_TASK_RUN_COLUMNS, row) for row in result["rows"]]

    async def update_task_run(self, run_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        assignments, values = _assignments("task_run", data)
        await db.execute_system(
            f"UPDATE {_TASK_RUNS} SET {assignments} WHERE id = %s",
            [*values, run_id],
        )
        return await self.get_task_run(run_id)

    async def delete_task_run(self, run_id: str) -> bool:
        result = await db.execute_system(f"DELETE FROM {_TASK_RUNS} WHERE id = %s", [run_id])
        return bool(result.get("affected"))

    async def list_running_task_runs(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Node rows currently ``running`` across every graph run.

        The reconciler's work list: a node is ``running`` only because a worker
        submitted a native ``SUBMIT TASK`` and is waiting on it, so this is the
        only set whose native trace can advance. It is bounded by in-flight
        work, not by the number of tasks.
        """
        result = await db.execute_system(
            f"SELECT {_TASK_RUN_COLUMNS} FROM {_TASK_RUNS} "
            "WHERE state = 'running' ORDER BY started_at LIMIT %s",
            [limit],
        )
        return [self._to_dict(_TASK_RUN_COLUMNS, row) for row in result["rows"]]

    async def get_node_run(self, graph_run_id: str, task_id: str) -> dict[str, Any] | None:
        """The latest attempt row for a node within a graph run, if any."""
        result = await db.execute_system(
            f"SELECT {_TASK_RUN_COLUMNS} FROM {_TASK_RUNS} "
            "WHERE graph_run_id = %s AND task_id = %s "
            "ORDER BY attempt DESC LIMIT 1",
            [graph_run_id, task_id],
        )
        if not result["rows"]:
            return None
        return self._to_dict(_TASK_RUN_COLUMNS, result["rows"][0])

    async def list_node_runs(self, graph_run_id: str) -> list[dict[str, Any]]:
        """Node rows for a graph run keyed by task id (latest attempt wins)."""
        runs = await self.list_task_runs(graph_run_id)
        latest: dict[str, dict[str, Any]] = {}
        for run in runs:
            task_id = run.get("task_id")
            if task_id and (task_id not in latest or run["attempt"] >= latest[task_id]["attempt"]):
                latest[task_id] = run
        return list(latest.values())

    async def transition_task_run(
        self,
        run_id: str,
        from_states: list[str],
        to_state: str,
        *,
        query_id: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Conditional node-state write. True only when this caller moved it.

        This is the intended idempotency point for node execution: a redelivered
        graph run finds a terminal node and skips it. Verify the affected-row
        behavior under concurrent FE sessions before relying on it in production.
        """
        if not from_states:
            return False
        placeholders = ", ".join(["%s"] * len(from_states))
        sets = ["state = %s"]
        values: list[Any] = [to_state]
        if query_id is not None:
            sets.append("starrocks_query_id = %s")
            values.append(query_id)
        if error_message is not None:
            sets.append("error_message = %s")
            values.append(error_message)
        if to_state in {"success", "failed", "skipped"}:
            sets.append("finished_at = NOW()")
        result = await db.execute_system(
            f"UPDATE {_TASK_RUNS} SET {', '.join(sets)} "
            f"WHERE id = %s AND state IN ({placeholders})",
            [*values, run_id, *from_states],
        )
        return bool(result.get("affected"))

    async def create_task_run_once(
        self, graph_run_id: str, task_id: str, attempt: int = 1
    ) -> dict[str, Any]:
        """Create a node run, or return the one that already exists.

        At-least-once delivery means two workers may race to create the same
        node run. Two things make this safe:

        * the row id is **deterministic** — a UUID v5 over
          ``(graph_run_id, task_id, attempt)`` — so both racers target the same
          primary key instead of two random ids (the previous bug: a plain
          ``uuid4()`` let both rows exist and the node ran twice); and
        * the insert is ``INSERT … SELECT … WHERE NOT EXISTS``, so it never
          overwrites a row a racer already advanced past ``pending`` (a plain
          ``INSERT`` is a destructive upsert on a Primary-Key table).
        """
        run_id = _node_run_id(graph_run_id, task_id, attempt)
        await db.execute_system(
            f"""
            INSERT INTO {_TASK_RUNS}
            (id, graph_run_id, task_id, attempt, state, delegated,
             starrocks_query_id, error_message, started_at, finished_at)
            SELECT %s, %s, %s, %s, 'pending', 1, NULL, NULL, NOW(), NULL
            WHERE NOT EXISTS (SELECT 1 FROM {_TASK_RUNS} WHERE id = %s)
            """,
            [run_id, graph_run_id, task_id, attempt, run_id],
        )
        created = await self.get_task_run(run_id)
        assert created is not None
        return created

    async def mark_task_run_heartbeat(self, run_id: str) -> None:
        """Refresh a node's liveness stamp while it executes."""
        await db.execute_system(
            f"UPDATE {_TASK_RUNS} SET heartbeat_at = NOW() WHERE id = %s", [run_id]
        )

    async def mark_graph_run_heartbeat(self, run_id: str) -> None:
        await db.execute_system(
            f"UPDATE {_GRAPH_RUNS} SET heartbeat_at = NOW() WHERE id = %s", [run_id]
        )

    async def list_stale_task_runs(
        self, older_than_seconds: int, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """``RUNNING`` node rows whose heartbeat lapsed — abandoned work.

        A row that never stamped a heartbeat falls back to ``started_at``. These
        are candidates for re-evaluation, not trusted progress (design §2,
        rule 3): a worker that died mid-node leaves exactly this row.
        """
        result = await db.execute_system(
            f"SELECT {_TASK_RUN_COLUMNS} FROM {_TASK_RUNS} "
            "WHERE state = 'running' "
            "AND COALESCE(heartbeat_at, started_at) < "
            "DATE_SUB(NOW(), INTERVAL %s SECOND) "
            "ORDER BY started_at LIMIT %s",
            [older_than_seconds, limit],
        )
        return [self._to_dict(_TASK_RUN_COLUMNS, row) for row in result["rows"]]

    async def list_stale_graph_runs(
        self, older_than_seconds: int, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """``RUNNING`` graph runs whose heartbeat lapsed."""
        result = await db.execute_system(
            f"SELECT {_GRAPH_RUN_COLUMNS} FROM {_GRAPH_RUNS} "
            "WHERE state = 'running' "
            "AND COALESCE(heartbeat_at, started_at) < "
            "DATE_SUB(NOW(), INTERVAL %s SECOND) "
            "ORDER BY started_at LIMIT %s",
            [older_than_seconds, limit],
        )
        runs = [self._to_dict(_GRAPH_RUN_COLUMNS, row) for row in result["rows"]]
        for run in runs:
            run["wal_marks"] = self._decode_wal_marks(run["wal_marks"])
        return runs


task_orchestration_repository = TaskOrchestrationRepository()
