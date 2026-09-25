"""An Agent Studio semantic turn uses one published View catalog."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.tools.semantic_query import SemanticQueryTool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from app.modules.intelligence.semantic_views import semantic_view_service
from app.modules.query.service import query_service
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame


async def test_bound_published_view_trajectory_uses_consent_and_finishes(monkeypatch):
    definition = parse_ossie(
        Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    ).as_dict()
    fingerprint = SemanticModelIR.from_ossie(definition).fingerprint
    active = {
        "id": "sales-view", "name": "nova_sales", "version": 2,
        "status": "ACTIVE", "fingerprint": fingerprint, "definition": definition,
    }
    get_active = AsyncMock(return_value=active)
    legacy_get = AsyncMock()
    legacy_vqr = AsyncMock()
    execute = AsyncMock(return_value=[SimpleNamespace(
        columns=["total_revenue"], rows=[[100]], row_count=1, error=None,
    )])
    monkeypatch.setattr(semantic_view_service, "get_active_for_agent", get_active)
    monkeypatch.setattr(agent_repository, "get_semantic_model", legacy_get)
    monkeypatch.setattr(agent_repository, "list_verified_queries", legacy_vqr)
    monkeypatch.setattr(agent_repository, "record_semantic_usage", AsyncMock())
    monkeypatch.setattr(query_service, "execute_statements", execute)

    registry = ToolRegistry()
    registry.register(SemanticQueryTool())
    provider = ScriptedProvider(script=[
        tool_call_frame(
            "view-query", name="semantic_query",
            arguments={"question": "What is total revenue?"},
        ),
        text_frame("Total revenue is 100."),
    ])
    context = LoopContext(
        user_name="reader", user={
            "username": "reader", "encrypted_password": "sealed", "active_role": "analyst",
            "assigned_roles": ["analyst"],
        },
        role="analyst", agent_id="agent-1", semantic_view_ids=["sales-view"],
    )
    consented = []

    async def consent(invocation, classification):
        consented.append((invocation.tool_name, classification))
        return True

    frames = [
        frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="published-view", user_name="reader", title="Eval"),
            user_content="What is total revenue?",
            context=context,
            resolve_consent=consent,
        )
    ]

    assert consented == [("semantic_query", "read_only")]
    assert [step["name"] for step in context.steps if step["kind"] == "tool"] == [
        "semantic_query"
    ]
    assert get_active.await_count >= 1
    assert all(call.kwargs["agent_id"] == "agent-1" for call in get_active.await_args_list)
    execute.assert_awaited_once()
    legacy_get.assert_not_awaited()
    legacy_vqr.assert_not_awaited()
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")
    assert "sealed" not in "".join(frames)
