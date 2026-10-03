"""Semantic execution supplies health facts and keeps legacy confidence out of prose."""

from copy import deepcopy

import pytest

from app.modules.agents.tools.semantic_query import _table_payload
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.query.repository import QueryResult
from tests.unit.test_semantic_guidance_fallback import USER
from tests.unit.test_semantic_llm_planner import _plan, setup_tool


async def run(tool):
    return await tool.run(
        ToolInvocation(tool_call_id="health", tool_name="semantic_query",
                       arguments={"question": "Revenue by city"}),
        LoopContext(user_name="alice", user={**USER, "security_context_version": 7}),
    )


@pytest.fixture
def enabled(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True, raising=False)


@pytest.mark.usefixtures("enabled")
async def test_successful_published_execution_has_unknown_freshness_without_watermark(monkeypatch):
    tool, provider, execute = setup_tool(monkeypatch, _plan(dimensions=["city"]))
    tool._resolve_model.return_value.update(
        status="ACTIVE", version=2, fingerprint="published-definition",
    )
    execute.return_value = [QueryResult(columns=["city", "total_revenue"], rows=[["Jakarta", 100]])]
    outcome = await run(tool)
    health = outcome.evidence["health"]
    assert outcome.ok and health["label"] == "moderate"
    assert health["facts"]["semantic_grounding"] == "published"
    assert health["data_freshness"]["status"] == "unknown"
    assert health["facts"]["coverage"] == "complete"
    assert "confidence:" not in outcome.summary
    assert outcome.data["confidence"] == 0.8
    provider.complete.assert_awaited_once()
    assert execute.await_args.kwargs["security_context_version"] == 7


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("results, expected", [
    ([QueryResult(columns=["total_revenue"], rows=[[100]], truncated=True)], "truncated"),
    ([QueryResult(columns=["total_revenue"], rows=[[n] for n in range(51)])], "truncated"),
    ([QueryResult(columns=["total_revenue"], rows=[["X" * 14000]])], "truncated"),
    ([], "unknown"),
])
async def test_fetch_and_preview_bounds_reduce_coverage(monkeypatch, results, expected):
    tool, _, execute = setup_tool(monkeypatch, _plan())
    tool._resolve_model.return_value.update(status="ACTIVE", version=2, fingerprint="definition")
    execute.return_value = results
    outcome = await run(tool)
    assert outcome.evidence["health"]["facts"]["coverage"] == expected
    assert outcome.evidence["health"]["label"] in {"limited", "insufficient"}


@pytest.mark.usefixtures("enabled")
async def test_clarification_and_execution_failure_never_claim_strong_evidence(monkeypatch):
    from app.modules.agents.repository import agent_repository

    tool, _, execute = setup_tool(monkeypatch, _plan(unresolved_concepts=[{
        "text": "payroll", "material": True, "type_hint": "metric",
    }]))
    outcome = await run(tool)
    assert outcome.evidence["health"]["label"] == "insufficient"
    assert outcome.evidence["health"]["facts"]["semantic_ambiguity"] == "unresolved"
    execute.assert_not_awaited()
    tool, _, execute = setup_tool(monkeypatch, _plan())
    execute.return_value = [QueryResult(error="private engine details")]
    outcome = await run(tool)
    assert outcome.evidence["health"]["label"] == "insufficient"
    assert outcome.evidence["health"]["facts"]["execution_status"] == "failed"
    assert "private engine details" not in str(outcome.evidence)
    assert agent_repository.record_semantic_usage.await_args.kwargs["succeeded"] is False
    assert agent_repository.record_semantic_usage.await_args.kwargs["active_role"] == "analyst"
    assert agent_repository.record_semantic_usage.await_args.kwargs["security_context_version"] == 7


async def test_feature_disabled_retains_numeric_field_and_suppresses_new_assessment(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False, raising=False)
    tool, _, _ = setup_tool(monkeypatch, _plan())
    outcome = await run(tool)
    assert outcome.data["confidence"] == 0.8
    assert "evidence_health" not in outcome.data
    assert "confidence:" not in outcome.summary


def test_artifact_preview_retains_actual_fetch_truncation_and_marks_local_bound():
    query = QueryResult(columns=["metric"], rows=[[number] for number in range(201)])
    before = deepcopy(query)
    payload = _table_payload([query])
    assert len(payload["rows"]) == 200 and payload["truncated"]
    assert query == before
    fetched = QueryResult(columns=["metric"], rows=[[1]], truncated=True)
    assert _table_payload([fetched])["truncated"]
