import json

import httpx
import pytest

from scripts.configure_autopilot_model import configure


@pytest.mark.parametrize("routing_failure", [False, True])
async def test_model_setup_preserves_decision_and_parameters_and_reports_partial_write(
    routing_failure,
):
    routing = {
        "enabled": True,
        "decision_model_id": "decision",
        "light_model_id": "old",
        "heavy_model_id": "old",
        "min_probability": 0.9,
        "min_confidence": 0.7,
        "timeout_seconds": 3.5,
    }
    state = {"default": {"model_id": "old"}, "routing": routing.copy()}
    calls = []

    def handle(request):
        path = request.url.path.removeprefix("/api/v1/")
        calls.append((request.method, path))
        if path == "auth/me":
            value = {"active_role": "ACCOUNTADMIN"}
        elif path == "ai/providers":
            value = {"providers": [{"id": "kenari", "name": "Kenari", "has_api_key": True}]}
        elif path == "ai/providers/kenari/models":
            value = {"models": [{"id": "deepseek", "name": "deepseek-v4-1-flash", "type": "llm"}]}
        elif path == "ai/default-model":
            if request.method == "PUT":
                state["default"] = json.loads(request.content)
            value = state["default"]
        else:
            assert path == "ai/decision-settings"
            if request.method == "PUT":
                if routing_failure:
                    return httpx.Response(503)
                state["routing"] = json.loads(request.content)
            value = state["routing"]
        return httpx.Response(200, json=value)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://localhost"
    ) as client:
        if routing_failure:
            with pytest.raises(ValueError, match="Global default saved; routing update failed"):
                await configure(client)
            assert state["default"] == {"model_id": "deepseek"}
            assert state["routing"] == routing
        else:
            result = await configure(client)
            assert result["model"] == "deepseek-v4-1-flash"
            assert state["routing"] == {
                **routing,
                "light_model_id": "deepseek",
                "heavy_model_id": "deepseek",
            }
            assert state["default"] == {"model_id": "deepseek"}
            assert not any("api-key" in path for _, path in calls)


async def test_model_setup_refuses_inactive_administrator_before_writing():
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json={"active_role": "SYSADMIN", "roles": ["ACCOUNTADMIN"]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://localhost"
    ) as client:
        with pytest.raises(ValueError, match="active ACCOUNTADMIN"):
            await configure(client)
    assert calls == ["GET"]


async def test_judge_uses_strict_schema_and_rejects_provider_extra_fields(monkeypatch):
    from unittest.mock import AsyncMock

    from pydantic import ValidationError

    from app.modules.query_autopilot import judge as module

    monkeypatch.setattr(module, "resolve_judge", AsyncMock(return_value=object()))
    values = {
        "detection_validity": 4,
        "evidence_quality": 4,
        "diagnosis_quality": 4,
        "recommendation_quality": 4,
        "safety": 5,
        "experiment_validity": 4,
        "outcome_interpretation": 4,
        "explanation_quality": 4,
        "critical_hallucinations": 0,
        "independent_root_cause": "UNKNOWN",
        "explanation": "Evidence is insufficient.",
    }
    client = AsyncMock()
    client.complete.return_value = {"content": json.dumps(values)}
    result = await module.judge({"sql": "secret SQL", "api_key": "secret-key"}, client)
    assert result.mean_score == 4.125
    request = client.complete.call_args.kwargs
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert "secret" not in request["messages"][1]["content"]
    client.complete.return_value = {"content": json.dumps({**values, "extra_note": "unknown"})}
    with pytest.raises(ValidationError):
        await module.judge({}, client)


