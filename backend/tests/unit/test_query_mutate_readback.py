"""After an approved write, Neva says done only for what the role can now see."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools import query_mutate as module

USER = {"username": "alice", "encrypted_password": "enc", "active_role": "analyst",
        "assigned_roles": ["analyst"], "session_id": "s", "security_context_version": 1}


async def _write(monkeypatch, sql, desc):
    monkeypatch.setattr(module, "write_audit_log", AsyncMock())
    service = "app.modules.query.service.query_service"
    monkeypatch.setattr(f"{service}.execute_statements", AsyncMock(
        return_value=[SimpleNamespace(error=None, row_count=0, columns=[], rows=[],
                                     affected_rows=0, warnings=[])]))
    looked = AsyncMock(side_effect=desc)
    monkeypatch.setattr(f"{service}.execute", looked)
    context = SimpleNamespace(user=USER, database="sales", schema_name=None,
                              audit_session_id="a")
    outcome = await module.query_mutate_tool.run(
        ToolInvocation("m1", "query_mutate", {"sql": sql}), context)
    return outcome, looked


async def test_a_created_table_is_read_back_as_the_caller(monkeypatch):
    outcome, looked = await _write(
        monkeypatch, "CREATE TABLE sales.daily (d DATE, amount DECIMAL(12,2))",
        [SimpleNamespace(error=None, row_count=2)])
    assert outcome.ok
    assert outcome.data["readback"] == [{
        "object": "sales.daily", "kind": "table", "action": "create",
        "visible": True, "columns": 2, "confirmed": True,
    }]
    assert looked.await_args.kwargs["sql"] == "DESC sales.daily"
    assert looked.await_args.kwargs["role"] == "analyst"
    assert "exists with 2 column(s)" in outcome.summary


async def test_a_dropped_view_is_confirmed_gone(monkeypatch):
    outcome, _ = await _write(monkeypatch, "DROP VIEW IF EXISTS sales.v",
                              [SimpleNamespace(error="Unknown table 'v'", row_count=0)])
    assert outcome.data["readback"][0]["confirmed"] is True
    assert "is gone" in outcome.summary


@pytest.mark.parametrize(("desc", "words"), [
    ([SimpleNamespace(error="Access denied", row_count=0)], "not visible to this role"),
    ([RuntimeError("engine down")], "not confirmed"),
])
async def test_an_unseen_result_is_never_reported_as_done(monkeypatch, desc, words):
    outcome, _ = await _write(monkeypatch, "CREATE VIEW sales.v AS SELECT 1", desc)
    assert outcome.data["readback"][0]["confirmed"] is False
    assert words in outcome.summary


async def test_rows_and_grants_need_no_object_readback(monkeypatch):
    outcome, looked = await _write(
        monkeypatch, "INSERT INTO sales.daily VALUES ('2026-01-01', 1)", [])
    looked.assert_not_awaited()
    assert outcome.data["readback"] == []


async def test_an_engine_refusal_raised_for_a_dropped_table_means_it_is_gone(monkeypatch):
    from app.core.exceptions import StarRocksError

    outcome, _ = await _write(monkeypatch, "DROP TABLE sales.daily",
                              [StarRocksError("Unknown table 'daily'", engine_code=1146)])
    assert outcome.data["readback"][0]["confirmed"] is True
    assert "is gone" in outcome.summary
