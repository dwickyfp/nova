import json
from unittest.mock import AsyncMock

import pytest

from tests.benchmark.query_autopilot.measured_judge import review, review_cases


def test_review_never_includes_sql_logs_credentials_or_fixture_diagnoses():
    value = review_cases({"cases": [
        {"case": "I", "diagnosis": [{"category": "ENGINE_MEMORY_LIMIT"}]},
        {"case": "E", "phases": {"before": {
            "query_id": "query-id", "sql": "secret-sql", "password": "private",
            "findings": [{"detector": "cardinality_error", "measured": {"q_error": 100}}],
            "diagnosis": [{"category": "CARDINALITY_ESTIMATE", "confidence": 0.9}],
        }}},
    ]}, None, {"wall_clock_historical_acceptance": "NOT_RUN"})
    assert len(value) == 1
    serialized = json.dumps(value)
    assert "secret-sql" not in serialized and "private" not in serialized
    assert "query-id" not in serialized and "ENGINE_MEMORY_LIMIT" not in serialized
    assert value[0]["evidence"]["proposed_next_steps"]
    assert value[0]["evidence"]["evidence_count"] == 1
    assert value[0]["evidence"]["execution_authority"] == "none"


async def test_provider_errors_are_retained_as_failures(monkeypatch, tmp_path):
    from tests.benchmark.query_autopilot import measured_judge

    monkeypatch.setattr(measured_judge.db, "init_system_pool", AsyncMock())
    monkeypatch.setattr(measured_judge.db, "close_system_pool", AsyncMock())
    monkeypatch.setattr(measured_judge, "judge", AsyncMock(side_effect=RuntimeError("private")))
    value = await review([{ "case": "E", "review": {}, "evidence": {} }], tmp_path)
    assert value["status"] == "FAIL" and value["valid_cases"] == 0
    assert value["mean"] is None
    assert "private" not in (tmp_path / "measured-judge.json").read_text()


async def test_empty_measurements_cannot_be_a_passing_judge_run(tmp_path):
    with pytest.raises(ValueError, match="no_retained_measured_cases"):
        await review([], tmp_path)


async def test_unavailable_registry_produces_a_failed_report_without_provider_calls(
    monkeypatch, tmp_path,
):
    from tests.benchmark.query_autopilot import measured_judge

    monkeypatch.setattr(
        measured_judge.db, "init_system_pool", AsyncMock(side_effect=OSError("private-endpoint")),
    )
    monkeypatch.setattr(measured_judge.db, "close_system_pool", AsyncMock())
    provider = AsyncMock()
    monkeypatch.setattr(measured_judge, "judge", provider)
    result = await review([
        {"case": "A", "review": {}, "evidence": {}},
        {"case": "B", "review": {}, "evidence": {}},
    ], tmp_path)
    assert result["status"] == "FAIL" and result["valid_cases"] == 0
    assert result["requested_cases"] == 2 and result["mean"] is None
    assert all(r["reason"] == "control_plane_unavailable" for r in result["cases"])
    assert all(r["initialization_error_type"] == "OSError" for r in result["cases"])
    provider.assert_not_awaited()
    assert "private-endpoint" not in (tmp_path / "measured-judge.json").read_text()
    progress = json.loads((tmp_path / "measured-judge-progress.json").read_text())
    assert progress["status"] == "RUNNING"


def test_measured_aggregate_review_preserves_structure_but_does_not_invent_a_trial():
    from tests.benchmark.query_autopilot.scenarios_live import facts_for

    value = review_cases({"cases": [{
        "case": "F-detection", "latency": facts_for([10] * 120).latency.as_dict(),
        "query_ids": ["private-engine-id"],
        "facts": {"mv_eligible": True, "grouping_key_count": 1,
                  "aggregate_expression_count": 1, "replay_eligible": True,
                  "compatible_aggregates": 10, "sql": "private-sql"},
        "findings": [{"detector": "materialized_view"}],
    }]}, None, None)[0]
    facts = value["evidence"]["measurements"]
    assert facts["count"] == 120 and facts["grouping_key_count"] == 1
    assert facts["aggregate_expression_count"] == 1 and facts["replay_eligible"] is True
    assert value["evidence"]["action"]["risk"] == "APPROVAL"
    assert value["evidence"]["experiment"]["status"] is None
    assert value["evidence"]["action"]["state"] is None
    assert value["evidence"]["evidence_count"] == 1
    assert "private" not in json.dumps(value)


