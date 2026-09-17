"""Task Manager service — submit, schedule, and monitor StarRocks tasks.

Every method takes the caller's StarRocks ``asyncmy.Connection`` as its first
argument. The connection is opened by ``get_user_connection`` in the dependency
layer, so queries run **as the authenticated user** and the engine's own
privilege filter applies. Nothing here holds or opens a root connection.
"""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any, Protocol

from .schemas import (
    TaskResponse,
    TaskRunResponse,
)

log = logging.getLogger(__name__)


class TaskCursor(Protocol):
    """The slice of asyncmy's cursor surface the service actually uses."""

    async def __aenter__(self) -> TaskCursor: ...
    async def __aexit__(self, *exc_info: Any) -> None: ...
    async def execute(self, sql: str, params: Any = None) -> None: ...
    async def fetchall(self) -> list[dict]: ...
    async def fetchone(self) -> dict | None: ...


class TaskConnection(Protocol):
    """A StarRocks connection supplying cursors.

    Structural typing keeps mypy happy without asyncmy stubs and, more
    importantly, documents the injection contract: the service needs *a*
    connection, not specifically a root one.
    """

    def cursor(self, cursor_class: Any = None) -> TaskCursor: ...


def _dict_cursor(conn: TaskConnection) -> TaskCursor:
    """Open a row-as-dict cursor on *conn*.

    Real asyncmy connections must be told to return mappings. ``asyncmy`` ships
    no stubs, so the import is hidden from mypy via ``importlib`` while the
    service keeps the runtime behaviour the previous implementation had.
    """
    dict_cursor = importlib.import_module("asyncmy.cursors").DictCursor
    return conn.cursor(dict_cursor)


