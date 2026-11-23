"""A Stream cannot be shadowed by a table, and a shadowed name is never resolved silently."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.streams import sql as sql_module
from app.modules.streams.sql import StreamSql
from app.sql_frontend.parser import parse_statement

STREAMS = {("shop", "orders_changes")}


def _context(sql: str, database: str | None = "shop"):
    parsed = parse_statement(sql)
    context = SimpleNamespace(
        statements={0: SimpleNamespace(parsed=parsed)}, database=database,
        username="alice", role="finance", security_context_version=1,
        transaction_active=False,
    )
    plan = SimpleNamespace(source_key=0, transaction_intent=None, stream_functions=())
    return plan, context


@pytest.fixture
def streams(monkeypatch):
    handler = StreamSql()
    monkeypatch.setattr(handler, "_stream_names", AsyncMock(return_value=set(STREAMS)))
    monkeypatch.setattr(
        sql_module.source_registry, "captured_tables", AsyncMock(return_value={})
    )
    exists = AsyncMock(return_value=False)
    monkeypatch.setattr(sql_module.stream_service, "engine_object_exists", exists)
    read = AsyncMock(return_value="read")
    monkeypatch.setattr(handler, "_read", read)
    handler.exists, handler.read = exists, read
    return handler


@pytest.mark.parametrize("sql", [
    "CREATE TABLE orders_changes (a INT) DUPLICATE KEY(a) DISTRIBUTED BY HASH(a) BUCKETS 1",
    "CREATE TABLE IF NOT EXISTS shop.ORDERS_CHANGES (a INT)",
    "CREATE TEMPORARY TABLE orders_changes (a INT)",
    "CREATE TABLE `orders_changes` AS SELECT 1 AS a",
    "CREATE TABLE default_catalog.shop.orders_changes LIKE shop.orders",
    "CREATE VIEW orders_changes AS SELECT 1 AS a",
    "CREATE OR REPLACE VIEW shop.orders_changes AS SELECT 1 AS a",
    "CREATE MATERIALIZED VIEW orders_changes REFRESH ASYNC AS SELECT 1 AS a",
    "ALTER TABLE shop.orders RENAME orders_changes",
    "ALTER TABLE orders RENAME `Orders_Changes`",
    "ALTER MATERIALIZED VIEW mv RENAME orders_changes",
    "RECOVER TABLE orders_changes",
])
async def test_creating_or_renaming_onto_a_stream_name_is_refused(streams, sql):
    plan, context = _context(sql)
    result = await streams.intercept(SimpleNamespace(), plan, context)
    assert result is not None and result.error_code == "STREAM_NAME_COLLISION"
    assert "shop.orders_changes" in result.error


@pytest.mark.parametrize("sql,database", [
    ("CREATE TABLE other (a INT)", "shop"),
    ("CREATE TABLE elsewhere.orders_changes (a INT)", "shop"),
    ("CREATE TABLE orders_changes (a INT)", "elsewhere"),
    ("CREATE TABLE hive_cat.shop.orders_changes (a INT)", "shop"),
    ("ALTER TABLE orders RENAME orders_archive", "shop"),
    ("ALTER TABLE orders ADD COLUMN c INT", "shop"),
    ("CREATE VIEW v AS SELECT * FROM orders_changes", "shop"),
])
async def test_other_names_pass(streams, sql, database):
    plan, context = _context(sql, database)
    result = await streams.intercept(SimpleNamespace(), plan, context)
    assert result is None or result == "read"


@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders_changes",
    "INSERT INTO shop.archive SELECT * FROM orders_changes",
    "SELECT * FROM shop.orders o JOIN shop.orders_changes c ON o.id = c.id",
])
async def test_engine_object_with_a_stream_name_is_ambiguous(streams, sql):
    streams.exists.return_value = True
    plan, context = _context(sql)
    result = await streams.intercept(SimpleNamespace(), plan, context)
    assert result.error_code == "STREAM_NAME_AMBIGUOUS"
    assert "both a stream and a table" in result.error
    streams.read.assert_not_awaited()
    streams.exists.assert_awaited_with("shop", "orders_changes")


async def test_a_stream_without_a_twin_is_read(streams):
    plan, context = _context("INSERT INTO shop.archive SELECT * FROM orders_changes")
    assert await streams.intercept(SimpleNamespace(), plan, context) == "read"


async def test_without_streams_nothing_is_looked_up(monkeypatch, streams):
    monkeypatch.setattr(streams, "_stream_names", AsyncMock(return_value=set()))
    plan, context = _context("CREATE TABLE orders_changes (a INT)")
    assert await streams.intercept(SimpleNamespace(), plan, context) is None
    streams.exists.assert_not_awaited()


async def test_create_stream_collides_case_insensitively(monkeypatch):
    from app.modules.streams.service import stream_service

    seen = []

    async def execute(sql, params=None):
        seen.append((sql, params))
        return {"rows": [(1,)]}

    monkeypatch.setattr(stream_service, "_execute", execute)
    assert await stream_service.engine_object_exists("Shop", "Orders_Changes")
    sql, params = seen[0]
    assert "LOWER(TABLE_NAME)=LOWER(%s)" in sql and params == ("Shop", "Orders_Changes")
