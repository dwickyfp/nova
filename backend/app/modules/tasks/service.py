"""Task Manager service — submit, schedule, and monitor StarRocks tasks.

Uses the asyncmy direct-connection pattern (root system connection) with
try/finally for every operation, matching the stages service convention.
"""

from __future__ import annotations

import json
import logging

import asyncmy
import asyncmy.cursors

from app.core.config import settings

from .schemas import (
    TaskResponse,
    TaskRunResponse,
)

log = logging.getLogger(__name__)


class TaskService:
    """Business logic for StarRocks task management."""

    # ── DB helpers ──────────────────────────────────────────────

    @staticmethod
    async def _connect() -> asyncmy.Connection:
        """Create a direct asyncmy connection to StarRocks (system admin)."""
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=settings.STARROCKS_ROOT_USER,
            password=settings.STARROCKS_ROOT_PASSWORD,
            autocommit=True,
        )

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

    async def list_tasks(self) -> list[TaskResponse]:
        """List all tasks from information_schema.tasks."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute("SELECT * FROM information_schema.tasks")
                rows = await cur.fetchall()
                return [self._row_to_task(r) for r in rows]
        finally:
            conn.close()

    async def get_task(self, name: str) -> TaskResponse | None:
        """Get a single task by name."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM information_schema.tasks "
                    "WHERE TASK_NAME = %s",
                    (name,),
                )
                row = await cur.fetchone()
                return self._row_to_task(row) if row else None
        finally:
            conn.close()

    async def create_task(self, data: dict) -> dict:
        """Build and execute a SUBMIT TASK statement.

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
        log.info("Submitting task: %s", submit_sql)

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(submit_sql)
            return {"success": True, "task_name": name, "sql": submit_sql}
        except Exception as exc:
            log.error("SUBMIT TASK failed: %s", exc)
            raise
        finally:
            conn.close()

    async def suspend_task(self, name: str) -> dict:
        """Suspend (pause) a running periodic task."""
        alter_sql = f"ALTER TASK `{name}` SUSPEND"
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(alter_sql)
            return {"success": True, "task_name": name, "action": "suspended"}
        finally:
            conn.close()

    async def resume_task(self, name: str) -> dict:
        """Resume a suspended periodic task."""
        alter_sql = f"ALTER TASK `{name}` RESUME"
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(alter_sql)
            return {"success": True, "task_name": name, "action": "resumed"}
        finally:
            conn.close()

    async def drop_task(self, name: str, force: bool = False) -> dict:
        """Drop a task.  When *force* is True, uses IF EXISTS + FORCE."""
        if force:
            drop_sql = f"DROP TASK IF EXISTS `{name}` FORCE"
        else:
            drop_sql = f"DROP TASK `{name}`"

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(drop_sql)
            return {"success": True, "task_name": name, "action": "dropped"}
        finally:
            conn.close()

    # ── Task runs ───────────────────────────────────────────────

    async def list_task_runs(self, task_name: str) -> list[TaskRunResponse]:
        """List runs for a task from information_schema.task_runs."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM information_schema.task_runs "
                    "WHERE TASK_NAME = %s "
                    "ORDER BY CREATE_TIME DESC",
                    (task_name,),
                )
                rows = await cur.fetchall()
                return [self._row_to_run(r) for r in rows]
        finally:
            conn.close()


# Singleton
task_service = TaskService()
