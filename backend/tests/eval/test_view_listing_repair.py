"""Unsupported view discovery can use the loop's existing one-repair path."""

from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from app.modules.assistant.tools.query_execute import QueryExecuteTool
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import TurnResult
from tests.unit.test_assistant_stage_c import FakeResult


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_view_listing_repair_preserves_consent_and_user_identity(monkeypatch, approved):
    corrected = (
        "SELECT TABLE_NAME FROM information_schema.views "
        "WHERE TABLE_SCHEMA = 'NOVA_SALES' ORDER BY TABLE_NAME"
    )
    execute = AsyncMock(return_value=[
        FakeResult(columns=["TABLE_NAME"], rows=[["vw_customer_360"]]),
    ])
    monkeypatch.setattr("app.modules.query.service.query_service.execute_statements", execute)
    monkeypatch.setattr(
        "app.modules.assistant.tools.query_execute.write_audit_log", AsyncMock(),
    )
    registry = ToolRegistry()
    registry.register(QueryExecuteTool())
    provider = ScriptedProvider(
        [
            tool_call_frame("bad", name="query_execute", arguments={"sql": "SHOW VIEWS"}),
            tool_call_frame("fixed", name="query_execute", arguments={"sql": corrected}),
            text_frame("View yang tersedia: vw_customer_360."),
        ],
        turn_plan={
            "intent": "schema_inspection", "tools": ["query_execute"],
            "required_tools": ["query_execute"],
        },
    )
    context = LoopContext(user_name="analyst", database="NOVA_SALES", user={
        "username": "analyst", "encrypted_password": "test-encrypted-credential",
        "active_role": "ANALYST", "assigned_roles": ["ANALYST"],
        "session_id": "test-session", "security_context_version": 1,
    })
    consent = AsyncMock(return_value=approved)
    frames = [frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
        thread=thread(), user_content="Daftar SQL view di NOVA_SALES", context=context,
        resolve_consent=consent,
    )]
    result = TurnResult(frames=frames)
    assert "test-encrypted-credential" not in "".join(frames)
    if approved:
        assert result.finish_reason == "stop", result.error_codes
        assert "vw_customer_360" in "".join(frames)
        execute.assert_awaited_once()
        assert execute.call_args.kwargs["sql"] == corrected
        assert execute.call_args.kwargs["username"] == "analyst"
        assert execute.call_args.kwargs["role"] == "ANALYST"
        assert execute.call_args.kwargs["confirm_destructive"] is False
        assert consent.await_count == 2
    else:
        assert result.finish_reason == "denied"
        execute.assert_not_awaited()
        assert consent.await_count == 1
