"""Only the current governed intent can canonicalize a validated semantic result."""

import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.agents import business_results
from app.modules.assistant.evidence_health import assess_evidence
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.intelligence.contracts import fingerprint
from app.modules.intelligence.evidence import EvidenceEnvelope
from tests.unit.test_automatic_investigation import automatic_mission as automatic_mission
from tests.unit.test_automatic_investigation import comparison as comparison
from tests.unit.test_automatic_investigation import lifecycle as lifecycle
from tests.unit.test_automatic_investigation import seed
from tests.unit.test_evidence_health import NOW, strong_facts
from tests.unit.test_intelligence_engine import REF, USER


def governed_outcome():
    valid = seed()
    envelope = EvidenceEnvelope(
        health=assess_evidence(strong_facts(semantic_version=REF.version,
                              semantic_fingerprint=REF.fingerprint), assessed_at=NOW),
        semantic=REF, metrics=["revenue"], dimensions=["city"],
        timezone=valid.execution_time.timezone,
        validated_plan_fingerprint=valid.plan_fingerprint, model_fingerprint=REF.fingerprint,
    )
    return ToolOutcome(ok=True, summary="Governed revenue", business_result={
        "envelope": envelope, "seed": valid, "required_inputs": [],
        "population_fingerprint": fingerprint({}),
    })


@pytest.mark.parametrize("intent", ["ANSWER", "ANALYZE", "RESEARCH", "PLAN", "ACT"])
async def test_non_investigate_turn_never_runs_automatic_comparison(
    comparison, automatic_mission, monkeypatch, intent,
):
    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    automatic = AsyncMock()
    monkeypatch.setattr(business_results.intelligence_service, "automatic_investigation", automatic)
    context = LoopContext(user_name="alice", user=USER, mission_id="mission", run_id="run",
                          agent_id="finance", effective_work_intent=intent)
    result = await business_results.governed_result(
        ToolInvocation("call", "semantic_query", {}), governed_outcome(), context,
    )
    automatic.assert_not_awaited()
    assert result.provider_observation is None
    assert result.trace_metadata["business_canonicalization"][
        "business_canonicalization_status"] == "skipped"
    assert automatic_mission.value.semantic_anchors


async def test_validated_investigate_snapshot_is_exact_bounded_and_reusable(
    comparison, automatic_mission, monkeypatch,
):
    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    monkeypatch.setattr(business_results, "intelligence_service", comparison[0])
    context = LoopContext(user_name="alice", user=USER, mission_id="mission", run_id="run",
                          agent_id="finance", effective_work_intent="INVESTIGATE")
    call, outcome = ToolInvocation("call", "semantic_query", {}), governed_outcome()
    first = await business_results.governed_result(call, outcome, context)
    repeated = await business_results.governed_result(call, outcome, context)
    assert first.provider_observation == repeated.provider_observation
    assert first.provider_observation["hypotheses"]
    assert first.trace_metadata["business_canonicalization"][
        "business_canonicalization_status"] == "created"
    assert repeated.trace_metadata["business_canonicalization"][
        "business_canonicalization_status"] == "reused"
    assert "scope" not in first.public_event["investigation"]
    assert "current_binding" not in first.public_event["mission"]


async def test_unsafe_seed_returns_existing_inputs_instead_of_an_invented_investigation(
    comparison, automatic_mission, monkeypatch,
):
    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    context = LoopContext(user_name="alice", user=USER, mission_id="mission", run_id="run",
                          agent_id="finance", effective_work_intent="INVESTIGATE")
    outcome = governed_outcome()
    outcome = replace(outcome, business_result={**outcome.business_result, "seed": None,
                      "required_inputs": ["time_dimension", "comparison_window"]})
    result = await business_results.governed_result(
        ToolInvocation("call", "semantic_query", {}), outcome, context,
    )
    assert result.public_event["status"] == "clarification"
    assert result.provider_observation["required_inputs"] == [
        "time_dimension", "comparison_window",
    ]
    assert result.trace_metadata["business_canonicalization"][
        "business_canonicalization_status"] == "incomplete"


async def test_refused_anchor_records_failed_cost_without_control_fields(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    monkeypatch.setattr(business_results.mission_service, "record_anchor",
                        AsyncMock(side_effect=HTTPException(403, "secret lease owner")))
    context = LoopContext(user_name="alice", user=USER, mission_id="mission", run_id="run",
                          effective_work_intent="INVESTIGATE")
    result = await business_results.governed_result(None, governed_outcome(), context)
    assert result.trace_metadata["business_canonicalization"][
        "business_canonicalization_status"] == "failed"
    assert "secret" not in json.dumps(result.public_event)
    assert result.provider_observation is None


async def test_canonical_observation_truncates_utf8_and_preserves_causal_labels(
    comparison, automatic_mission, monkeypatch,
):
    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    service = comparison[0]
    result = await service.automatic_investigation(seed(), USER, mission_id="mission",
                                                 agent_id="finance")
    investigation = result["investigation"].model_copy(deep=True)
    original = investigation.hypotheses[0]
    investigation.hypotheses = [original.model_copy(update={
        "id": f"hypothesis-{index}", "label": "語" * 512,
        "next_test": "語" * 1000, "causal_status": status,
    }) for index, status in enumerate(["arithmetic", "association", "supported_effect"] * 10)]
    observation = business_results.canonical_observation(
        investigation, result["news"], result["comparison"], target_metric="revenue",
    )
    assert len(json.dumps(observation, ensure_ascii=False,
                          separators=(",", ":")).encode()) <= 16 * 1024
    assert len(observation["hypotheses"]) <= 10
    assert len(observation["evidence_refs"]) <= 20
    assert observation["truncation"]["truncated"]
    assert [item["causal_status"] for item in observation["hypotheses"]][:3] == [
        "arithmetic", "association", "supported_effect",
    ]
