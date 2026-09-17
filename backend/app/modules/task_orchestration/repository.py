"""Repository for Phase 9 task-orchestration state in NOVA_SYSTEM.

Every read and write goes through ``db.execute_system`` — there is no ad-hoc
root connection here, unlike the legacy ``modules/tasks/service.py``. No method
accepts or stores a credential value; only object names are persisted.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.core.database import db

_TASKS = "NOVA_SYSTEM.CONFIG_TASKS"
_EDGES = "NOVA_SYSTEM.CONFIG_TASK_EDGES"
_GRAPH_RUNS = "NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS"
_TASK_RUNS = "NOVA_SYSTEM.CONFIG_TASK_RUNS"

_TASK_COLUMNS = (
    "id, name, database_name, definition, schedule_kind, schedule_expr, timezone, "
    "when_expr, overlap_policy, owner_role, created_by, version, created_at, updated_at"
)
_EDGE_COLUMNS = "id, graph_id, parent_task, child_task, created_at"
_GRAPH_RUN_COLUMNS = (
    "id, graph_id, trigger_type, state, wal_marks, started_at, finished_at"
)
_TASK_RUN_COLUMNS = (
    "id, graph_run_id, task_id, attempt, state, delegated, starrocks_query_id, "
    "error_message, started_at, finished_at"
)


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

    # ── Tasks ──────────────────────────────────────────────────

    async def create_task(self, data: dict[str, Any], created_by: str | None) -> dict[str, Any]:
        task_id = str(uuid4())
        await db.execute_system(
            f"""
            INSERT INTO {_TASKS}
            (id, name, database_name, definition, schedule_kind, schedule_expr,
             timezone, when_expr, overlap_policy, owner_role, created_by, version,
             created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, NOW(), NOW())
            """,
            [
                task_id,
                data["name"],
                data.get("database_name"),
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

    async def list_tasks(self, graph_id: str | None = None) -> list[dict[str, Any]]:
        if graph_id is None:
            sql = f"SELECT {_TASK_COLUMNS} FROM {_TASKS} ORDER BY name"
            params: list[Any] = []
        else:
            sql = (
                f"SELECT {_TASK_COLUMNS} FROM {_TASKS} "
                "WHERE id IN (SELECT task_id FROM NOVA_SYSTEM.CONFIG_TASK_EDGES "
                "WHERE graph_id = %s) ORDER BY name"
            )
            params = [graph_id]
        result = await db.execute_system(sql, params)
        return [self._to_dict(_TASK_COLUMNS, row) for row in result["rows"]]

    async def update_task(self, task_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        assignments = ", ".join(f"{key} = %s" for key in data)
        await db.execute_system(
            f"UPDATE {_TASKS} SET {assignments}, version = version + 1, updated_at = NOW() "
            "WHERE id = %s",
            [*data.values(), task_id],
        )
        return await self.get_task(task_id)

    async def delete_task(self, task_id: str) -> bool:
        result = await db.execute_system(
            f"DELETE FROM {_TASKS} WHERE id = %s", [task_id]
        )
        return bool(result.get("affected"))

    # ── Edges ──────────────────────────────────────────────────

    async def create_edge(self, graph_id: str, data: dict[str, Any]) -> dict[str, Any]:
        edge_id = str(uuid4())
        await db.execute_system(
            f"""
            INSERT INTO {_EDGES} (id, graph_id, parent_task, child_task, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            """,
            [edge_id, graph_id, data["parent_task"], data["child_task"]],
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

    async def update_edge(self, edge_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        assignments = ", ".join(f"{key} = %s" for key in data)
        await db.execute_system(
            f"UPDATE {_EDGES} SET {assignments} WHERE id = %s",
            [*data.values(), edge_id],
        )
        return await self.get_edge(edge_id)

    async def delete_edge(self, edge_id: str) -> bool:
        result = await db.execute_system(f"DELETE FROM {_EDGES} WHERE id = %s", [edge_id])
        return bool(result.get("affected"))

    # ── Graph runs ─────────────────────────────────────────────

    async def create_graph_run(self, data: dict[str, Any]) -> dict[str, Any]:
        run_id = str(uuid4())
        await db.execute_system(
            f"""
            INSERT INTO {_GRAPH_RUNS}
            (id, graph_id, trigger_type, state, wal_marks, started_at, finished_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), NULL)
            """,
            [
                run_id,
                data["graph_id"],
                data.get("trigger_type", "manual"),
                data.get("state", "pending"),
                self._encode_wal_marks(data.get("wal_marks")),
            ],
        )
        created = await self.get_graph_run(run_id)
        assert created is not None
        return created

    async def get_graph_run(self, run_id: str) -> dict[str, Any] | None:
        result = await db.execute_system(
            f"SELECT {_GRAPH_RUN_COLUMNS} FROM {_GRAPH_RUNS} WHERE id = %s", [run_id]
        )
        if not result["rows"]:
            return None
        row = self._to_dict(_GRAPH_RUN_COLUMNS, result["rows"][0])
        row["wal_marks"] = self._decode_wal_marks(row["wal_marks"])
        return row

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

    async def update_graph_run(
        self, run_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        payload = dict(data)
        if "wal_marks" in payload:
            payload["wal_marks"] = self._encode_wal_marks(payload["wal_marks"])
        assignments = ", ".join(f"{key} = %s" for key in payload)
        await db.execute_system(
            f"UPDATE {_GRAPH_RUNS} SET {assignments} WHERE id = %s",
            [*payload.values(), run_id],
        )
        return await self.get_graph_run(run_id)

    async def delete_graph_run(self, run_id: str) -> bool:
        result = await db.execute_system(
            f"DELETE FROM {_GRAPH_RUNS} WHERE id = %s", [run_id]
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

    async def update_task_run(
        self, run_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        assignments = ", ".join(f"{key} = %s" for key in data)
        await db.execute_system(
            f"UPDATE {_TASK_RUNS} SET {assignments} WHERE id = %s",
            [*data.values(), run_id],
        )
        return await self.get_task_run(run_id)

    async def delete_task_run(self, run_id: str) -> bool:
        result = await db.execute_system(
            f"DELETE FROM {_TASK_RUNS} WHERE id = %s", [run_id]
        )
        return bool(result.get("affected"))


task_orchestration_repository = TaskOrchestrationRepository()
