"""Notebook write-back to Postgres/Snowflake: Ranger first, write credential, bound values."""

from __future__ import annotations

import pytest

from app.modules.catalog import writeback
from app.modules.catalog.sources import SOURCES
from app.modules.catalog.sources.base import Credentials
from app.modules.catalog.sources.writes import (
    WriteRefused,
    WriteRequest,
    WriteResult,
    match_columns,
)

CATALOG = {"name": "pg_shop", "kind": "foreign", "dropped_at": None,
           "properties": {"nova.connection": "shop_pg"}}
CONNECTION = {"name": "shop_pg", "connector_type": "postgres", "host": "db", "port": 5432,
              "database_name": "shop", "options": {}, "secret_ref": "nova://shop_pg"}


class Recorder:
    def __init__(self) -> None:
        self.requests: list[WriteRequest] = []
        self.audits: list[dict] = []
        self.access: list[tuple[str, str, str, str | None]] = []
        self.allowed: set[str] = {"insert", "update", "delete"}
        self.credentials: Credentials | None = Credentials("writer", "s3cret-pw")


@pytest.fixture
def env(monkeypatch):
    rec = Recorder()

    async def get_catalog(name):
        return CATALOG if name == "pg_shop" else None

    async def get_connection(name):
        return CONNECTION if name == "shop_pg" else None

    async def write_credentials(connection):
        return rec.credentials

    async def role_allows(*, principal, active_role, requests):
        for request in requests:
            rec.access.append((request.access, request.catalog, request.database, request.table))
        return [request.access in rec.allowed for request in requests]

    async def write_rows(endpoint, credentials, request):
        rec.requests.append(request)
        return WriteResult(inserted=len(request.rows))

    async def audit(**kwargs):
        rec.audits.append(kwargs)

    from app.modules.access_control.service import access_control_service

    monkeypatch.setattr(writeback.catalog_repo, "get_catalog", get_catalog)
    monkeypatch.setattr(writeback.connection_repo, "get", get_connection)
    monkeypatch.setattr(writeback.connection_service, "write_credentials", write_credentials)
    monkeypatch.setattr(access_control_service, "role_allows", role_allows)
    monkeypatch.setattr(SOURCES["postgres"], "write_rows", write_rows)
    monkeypatch.setattr(writeback, "write_audit_log", audit)
    return rec


async def _write(**kwargs):
    base = {"username": "alice", "active_role": "analyst", "target": "pg_shop.public.orders",
            "columns": ("id", "v"), "rows": [(1, "a"), (2, "b")]}
    base.update(kwargs)
    return await writeback.write(**base)


async def test_an_allowed_append_writes_and_is_audited(env):
    result = await _write()
    assert result.inserted == 2 and env.requests[0].schema == "public"
    assert env.access == [("insert", "pg_shop", "public", "orders")]
    assert env.audits[-1]["status"] == "SUCCESS" and env.audits[-1]["rows_affected"] == 2


async def test_ranger_is_asked_before_the_credential_and_a_refusal_is_audited(env):
    env.allowed = {"insert"}
    with pytest.raises(writeback.WritebackDenied, match="UPDATE"):
        await _write(mode="merge", keys=("id",))
    assert env.requests == [] and env.audits[-1]["status"] == "DENIED"


@pytest.mark.parametrize(
    ("mode", "matched", "not_matched", "needed"),
    [
        ("append", "update", "insert", ("insert",)),
        ("overwrite", "update", "insert", ("delete", "insert")),
        ("merge", "update", "insert", ("insert", "update")),
        ("merge", "delete", "ignore", ("delete",)),
    ],
)
def test_each_mode_needs_its_privileges(mode, matched, not_matched, needed):
    assert writeback.required_access(mode, matched, not_matched) == needed


async def test_without_a_write_credential_the_write_is_refused(env):
    env.credentials = None
    with pytest.raises(WriteRefused, match="no write connection"):
        await _write()