def test_measured_review_preserves_the_runtime_detector_vocabulary():
    from app.modules.query_autopilot.detection import Detector

    for detector in Detector:
        cases = review_cases({"cases": [{
            "case": "F-detection", "findings": [{"detector": detector}],
        }]}, None, None)
        assert cases[0]["review"]["findings"] == [{"detector": detector}]


def test_detector_measurements_survive_both_measured_review_projections():
    result = review_cases({"cases": [{
        "case": "F-detection", "findings": [
            {"detector": "high_frequency", "severity": "info", "measured": {"count": 120},
             "evidence_ids": ["private-id"]},
        ],
    }]}, None, None)[0]
    expected = [{"detector": "high_frequency", "severity": "info", "measured": {"count": 120}}]
    assert result["review"]["findings"] == expected
    assert result["evidence"]["detected_findings"] == expected
    assert "private" not in json.dumps(result)


def test_verified_operator_evidence_survives_measured_review_redaction():
    result = review_cases({"cases": [{"case": "E", "phases": {"before": {
        "query_id": "private-id", "findings": [{"detector": "cardinality_error"}],
        "operators": {"query_id_verified": True, "operators": [
            {"operator": "OLAP_SCAN", "node_id": 0, "estimated_rows": 1, "actual_rows": 1500},
        ]},
    }}}]}, None, None)[0]
    assert result["evidence"]["operator_evidence"] == [
        {"operator": "OLAP_SCAN", "node_id": 0, "estimated_rows": 1, "actual_rows": 1500},
    ]
    assert "private" not in json.dumps(result)


def test_measured_mv_trial_retains_inconclusive_verdict_without_exposing_ids():
    case = review_cases({"cases": []}, None, None, {
        "evidence_kind": "real_native_engine_sandbox", "candidate_state": "INCONCLUSIVE",
        "native_rewrite_observed": True, "sandbox_object_ready": True,
        "ranger_acceptance": "NOT_RUN", "experiment": {"result": {
            "status": "INCONCLUSIVE", "correctness": "EQUIVALENT", "repetitions": 30,
            "before": {"count": 30, "mean_ms": 100, "query_ids": ["private-before-id"]},
            "after": {"count": 30, "mean_ms": 40, "query_ids": ["private-after-id"]},
        }, "plans": {"before": {"operators": [
            {"operator": "OlapScanNode", "estimates": {"cardinality": 500},
             "attributes": {"TABLE": "private-table"}},
        ]}}},
    })[0]
    assert case["review"]["experiment"]["status"] == "INCONCLUSIVE"
    assert case["review"]["action"]["risk"] == "APPROVAL"
    assert case["review"]["facts"]["ranger_acceptance_proven"] is False
    assert case["evidence"]["plan_evidence"]["before"] == {
        "operators": [{"operator": "OLAP_SCAN", "estimates": {"cardinality": 500}}],
    }
    assert "private" not in json.dumps(case)


def test_queue_projection_uses_a_bound_execution_instead_of_an_unbound_maximum():
    value = review_cases({"cases": []}, {"phases": {"contended": {
        "count": 2, "findings": [{"detector": "contention"}],
        "observations": [
            {"query_id": "bound", "facts": {"queue_ms": 200, "execution_ms": 10},
             "pending_observed": True, "limit_occupied": True},
            {"query_id": "unbound", "facts": {"queue_ms": 1000, "execution_ms": 1},
             "pending_observed": False, "limit_occupied": False},
        ],
    }}}, None)[0]
    facts = value["review"]["facts"]
    assert facts["queue_ms"] == 200 and facts["execution_ms"] == 10
    assert facts["saturated"] is True
    assert facts["bound_pending_samples"] == 1 and facts["counter_pair_sample_count"] == 2
    profiles = value["evidence"]["execution_profile_samples"]
    assert [p["queue_ms"] for p in profiles] == [200, 1000]
    assert [p["pending_observed"] for p in profiles] == [True, False]


