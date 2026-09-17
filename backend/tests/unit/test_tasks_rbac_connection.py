"""Unit tests proving ``TaskService`` runs on an injected connection.

The security defect was that every method opened its own **root** connection, so
the engine's privilege filter was bypassed. These tests pin the fix without a
database: a fake connection records what ran, and the assertion is that the
service used *that* connection — the caller's — and opened none of its own.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from app.modules.tasks.service import TaskService, task_service

SERVICE_PATH = Path(inspect.getfile(TaskService))

TASK_ROW = {
    "TASK_NAME": "etl_hourly",
    "DATABASE": "warehouse",
    "STATE": "ACTIVE",
    "SCHEDULE": "Every 1 HOUR",
    "DEFINITION": "INSERT INTO agg SELECT 1",
    "CREATE_TIME": "2026-09-17 10:00:00",
    "PROPERTIES": '{"priority": "1"}',
}

RUN_ROW = {
    "TASK_NAME": "etl_hourly",
    "CREATE_TIME": "2026-09-17 10:00:00",
    "FINISH_TIME": "2026-09-17 10:00:05",
    "STATE": "SUCCESS",
    "ERROR_MESSAGE": None,
    "PROPERTIES": None,
}


class FakeCursor:
    def __init__(self, owner: FakeConnection) -> None:
        self._owner = owner

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def execute(self, sql: str, params: Any = None) -> None:
        self._owner.executed.append((sql, params))

    async def fetchall(self) -> list[dict]:
        return list(self._owner.rows)

    async def fetchone(self) -> dict | None:
        return self._owner.rows[0] if self._owner.rows else None


class FakeConnection:
    """Records executed statements; returns canned rows. Never touches a network."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.executed: list[tuple[str, Any]] = []
        self.closed = False

    def cursor(self, _cursor_cls: Any = None) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def conn() -> FakeConnection:
    return FakeConnection()


class TestInjectedConnectionIsUsed:
    """Each method must run on the connection it was handed."""

    async def test_list_tasks_uses_injected_connection(self, conn):
        conn.rows = [TASK_ROW]
        tasks = await task_service.list_tasks(conn)
        assert [t.name for t in tasks] == ["etl_hourly"]
        assert len(conn.executed) == 1
        assert "information_schema.tasks" in conn.executed[0][0]

    async def test_get_task_uses_injected_connection(self, conn):
        conn.rows = [TASK_ROW]
        task = await task_service.get_task(conn, "etl_hourly")
        assert task is not None and task.name == "etl_hourly"
        sql, params = conn.executed[0]
        assert "WHERE TASK_NAME = %s" in sql
        assert params == ("etl_hourly",)

    async def test_get_task_returns_none_when_invisible(self, conn):
        conn.rows = []
        assert await task_service.get_task(conn, "hidden_task") is None

    async def test_create_task_uses_injected_connection(self, conn):
        result = await task_service.create_task(
            conn,
            {"name": "etl_daily", "sql": "INSERT INTO t SELECT 1"},
        )
        assert result["success"] is True
        sql, _ = conn.executed[0]
        assert sql.startswith("SUBMIT TASK")
        assert "etl_daily" in sql

    async def test_suspend_resume_drop_use_injected_connection(self, conn):
        assert (await task_service.suspend_task(conn, "etl_daily"))["action"] == "suspended"
        assert (await task_service.resume_task(conn, "etl_daily"))["action"] == "resumed"
        assert (await task_service.drop_task(conn, "etl_daily"))["action"] == "dropped"
        statements = [sql for sql, _ in conn.executed]
        assert statements == [
            "ALTER TASK `etl_daily` SUSPEND",
            "ALTER TASK `etl_daily` RESUME",
            "DROP TASK `etl_daily`",
        ]

    async def test_list_task_runs_uses_injected_connection(self, conn):
        conn.rows = [RUN_ROW]
        runs = await task_service.list_task_runs(conn, "etl_hourly")
        assert runs[0].state == "SUCCESS"
        sql, params = conn.executed[0]
        assert "information_schema.task_runs" in sql
        assert params == ("etl_hourly",)


class TestCallerConnectionIsNotClosed:
    """The dependency layer owns the connection lifecycle, not the service."""

    async def test_service_does_not_close_the_connection(self, conn):
        conn.rows = [TASK_ROW]
        await task_service.list_tasks(conn)
        await task_service.get_task(conn, "etl_hourly")
        await task_service.list_task_runs(conn, "etl_hourly")
        assert conn.closed is False


class TestNoRootConnectionInSource:
    """No root settings, no ad-hoc connect, no ``_connect`` helper."""

    def test_source_references_no_root_credentials(self):
        source = SERVICE_PATH.read_text()
        assert "STARROCKS_ROOT_USER" not in source
        assert "STARROCKS_ROOT_PASSWORD" not in source
        assert "asyncmy.connect" not in source
        assert "async def _connect" not in source
        assert "def _root_connect" not in source

    def test_no_service_method_builds_its_own_connection(self):
        """Every public method takes the injected connection as its first arg."""
        tree = ast.parse(SERVICE_PATH.read_text())
        service_cls = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "TaskService"
        )
        methods = [
            node for node in service_cls.body
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and not node.name.startswith("_")
        ]
        assert methods, "no public methods found"
        for method in methods:
            args = [a.arg for a in method.args.args]
            assert args[:2] == ["self", "conn"], (
                f"{method.name} signature is {args!r}, expected self, conn first"
            )

    def test_singleton_carries_no_connection_attribute(self):
        assert not any(
            value is not None for value in vars(task_service).values()
        )
