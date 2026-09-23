import asyncio
import json

import pytest

from app.modules.assistant.intelligence import TurnIntent, TurnRouter
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult, _data


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_expires", [False, True])
async def test_approval_wait_preserves_execution_budget(monkeypatch, tool_expires):
    import app.modules.assistant.service as service

    clock = [0.0]
    monkeypatch.setattr(service, "_budget_time", lambda: clock[0])
    cancelled = asyncio.Event()

    class Tool(EvalTool):
        async def run(self, invocation, context):
            if tool_expires:
                clock[0] += 61
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return await super().run(invocation, context)

    tool = Tool("query_execute")
    registry = ToolRegistry()
    registry.register(tool)
    loop = AssistantLoop(
        provider=ScriptedProvider([tool_call_frame("query", sql="SHOW DATABASES"), text_frame()]),
        registry=registry,
        time_budget_seconds=60,
        system_prompt="test",
    )
    approvals = []

    async def consent(invocation, classification):
        approvals.append(classification)
        clock[0] += 120
        return True

    frames = [
        frame
        async for frame in loop.run(
            thread=thread(),
            user_content="SHOW DATABASES",
            context=LoopContext("bench"),
            resolve_consent=consent,
        )
    ]
    result = TurnResult(frames=frames)
    assert approvals == ["read_only"]
    if tool_expires:
        assert result.finish_reason == "timeout"
        assert cancelled.is_set()
        assert any(
            _data(frame).get("status") == "failed"
            for frame in frames
            if "event: tool_status" in frame
        )
    else:
        assert result.finish_reason == "stop"
        assert len(tool.runs) == 1


@pytest.mark.asyncio
async def test_abandoned_approval_has_its_own_bound(monkeypatch):
    import app.modules.assistant.service as service

    monkeypatch.setattr(service, "CONSENT_TIMEOUT_SECONDS", 0)
    tool = EvalTool("query_execute")
    registry = ToolRegistry()
    registry.register(tool)
    loop = AssistantLoop(
        provider=ScriptedProvider([tool_call_frame("query")]),
        registry=registry,
        system_prompt="test",
    )

    async def consent(invocation, classification):
        await asyncio.Event().wait()

    frames = [
        frame
        async for frame in loop.run(
            thread=thread(),
            user_content="SELECT 1",
            context=LoopContext("bench"),
            resolve_consent=consent,
        )
    ]
    assert TurnResult(frames=frames).finish_reason == "consent_timeout"
    assert tool.runs == []
    assert any(_data(frame).get("status") == "cancelled" for frame in frames)


@pytest.mark.asyncio
async def test_capability_help_cannot_execute_database_query():
    tool = EvalTool("query_execute")
    registry = ToolRegistry()
    registry.register(tool)
    snapshots = []

    class Provider(ScriptedProvider):
        async def stream(self, **kwargs):
            snapshots.append(kwargs)
            async for frame in super().stream(**kwargs):
                yield frame

    loop = AssistantLoop(
        provider=Provider(
            [tool_call_frame("wrong", sql="SHOW DATABASES"), text_frame("I can help with SQL.")]
        ),
        registry=registry,
        system_prompt="test",
    )

    async def consent(invocation, classification):
        raise AssertionError("Capability help must not request database access")

    frames = [
        frame
        async for frame in loop.run(
            thread=thread(),
            user_content="Show me what Nove can do",
            context=LoopContext("bench"),
            resolve_consent=consent,
        )
    ]
    assert tool.runs == []
    assert snapshots[0]["tools"] is None
    assert "query_execute" in json.dumps(snapshots[0]["messages"])
    assert TurnResult(frames=frames).finish_reason == "stop"


@pytest.mark.parametrize(
    "prompt", ["Show me what Nove can do", "What can you do?", "Nova bisa apa?"]
)
def test_capability_help_routing(prompt):
    assert TurnRouter().route(prompt).intent == TurnIntent.CAPABILITY_HELP


def test_sql_and_data_requests_keep_their_routes():
    assert TurnRouter().route("SHOW DATABASES").intent == TurnIntent.RAW_SQL_QUERY
    assert TurnRouter().route("What can you tell me about sales?").needs_data