def governed_fixture():
    from app.modules.query_autopilot.models import Scope

    scope = Scope(principal="alice", active_role="marketing", security_context_version=1)
    return {
        "evidence_kind": "isolated_governed_complete_cycle", "status": "FAIL",
        "candidate": {"id": "private-candidate", "kind": "MATERIALIZED_VIEW",
                      "family_id": "private-family", "evidence_ids": ["private-proof"],
                      "scope": scope.model_dump(mode="json"),
                      "state": "INCONCLUSIVE", "sql": "private-sql"},
        "experiments": [{
            "candidate_id": "private-candidate", "plans": {}, "result": {
                "status": "REGRESSED", "reason": "target_regressed",
                "correctness": "EQUIVALENT", "repetitions": 30,
                "before": {"count": 30, "mean_ms": 40,
                           "query_ids": [f"private-before-{i}" for i in range(30)]},
                "after": {"count": 30, "mean_ms": 100,
                          "query_ids": [f"private-after-{i}" for i in range(30)]},
            },
        }],
        "ranger_acceptance": [{
            "id": "private-proof", "family_id": "private-family", "cohort_id": scope.cohort_id,
            "query_ids": [f"private-governed-{i}" for i in range(60)],
            "kind": "ranger_acceptance", "source": "patched_fe_acceptance",
            "availability": "available", "summary": {
                "row_filter": "PASS", "masking": "PASS", "complete_result_comparisons": 60,
                "scope": scope.model_dump(mode="json"),
                "password": "private-password",
            },
        }],
        "action": None, "outcome": None,
    }


def test_governed_review_retains_a_rejected_regression_without_inventing_an_outcome():
    cases = review_cases({"cases": []}, None, None, governed_cycles=(governed_fixture(),))
    assert len(cases) == 1
    evidence = cases[0]["evidence"]
    assert evidence["experiment"]["status"] == "REGRESSED"
    assert evidence["experiment"]["reason"] == "target_regressed"
    assert evidence["action"]["state"] == "INCONCLUSIVE"
    assert evidence["measurements"]["ranger_acceptance_proven"] is True
    assert evidence["outcome"]["state"] is None
    assert evidence["evidence_count"] == 60
    assert "private" not in json.dumps(cases)


@pytest.mark.parametrize("change", ["wrong_candidate", "short_phase", "uncorrelated", "unmeasured"])
def test_governed_review_requires_complete_correlated_measurements(change):
    fixture = governed_fixture()
    if change == "wrong_candidate":
        fixture["candidate"]["id"] = "other"
    elif change == "short_phase":
        fixture["experiments"][0]["result"]["after"]["count"] = 29
    elif change == "uncorrelated":
        fixture["experiments"][0]["result"]["after"]["query_ids"].pop()
    else:
        fixture["experiments"][0].pop("result")
    assert review_cases({"cases": []}, None, None, governed_cycles=(fixture,)) == []


@pytest.mark.parametrize("change", ["unavailable", "unproved_mask", "partial", "native"])
def test_governed_review_does_not_infer_missing_security_proof(change):
    fixture = governed_fixture()
    proof = fixture["ranger_acceptance"][0]
    if change == "unavailable":
        proof["availability"] = "unavailable"
    elif change == "unproved_mask":
        proof["summary"]["masking"] = "FAIL"
    elif change == "partial":
        proof["summary"]["complete_result_comparisons"] = 59
    else:
        proof["source"] = "native_fixture"
    value = review_cases({"cases": []}, None, None, governed_cycles=(fixture,))[0]
    assert value["evidence"]["measurements"]["ranger_acceptance_proven"] is False


