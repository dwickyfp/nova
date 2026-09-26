from unittest.mock import AsyncMock

import pytest

from app.modules.agents.registry import build_registry
from app.modules.assistant.service import AssistantLoop, LoopContext
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import TurnResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "index,filters,allowed",
    [
        ("sales_docs", {}, True),
        ("sales_docs", {"region": "US"}, False),
        ("finance", {}, False),
    ],
)
async def test_resource_scope_in_production_loop(monkeypatch, index, filters, allowed):
    query = AsyncMock(
        return_value={
            "version": 1,
            "hits": [
                {"source_key": "policy", "content": "Returns are accepted for 30 days.", "rank": 1},
            ],
        }
    )
    monkeypatch.setattr("app.modules.agents.tools.ai_search.search_service.query", query)
    registry = build_registry(
        {
            "default_tools": ["ai_search"],
            "resource_bindings": {
                "search_indexes": [{"index": "sales_docs", "filters": {"region": "ID"}}],
            },
        }
    )
    provider = ScriptedProvider(
        [
            tool_call_frame(
                "search",
                name="ai_search",
                arguments={
                    "index": index,
                    "query": "return policy",
                    "filters": filters,
                },
            ),
            text_frame(
                "Returns are accepted for 30 days."
                if allowed
                else "The configured source is unavailable."
            ),
        ]
    )
    context = LoopContext(
        "reader",
        agent_id="sales",
        role="sales",
        user={
            "username": "reader",
            "encrypted_password": "sealed",
            "active_role": "sales",
            "assigned_roles": ["sales"],
            "session_id": "eval-session",
            "security_version": 1,
        },
    )
    consent = AsyncMock(return_value=True)
    result = TurnResult(
        frames=[
            frame
            async for frame in AssistantLoop(
                provider=provider,
                registry=registry,
                max_iterations=3,
            ).run(
                thread=thread(),
                user_content="Use AI Search for the return policy",
                context=context,
                resolve_consent=consent,
            )
        ]
    )
    if allowed:
        query.assert_awaited_once()
        assert query.call_args.args[1].filters == {"region": "ID"}
        assert result.finish_reason == "stop"
    else:
        query.assert_not_awaited()
        expected_error = "conflict with" if index == "sales_docs" else "not bound"
        assert expected_error in "".join(result.frames)
        assert result.finish_reason == "error"
    consent.assert_awaited()
    assert "sealed" not in "".join(result.frames)
