"""validate_sql plans one read statement as the caller; nothing runs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools import validate_sql as module

USER = {"username": "alice", "encrypted_password": "enc", "active_role": "analyst",
        "assigned_roles": ["analyst"], "session_id": "s", "security_context_version": 1}


def _context(**fields):
    return SimpleNamespace(user=USER, database="sales", schema_name=None, **fields)


async def _check(monkeypatch, sql, *, error=None, raises=None):
    explain = AsyncMock(
        side_effect=raises,
        return_value=SimpleNamespace(error=error),
    )
    monkeypatch.setattr("app.modules.query.service.query_service.explain", explain)
    outcome = await module.validate_sql_tool.run(
        ToolInvocation("v1", "validate_sql", {"sql": sql}), _context()
    )
    return outcome, explain


async def test_a_planned_select_is_verified_as_the_caller_without_running(monkeypatch):
    outcome, explain = await _check(monkeypatch, "SELECT city FROM outlets")
    assert outcome.data["valid"] and outcome.data["objects_verified"]
    assert outcome.data["executed"] is False
    kwargs = explain.await_args.kwargs
    assert kwargs["sql"] == "SELECT city FROM outlets"
    assert kwargs["role"] == "analyst" and kwargs["username"] == "alice"


async def test_a_missing_column_fails_the_check_with_the_reason(monkeypatch):
    outcome, _ = await _check(
        monkeypatch, "SELECT town FROM outlets", error="Unknown column 'town' in 'outlets'"
    )
    assert not outcome.data["valid"]
    assert "town" in outcome.data["statements"][0]["errors"][0]
    assert "could not plan" in outcome.summary


async def test_a_refused_table_is_reported_plainly(monkeypatch):
    outcome, _ = await _check(
        monkeypatch, "SELECT * FROM payroll", error="Access denied; 5203 no SELECT privilege"
    )
    assert not outcome.data["valid"]
    assert outcome.data["statements"][0]["errors"] == [
        "the active role may not read one of these tables."
    ]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM @sales_stage/orders.csv",
    "INSERT INTO t VALUES (1)",
    "SELECT 1; SELECT 2",
    "EXPLAIN ANALYZE SELECT 1",
    "SELECT * FROM FILES('path'='s3://b/x', 'aws.s3.endpoint'='http://example.test')",
    "SELECT * FROM TABLE(generate_series(1, 3))",
    "SELECT a FROM t JOIN unnest_rows(t.items) u",
])
async def test_stages_writes_and_several_statements_are_never_planned(monkeypatch, sql):
    outcome, explain = await _check(monkeypatch, sql)
    explain.assert_not_awaited()
    assert outcome.data["objects_verified"] is False


async def test_a_slow_catalog_means_not_verified_rather_than_wrong(monkeypatch):
    outcome, _ = await _check(monkeypatch, "SELECT 1", raises=TimeoutError())
    assert outcome.data["valid"] and outcome.data["objects_verified"] is False


@pytest.mark.parametrize(("code", "message", "verified", "reason"), [
    (5204, "Access denied; you need the ANY privilege on DATABASE sales", True,
     "the active role may not read one of these tables."),
    (1064, "Column 'town' cannot be resolved", True, "Column 'town' cannot be resolved"),
    (None, "Can't connect to the database", False, None),
])
async def test_an_engine_refusal_raised_by_the_query_service_is_the_result(
    monkeypatch, code, message, verified, reason
):
    from app.core.exceptions import StarRocksError

    outcome, _ = await _check(
        monkeypatch, "SELECT town FROM outlets",
        raises=StarRocksError(message, engine_code=code),
    )
    assert outcome.data["objects_verified"] is verified
    assert outcome.data["valid"] is (reason is None)
    if reason:
        assert outcome.data["statements"][0]["errors"] == [reason]
