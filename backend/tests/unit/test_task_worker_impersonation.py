"""The unattended task worker must execute as the stored owner, never itself."""

from contextlib import asynccontextmanager
from datetime import datetime

import pytest

from app.common.identifiers import InvalidIdentifierError
from app.modules.task_orchestration import execution as execution_module
from app.modules.task_orchestration.credentials import CredentialUnavailable
from app.modules.task_orchestration.execution import (
    DelegateExecutor,
    ExecutionResult,
    TaskSpec,
)


class _Cursor:
    def __init__(self, statements: list[str], *, confirmed_user: str = "bob@%") -> None:
        self.statements = statements
        self.confirmed_user = confirmed_user
        self.last = ""
        self.current_role = "analyst"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, statement: str, params=None):
        self.last = statement
        self.statements.append(statement)
        if statement.startswith("SET ROLE "):
            self.current_role = statement.removeprefix("SET ROLE ")

    async def fetchone(self):
        if "CURRENT_USER" in self.last:
            return {"nova_effective_user": self.confirmed_user}
        if "CURRENT_ROLE" in self.last:
            return {"nova_active_role": self.current_role}
        if "nova_when" in self.last:
            return {"nova_when": 1}
        return None


class _Conn:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self, *_):
        return self._cursor


def _install_conn(monkeypatch, *, confirmed_user: str = "bob@%"):
    statements: list[str] = []
    logins: list[tuple[str, str, str | None]] = []
    cursor = _Cursor(statements, confirmed_user=confirmed_user)

    @asynccontextmanager
    async def user_conn(user, password, database=None):
        logins.append((user, password, database))
        yield _Conn(cursor)

    @asynccontextmanager
    async def system_conn():
        yield _Conn(cursor)

    monkeypatch.setattr(execution_module.db, "user_conn", user_conn)
    monkeypatch.setattr(execution_module.db, "system_conn", system_conn)
    return statements, logins


@pytest.mark.asyncio
async def test_impersonation_precedes_role_when_and_submit(monkeypatch):
    statements, logins = _install_conn(monkeypatch)
    executor = DelegateExecutor(
        None, impersonation_user="nova_task_worker", impersonation_password="private-secret"
    )
    spec = TaskSpec("daily_load", "INSERT INTO x SELECT 1", "warehouse", "analyst")

    assert await executor.evaluate_when("1 = 1", spec=spec, owner="bob")

    async def no_watermark(*_):
        return None

    async def completed(*_, **__):
        return ExecutionResult(query_id="q1", state="FINISHED")

    monkeypatch.setattr(executor, "_latest_create_time", no_watermark)
    monkeypatch.setattr(executor, "_await_completion", completed)
    assert (await executor.execute(spec, "bob")).state == "FINISHED"

    assert logins == [
        ("nova_task_worker", "private-secret", None),
        ("nova_task_worker", "private-secret", None),
    ]
    assert statements[:6] == [
        "EXECUTE AS 'bob'@'%' WITH NO REVERT",
        "SELECT CURRENT_USER() AS nova_effective_user",
        "USE `warehouse`",
        "SET ROLE analyst",
        "SELECT CURRENT_ROLE() AS nova_active_role",
        "SELECT (1 = 1) AS nova_when",
    ]
    assert statements[-1] == "SUBMIT TASK `warehouse`.`daily_load` AS INSERT INTO x SELECT 1"
    assert "private-secret" not in str(statements)


@pytest.mark.asyncio
async def test_worker_role_activates_before_impersonation(monkeypatch):
    statements, _ = _install_conn(monkeypatch)
    executor = DelegateExecutor(
        None,
        impersonation_user="nova_task_worker",
        impersonation_password="private-secret",
        impersonation_role="task_impersonator",
    )

    assert await executor.evaluate_when(
        "1 = 1", spec=TaskSpec("daily_load", "SELECT 1", "warehouse", "analyst"),
        owner="bob",
    )
    assert statements[:4] == [
        "SET ROLE task_impersonator",
        "SELECT CURRENT_ROLE() AS nova_active_role",
        "EXECUTE AS 'bob'@'%' WITH NO REVERT",
        "SELECT CURRENT_USER() AS nova_effective_user",
    ]