def test_governed_review_preserves_observed_control_regression_and_verified_compensation():
    fixture = governed_fixture()
    fixture["candidate"]["state"] = "ROLLED_BACK"
    fixture["outcome"] = {
        "state": "REGRESSED", "reason": "untargeted_workload_regressed",
        "rollback": "verified_object_removed", "correctness": "sandbox_equivalence_only",
        "causality": "observational_post_application_window",
        "gain_evidence": {"before_mean_ms": 100, "after_mean_ms": 40,
                          "repeatable_gain": True, "mean_difference_margin_ms": 10,
                          "secret": "private"},
        "controls": {"private-control-id": {
            "before": {"count": 30, "total": 3000, "maximum": 120, "bins": {"secret": 1}},
            "after": {"count": 30, "total": 9000, "maximum": 400},
        }},
    }
    evidence = review_cases({"cases": []}, None, None, governed_cycles=(fixture,))[0]["evidence"]
    outcome = evidence["outcome"]
    assert outcome["state"] == "REGRESSED"
    assert outcome["reason"] == "untargeted_workload_regressed"
    assert outcome["rollback"] == "verified_object_removed"
    assert outcome["gain_evidence"]["repeatable_gain"] is True
    assert outcome["controls"][0]["after"]["total"] == 9000
    assert evidence["stage_availability"]["outcome"] == "AVAILABLE"
    assert "private" not in json.dumps(evidence) and "secret" not in json.dumps(evidence)


def test_unassessed_stages_and_missing_evidence_remain_explicit_after_redaction():
    from app.modules.query_autopilot.judge import reduced_evidence

    value = reduced_evidence({
        "action": {"kind": "private"}, "experiment": {"status": "private"},
        "outcome": {"state": "private", "reason": "private", "rollback": "private"},
        "baseline": {"eligible": False, "historical_count": 0, "current_count": 1},
    })
    assert all(stage == "NOT_ASSESSED" for stage in value["stage_availability"].values())
    assert value["evidence_availability"] == {
        "historical_baseline": "INSUFFICIENT_HISTORY", "operator_profile": "UNAVAILABLE",
        "plan_comparison": "UNAVAILABLE", "execution_profile": "UNAVAILABLE",
    }
    assert value["outcome"]["reason"] is value["outcome"]["rollback"] is None
    assert "private" not in json.dumps(value)


def test_trial_mean_gain_is_explained_against_variation_and_selective_profiles():
    from app.modules.query_autopilot.judge import reduced_evidence

    trial = {
        "status": "NO_IMPROVEMENT", "reason": "benefit_not_repeatable_or_below_threshold",
        "before": {"count": 30, "mean_ms": 102, "stddev_ms": 102,
                   "resource": [{"execution_ms": 30}]},
        "after": {"count": 30, "mean_ms": 68, "stddev_ms": 61,
                  "resource": [{"execution_ms": 5}]},
    }
    case = reduced_evidence({"experiment": trial})
    second = reduced_evidence({"experiment": case["experiment"]})
    assert second["experiment"]["gain_evidence"]["repeatable_gain"] is False
    assert second["experiment"]["gain_evidence"]["mean_difference_margin_ms"] > 34
    sampling = second["experiment"]["before"]["resource_sampling"]
    assert sampling["sample_count"] == 1 and sampling["population_count"] == 30
    assert sampling["latency_basis"] == "selective_engine_profiles"
    assert second["proposed_next_steps"][0].startswith("Withhold application.")