@pytest.mark.parametrize("target", ["nova.sales.orders", "orders", "sales.orders"])
async def test_only_managed_external_catalogs_are_written(env, target):
    with pytest.raises(WriteRefused):
        await _write(target=target)
    assert env.requests == []


async def test_a_driver_error_reaches_the_caller_without_the_credential(env, monkeypatch):
    async def failing(endpoint, credentials, request):
        raise RuntimeError("connection to db failed: password=s3cret-pw rejected\nmore")

    monkeypatch.setattr(SOURCES["postgres"], "write_rows", failing)
    with pytest.raises(WriteRefused) as refused:
        await _write()
    assert "s3cret-pw" not in str(refused.value)
    assert "s3cret-pw" not in str(env.audits[-1]["error_message"])


async def test_too_many_rows_are_refused_before_anything_runs(env, monkeypatch):
    monkeypatch.setattr(writeback.settings, "NOTEBOOK_WRITE_MAX_ROWS", 1)
    with pytest.raises(WriteRefused, match="at most"):
        await _write()
    assert env.access == []


@pytest.mark.parametrize(
    "kwargs",
    [{"mode": "upsert"}, {"mode": "merge"}, {"mode": "merge", "keys": ("missing",)},
     {"columns": ("id", "ID")}, {"when_matched": "replace", "mode": "merge", "keys": ("id",)}],
)
def test_malformed_requests_are_refused(kwargs):
    base = {"schema": "s", "table": "t", "columns": ("id", "v"), "rows": []}
    base.update(kwargs)
    with pytest.raises(WriteRefused):
        WriteRequest(**base)


def test_columns_match_the_target_spelling():
    assert match_columns(["ID", "name"], ["id", "Name", "x"]) == ["id", "Name"]
    with pytest.raises(WriteRefused, match="nope"):
        match_columns(["nope"], ["id"])


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    async def fetchall(self):
        return [("id",), ("v",)]

    async def fetchone(self):
        return self.conn.scalars.pop(0) if self.conn.scalars else (0,)


class FakeCopy:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def write_row(self, row):
        self.conn.copied.append(tuple(row))


class FakeCopyCursor:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def copy(self, statement):
        self.conn.statements.append(statement.as_string(None))
        return FakeCopy(self.conn)


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list = []
        self.copied: list = []
        self.scalars = [(160000,), (1,), (2,)]  # version, matched, total; duplicate→None first

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def transaction(self):
        return self

    def cursor(self):
        return FakeCopyCursor(self)

    async def execute(self, statement, params=None):
        text = statement if isinstance(statement, str) else statement.as_string(None)
        self.statements.append(text)
        self.params.append(params)
        if "HAVING COUNT(*) > 1" in text:
            self.scalars.insert(0, None)
        return FakeCursor(self)


async def test_postgres_merge_loads_with_copy_and_binds_no_values(monkeypatch):
    import psycopg

    connection = FakeConnection()

    async def connect(**kwargs):
        assert kwargs["password"] == "s3cret-pw"
        return connection

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    source = SOURCES["postgres"]
    request = WriteRequest(schema="public", table="orders", columns=("ID", "v"),
                           rows=[(1, "x'); DROP TABLE orders; --")], mode="merge", keys=("id",))
    from app.modules.catalog.sources.base import Endpoint

    result = await source.write_rows(Endpoint("db", 5432, "shop"), Credentials("w", "s3cret-pw"),
                                     request)
    text = "\n".join(connection.statements)
    assert 'COPY "nova_writeback_load" ("id", "v") FROM STDIN' in text
    assert 'MERGE INTO "public"."orders" t USING "nova_writeback_load" s ON t."id" = s."id"' in text
    assert "DROP TABLE" not in text  # values only travel through COPY
    assert connection.copied == [(1, "x'); DROP TABLE orders; --")]
    assert result.updated == 1 and result.inserted == 1
