from __future__ import annotations

import pytest

from app.common.sql_guard import split_sql_statements
from app.core.exceptions import ForbiddenSQLError
from app.modules.agents.registry import add_custom_tools, build_registry
from app.modules.agents.tools.custom_tool import (
    CustomToolRunner,
    validate_custom_tool_definition,
)
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from app.modules.assistant.tools.query_execute import QueryExecuteTool
from app.modules.query.repository import QueryResult
from app.modules.query.sql_pipeline import guard_user_statement
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame


def _tool(name: str, statement: str, *, output_mode: str = "result") -> CustomToolRunner:
    return CustomToolRunner(
        {
            "name": name,
            "kind": "procedure",
            "description": f"Run {name}",
            "database_name": "scratch",
            "definition": {
                "parameters": [
                    {"name": "value", "type": "string", "description": "Value to use"}
                ],
                "statements": [statement],
                "output_mode": output_mode,
            },
        }
    )


def _context() -> LoopContext:
    return LoopContext(
        user_name="analyst",
        database="scratch",
        schema_name="default",
        thread_id="custom-tool-eval",
        role="ACCOUNTADMIN",
        user={
            "username": "analyst",
            "encrypted_password": "encrypted-only-for-test",
            "active_role": "analyst",
            "assigned_roles": ["analyst"],
            "security_context_version": 1,
        },
    )


class GuardedQueryService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_statements(self, **kwargs):
        self.calls.append(kwargs)
        results = []
        for statement in split_sql_statements(kwargs["sql"]):
            try:
                guard_user_statement(
                    statement,
                    confirm_destructive=kwargs["confirm_destructive"],
                    allow_stage_export=kwargs.get("allow_stage_export", False),
                )
            except Exception as exc:
                results.append(QueryResult(error=str(exc)))
                break
            if statement.lstrip().upper().startswith("SELECT"):
                results.append(QueryResult(columns=["value"], rows=[["ok"]], row_count=1))
            else:
                results.append(QueryResult(affected_rows=1, row_count=1))
        return results


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO t (value) VALUES ({{value}})",
        "SELECT value FROM t WHERE value = {{value}}",
        "UPDATE t SET value = {{value}} WHERE id = 1",
        "DELETE FROM t WHERE value = {{value}}",
        "SELECT * FROM @stage1.data.csv WHERE value = {{value}}",
        "INSERT INTO t SELECT * FROM @stage1.data.csv WHERE value = {{value}}",
    ],
)
async def test_custom_sql_tool_dispatches_supported_sql_with_caller_context(
    monkeypatch, statement: str
) -> None:
    import app.modules.query.service as query_module

    query = GuardedQueryService()
    monkeypatch.setattr(query_module, "query_service", query)
    tool = _tool("operation", statement)
    context = _context()
    outcome = await tool.run(
        ToolInvocation("op", tool.name, {"value": "O'Brien"}), context
    )

    assert outcome.ok, outcome.error
    assert len(query.calls) == 1
    call = query.calls[0]
    assert call["username"] == "analyst"
    assert call["encrypted_password"] == "encrypted-only-for-test"
    assert call["role"] == "analyst"
    assert call["database"] == "scratch"
    assert call["sql"] == statement.replace("{{value}}", "'O''Brien'") + ";"
    if statement.startswith("SELECT"):
        assert outcome.table == {"title": "", "columns": ["value"], "rows": [["ok"]]}
        assert context.last_result == {"title": "", "columns": ["value"], "rows": [["ok"]]}


async def test_insert_then_select_tools_run_in_one_agent_turn(monkeypatch) -> None:
    import app.modules.agents.repository as agent_repository_module
    import app.modules.query.service as query_module

    query = GuardedQueryService()
    monkeypatch.setattr(query_module, "query_service", query)
    insert = _tool("insert_row", "INSERT INTO t (value) VALUES ({{value}})", output_mode="run")
    select = _tool("select_rows", "SELECT value FROM t WHERE value = {{value}}")
    unrelated = _tool("unrelated", "SELECT 2 WHERE value = {{value}}")

    class ToolRepository:
        async def list_custom_tools(self, *, owner_name):
            assert owner_name == "analyst"
            return [insert.tool, select.tool, unrelated.tool]

    monkeypatch.setattr(agent_repository_module, "agent_repository", ToolRepository())
    agent = {
        "owner_name": "analyst",
        "default_tools": ["custom:insert_row", "custom:select_rows"],
    }
    registry = build_registry(agent)
    await add_custom_tools(registry, agent)
    assert registry.names() == [insert.name, select.name]
    provider = ScriptedProvider(
        script=[
            tool_call_frame("i", name=insert.name, arguments={"value": "abc"}),
            tool_call_frame("s", name=select.name, arguments={"value": "abc"}),
            text_frame("Inserted and checked the row."),
        ]
    )
    thread = AssistantThread(thread_id="custom-tool-eval", user_name="analyst", title="Eval")
    thread.consent.always_allow_read_only = True
    prompts: list[tuple[str, str]] = []

    async def consent(invocation, classification):
        prompts.append((invocation.tool_name, classification))
        return True

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=thread,
            user_content="Insert abc and verify it",
            context=_context(),
            resolve_consent=consent,
        )
    ]

    assert [call["sql"] for call in query.calls] == [
        "INSERT INTO t (value) VALUES ('abc');",
        "SELECT value FROM t WHERE value = 'abc';",
    ]
    assert prompts == [(insert.name, insert.classification)]
    assert insert.classification != "read_only"
    assert any('"finish_reason":"stop"' in frame.replace(" ", "") for frame in frames)


