from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import business_results
from app.modules.agents.quality import case_result, observed_trace
from app.modules.agents.quality_scoring import Assertion, PromotionGates, score_assertion
from app.modules.assistant.evidence_health import EvidenceFacts, assess_evidence
from app.modules.assistant.measurements import AttemptMeasurements, measurement_scope
from app.modules.intelligence.engine import CanonicalizationCollector
from app.modules.intelligence.evidence import EvidenceEnvelope
from tests.unit.test_automatic_investigation import automatic_mission as _automatic_mission
from tests.unit.test_automatic_investigation import seed
from tests.unit.test_chat_investigation import comparison as _comparison
from tests.unit.test_intelligence_canonicalization_metrics import timed as _timed
from tests.unit.test_intelligence_engine import END, REF, USER
from tests.unit.test_intelligence_engine import lifecycle as _lifecycle

comparison, lifecycle, automatic_mission, timed = (
    _comparison, _lifecycle, _automatic_mission, _timed,
)


def hook_input(intent, *, comparison=True):
    value = seed()
    health = assess_evidence(EvidenceFacts(
        semantic_grounding="published", execution_status="success", coverage="complete",
        semantic_view_id=REF.view_id, semantic_version=REF.version,
        semantic_fingerprint=REF.fingerprint,
    ), assessed_at=END)
    envelope = EvidenceEnvelope(
        health=health, semantic=REF, metrics=[value.target_metric],
        validated_plan_fingerprint=value.plan_fingerprint, model_fingerprint=REF.fingerprint,
    )
    context = SimpleNamespace(mission_id="mission", effective_work_intent=intent,
                              agent_id="finance", user=USER)
    outcome = SimpleNamespace(business_result={
        "envelope": envelope, "seed": value if comparison else None,
        "population_fingerprint": value.plan_fingerprint, "required_inputs": [],
    })
    return context, outcome


async def test_quality_preserves_internal_cost_without_changing_selected_tools(comparison):
    service, _, source, body = comparison
    measured = AttemptMeasurements()
    with measurement_scope(measured):
        measured.increment("tool_dispatches")
        result = await service.initiate_investigation(body, USER)
    counts, measurement = measured.observation(None)
    trace = observed_trace([
        {"kind": "tool", "name": "semantic_query"},
        {"kind": "quality_observation", "duration_ms": 100, "counts": counts,
         "measurement": measurement, "facts": {
             "execution": {"finish_reason": "stop"},
             "business_canonicalization": [result["metrics"]],
         }},
    ])
    assert len(source.calls) == 4
    internal = trace["facts"]["business_canonicalization"][0]
    assert internal["automatic_investigation_query_count"] == 4
    assert trace["counts"]["tool_calls"] == 1
    assert trace["tool_names"] == ["semantic_query"]
    assert score_assertion(Assertion(
        scorer="tool_selection", expected={"required": ["semantic_query"],
                                           "forbidden": ["driver", "comparison"]},
    ), trace).status == "pass"
    assert score_assertion(Assertion(
        scorer="efficiency", expected={"tool_calls": 1},
    ), trace).status == "pass"
    assert score_assertion(Assertion(
        scorer="efficiency", expected={"tool_calls": 0},
    ), trace).status == "fail"
    persisted = case_result({"id": "case", "revision": 1, "assertions": []}, trace, "trace",
                            PromotionGates())
    restored = json.loads(json.dumps(persisted))
    assert restored["trace"]["facts"]["business_canonicalization"] == [result["metrics"]]
    result["metrics"]["comparison_query_count"] = 999
    assert restored["trace"]["facts"]["business_canonicalization"][0]["comparison_query_count"] == 2


async def test_internal_queries_cannot_establish_selected_tool_evidence(comparison):
    service, _, _, body = comparison
    result = await service.initiate_investigation(body, USER)
    trace = observed_trace([{
        "kind": "quality_observation", "counts": {"tool_calls": 0},
        "facts": {"business_canonicalization": [result["metrics"]]},
    }])
    assert score_assertion(Assertion(
        scorer="tool_selection", expected={"required": ["semantic_query"]},
    ), trace).status == "fail"
    assert score_assertion(Assertion(
        scorer="efficiency", expected={"tool_calls": 0},
    ), trace).status == "pass"


