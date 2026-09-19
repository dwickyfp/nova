"""NOVA-101 — the second-order SQLi sinks PR #92 did not cover.

Two pre-existing sinks interpolate caller input into SQL as raw f-strings:

* ``tasks/service.py`` builds ``SUBMIT TASK`` from ``interval``, ``start_time``
  and ``properties``. The driver runs trailing ``;``-separated statements when
  statement 1 parses, so a payload in any of them executes a second statement
  on the caller's connection.
* the ``SET ROLE`` sinks (``objects/repository.py``, ``query/repository.py``,
  ``ml_engine/service.py``) strip only backticks/quotes and then interpolate a
  caller-supplied ``role``.

The bar (issue acceptance criteria):

* **AC1** — a ``;``-bearing ``interval``, ``start_time``, ``properties``
  key/value, or ``role`` is refused *before any SQL is built*.
* **AC2** — the exact payloads no longer execute a second statement; asserted
  on the assembled SQL / cursor calls, not just an exception.
* **AC3** — legitimate scheduling and ``SET ROLE`` still assemble and execute.

Unit-level: no engine, no network. The connection records every statement it
is handed, which is what lets the tests assert "nothing was executed" for the
negative cases and "the legitimate statement executed" for the positive ones.
"""

from __future__ import annotations

import pytest

from app.common.identifiers import (
    check_identifier,
    check_interval,
    check_start_time,
)
from app.core.exceptions import ForbiddenSQLError
from app.modules.objects.repository import ObjectRepository
from app.modules.tasks.service import TaskService

#: Payloads confirmed by QA on live StarRocks 4.1.4. Each closes the clause
#: it is injected into and opens a second statement that sets a marker the
#: harness would observe if it ran.
INJECTED_INTERVAL = "1 HOUR) AS INSERT INTO qa_nova89.t SELECT 1; SET @m1 = 77; --"
INJECTED_START_TIME = "2026-01-01') EVERY(INTERVAL 1 HOUR); SET @m2 = 77; --"
INJECTED_PROPERTY_KEY = "replication_num'; SET @m3 = 77; --"
INJECTED_PROPERTY_VALUE = "1'; SET @m4 = 77; --"
INJECTED_ROLE = "qa_probe_role; SET @role_probe3 = 4242; --"

#: The marker string every injected payload carries. If this appears in an
#: executed statement the injection survived.
INJECTION_MARKER = "SET @"