async def test_denied_write_never_reaches_query_service(monkeypatch) -> None:
    import app.modules.query.service as query_module

    query = GuardedQueryService()
    monkeypatch.setattr(query_module, "query_service", query)
    tool = _tool("write_row", "INSERT INTO t (value) VALUES ({{value}})")
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(
        script=[
            tool_call_frame("w", name=tool.name, arguments={"value": "abc"}),
            text_frame("done"),
        ]
    )

    async def deny(_invocation, _classification):
        return False

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="custom-tool-eval", user_name="analyst", title="Eval"),
            user_content="Insert abc",
            context=_context(),
            resolve_consent=deny,
        )
    ]
    assert query.calls == []
    assert any('"finish_reason":"denied"' in frame.replace(" ", "") for frame in frames)


async def test_stage_export_custom_tool_requires_consent_and_reaches_pipeline(monkeypatch) -> None:
    import app.modules.query.service as query_module

    query = GuardedQueryService()
    monkeypatch.setattr(query_module, "query_service", query)
    tool = CustomToolRunner(
        {
            "name": "export_stage",
            "kind": "procedure",
            "description": "Export the scratch table",
            "database_name": "scratch",
            "definition": {
                "parameters": [],
                "statements": ["COPY INTO @stage1.out.parquet FROM t"],
                "output_mode": "run",
            },
        }
    )
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(
        script=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "e",
                        "type": "function",
                        "function": {"name": tool.name, "arguments": "{}"},
                    }
                ],
            },
            text_frame("Exported."),
        ]
    )
    thread = AssistantThread(thread_id="custom-tool-eval", user_name="analyst", title="Eval")
    thread.consent.always_allow_read_only = True
    prompts = []

    async def consent(invocation, classification):
        prompts.append((invocation.tool_name, classification))
        return True

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=thread,
            user_content="Export scratch table to stage",
            context=_context(),
            resolve_consent=consent,
        )
    ]
    assert prompts == [(tool.name, "destructive")]
    assert len(query.calls) == 1
    assert query.calls[0]["allow_stage_export"] is True
    assert query.calls[0]["sql"] == "COPY INTO @stage1.out.parquet FROM t;"
    assert any('"finish_reason":"stop"' in frame.replace(" ", "") for frame in frames)


async def test_default_guard_and_query_tool_still_block_stage_exports(monkeypatch) -> None:
    import app.modules.query.service as query_module

    query = GuardedQueryService()
    monkeypatch.setattr(query_module, "query_service", query)
    for sql in (
        "SELECT * FROM t INTO @stage1.out.parquet",
        "INSERT INTO FILES('path'='x') SELECT * FROM t",
    ):
        with pytest.raises(ForbiddenSQLError):
            guard_user_statement(sql, confirm_destructive=True)
        if "INTO FILES" in sql:
            with pytest.raises(ForbiddenSQLError):
                guard_user_statement(sql, confirm_destructive=True, allow_stage_export=True)
        tool = QueryExecuteTool()

        async def no_audit(**_kwargs):
            return None

        monkeypatch.setattr(tool, "_audit", no_audit)
        outcome = await tool.run(ToolInvocation("q", tool.name, {"sql": sql}), _context())
        assert not outcome.ok
    assert query.calls == []


