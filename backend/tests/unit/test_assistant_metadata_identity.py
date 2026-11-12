"""A role-changing tool does not leave later metadata work under the earlier role."""

from unittest.mock import AsyncMock

from app.core.metadata_actor import acting_as, current_actor
from app.modules.access_control.security_context import SecurityContext
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolInvocation, ToolOutcome, ToolRegistry


async def test_each_tool_uses_its_current_context_and_preserves_the_run_actor(monkeypatch):
    original = SecurityContext("alice", "analyst", session_id="login", security_context_version=1)
    user = {
        "username": "alice",
        "active_role": "analyst",
        "assigned_roles": ["analyst", "reviewer"],
        "session_id": "login",
        "security_context_version": 1,
    }
    context = LoopContext("alice", user=user)
    loop = AssistantLoop(provider=AsyncMock(), registry=ToolRegistry())
    observed = []

    async def execute(_tool, _invocation, current):
        observed.append(current_actor())
        current.user = {**current.user, "active_role": "reviewer", "security_context_version": 2}
        return ToolOutcome(ok=True, summary="Done")

    monkeypatch.setattr(loop, "_dispatch_bound_tool", execute)
    with acting_as(original):
        for number in range(2):
            result = await loop._dispatch_tool(
                object(), ToolInvocation(str(number), "fixture", {}), context
            )
            assert result.ok
            assert current_actor() == original
    assert observed == [original, original.switched("reviewer")]
    assert all(actor.session_id == "login" for actor in observed)


async def test_invalid_active_role_refuses_before_any_tool_effect(monkeypatch):
    context = LoopContext("alice", user={"username": "alice", "active_role": "reviewer",
                                        "assigned_roles": ["analyst"], "session_id": "login"})
    loop = AssistantLoop(provider=AsyncMock(), registry=ToolRegistry())
    execute = AsyncMock()
    monkeypatch.setattr(loop, "_dispatch_bound_tool", execute)
    result = await loop._dispatch_tool(object(), ToolInvocation("one", "fixture", {}), context)
    assert not result.ok and result.error_class == "ACCESS_DENIED"
    execute.assert_not_awaited()