class RecordingConnection:
    """A caller connection that records every statement executed on it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def cursor(self, cursor_class=None):
        return _RecordingCursor(self)


class _RecordingCursor:
    def __init__(self, conn: RecordingConnection) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def execute(self, sql: str, params=None) -> None:
        self._conn.calls.append(sql)

    description = None

    async def fetchall(self) -> list:
        return []

    async def fetchone(self):
        return None


@pytest.fixture
def conn() -> RecordingConnection:
    return RecordingConnection()


@pytest.fixture
def service() -> TaskService:
    return TaskService()


@pytest.fixture
def repo() -> ObjectRepository:
    return ObjectRepository()


def _task_payload(**overrides) -> dict:
    payload = {
        "name": "t1",
        "sql": "INSERT INTO qa_nova89.t SELECT 1",
        "database": "qa_nova89",
        "schedule_type": "periodic",
        "interval": "1 HOUR",
        "start_time": None,
        "properties": {},
    }
    payload.update(overrides)
    return payload


# ── AC1/AC2: tasks/service.py — interval ────────────────────────────────────


class TestTaskIntervalIsAllowListed:
    async def test_injected_interval_is_refused_before_sql(self, service, conn):
        with pytest.raises(ForbiddenSQLError):
            await service.create_task(conn, _task_payload(interval=INJECTED_INTERVAL))
        assert conn.calls == [], "no statement may reach the connection"

    @pytest.mark.parametrize(
        "interval",
        [
            "1 HOUR); DROP TABLE t; --",
            "HOUR",
            "1",
            "1 HOURS",
            "-1 HOUR",
            "1 HOUR;",
            "1 HOUR AS SELECT 1",
        ],
    )
    async def test_malformed_intervals_are_refused(self, service, conn, interval):
        with pytest.raises(ForbiddenSQLError):
            await service.create_task(conn, _task_payload(interval=interval))
        assert conn.calls == []

    @pytest.mark.parametrize(
        "interval",
        ["1 HOUR", "30 MINUTE", "2 DAY", "1 WEEK", "1 MONTH", "1 YEAR", "5 SECOND"],
    )
    async def test_valid_intervals_still_assemble(self, service, conn, interval):
        result = await service.create_task(conn, _task_payload(interval=interval))
        assert f"EVERY(INTERVAL {interval})" in result["sql"]
        # The session switches to the task database first so an unqualified
        # body resolves; the submit is the last statement.
        assert conn.calls == ["USE `qa_nova89`", result["sql"]]

    async def test_injection_marker_never_reaches_the_connection(self, service, conn):
        with pytest.raises(ForbiddenSQLError):
            await service.create_task(conn, _task_payload(interval=INJECTED_INTERVAL))
        assert all(INJECTION_MARKER not in c for c in conn.calls)


# ── AC1/AC2: tasks/service.py — start_time ──────────────────────────────────


class TestTaskStartTimeIsAllowListed:
    async def test_injected_start_time_is_refused_before_sql(self, service, conn):
        with pytest.raises(ForbiddenSQLError):
            await service.create_task(
                conn, _task_payload(start_time=INJECTED_START_TIME)
            )
        assert conn.calls == []

    @pytest.mark.parametrize(
        "start_time",
        [
            "2026-01-01') EVERY(INTERVAL 1 HOUR); SET @m2 = 77; --",
            "2026-01-01'",
            "not a datetime",
            "2026-13-45 99:99:99",
        ],
    )
    async def test_malformed_start_times_are_refused(self, service, conn, start_time):
        with pytest.raises(ForbiddenSQLError):
            await service.create_task(conn, _task_payload(start_time=start_time))
        assert conn.calls == []

    @pytest.mark.parametrize(
        "start_time",
        ["2026-01-01 08:00:00", "2026-01-01T08:00:00", "2026-01-01 08:00"],
    )
    async def test_valid_start_times_still_assemble(self, service, conn, start_time):
        result = await service.create_task(conn, _task_payload(start_time=start_time))
        assert f"SCHEDULE START('{start_time}')" in result["sql"]
        # The session switches to the task database first so an unqualified
        # body resolves; the submit is the last statement.
        assert conn.calls == ["USE `qa_nova89`", result["sql"]]


# ── AC1/AC2: tasks/service.py — properties ──────────────────────────────────


class TestTaskPropertiesAreAllowListed:
    async def test_injected_property_key_is_refused_before_sql(self, service, conn):
        with pytest.raises(ForbiddenSQLError):
            await service.create_task(
                conn, _task_payload(properties={INJECTED_PROPERTY_KEY: "1"})
            )
        assert conn.calls == []

    async def test_injected_property_value_is_refused_before_sql(self, service, conn):
        with pytest.raises(ForbiddenSQLError):
            await service.create_task(
                conn, _task_payload(properties={"replication_num": INJECTED_PROPERTY_VALUE})
            )
        assert conn.calls == []

    async def test_injection_marker_never_reaches_the_connection(self, service, conn):
        for properties in (
            {INJECTED_PROPERTY_KEY: "1"},
            {"replication_num": INJECTED_PROPERTY_VALUE},
        ):
            with pytest.raises(ForbiddenSQLError):
                await service.create_task(conn, _task_payload(properties=properties))
        assert all(INJECTION_MARKER not in c for c in conn.calls)

    async def test_valid_properties_still_assemble(self, service, conn):
        result = await service.create_task(
            conn,
            _task_payload(properties={"replication_num": "3", "storage_medium": "SSD"}),
        )
        assert 'PROPERTIES ("replication_num" = "3", "storage_medium" = "SSD")' in result["sql"]
        # The session switches to the task database first so an unqualified
        # body resolves; the submit is the last statement.
        assert conn.calls == ["USE `qa_nova89`", result["sql"]]


# ── AC1/AC2/AC3: SET ROLE identifier allow-list ─────────────────────────────


class TestSetRoleIsAllowListed:
    async def test_injected_role_is_refused_before_any_sql(self, repo, conn, monkeypatch):
        monkeypatch.setattr(
            "app.modules.objects.repository.decrypt_password", lambda _: "pw"
        )
        monkeypatch.setattr(
            "app.modules.objects.repository.db.user_conn", lambda *a, **k: _Ctx(conn)
        )
        with pytest.raises(ForbiddenSQLError):
            await repo.list_databases(
                username="u", encrypted_password="enc", role=INJECTED_ROLE
            )
        assert conn.calls == [], "SET ROLE must not be built from a payload"

    async def test_valid_role_still_executes_set_role(self, repo, conn, monkeypatch):
        monkeypatch.setattr(
            "app.modules.objects.repository.decrypt_password", lambda _: "pw"
        )
        monkeypatch.setattr(
            "app.modules.objects.repository.db.user_conn", lambda *a, **k: _Ctx(conn)
        )
        await repo.list_databases(username="u", encrypted_password="enc", role="analyst")
        assert conn.calls[0] == "SET ROLE analyst"
        assert "SET ROLE" in conn.calls[0]

    async def test_none_role_executes_no_set_role(self, repo, conn, monkeypatch):
        monkeypatch.setattr(
            "app.modules.objects.repository.decrypt_password", lambda _: "pw"
        )
        monkeypatch.setattr(
            "app.modules.objects.repository.db.user_conn", lambda *a, **k: _Ctx(conn)
        )
        await repo.list_databases(username="u", encrypted_password="enc", role=None)
        assert all("SET ROLE" not in c for c in conn.calls)


class _Ctx:
    """Minimal async context manager standing in for ``db.user_conn``."""

    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc_info) -> None:
        return None


# ── Unit-level: the new allow-list helpers ──────────────────────────────────


class TestIdentifierHelperReuse:
    def test_check_interval_accepts_the_documented_units(self):
        for value in ("1 SECOND", "1 MINUTE", "1 HOUR", "1 DAY", "1 WEEK", "1 MONTH", "1 YEAR"):
            assert check_interval(value) == value

    def test_check_interval_rejects_a_semicolon(self):
        with pytest.raises(ForbiddenSQLError):
            check_interval(INJECTED_INTERVAL)

    def test_check_start_time_accepts_iso_and_space_forms(self):
        assert check_start_time("2026-01-01 08:00:00") == "2026-01-01 08:00:00"
        assert check_start_time("2026-01-01T08:00:00") == "2026-01-01T08:00:00"

    def test_check_start_time_rejects_a_quote(self):
        with pytest.raises(ForbiddenSQLError):
            check_start_time(INJECTED_START_TIME)

    def test_check_identifier_rejects_the_role_payload(self):
        with pytest.raises(ForbiddenSQLError):
            check_identifier(INJECTED_ROLE, field="role")