async def test_identifier_parameter_cannot_downgrade_stage_export_consent(monkeypatch) -> None:
    import app.modules.query.service as query_module

    query = GuardedQueryService()
    monkeypatch.setattr(query_module, "query_service", query)
    tool = CustomToolRunner(
        {
            "name": "dynamic_clause",
            "kind": "procedure",
            "description": "Use a configured clause",
            "definition": {
                "parameters": [
                    {"name": "verb", "type": "identifier", "description": "Clause verb"}
                ],
                "statements": ["SELECT 1 {{verb}} @stage1.out.parquet"],
            },
        }
    )
    assert tool.classification == "read_only"
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(
        script=[tool_call_frame("x", name=tool.name, arguments={"verb": "INTO"})]
    )
    thread = AssistantThread(thread_id="custom-tool-eval", user_name="analyst", title="Eval")
    thread.consent.always_allow_read_only = True
    prompts = []

    async def deny(invocation, classification):
        prompts.append((invocation.tool_name, classification))
        return False

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=thread,
            user_content="Run the custom clause",
            context=_context(),
            resolve_consent=deny,
        )
    ]
    assert prompts == [(tool.name, "destructive")]
    assert query.calls == []
    assert any('"finish_reason":"denied"' in frame.replace(" ", "") for frame in frames)


async def test_stage_export_permission_does_not_bypass_accountadmin_guard(monkeypatch) -> None:
    import app.modules.agents.tools.custom_tool as custom_tool_module
    import app.modules.query.service as query_module

    query = GuardedQueryService()
    monkeypatch.setattr(query_module, "query_service", query)
    audits = []

    async def capture_audit(**fields):
        audits.append(fields)

    monkeypatch.setattr(custom_tool_module, "write_audit_log", capture_audit)
    sql = "DROP ROLE ACCOUNTADMIN"
    with pytest.raises(ForbiddenSQLError):
        guard_user_statement(sql, confirm_destructive=True, allow_stage_export=True)
    tool = CustomToolRunner(
        {
            "name": "forbidden",
            "kind": "procedure",
            "description": "Try a protected operation",
            "definition": {"parameters": [], "statements": [sql]},
        }
    )
    outcome = await tool.run(ToolInvocation("x", tool.name, {}), _context())
    assert not outcome.ok
    assert query.calls == []
    assert len(audits) == 1
    assert audits[0]["action"] == "custom_tool_preflight"
    assert audits[0]["status"] == "ERROR"
    assert audits[0]["decision"] == "DENY"
    assert "sql_text" not in audits[0]


def test_function_parameters_are_rendered_in_advertised_order() -> None:
    tool = CustomToolRunner(
        {
            "name": "score",
            "kind": "function",
            "description": "Calculate a score",
            "database_name": "scratch",
            "function_name": "calculate_score",
            "definition": {
                "parameters": [
                    {"name": "amount", "type": "number", "description": "Amount"},
                    {"name": "factor", "type": "number", "description": "Factor"},
                ]
            },
        }
    )
    assert tool.parameters["required"] == ["amount", "factor"]
    assert tool._function_sql(
        ToolInvocation("f", tool.name, {"factor": 2, "amount": 10})
    ) == "SELECT `scratch`.`calculate_score`(10, 2)"


def test_function_legacy_args_and_missing_argument() -> None:
    tool = CustomToolRunner(
        {
            "name": "legacy_score",
            "kind": "function",
            "description": "Calculate a legacy score",
            "database_name": "scratch",
            "function_name": "legacy_score",
            "definition": {"args": ["amount", "factor"]},
        }
    )
    assert tool._function_sql(
        ToolInvocation("f", tool.name, {"amount": 10, "factor": 2})
    ) == "SELECT `scratch`.`legacy_score`(10, 2)"
    missing = tool._function_sql(ToolInvocation("f", tool.name, {"amount": 10}))
    assert not isinstance(missing, str)
    assert not missing.ok


def _typed_tool(kind: str) -> CustomToolRunner:
    return CustomToolRunner(
        {
            "name": "typed",
            "kind": "procedure",
            "description": "Render one typed parameter",
            "definition": {
                "parameters": [{"name": "value", "type": kind, "description": "Value"}],
                "statements": ["SELECT {{value}}"],
            },
        }
    )


@pytest.mark.parametrize(
    ("kind", "value", "rendered"),
    [
        ("integer", 7, "7"),
        ("int", -7, "-7"),
        ("number", 1.25, "1.25"),
        ("float", 1.25, "1.25"),
        ("boolean", True, "TRUE"),
        ("boolean", False, "FALSE"),
        ("array", [1, "O'Brien", False, None], "[1, 'O''Brien', FALSE, NULL]"),
        ("identifier", "safe_table", "safe_table"),
    ],
)
def test_typed_parameters_render_as_safe_sql(kind: str, value, rendered: str) -> None:
    tool = _typed_tool(kind)
    assert tool._procedure_sql(ToolInvocation("p", tool.name, {"value": value})) == (
        f"SELECT {rendered};"
    )


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("integer", True),
        ("integer", "7; DROP TABLE t"),
        ("number", "1; DROP TABLE t"),
        ("number", float("nan")),
        ("number", float("inf")),
        ("boolean", "true"),
        ("array", {"not": "an array"}),
        ("array", [float("inf")]),
        ("identifier", "t; DROP TABLE users"),
    ],
)
def test_invalid_typed_parameters_return_tool_errors(kind: str, value) -> None:
    tool = _typed_tool(kind)
    outcome = tool._procedure_sql(ToolInvocation("p", tool.name, {"value": value}))
    assert not isinstance(outcome, str)
    assert not outcome.ok


