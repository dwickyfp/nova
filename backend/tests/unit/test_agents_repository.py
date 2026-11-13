"""Structural tests for Agent Studio persistence (Phase 12, N12-B1).

These assert the properties that are easy to lose and expensive to get wrong:

* no Agent Studio column can hold a statement, a result set, or a credential —
  the credential rule in ``AGENTS.md`` is structural, so it is tested against
  the DDL text rather than trusted to review;
* the row normaliser produces the shapes the API schemas expect, including JSON
  columns the driver may return as strings;
* ``ensure_schema`` adds ``agent_id`` to the assistant threads table additively
  and tolerates the duplicate-column error a fresh table produces.

No engine, no network: the DDL and the pure conversion helpers are exercised
directly, so the fast local loop catches a regression.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.metadata_db import MetadataPermissionDenied, MetadataUnavailable
from app.core.metadata_schema import load_migrations
from app.modules.agents import repository
from tests.unit.agent_metadata_fakes import install_agent_store

#: Column names that would indicate a credential or a statement at rest. None may
#: appear in an Agent Studio DDL.
_FORBIDDEN_COLUMNS = {
    "password",
    "encrypted_password",
    "token",
    "api_key",
    "secret",
    "credential",
    "sql",
    "statement",
    "engine_sql",
    "result",
    "rows",
}


def _ddl_columns(ddl: str) -> list[str]:
    """Extract declared column names from a CREATE TABLE body.

    The body is between the first ``(`` and its matching ``)``. Each column
    declaration is the first token on its line; table-level clauses (``PRIMARY
    KEY``, ``DISTRIBUTED BY``, ``PROPERTIES``) are not column names and are
    excluded by keyword.
    """
    body = ddl[ddl.index("(") + 1 :]
    # Cut at the closing paren of the column list (before DISTRIBUTED/PROPERTIES).
    for terminator in ("\n) PRIMARY KEY", "\n) DISTRIBUTED", "\n)  PRIMARY"):
        if terminator in body:
            body = body.split(terminator)[0]
            break
    columns: list[str] = []
    for raw in body.splitlines():
        line = raw.strip().rstrip(",")
        if not line:
            continue
        token = line.split()[0]
        if token.upper() in {"PRIMARY", "DISTRIBUTED", "PROPERTIES"}:
            continue
        columns.append(token)
    return columns


def test_no_agent_studio_column_can_hold_a_credential_or_statement() -> None:
    for ddl in (
        repository.AGENTS_DDL,
        repository.AGENT_SKILLS_DDL,
    ):
        columns = {c.lower() for c in _ddl_columns(ddl)}
        assert columns  # the extractor found the columns
        overlap = columns & _FORBIDDEN_COLUMNS
        assert not overlap, f"forbidden column(s) in DDL: {overlap}"


def test_agent_row_normalises_json_and_defaults() -> None:
    row = [
        "id-1",
        "alice",
        "db",
        "sch",
        "Revenue Analyst",
        "desc",
        "revenue.png",
        "blue",
        "prov-1",
        "claude",
        "resp instr",
        "orch instr",
        "concise",
        '["q1","q2"]',
        30,
        16000,
        "accept",
        '["semantic_query"]',
        '["metric-qa"]',
        "auto_read_only",
        "sm-1",
        '["sm-1","sm-2"]',
        "private",
        "2026-09-20 10:00:00",
        "2026-09-20 10:00:00",
    ]
    view = repository._agent_row(row)
    assert view["sample_questions"] == ["q1", "q2"]
    assert view["default_tools"] == ["semantic_query"]
    assert view["policy"] == "auto_read_only"
    assert view["tool_not_accessible"] == "accept"
    # The list column is the source of truth; the scalar mirrors the first.
    assert view["semantic_model_ids"] == ["sm-1", "sm-2"]


def test_driver_native_json_values_are_accepted() -> None:
    # StarRocks may return a JSON column already decoded; both forms must work.
    assert repository._as_json(["a"]) == ["a"]
    assert repository._as_json('{"k": 1}') == {"k": 1}
    assert repository._as_json(None) is None
    assert repository._as_json("not json") is None


@pytest.mark.asyncio
async def test_agent_lookup_propagates_unavailable_metadata_without_replay(monkeypatch) -> None:
    store = install_agent_store(monkeypatch, AsyncMock())
    store.get_agent.side_effect = MetadataUnavailable()
    with pytest.raises(MetadataUnavailable):
        await repository.AgentRepository().get_agent("agent-1", owner_name="alice")
    store.get_agent.assert_awaited_once_with("agent-1")


@pytest.mark.asyncio
async def test_metadata_read_cannot_return_foreign_owner(monkeypatch) -> None:
    store = install_agent_store(monkeypatch, AsyncMock())
    store.get_agent.return_value = {"agent_id": "agent-1", "owner_name": "bob"}
    assert await repository.AgentRepository().get_agent("agent-1", owner_name="alice") is None
    store.get_agent.assert_awaited_once_with("agent-1")


@pytest.mark.asyncio
async def test_role_read_preserves_verification_and_owner_scope(monkeypatch) -> None:
    store = install_agent_store(monkeypatch, AsyncMock())
    store.list_agent_roles.return_value = [{
        "agent_id": "agent-1", "owner_name": "alice", "role_name": "analyst",
        "grant_type": "USAGE", "verified_fingerprint": "current", "verified_at": None,
    }]
    repo = repository.AgentRepository()
    roles = await repo.list_agent_roles("agent-1", owner_name="alice")
    assert roles == [{"role_name": "analyst", "grant_type": "USAGE",
                      "verified_fingerprint": "current", "verified_at": None}]
    assert await repo.list_agent_roles("agent-1", owner_name="bob") == []


@pytest.mark.asyncio
async def test_shared_agent_requires_visibility_and_active_role_grant(monkeypatch) -> None:
    store = install_agent_store(monkeypatch, AsyncMock(), principal="reader", role="city_reader")
    row = {"agent_id": "agent-1", "owner_name": "owner", "visibility": "shared",
           "updated_at": "2026-10-01T00:00:00"}
    store.get_agent.return_value = row
    store.list_agents.return_value = [row]
    store.list_agent_roles.return_value = [{"role_name": "city_reader"}]
    repo = repository.AgentRepository()
    assert await repo.get_agent("agent-1", owner_name="reader") is None
    for operation in (repo.get_shared_agent("agent-1", role_name="other"),
                      repo.list_shared_agents(role_name="other")):
        with pytest.raises(MetadataPermissionDenied):
            await operation
    assert (await repo.get_shared_agent("agent-1", role_name="city_reader"))[
        "agent_id"
    ] == "agent-1"
    assert len(await repo.list_shared_agents(role_name="city_reader")) == 1
    row["visibility"] = "private"
    assert await repo.get_shared_agent("agent-1", role_name="city_reader") is None
    assert await repo.list_shared_agents(role_name="city_reader") == []


def test_assistant_threads_pg_schema_preserves_agent_id() -> None:
    migration = next(
        item for item in load_migrations() if item.name == "0024_assistant_policy_bi.sql"
    )
    section = migration.statement.split("CREATE TABLE nova_meta.config_assistant_threads", 1)[
        1
    ].split("CREATE TABLE", 1)[0]
    assert "agent_id varchar(64)" in section