def test_skip_projection_and_legacy_observations_remain_explicit():
    collector = CanonicalizationCollector()
    with collector.operation():
        collector.complete("skipped")
    trace = observed_trace([{
        "kind": "quality_observation", "counts": {"tool_calls": 0},
        "facts": {"business_canonicalization": collector.snapshot().model_dump(mode="json")},
    }])
    assert trace["facts"]["business_canonicalization"]["business_canonicalization_status"] == (
        "skipped"
    )
    legacy = observed_trace([{"kind": "quality_observation", "facts": {}, "counts": None}])
    assert "business_canonicalization" not in legacy["facts"]
    assert score_assertion(Assertion(
        scorer="efficiency", expected={"tool_calls": 0},
    ), legacy).status == "unavailable"


@pytest.mark.parametrize("intent,comparison", [
    ("ANSWER", True), ("ANSWER", False), ("ANALYZE", False), ("ANALYZE", True),
])
async def test_answer_lookup_and_analysis_skip_automatic_investigation(
    monkeypatch, intent, comparison,
):
    monkeypatch.setattr(business_results.settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    automatic = AsyncMock()
    monkeypatch.setattr(business_results.intelligence_service, "automatic_investigation", automatic)
    anchor = AsyncMock(return_value=SimpleNamespace(mission_id="mission"))
    monkeypatch.setattr(business_results.mission_service, "record_anchor", anchor)
    context, outcome = hook_input(intent, comparison=comparison)
    result = await business_results.governed_result(None, outcome, context)
    automatic.assert_not_awaited()
    anchor.assert_awaited_once()
    metrics = result.trace_metadata["business_canonicalization"]
    assert metrics["business_canonicalization_status"] == "skipped"
    assert metrics["automatic_investigation_query_count"] == 0
    assert metrics["canonical_investigation_created"] is False
    assert result.provider_observation is None


@pytest.mark.parametrize("reused", [False, True])
async def test_complete_hook_includes_mission_reauthorization_queries_and_linkage_time(
    timed, automatic_mission, monkeypatch, reused,
):
    service, _, source, _, clock = timed
    monkeypatch.setattr(business_results.settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    monkeypatch.setattr(business_results, "intelligence_service", service)

    async def anchor(*args, **kwargs):
        clock.now += 0.002
        return automatic_mission.value.model_copy(deep=True)

    link = business_results.mission_service._link_automatic_investigation

    async def measured_link(*args, **kwargs):
        value = await link(*args, **kwargs)
        clock.now += 0.011
        return value

    monkeypatch.setattr(business_results.mission_service, "record_anchor", anchor)
    monkeypatch.setattr(business_results.mission_service, "_link_automatic_investigation",
                        measured_link)
    context, outcome = hook_input("INVESTIGATE")
    if reused:
        await business_results.governed_result(None, outcome, context)
        source.calls.clear()
    result = await business_results.governed_result(None, outcome, context)
    metrics = result.trace_metadata["business_canonicalization"]
    expected_total, expected_analysis = (10, 0) if reused else (8, 2)
    assert metrics["automatic_investigation_query_count"] == len(source.calls) == expected_total
    assert metrics["comparison_query_count"] == metrics["driver_query_count"] == expected_analysis
    assert metrics["other_query_count"] == (10 if reused else 4)
    assert metrics["business_canonicalization_duration_ms"] == pytest.approx(
        metrics["query_duration_ms"] + metrics["persistence_duration_ms"] + 13
    )
    assert metrics["business_canonicalization_status"] == ("reused" if reused else "created")
    assert metrics["canonical_investigation_created"] is (not reused)
    assert metrics["canonical_investigation_reused"] is reused
    if reused:
        assert metrics["persistence_duration_ms"] == 0
        assert metrics["investigation_persistence_duration_ms"] == 0
    assert result.provider_observation["baseline_value"] == 100
    assert result.provider_observation["current_value"] == 60
    safe = json.dumps(metrics)
    for private in (USER["session_id"], USER["username"], REF.fingerprint, "worker", "lease"):
        assert private not in safe