def test_profile_pairs_are_bounded_correlated_and_redacted_across_both_projections():
    from app.modules.query_autopilot.judge import reduced_evidence

    pairs = [{"queue_ms": 500, "execution_ms": 10, "query_id_verified": True,
              "pending_observed": True, "limit_occupied": True,
              "sql": "private-sql", "query_id": "private-id", "password": "private-secret"}]
    evidence = reduced_evidence({"profile_samples": [
        {"queue_ms": 1000, "execution_ms": 1, "query_id_verified": False},
        {"queue_ms": float("nan"), "execution_ms": 1, "query_id_verified": True},
        *pairs * 120,
    ]})
    assert len(evidence["execution_profile_samples"]) == 98
    second = reduced_evidence({"profile_samples": evidence["execution_profile_samples"]})
    assert second["execution_profile_samples"] == evidence["execution_profile_samples"]
    assert second["evidence_availability"]["execution_profile"] == "AVAILABLE"
    assert "private" not in json.dumps(second)


@pytest.mark.parametrize("changed", [False, True])
def test_durable_review_uses_the_actual_scoped_baseline_including_failed_run_history(changed):
    from app.modules.query_autopilot.models import Scope

    scope = Scope(principal="private-user", active_role="private-role", security_context_version=1)
    family = {
        "family_id": "private-family", "cohort_id": scope.cohort_id,
        "scope": scope.model_dump(mode="json"), "window": "2026-10-02T15:03:30+00:00",
        "baseline": {"eligible": True, "current_count": 30, "historical_count": 154,
                     "historical_p95": 1254, "current_p95": 4181, "upper_envelope": 4259},
        "findings": [{"detector": "contention", "measured": {
            "queue_ms": 3805, "execution_ms": 3.258,
        }}], "diagnosis": [{"category": "RESOURCE_CONTENTION", "confidence": 0.9}],
    }
    if changed:
        family["scope"] = {**family["scope"], "principal": "other"}
    pipeline = {"evidence_kind": "isolated_collected_wall_clock_workload", "status": "FAIL",
                "durable_pipeline": {"scope": scope.model_dump(mode="json"),
                                     "family_id": "private-family", "family": family},
                "contention": {"phases": {"contended": {"observations": [{
                    "query_id": "private-id", "observed_at": "2026-10-02T15:30:00+00:00",
                    "facts": {"queue_ms": 3805, "execution_ms": 3.258},
                    "pending_observed": True, "limit_occupied": True,
                }]}}}}
    cases = review_cases({}, None, None, pipeline=pipeline)
    if changed:
        assert cases == []
        return
    evidence = cases[0]["evidence"]
    assert cases[0]["case"] == "D-durable"
    assert evidence["baseline"]["historical_count"] == 154
    assert evidence["baseline"]["upper_envelope"] == 4259
    assert [f["detector"] for f in evidence["detected_findings"]] == ["contention"]
    assert evidence["execution_profile_samples"][0]["queue_ms"] == 3805
    assert "private" not in json.dumps(cases)


@pytest.mark.parametrize("tamper", [None, "binding", "candidate", "role", "actor"])
def test_governed_approval_requires_the_exact_durable_administrative_binding(tamper):
    from app.modules.query_autopilot.models import Candidate

    fixture = governed_fixture()
    fixture["candidate"].pop("sql")
    candidate = Candidate.model_validate({
        **fixture["candidate"], "targets": ["private-table"],
        "enrollment_id": "private-enrollment", "enrollment_version": 1, "policy_version": 1,
    })
    candidate = candidate.model_copy(update={
        "approval_digest": candidate.binding, "approved_by": "private-admin",
    })
    fixture["candidate"] = candidate.model_dump(mode="json")
    action = {"binding": candidate.binding, "candidate_id": candidate.id,
              "actor": candidate.approved_by, "actor_role": "ACCOUNTADMIN", "state": "APPLIED"}
    if tamper:
        key = {"binding": "binding", "candidate": "candidate_id", "role": "actor_role",
               "actor": "actor"}[tamper]
        action[key] = "private-invalid"
    fixture["action"] = action
    evidence = review_cases({"cases": []}, None, None, governed_cycles=(fixture,))[0]["evidence"]
    assert evidence["approval"]["candidate_binding_verified"] is (tamper is None)
    assert evidence["evaluation_scope"] == "isolated_fixture"
    assert evidence["execution_authority_subject"] == "reviewer"
    assert "private" not in json.dumps(evidence)
