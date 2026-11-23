"""A captured write is refused while a row filter or mask could cover its source."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.streams import capture as capture_module
from app.modules.streams.capture import Caller, CaptureCoordinator
from app.modules.streams.codec import Column
from app.modules.streams.schemas import StreamError
from app.modules.streams.sources import READY, SourceRecord
from app.sql_frontend.parser import parse_statement

SOURCE = SourceRecord(
    "s" * 32, 1, "shop", "customers", 42, ("id",),
    (Column("id", "bigint"), Column("email", "varchar(64)"), Column("status", "varchar(8)")),
    "f" * 64, "__nova_stream_changes_x", "__nova_stream_lineage_x", READY,
)


@pytest.fixture
def env(monkeypatch):
    from app.integrations.ranger.client import ranger_client
    from app.modules.access_control.service import access_control_service

    monkeypatch.setattr(settings, "RANGER_PASSWORD", "secret")
    state = {"policies": [], "locked": False, "writer": False}

    async def list_policies(*, page_size=100, service_name=None):
        return [] if service_name else state["policies"]

    monkeypatch.setattr(ranger_client, "list_policies", list_policies)
    monkeypatch.setattr(ranger_client, "get_service",
                        AsyncMock(return_value={"name": settings.RANGER_SERVICE_NAME}))
    monkeypatch.setattr(access_control_service, "role_allows", AsyncMock(return_value=[True]))

    @contextlib.asynccontextmanager
    async def lock(_source_id):
        state["locked"] = True
        yield

    @contextlib.asynccontextmanager
    async def writer_conn():
        state["writer"] = True
        yield None

    monkeypatch.setattr(capture_module, "source_lock", lock)
    monkeypatch.setattr(capture_module, "writer_conn", writer_conn)
    return state


def _caller():
    return Caller("support", "SUPPORT", AsyncMock(return_value=([], [])), AsyncMock())


def _mask(table: str) -> dict:
    return {
        "service": settings.RANGER_SERVICE_NAME, "isEnabled": True, "policyType": 1,
        "resources": {
            "catalog": {"values": ["default_catalog"]}, "database": {"values": ["shop"]},
            "table": {"values": [table]}, "column": {"values": ["email"]},
        },
    }


@pytest.mark.parametrize("sql,kind", [
    ("UPDATE shop.customers SET status = 'vip' WHERE id = 7", "update"),
    ("DELETE FROM shop.customers WHERE id = 7", "delete"),
    ("INSERT INTO shop.customers VALUES (8, 'a@b.c', 'new')", "insert"),
])
@pytest.mark.parametrize("table", ["customers", "cust*"])
async def test_mask_added_after_the_stream_refuses_the_write(env, sql, kind, table):
    env["policies"] = [_mask(table)]
    parsed = parse_statement(sql)
    caller = _caller()
    with pytest.raises(StreamError) as exc:
        await CaptureCoordinator().write(
            SOURCE, parsed.statement_context, parsed.normalized_sql, kind, caller
        )
    assert exc.value.code == "STREAM_GOVERNANCE_UNSUPPORTED"
    assert "shop.customers" in str(exc.value) and "Drop its streams" in str(exc.value)
    caller.query.assert_not_awaited()
    caller.run_native.assert_not_awaited()
    assert not env["locked"] and not env["writer"]


async def test_write_without_ranger_is_refused(monkeypatch, env):
    monkeypatch.setattr(settings, "RANGER_PASSWORD", "")
    parsed = parse_statement("DELETE FROM shop.customers WHERE id = 7")
    caller = _caller()
    with pytest.raises(StreamError, match="need Ranger"):
        await CaptureCoordinator().write(
            SOURCE, parsed.statement_context, parsed.normalized_sql, "delete", caller
        )
    assert not env["locked"]


async def test_access_is_checked_before_governance(monkeypatch, env):
    from app.modules.access_control.service import access_control_service

    monkeypatch.setattr(access_control_service, "role_allows", AsyncMock(return_value=[False]))
    env["policies"] = [_mask("customers")]
    parsed = parse_statement("DELETE FROM shop.customers WHERE id = 7")
    with pytest.raises(StreamError) as exc:
        await CaptureCoordinator().write(
            SOURCE, parsed.statement_context, parsed.normalized_sql, "delete", _caller()
        )
    assert exc.value.code == "STREAM_ACCESS_DENIED"


async def test_unrelated_mask_lets_the_write_reach_the_lock(monkeypatch, env):
    env["policies"] = [_mask("orders")]
    registry = AsyncMock()
    registry.get = AsyncMock(return_value=None)  # no longer captured: native write
    monkeypatch.setattr(capture_module, "source_registry", registry)
    parsed = parse_statement("DELETE FROM shop.customers WHERE id = 7")
    caller = _caller()
    outcome = await CaptureCoordinator().write(
        SOURCE, parsed.statement_context, parsed.normalized_sql, "delete", caller
    )
    assert env["locked"]
    caller.run_native.assert_awaited_once()
    assert outcome.commit_sequence is None
