"""Unit tests for the custom-tool runtime and registry composition.

Pure logic: SQL rendering for both tool kinds, literal safety, and the
structural registration of custom tools. No engine, no network.
"""

from __future__ import annotations

from app.modules.agents.registry import add_custom_tools
from app.modules.agents.tools.custom_tool import CustomToolRunner, _literal
from app.modules.assistant.tools import ToolInvocation, ToolRegistry


def test_literal_quotes_and_escapes_strings() -> None:
    assert _literal("abc") == "'abc'"
    assert _literal("O'Brien") == "'O''Brien'"
    # A value that looks like SQL stays a string.
    assert _literal("1; DROP TABLE x") == "'1; DROP TABLE x'"


def test_literal_numeric_and_keywords() -> None:
    assert _literal(5) == "5"
    assert _literal(1.5) == "1.5"
    assert _literal(True) == "TRUE"
    assert _literal(None) == "NULL"


def test_function_sql_validates_identifiers() -> None:
    tool = {
        "name": "DISCOUNT",
        "kind": "function",
        "database_name": "NOVA_DEMO",
        "function_name": "get_discount",
        "definition": {"args": ["pct"]},
    }
    runner = CustomToolRunner(tool)
    sql = runner._function_sql(
        ToolInvocation("1", "custom_DISCOUNT", {"pct": 10})
    )
    assert sql == "SELECT `NOVA_DEMO`.`get_discount`(10)"


def test_function_sql_rejects_unsafe_name() -> None:
    tool = {
        "name": "BAD",
        "kind": "function",
        "database_name": "db",
        "function_name": "fn; DROP TABLE x",
        "definition": {"args": []},
    }
    runner = CustomToolRunner(tool)
    outcome = runner._function_sql(ToolInvocation("1", "custom_BAD", {}))
    # Returns a ToolOutcome describing the refusal, not SQL.
    assert not isinstance(outcome, str)
    assert outcome.ok is False


def test_procedure_sql_substitutes_parameters() -> None:
    tool = {
        "name": "TOP",
        "kind": "procedure",
        "definition": {
            "parameters": [{"name": "limit_n", "type": "int"}],
            "statements": ["SELECT * FROM t LIMIT {{limit_n}}"],
        },
    }
    runner = CustomToolRunner(tool)
    sql = runner._procedure_sql(ToolInvocation("1", "custom_TOP", {"limit_n": 3}))
    assert sql == "SELECT * FROM t LIMIT 3;"


def test_procedure_sql_requires_parameters() -> None:
    tool = {
        "name": "TOP",
        "kind": "procedure",
        "definition": {
            "parameters": [{"name": "limit_n", "type": "int"}],
            "statements": ["SELECT * FROM t LIMIT {{limit_n}}"],
        },
    }
    runner = CustomToolRunner(tool)
    outcome = runner._procedure_sql(ToolInvocation("1", "custom_TOP", {}))
    assert not isinstance(outcome, str)
    assert outcome.ok is False


async def test_add_custom_tools_registers_selected(monkeypatch) -> None:
    """A selected custom tool is registered; an unselected one is not."""

    class FakeRepo:
        async def list_custom_tools(self, *, owner_name):
            return [
                {"name": "TOP", "kind": "procedure", "definition": {}},
                {"name": "OTHER", "kind": "function", "definition": {}},
            ]

    import app.modules.agents.repository as repo_mod

    monkeypatch.setattr(repo_mod, "agent_repository", FakeRepo())
    registry = ToolRegistry()
    await add_custom_tools(
        registry,
        {"owner_name": "root", "default_tools": ["custom:TOP"]},
    )
    assert registry.get("custom_TOP") is not None
    assert registry.get("custom_OTHER") is None


async def test_run_output_mode_reports_no_rows(monkeypatch) -> None:
    """output_mode='run' returns a status line, not the result rows."""

    class FakeResult:
        columns = ["a"]
        rows = [[1], [2]]
        row_count = 2
        error = None

    class FakeQueryService:
        async def execute_statements(self, **kwargs):
            return [FakeResult()]

    import app.modules.query.service as qs

    monkeypatch.setattr(qs, "query_service", FakeQueryService())
    tool = {
        "name": "DO_IT",
        "kind": "procedure",
        "database_name": "db",
        "definition": {
            "parameters": [],
            "statements": ["SELECT 1"],
            "output_mode": "run",
        },
    }
    runner = CustomToolRunner(tool)

    class Ctx:
        user = {"username": "u", "encrypted_password": "e"}

    outcome = await runner.run(
        ToolInvocation("1", "custom_DO_IT", {}), Ctx()
    )
    assert outcome.ok
    assert "row" in outcome.summary
    assert "|" not in outcome.summary
