"""The shared loop carries deterministic semantic health through durable replay."""

from unittest.mock import AsyncMock

import pytest

from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.runtime import semantic_ir_to_definition
from app.modules.agents.tools.semantic_query import SemanticQueryTool
from app.modules.assistant.evidence_health import replay_evidence_health
from app.modules.assistant.intelligence import EvidenceTracker
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from app.modules.intelligence.semantic_views import semantic_view_service
from app.modules.query.repository import QueryResult
from app.modules.query.service import query_service
from tests.benchmark.harness import ScriptedProvider, text_frame
from tests.unit.test_semantic_intelligence import sales_model


@pytest.mark.parametrize("truncated, label", [(False, "moderate"), (True, "limited")])
async def test_governed_health_survives_loop_replay_without_extra_model_call(
    monkeypatch, truncated, label,
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True, raising=False)
    definition = semantic_ir_to_definition(sales_model())
    view = {
        "id": "sales", "version": 2, "name": "sales", "status": "ACTIVE",
        "fingerprint": SemanticModelIR.from_ossie(definition).fingerprint, "definition": definition,
    }
    monkeypatch.setattr(semantic_view_service, "get_active_for_agent", AsyncMock(return_value=view))
    usage = AsyncMock()
    monkeypatch.setattr(agent_repository, "record_semantic_usage", usage)
    execute = AsyncMock(return_value=[QueryResult(
        columns=["total_revenue"], rows=[[100]], row_count=1, truncated=truncated,
    )])
    monkeypatch.setattr(query_service, "execute_statements", execute)
    registry = ToolRegistry()
    registry.register(SemanticQueryTool())
    provider = ScriptedProvider(script=[text_frame("Total revenue is 100.")], turn_plan={
        "intent": "semantic_analytics", "tools": ["semantic_query"],
        "required_tools": ["semantic_query"], "ml_task": None,
        "intent_frame": {"language": "en"},
        "primary_plan": {
            "metrics": ["total_revenue"], "dimensions": [], "filters": [], "named_filters": [],
            "time": None, "order_by": [], "limit": None, "unresolved_concepts": [],
        }, "primary_view": "sales",
    })
    context = LoopContext(
        user_name="alice", user={
            "username": "alice", "encrypted_password": "sealed", "active_role": "ANALYST",
            "assigned_roles": ["ANALYST"], "security_context_version": 3,
        }, role="ANALYST", agent_id="agent", semantic_view_ids=["sales"],
    )
    consent = AsyncMock(return_value=True)
    frames = [frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
        thread=AssistantThread(thread_id="health", user_name="alice", title="Eval"),
        user_content="What is total revenue?", context=context, resolve_consent=consent,
    )]
    assert provider.calls == 1
    execute.assert_awaited_once()
    consent.assert_awaited_once()
    assert usage.await_args.kwargs["active_role"] == "ANALYST"
    assert usage.await_args.kwargs["security_context_version"] == 3
    assert usage.await_args.kwargs["semantic_version"] == 2
    assert execute.await_args.kwargs["security_context_version"] == 3
    step = next(step for step in context.steps if step["kind"] == "tool")
    health = step["trace_detail"]["evidence_health"]
    assert health["label"] == label
    assert health["data_freshness"]["status"] == "unknown"
    assert health["facts"]["causal_strength"] == "unknown"
    replay = EvidenceTracker()
    replay.restore(context.verified_evidence)
    assert replay.items[0].metadata["evidence_health"] == health
    assert replay_evidence_health(health).model_dump(mode="json") == health
    assert "sealed" not in str(health) and "sealed" not in "".join(frames)
