"""Per-call-site regression tests for the data-egress guard (NOVA-86, AC2).

The shared guard is unit-tested in ``test_sql_guard_egress.py``. These tests pin
the *wiring*: each user-facing path must consult the guard **before** it hands
the statement to the engine. A test on the guard function alone stays green if a
future refactor removes the ``guard_sql`` call from a router, which is exactly
the regression AC2 exists to prevent.

Each test stubs the engine boundary and asserts it was never reached.
"""

import pytest

from app.core.exceptions import ForbiddenSQLError
from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService

#: One representative payload per clause family. The shared guard's own module
#: covers the full matrix; here the point is that the path refuses at all.
EGRESS_PAYLOADS = [
    "SELECT 1 INTO OUTFILE 's3://b/x' FORMAT AS CSV",
    "SELECT 1 INTO FILES('path'='s3://b/x')",
    "SELECT 1 INTO @stage1.x.csv",
    "SELECT 1 INTO@stage1.x.csv",
]


class RecordingRepo:
    """Stub repository that records the SQL handed to the engine."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_as_user(self, sql, **kwargs):
        self.calls.append(sql)
        return QueryResult(executed_sql=sql, columns=["v"], rows=[[1]], row_count=1)


@pytest.fixture
def query_service(monkeypatch):
    """A QueryService with a recording repo and no real audit/decrypt."""
    svc = QueryService()
    repo = RecordingRepo()
    svc._repo = repo

    async def no_audit(**kwargs):
        return None

    monkeypatch.setattr("app.modules.query.service.write_audit_log", no_audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    return svc, repo


# ── query/service.py:217 — POST /api/v1/query/execute ───────────────────────


@pytest.mark.parametrize("sql", EGRESS_PAYLOADS)
async def test_query_execute_refuses_egress_before_the_engine(query_service, sql):
    svc, repo = query_service
    with pytest.raises(ForbiddenSQLError):
        await svc.execute(sql=sql, username="analyst", encrypted_password="enc")
    assert repo.calls == [], "the engine was reached with an egress statement"


@pytest.mark.parametrize("sql", EGRESS_PAYLOADS)
async def test_query_execute_refuses_egress_even_when_destructive_is_confirmed(query_service, sql):
    # ``confirm_destructive`` is for DROP/TRUNCATE, not for consenting to egress.
    svc, repo = query_service
    with pytest.raises(ForbiddenSQLError):
        await svc.execute(
            sql=sql,
            username="analyst",
            encrypted_password="enc",
            confirm_destructive=True,
        )
    assert repo.calls == []


async def test_query_execute_still_runs_an_ordinary_select(query_service):
    # The over-block control: a plain SELECT must still reach the engine.
    svc, repo = query_service
    await svc.execute(sql="SELECT 1", username="analyst", encrypted_password="enc")
    assert repo.calls == ["SELECT 1"]


async def test_query_explain_refuses_egress_before_the_engine(query_service):
    # ``query/service.py:949`` — the EXPLAIN path consults the same guard.
    svc, repo = query_service
    with pytest.raises(ForbiddenSQLError):
        await svc.explain(
            sql="SELECT 1 INTO OUTFILE 's3://b/x'",
            username="analyst",
            encrypted_password="enc",
        )
    assert repo.calls == []


# ── views/router.py — view create / MV create / drop ────────────────────────


class RecordingConnection:
    """Stub for the caller's StarRocks connection.

    Since NOVA-89 the DDL routers run on the caller's connection, not the root
    pool, so this is the engine boundary the tests must pin. ``cursor()``
    returns a context manager whose ``execute`` records the statement.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def cursor(self):
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


@pytest.fixture
def views_conn():
    return RecordingConnection()


async def test_view_create_refuses_egress_before_the_engine(views_conn):
    from app.modules.views.router import CreateViewRequest, create_view

    req = CreateViewRequest(
        database="db1",
        view_name="v1",
        select_sql="SELECT 1 INTO OUTFILE 's3://b/x'",
    )
    with pytest.raises(ForbiddenSQLError):
        await create_view(req, user={"username": "analyst"}, conn=views_conn)
    assert views_conn.calls == []


async def test_materialized_view_create_refuses_egress_before_the_engine(views_conn):
    from app.modules.views.router import (
        CreateMaterializedViewRequest,
        create_materialized_view,
    )

    req = CreateMaterializedViewRequest(
        database="db1",
        mv_name="mv1",
        select_sql="SELECT * FROM t INTO @stage1.x.csv",
    )
    with pytest.raises(ForbiddenSQLError):
        await create_materialized_view(req, user={"username": "analyst"}, conn=views_conn)
    assert views_conn.calls == []


async def test_view_create_still_runs_an_ordinary_select(views_conn):
    from app.modules.views.router import CreateViewRequest, create_view

    req = CreateViewRequest(database="db1", view_name="v1", select_sql="SELECT 1")
    await create_view(req, user={"username": "analyst"}, conn=views_conn)
    assert len(views_conn.calls) == 1
    assert "SELECT 1" in views_conn.calls[0]


# ── tables/router.py — table DDL carries the same guard ─────────────────────


@pytest.fixture
def tables_conn():
    return RecordingConnection()


async def test_table_drop_refuses_egress_comment_obfuscation(tables_conn):
    # The DDL paths build the statement themselves. A table name that smuggles
    # an egress clause is refused before the engine — since NOVA-89 by the
    # identifier allow-list, which is also a ``ForbiddenSQLError``.
    from app.modules.tables.router import DropTableRequest, drop_table

    with pytest.raises(ForbiddenSQLError):
        await drop_table(
            DropTableRequest(database="db1", table="t INTO @stage1"),
            user={"username": "analyst"},
            conn=tables_conn,
        )
    assert tables_conn.calls == []


async def test_table_drop_still_runs_for_an_ordinary_table(tables_conn):
    from app.modules.tables.router import DropTableRequest, drop_table

    await drop_table(
        DropTableRequest(database="db1", table="t1"),
        user={"username": "analyst"},
        conn=tables_conn,
    )
    assert len(tables_conn.calls) == 1