async def test_judge_bounds_total_wall_time_and_cancels_the_shared_call(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from app.modules.query_autopilot import judge as module

    monkeypatch.setattr(module, "JUDGE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(module, "resolve_judge", AsyncMock(return_value=object()))
    cancelled = asyncio.Event()

    async def pending(**_):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    client = AsyncMock()
    client.complete.side_effect = pending
    with pytest.raises(TimeoutError):
        await module.judge({}, client)
    assert cancelled.is_set()


def test_judge_action_experiment_and_outcome_projection_is_closed_and_numeric():
    from app.modules.query_autopilot.judge import reduced_evidence

    reduced = reduced_evidence({
        "action": {"kind": "MATERIALIZED_VIEW", "risk": "APPROVAL", "sql": "private-sql"},
        "experiment": {
            "status": "INCONCLUSIVE", "correctness": "EQUIVALENT", "repetitions": 30,
            "before": {"count": 30, "mean_ms": 100, "query_ids": ["private-query"]},
            "after": {"count": 30, "mean_ms": 40, "api_key": "private-key"},
            "controls": {"before": {"count": 30}, "after": {"count": 30}},
            "snapshot": "private-snapshot", "reason": "private-log",
        },
        "outcome": {"state": "NO_IMPROVEMENT", "measured_gain": 0, "password": "private"},
    })
    assert "private" not in json.dumps(reduced)
    assert reduced["action"]["risk"] == "APPROVAL"
    assert reduced["experiment"]["status"] == "INCONCLUSIVE"
    assert reduced["experiment"]["before"] == {"count": 30, "mean_ms": 100}
    assert reduced["outcome"]["state"] == "NO_IMPROVEMENT"
    invalid = reduced_evidence({
        "action": {"kind": {"password": "private"}, "risk": "unsafe-string"},
        "experiment": {"status": "unsafe-string", "improvement": float("inf")},
    })
    assert invalid["action"]["kind"] is None and invalid["action"]["risk"] is None
    assert invalid["experiment"]["status"] is None
    assert "improvement" not in invalid["experiment"]


def test_judge_keeps_result_check_counts_and_blinds_types_and_digests():
    from app.modules.query_autopilot.judge import reduced_evidence

    proof = {"digest": "a" * 64, "types": ["private-type"], "row_count": 5,
             "bytes_compared": 100, "ordered": False}
    value = reduced_evidence({"experiment": {"result_comparison": {
        "before_proof": proof, "after_proof": proof,
        "checked_target_samples": 60, "checked_control_samples": 60,
        "target_equivalent": True, "control_equivalent": True, "snapshot_verified": True,
    }}})
    result = value["experiment"]["result_comparison"]
    assert result["digest_match"] is True and result["before_proof"]["column_count"] == 1
    assert result["checked_target_samples"] == 60
    assert "private" not in json.dumps(value) and "a" * 64 not in json.dumps(value)
    assert reduced_evidence({"experiment": value["experiment"]})["experiment"] == (
        value["experiment"]
    )
    invalid = reduced_evidence({"experiment": {"result_comparison": {
        "before_proof": None, "after_proof": ["private"],
    }}})
    assert invalid["experiment"]["result_comparison"] == {}


def test_judge_preserves_detector_identity_and_scoped_measurements_without_source_text():
    from app.modules.query_autopilot.judge import reduced_evidence

    reduced = reduced_evidence({"findings": [
        {"detector": "high_frequency", "severity": "info", "measured": {"count": 120},
         "evidence_ids": ["private-id"], "sql": "private-sql"},
        {"detector": "cardinality_error", "severity": "warning",
         "measured": {"q_error": 1500, "password": "private", "cpu_ms": float("inf")}},
        {"detector": "private-untrusted-text", "measured": {"count": 500}},
        {"detector": {"password": "private"}},
        {"detector": "rare_slow", "severity": {"password": "private"}},
    ]})
    assert reduced["detected_findings"] == [
        {"detector": "high_frequency", "severity": "info", "measured": {"count": 120}},
        {"detector": "cardinality_error", "severity": "warning", "measured": {"q_error": 1500}},
        {"detector": "rare_slow"},
    ]
    assert "private" not in json.dumps(reduced)
    assert "cpu_ms" not in reduced["measurements"]


def test_judge_retains_verified_operator_pairs_and_bounded_experiment_resources():
    from app.modules.query_autopilot.judge import reduced_evidence

    source = {
        "operators": {"query_id_verified": True, "query_id": "private-id", "operators": [
            {"operator": "OLAP_SCAN", "node_id": 0, "estimated_rows": 1, "actual_rows": 1500,
             "table": "private-table", "predicate": "private-predicate"},
            {"operator": "private-unknown", "node_id": 1, "actual_rows": 5},
        ]},
        "experiment": {
            "status": "INCONCLUSIVE",
            "reason": "materialized_view_rewrite_or_freshness_unproven",
            "before": {"count": 30, "resource": [
                {"cpu_ms": 10, "scanned_rows": 1500, "sql": "private-sql"},
            ] * 31},
        },
    }
    reduced = reduced_evidence(source)
    assert reduced["operator_evidence"] == [
        {"operator": "OLAP_SCAN", "node_id": 0, "estimated_rows": 1, "actual_rows": 1500},
    ]
    trial = reduced["experiment"]
    assert trial["reason"] == "materialized_view_rewrite_or_freshness_unproven"
    assert len(trial["before"]["resource_samples"]) == 30
    assert "private" not in json.dumps(reduced)
    assert reduced_evidence({"experiment": trial})["experiment"] == trial
    source["operators"]["query_id_verified"] = False
    source["experiment"]["reason"] = "private-error"
    invalid = reduced_evidence(source)
    assert invalid["operator_evidence"] == [] and "reason" not in invalid["experiment"]


def test_judge_retains_closed_plan_structure_and_estimates_without_identifiers_or_sql():
    from app.modules.query_autopilot.judge import reduced_evidence

    reduced = reduced_evidence({"plans": {
        "before": {"hash": "private-hash", "operators": [
            {"operator": "OlapScanNode", "node_id": 1, "attributes": {"TABLE": "private"},
             "estimates": {"cardinality": 500, "sql": "private", "cost": float("inf")}},
            {"operator": "private-operator", "estimates": {"cardinality": 1}},
        ]},
        "after": {"operators": [{"operator": "Project", "estimates": {"cardinality": 5}}] * 101},
    }})
    plans = reduced["plan_evidence"]
    assert plans["before"] == {
        "operators": [{"operator": "OLAP_SCAN", "estimates": {"cardinality": 500}}],
    }
    assert len(plans["after"]["operators"]) == 100
    assert "private" not in json.dumps(reduced)
    assert reduced_evidence({"plans": plans})["plan_evidence"] == plans


def test_diagnosis_explanation_uses_measured_numbers_and_discloses_sampling_limits():
    from app.modules.query_autopilot.guidance import explain

    text = explain(
        "UNKNOWN",
        {"count": 19, "p95_ms": 300, "statistics_stale": True},
        {"eligible": False, "historical_p95": 100},
    )
    assert "19 executions, P95 300 ms" in text
    assert "Historical P95 is 100 ms" in text
    assert "no percentile regression is established" in text
    assert "age alone" in text
    assert "secret" not in explain("UNKNOWN", {"p95_ms": "secret"}, {})
