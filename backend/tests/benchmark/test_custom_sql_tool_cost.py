from __future__ import annotations

import json

import pytest

from app.modules.agents.tools.custom_tool import CustomToolRunner
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from app.modules.query.repository import QueryResult
from tests.benchmark.harness import ScriptedProvider, measure, text_frame, tool_call_frame


def _runner(name: str, sql: str) -> CustomToolRunner:
    return CustomToolRunner(
        {
            "name": name,
            "kind": "procedure",
            "description": name,
            "database_name": "scratch",
            "definition": {
                "parameters": [{"name": "value", "type": "string", "description": "Value"}],
                "statements": [sql],
                "output_mode": "result",
            },
        }
    )


def _context() -> LoopContext:
    return LoopContext(
        user_name="bench",
        thread_id="bench-custom",
        user={
            "username": "bench",
            "encrypted_password": "encrypted-test-only",
            "active_role": "analyst",
            "assigned_roles": ["analyst"],
            "security_context_version": 1,
        },
    )


class QueryStub:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_statements(self, **kwargs):
        self.calls += 1
        if kwargs["sql"].startswith("SELECT"):
            return [QueryResult(columns=["value"], rows=[["abc"]], row_count=1)]
        return [QueryResult(affected_rows=1, row_count=1)]


@pytest.mark.benchmark
async def test_custom_sql_tools_offline_cost(monkeypatch, capsys) -> None:
    import app.modules.query.service as query_module

    query = QueryStub()
    monkeypatch.setattr(query_module, "query_service", query)
    insert = _runner("insert_row", "INSERT INTO t (value) VALUES ({{value}})")
    select = _runner("select_rows", "SELECT value FROM t WHERE value = {{value}}")
    registry = ToolRegistry()
    registry.register(insert)
    registry.register(select)

    async def direct_pair() -> None:
        context = _context()
        first = await insert.run(ToolInvocation("i", insert.name, {"value": "abc"}), context)
        second = await select.run(ToolInvocation("s", select.name, {"value": "abc"}), context)
        assert first.ok and second.ok

    async def full_turn() -> None:
        provider = ScriptedProvider(
            script=[
                tool_call_frame("i", name=insert.name, arguments={"value": "abc"}),
                tool_call_frame("s", name=select.name, arguments={"value": "abc"}),
                text_frame("Done."),
            ]
        )
        thread = AssistantThread(thread_id="bench-custom", user_name="bench", title="Bench")
        thread.consent.always_allow_read_only = True

        async def allow(_invocation, _classification):
            return True

        frames = [
            frame
            async for frame in AssistantLoop(provider=provider, registry=registry).run(
                thread=thread,
                user_content="Insert abc and verify it",
                context=_context(),
                resolve_consent=allow,
            )
        ]
        assert any('"finish_reason":"stop"' in frame.replace(" ", "") for frame in frames)

    direct = await measure(direct_pair, iterations=200)
    turn = await measure(full_turn, iterations=100)
    assert query.calls == 600
    assert direct["median_us"] > 0
    assert turn["median_us"] > 0
    print(
        json.dumps(
            {
                "scope": (
                    "offline Python only; fake provider and QueryService; "
                    "excludes LLM and StarRocks latency"
                ),
                "direct_insert_select_pair": direct,
                "full_two_tool_turn": turn,
            },
            sort_keys=True,
        )
    )