def test_optional_parameter_is_null_when_omitted() -> None:
    tool = CustomToolRunner(
        {
            "name": "optional",
            "kind": "procedure",
            "description": "Use an optional filter",
            "definition": {
                "parameters": [
                    {
                        "name": "filter_value",
                        "type": "string",
                        "description": "Filter value",
                        "required": False,
                    }
                ],
                "statements": ["SELECT * FROM t WHERE value = {{filter_value}}"],
            },
        }
    )
    assert tool.parameters["required"] == []
    assert tool._procedure_sql(ToolInvocation("p", tool.name, {})) == (
        "SELECT * FROM t WHERE value = NULL;"
    )


def test_preview_hides_credential_shaped_arguments() -> None:
    tool = _tool("lookup", "SELECT * FROM t WHERE value = {{value}}")
    preview = tool.preview(
        ToolInvocation("p", tool.name, {"value": "ordinary", "password": "secret-123"})
    )
    assert "secret-123" not in preview
    assert "password" in preview


async def test_result_mode_redacts_secret_columns_and_credential_values(monkeypatch) -> None:
    import app.modules.query.service as query_module

    class SensitiveQuery:
        async def execute_statements(self, **_kwargs):
            return [
                QueryResult(
                    columns=["password", "note"],
                    rows=[["plain-secret", "ghp_ABCDEFGHIJKLMNOPQRSTUVWX"]],
                    row_count=1,
                )
            ]

    monkeypatch.setattr(query_module, "query_service", SensitiveQuery())
    tool = _tool("lookup", "SELECT password, note FROM t WHERE value = {{value}}")
    outcome = await tool.run(ToolInvocation("p", tool.name, {"value": "abc"}), _context())
    assert outcome.ok
    assert "plain-secret" not in outcome.summary
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWX" not in outcome.summary
    assert "***" in outcome.summary
    assert outcome.table == {
        "title": "",
        "columns": ["password", "note"],
        "rows": [["***", "***"]],
    }


def test_parameter_value_cannot_add_a_second_statement() -> None:
    tool = _tool("lookup", "SELECT * FROM t WHERE value = {{value}}")
    sql = tool._procedure_sql(
        ToolInvocation("p", tool.name, {"value": "a'; DROP TABLE t; --"})
    )
    assert isinstance(sql, str)
    assert len(split_sql_statements(sql)) == 1
    assert "DROP TABLE" in sql


def test_invalid_identifier_argument_returns_a_tool_error() -> None:
    tool = CustomToolRunner(
        {
            "name": "lookup",
            "kind": "procedure",
            "description": "Look up an authorized table",
            "definition": {
                "parameters": [
                    {"name": "table_name", "type": "identifier", "description": "Table name"}
                ],
                "statements": ["SELECT * FROM {{table_name}}"],
            },
        }
    )
    outcome = tool._procedure_sql(
        ToolInvocation("p", tool.name, {"table_name": "t; DROP TABLE users"})
    )
    assert not isinstance(outcome, str)
    assert not outcome.ok


def test_comment_placeholder_is_not_required_or_substituted() -> None:
    definition = {
        "name": "lookup",
        "kind": "procedure",
        "description": "Read one value",
        "definition": {
            "parameters": [],
            "statements": ["SELECT 1 -- {{unused}}"],
        },
    }
    assert validate_custom_tool_definition(definition) == []
    tool = CustomToolRunner(definition)
    assert tool._procedure_sql(ToolInvocation("p", tool.name, {})) == (
        "SELECT 1 -- {{unused}};"
    )


async def test_partial_failure_does_not_report_success(monkeypatch) -> None:
    import app.modules.query.service as query_module

    class PartialFailure:
        async def execute_statements(self, **_kwargs):
            return [
                QueryResult(affected_rows=1, row_count=1),
                QueryResult(error="engine failed"),
            ]

    monkeypatch.setattr(query_module, "query_service", PartialFailure())
    tool = _tool("two_steps", "INSERT INTO t (value) VALUES ({{value}})")
    tool.tool["definition"]["statements"].append("INSERT INTO other_t SELECT * FROM t")
    outcome = await tool.run(ToolInvocation("x", tool.name, {"value": "abc"}), _context())
    assert not outcome.ok
    assert "success" not in outcome.summary.lower()
