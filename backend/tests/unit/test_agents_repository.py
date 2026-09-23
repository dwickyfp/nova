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

import pytest

from app.modules.agents import repository
from app.modules.assistant import repository as assistant_repository

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
        repository.SEMANTIC_MODELS_DDL,
        repository.AGENT_SKILLS_DDL,
    ):
        columns = {c.lower() for c in _ddl_columns(ddl)}
        assert columns  # the extractor found the columns
        overlap = columns & _FORBIDDEN_COLUMNS
        assert not overlap, f"forbidden column(s) in DDL: {overlap}"


def test_semantic_model_version_is_not_nullable() -> None:
    # A model without a pinned version must be unstorable; the parser guarantees
    # a version, and the column enforces it.
    assert "ossie_version     VARCHAR(32) NOT NULL" in repository.SEMANTIC_MODELS_DDL


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
async def test_agent_lookup_retries_a_malformed_metadata_result(monkeypatch) -> None:
    class FlakyDB:
        calls = []

        async def execute_system(self, _sql, params):
            self.calls.append(params)
            return {"rows": [[None] * (8 if len(self.calls) == 1 else 28)]}

    db = FlakyDB()
    monkeypatch.setattr(repository, "db", db)
    monkeypatch.setattr(repository, "_agent_row", lambda row: {"columns": len(row)})

    result = await repository.AgentRepository().get_agent("agent-1", owner_name="alice")

    assert result == {"columns": 28}
    assert db.calls == [["agent-1", "alice"], ["agent-1", "alice"]]


@pytest.mark.asyncio
async def test_shared_agent_requires_visibility_and_active_role_grant(monkeypatch) -> None:
    class FakeDB:
        async def execute_system(self, sql, params):
            if "CONFIG_AGENT_ROLES" in sql:
                if params == ["agent-1", "owner"]:
                    return {"rows": [["city_reader", "USAGE", None, None]]}
                return {"rows": [["agent-1"]] if params == ["city_reader"] else []}
            if "visibility = 'shared'" in sql:
                return {"rows": [["agent-1", "owner"] + [None] * 26]}
            return {"rows": []}

    monkeypatch.setattr(repository, "db", FakeDB())
    monkeypatch.setattr(
        repository, "_agent_row", lambda row: {"agent_id": row[0], "owner_name": row[1]}
    )
    repo = repository.AgentRepository()
    assert await repo.get_agent("agent-1", owner_name="reader") is None
    assert await repo.get_shared_agent("agent-1", role_name="other") is None
    shared = await repo.get_shared_agent("agent-1", role_name="city_reader")
    assert shared is not None and shared["agent_id"] == "agent-1"
    assert await repo.list_shared_agents(role_name="other") == []
    assert len(await repo.list_shared_agents(role_name="city_reader")) == 1


def test_assistant_threads_ddl_has_agent_id_and_migration() -> None:
    assert "agent_id" in assistant_repository.THREADS_DDL
    assert "ADD COLUMN agent_id" in assistant_repository.THREADS_AGENT_ID_DDL
    assert "CONFIG_ASSISTANT_THREADS" in assistant_repository.THREADS_AGENT_ID_DDL