@pytest.mark.asyncio
async def test_impersonation_identity_mismatch_stops_before_owner_sql(monkeypatch):
    statements, _ = _install_conn(monkeypatch, confirmed_user="nova_task_worker@%")
    executor = DelegateExecutor(
        None, impersonation_user="nova_task_worker", impersonation_password="private-secret"
    )

    with pytest.raises(CredentialUnavailable, match="did not confirm"):
        await executor.evaluate_when(
            "1 = 1", spec=TaskSpec("daily_load", "SELECT 1", "warehouse"), owner="bob"
        )

    assert statements == [
        "EXECUTE AS 'bob'@'%' WITH NO REVERT",
        "SELECT CURRENT_USER() AS nova_effective_user",
    ]


@pytest.mark.asyncio
async def test_invalid_owner_never_opens_worker_connection(monkeypatch):
    _, logins = _install_conn(monkeypatch)
    executor = DelegateExecutor(
        None, impersonation_user="nova_task_worker", impersonation_password="private-secret"
    )

    with pytest.raises(InvalidIdentifierError):
        await executor.evaluate_when(
            "1 = 1", spec=TaskSpec("daily_load", "SELECT 1", "warehouse"), owner="x' OR 1=1"
        )
    assert logins == []


@pytest.mark.asyncio
async def test_worker_login_error_does_not_expose_secret(monkeypatch):
    @asynccontextmanager
    async def failing_login(*_):
        raise RuntimeError("private-secret leaked by connector")
        yield  # pragma: no cover

    monkeypatch.setattr(execution_module.db, "user_conn", failing_login)
    executor = DelegateExecutor(
        None, impersonation_user="nova_task_worker", impersonation_password="private-secret"
    )
    with pytest.raises(CredentialUnavailable) as error:
        await executor.evaluate_when(
            "1 = 1", spec=TaskSpec("daily_load", "SELECT 1", "warehouse"), owner="bob"
        )
    assert "private-secret" not in str(error.value)
    assert error.value.__cause__ is None


def test_worker_requires_dedicated_account_pair():
    with pytest.raises(ValueError, match="both"):
        DelegateExecutor(None, impersonation_user="nova_task_worker")
    with pytest.raises(ValueError, match="dedicated"):
        DelegateExecutor(None, impersonation_user="root", impersonation_password="pw")


@pytest.mark.asyncio
async def test_same_second_native_run_is_not_lost_or_confused():
    created = datetime(2026, 9, 23, 12, 0, 0)

    class RowsCursor(_Cursor):
        rows: list[dict]

        async def fetchall(self):
            return self.rows

    cursor = RowsCursor([])
    conn = _Conn(cursor)
    executor = DelegateExecutor(
        None, impersonation_user="nova_task_worker", impersonation_password="private-secret"
    )
    cursor.rows = [
        {"QUERY_ID": "old-1", "CREATE_TIME": created},
        {"QUERY_ID": "old-2", "CREATE_TIME": created},
    ]
    watermark = await executor._latest_create_time(conn, "daily_load")
    assert watermark is not None
    assert watermark.query_ids == frozenset({"old-1", "old-2"})

    cursor.rows = [
        {"QUERY_ID": "old-2", "CREATE_TIME": created, "STATE": "FINISHED"},
        {"QUERY_ID": "new-1", "CREATE_TIME": created, "STATE": "RUNNING"},
    ]
    found = await executor._newest_run_after(conn, "daily_load", watermark)
    assert found is not None
    assert found.query_id == "new-1"


@pytest.mark.asyncio
async def test_native_poll_releases_system_connection_before_heartbeat(monkeypatch):
    in_use = 0
    poll_count = 0

    @asynccontextmanager
    async def system_conn():
        nonlocal in_use
        in_use += 1
        try:
            yield object()
        finally:
            in_use -= 1

    async def poll(*_):
        nonlocal poll_count
        poll_count += 1
        return ExecutionResult(query_id="q1", state="RUNNING" if poll_count == 1 else "FINISHED")

    async def heartbeat():
        assert in_use == 0, "metadata writes must not wait behind a held poll connection"

    monkeypatch.setattr(execution_module.db, "system_conn", system_conn)
    executor = DelegateExecutor(
        None,
        impersonation_user="nova_task_worker",
        impersonation_password="private-secret",
        poll_interval=0,
    )
    monkeypatch.setattr(executor, "_newest_run_after", poll)

    result = await executor._await_completion(
        None, "daily_load", watermark=None, heartbeat=heartbeat
    )
    assert result.state == "FINISHED"
    assert in_use == 0
    assert poll_count == 2
