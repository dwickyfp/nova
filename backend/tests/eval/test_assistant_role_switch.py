import json
from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from app.modules.assistant.tools.query_execute import QueryExecuteTool
from app.modules.query.repository import QueryResult
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import TurnResult


def context():
    return LoopContext(
        user_name="alice",
        user={
            "username": "alice",
            "encrypted_password": "secret-encrypted-value",
            "roles": ["finance", "marketing"],
            "active_role": "finance",
            "session_id": "session",
            "security_context_version": 1,
        },
    )


@pytest.mark.parametrize(
    "sql", ["USE ROLE marketing", "SET ROLE 'marketing'", "/* role */ USE ROLE `marketing`;"]
)
def test_role_change_requires_separate_approval(sql):
    tool = QueryExecuteTool()
    assert (
        tool.classification_for(ToolInvocation("switch", "query_execute", {"sql": sql}))
        == "session_change"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SET ROLE ALL",
        "SET ROLE NONE",
        "USE ROLE finance,marketing",
        "USE ROLE marketing; SELECT 1",
        "USE ROLE marketing; DROP TABLE x",
        "SET ROLE marketing garbage",
        "SET sql_mode = 'x'",
    ],
)
def test_invalid_role_changes_remain_denied(sql):
    assert (
        QueryExecuteTool().classification_for(
            ToolInvocation("switch", "query_execute", {"sql": sql})
        )
        == "denied"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("approved,granted", [(True, True), (False, True), (True, False)])
async def test_role_switch_trajectory(monkeypatch, approved, granted):
    from app.modules.auth.service import auth_service
    from app.modules.query.service import query_service

    tool = QueryExecuteTool()
    audit = AsyncMock()
    monkeypatch.setattr(tool, "_audit", audit)
    switch = AsyncMock(
        return_value={
            "active_role": "marketing",
            "roles": ["finance", "marketing"],
            "assigned_roles": ["finance", "marketing"],
            "security_context_version": 2,
        }
    )
    if not granted:
        switch.side_effect = PermissionError("must-not-leak-engine-details")
    monkeypatch.setattr(auth_service, "switch_role", switch)
    roles = []

    async def execute(**kwargs):
        roles.append(kwargs["role"])
        return [
            QueryResult(
                columns=["value"],
                rows=[["finance-secret" if kwargs["role"] == "finance" else "public"]],
                row_count=1,
            )
        ]

    monkeypatch.setattr(query_service, "execute_statements", execute)
    snapshots = []

    class Provider(ScriptedProvider):
        async def stream(self, **kwargs):
            snapshots.append(json.dumps(kwargs["messages"]))
            async for item in super().stream(**kwargs):
                yield item

    provider = Provider(
        [
            tool_call_frame("before", sql="SELECT value FROM analytics.t"),
            tool_call_frame("switch", sql="USE ROLE marketing"),
            tool_call_frame("after", sql="SELECT value FROM analytics.t"),
            text_frame("Finished"),
        ]
    )
    registry = ToolRegistry()
    registry.register(tool)
    loop = AssistantLoop(
        provider=provider, registry=registry, system_prompt="Execute requested SQL"
    )
    runtime = AssistantThread("thread", "alice", "Role test")
    runtime.consent.always_allow_read_only = True
    ctx = context()
    prompts = []

    async def consent(invocation, classification):
        prompts.append(classification)
        return approved

    frames = [
        frame
        async for frame in loop.run(
            thread=runtime,
            user_content="Inspect tables, switch to marketing, and inspect again",
            context=ctx,
            resolve_consent=consent,
        )
    ]
    result = TurnResult(frames=frames)
    assert "session_change" in prompts
    assert "secret-encrypted-value" not in "".join(frames)
    assert "must-not-leak-engine-details" not in "".join(frames)
    if approved and granted:
        assert roles == ["finance", "marketing"]
        assert ctx.user["active_role"] == "marketing"
        assert ctx.role == "marketing"
        assert "role_changed" in result.events
        assert result.finish_reason == "stop"
        assert "finance-secret" not in snapshots[2]
        assert prompts == ["session_change", "read_only"]
        assert not runtime.consent.always_allow_read_only
        switch.assert_awaited_once_with("session", "marketing")
        assert any(
            call.kwargs["sql"] == "USE ROLE marketing" and call.kwargs["status"] == "SUCCESS"
            for call in audit.call_args_list
        )
    else:
        assert roles == ["finance"]
        assert ctx.user["active_role"] == "finance"
        assert result.finish_reason == ("denied" if not approved else "error")
        if not approved:
            switch.assert_not_awaited()
