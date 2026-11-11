import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import router
from app.modules.agents.tools import mcp_tool
from app.modules.assistant.consent import ConsentBroker
from app.modules.assistant.schemas import ConsentDecisionRequest
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import thread_store
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import TurnResult


@pytest.mark.parametrize("decision", ["allow_once", "deny"])
async def test_immediate_mcp_decision_is_scoped_and_precedes_external_effect(monkeypatch, decision):
    broker = ConsentBroker()
    monkeypatch.setattr(router, "consent_broker", broker)
    monkeypatch.setattr(router, "_require_agent", AsyncMock())
    thread = thread_store.create(user_name="fixture-alice", title="Synthetic MCP")
    thread.consent.always_allow_read_only = True
    tool = mcp_tool.McpToolRunner(
        {"server_id": "server", "name": "Arithmetic"},
        {"tool_id": "fixture", "name": "total", "input_schema": {
            "type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"],
        }},
    )
    external = AsyncMock(return_value={"content": [{"type": "text", "text": '{"total":42}'}]})
    monkeypatch.setattr(mcp_tool.mcp_client, "call_tool", external)
    monkeypatch.setattr(mcp_tool, "write_audit_log", AsyncMock())
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider([
        tool_call_frame("call-1", name=tool.name, arguments={"a": 42}),
        text_frame("The external fixture returned 42."),
    ])
    user = {
        "username": "fixture-alice", "active_role": "ANALYST", "roles": ["ANALYST"],
        "security_context_version": 1, "session_id": "fixture-session",
    }

    def prepare(invocation, classification):
        return broker.open(
            invocation.tool_call_id, thread_id=thread.thread_id,
            user_name=user["username"], classification=classification,
        )

    frames = []
    try:
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=thread, user_content="Call the external fixture with a=42",
            context=LoopContext(user["username"], user=user), resolve_consent=prepare,
        ):
            frames.append(frame)
            if not frame.startswith("event: tool_call\n"):
                continue
            payload = json.loads(frame.split("data: ", 1)[1])
            assert payload["status"] == "pending"
            assert payload["classification"] == "destructive"
            external.assert_not_awaited()
            with pytest.raises(HTTPException) as foreign:
                await router.resolve_agent_tool_call(
                    "agent", "call-1", ConsentDecisionRequest(decision="allow_once"),
                    {"username": "fixture-bob"},
                )
            assert foreign.value.status_code == 404
            with pytest.raises(HTTPException) as session:
                await router.resolve_agent_tool_call(
                    "agent", "call-1", ConsentDecisionRequest(decision="allow_session"), user,
                )
            assert session.value.status_code == 400
            result = await router.resolve_agent_tool_call(
                "agent", "call-1", ConsentDecisionRequest(decision=decision), user,
            )
            assert result.status == ("approved" if decision == "allow_once" else "denied")
            assert not result.grant_active
        result = TurnResult(frames=frames)
        assert result.finish_reason == ("stop" if decision == "allow_once" else "denied")
        assert external.await_count == (1 if decision == "allow_once" else 0)
        assert broker.owner_of("call-1") is None
        if decision == "allow_once":
            external.assert_awaited_once_with(tool.server, "total", {"a": 42})
    finally:
        broker.cancel_all()
        thread_store.remove(thread.thread_id, user_name=user["username"])