class TaskService:
    """Business logic for StarRocks task management.

    Stateless by construction: the connection is injected per call, so the
    singleton never pins a connection (and never a root one).
    """

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _parse_properties(raw: str | dict | None) -> dict[str, str]:
        """Safely parse a PROPERTIES column value into a dict."""
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _row_to_task(row: dict) -> TaskResponse:
        """Map an information_schema.tasks row dict to a TaskResponse."""
        return TaskResponse(
            name=row.get("TASK_NAME") or row.get("Name") or "",
            database=row.get("DATABASE") or row.get("Database") or "",
            state=row.get("STATE") or row.get("State") or "",
            schedule=row.get("SCHEDULE") or row.get("Schedule") or "Manual",
            sql=row.get("DEFINITION") or row.get("Definition"),
            created_at=str(row["CREATE_TIME"]) if row.get("CREATE_TIME") else None,
            properties=TaskService._parse_properties(
                row.get("PROPERTIES") or row.get("Properties")
            ),
        )

    @staticmethod
    def _row_to_run(row: dict) -> TaskRunResponse:
        """Map an information_schema.task_runs row dict to a TaskRunResponse."""
        return TaskRunResponse(
            task_name=row.get("TASK_NAME") or row.get("Task_name") or "",
            create_time=str(row["CREATE_TIME"]) if row.get("CREATE_TIME") else "",
            finish_time=str(row["FINISH_TIME"]) if row.get("FINISH_TIME") else None,
            state=row.get("STATE") or row.get("State") or "",
            error_message=row.get("ERROR_MESSAGE") or row.get("Error_message"),
            properties=TaskService._parse_properties(
                row.get("PROPERTIES") or row.get("Properties")
            ),
        )

    # ── Task CRUD ───────────────────────────────────────────────

    async def list_tasks(self, conn: TaskConnection) -> list[TaskResponse]:
        """List tasks visible to the connection's user."""
        async with _dict_cursor(conn) as cur:
            await cur.execute("SELECT * FROM information_schema.tasks")
            rows = await cur.fetchall()
            return [self._row_to_task(r) for r in rows]

    async def get_task(
        self, conn: TaskConnection, name: str
    ) -> TaskResponse | None:
        """Get a single task by name, if the caller can see it."""
        async with _dict_cursor(conn) as cur:
            await cur.execute(
                "SELECT * FROM information_schema.tasks "
                "WHERE TASK_NAME = %s",
                (name,),
            )
            row = await cur.fetchone()
            return self._row_to_task(row) if row else None

    async def create_task(self, conn: TaskConnection, data: dict) -> dict:
        """Build and execute a SUBMIT TASK statement on the caller's connection.

        Scheduling variants:
          - One-shot:  ``SUBMIT TASK name AS sql;``
          - Periodic:  ``SUBMIT TASK name SCHEDULE EVERY(INTERVAL interval) AS sql;``
          - With start: ``SUBMIT TASK name SCHEDULE START('…') EVERY(INTERVAL interval) AS sql;``

        Optional PROPERTIES clause appended before AS when properties dict is
        non-empty.
        """
        name: str = data["name"]
        sql: str = data["sql"]
        database: str = data.get("database", "")
        schedule_type: str = data.get("schedule_type", "once")
        interval: str | None = data.get("interval")
        start_time: str | None = data.get("start_time")
        properties: dict[str, str] = data.get("properties", {})

        # Build the SUBMIT TASK statement
        parts: list[str] = ["SUBMIT TASK"]

        # Database-qualify if provided
        if database:
            parts.append(f"`{database}`.`{name}`")
        else:
            parts.append(f"`{name}`")

        # Schedule clause
        if schedule_type == "periodic" and interval:
            if start_time:
                parts.append(f"SCHEDULE START('{start_time}') EVERY(INTERVAL {interval})")
            else:
                parts.append(f"SCHEDULE EVERY(INTERVAL {interval})")

        # Properties clause
        if properties:
            props_str = ", ".join(
                f"'{k}' = '{v}'" for k, v in properties.items()
            )
            parts.append(f"PROPERTIES ({props_str})")

        # The task body
        parts.append(f"AS {sql}")

        submit_sql = " ".join(parts)
        # The statement carries the body SQL, never a credential; log the task
        # name only so credentials in a body can never reach the log.
        log.info("Submitting task %s", name)

        try:
            async with conn.cursor() as cur:
                await cur.execute(submit_sql)
        except Exception as exc:
            log.error("SUBMIT TASK %s failed: %s", name, exc)
            raise
        return {"success": True, "task_name": name, "sql": submit_sql}

    async def suspend_task(self, conn: TaskConnection, name: str) -> dict:
        """Suspend (pause) a running periodic task."""
        async with conn.cursor() as cur:
            await cur.execute(f"ALTER TASK `{name}` SUSPEND")
        return {"success": True, "task_name": name, "action": "suspended"}

    async def resume_task(self, conn: TaskConnection, name: str) -> dict:
        """Resume a suspended periodic task."""
        async with conn.cursor() as cur:
            await cur.execute(f"ALTER TASK `{name}` RESUME")
        return {"success": True, "task_name": name, "action": "resumed"}

    async def drop_task(self, conn: TaskConnection, name: str, force: bool = False) -> dict:
        """Drop a task.  When *force* is True, uses IF EXISTS + FORCE."""
        drop_sql = (
            f"DROP TASK IF EXISTS `{name}` FORCE" if force else f"DROP TASK `{name}`"
        )

        async with conn.cursor() as cur:
            await cur.execute(drop_sql)
        return {"success": True, "task_name": name, "action": "dropped"}

    # ── Task runs ───────────────────────────────────────────────

    async def list_task_runs(
        self, conn: TaskConnection, task_name: str
    ) -> list[TaskRunResponse]:
        """List runs for a task visible to the caller."""
        async with _dict_cursor(conn) as cur:
            await cur.execute(
                "SELECT * FROM information_schema.task_runs "
                "WHERE TASK_NAME = %s "
                "ORDER BY CREATE_TIME DESC",
                (task_name,),
            )
            rows = await cur.fetchall()
            return [self._row_to_run(r) for r in rows]


# Singleton — holds no connection; every call receives one.
task_service = TaskService()
