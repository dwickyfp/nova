"""A concurrent approval cannot authorize tools in another login or role."""

from uuid import UUID

import pytest

from app.modules.assistant import read_grants
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult


@pytest.mark.parametrize("changed", [
    {}, {"session_id": "other-login"}, {"active_role": "sales"},
    {"security_context_version": 2}, {"username": "bob"},
])
async def test_dispatch_and_prefetch_require_the_approving_context(monkeypatch, changed):
    monkeypatch.setattr(read_grants, "_redis", lambda: None)
    user = {"username": "alice", "active_role": "analyst", "roles": ["analyst", "sales"],
            "session_id": "first-login", "security_context_version": 1}
    runtime = AssistantThread("scope-test", "alice", "Synthetic query")
    tool = EvalTool("query_execute")
    registry = ToolRegistry()
    registry.register(tool)
    message = tool_call_frame("query-one", sql="SELECT 1")
    message["tool_calls"] += tool_call_frame("query-two", sql="SELECT 2")["tool_calls"]

    class Provider(ScriptedProvider):
        async def stream(self, **kwargs):
            if self.calls == 0:
                # Another request changes the shared holder during this turn.
                await read_grants.grant(runtime, {**user, **changed}, runtime.thread_id, True)
            async for frame in super().stream(**kwargs):
                yield frame

    approvals = []

    async def deny(invocation, classification):
        approvals.append((invocation.tool_call_id, invocation.provider_call_id, classification))
        return False

    frames = [frame async for frame in AssistantLoop(
        provider=Provider([message, text_frame("Both synthetic queries finished.")]),
        registry=registry,
    ).run(
        thread=runtime, user_content="Run SELECT 1 and SELECT 2",
        context=LoopContext("alice", user=user), resolve_consent=deny,
    )]
    result = TurnResult(frames=frames)
    if changed:
        assert len(approvals) == 1
        assert UUID(approvals[0][0]).version == 4
        assert approvals[0][1:] == ("query-one", "read_only")
        assert not tool.runs
        assert result.finish_reason == "denied"
    else:
        assert not approvals
        assert [call.provider_call_id for call in tool.runs] == ["query-one", "query-two"]
        assert len({call.tool_call_id for call in tool.runs}) == 2
        assert all(UUID(call.tool_call_id).version == 4 for call in tool.runs)
        assert result.finish_reason == "stop"
