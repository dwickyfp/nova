from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.query_autopilot.correctness import equivalent, prove_result
from app.modules.query_autopilot.detection import Detector, Facts, detect, diagnose, priority
from app.modules.query_autopilot.models import Policy, Scope
from app.modules.query_autopilot.statistics import (
    Distribution,
    Window,
    compare_baseline,
    mean_gain_evidence,
)


def distribution(value, count):
    result = Distribution()
    result.add(value, count)
    return result


def baseline(current=300, count=30):
    now = datetime(2026, 10, 1, tzinfo=UTC)
    return compare_baseline(
        Window(now, distribution(current, count)),
        [Window(now - timedelta(hours=i + 1), distribution(100, 40)) for i in range(3)],
    )


def test_merge_quantiles():
    a, b = distribution(10, 1000), distribution(1000, 10)
    a.merge(b)
    assert a.count == 1010
    assert a.quantile(0.95) == pytest.approx(10, rel=0.011)
    assert a.quantile(0.99) == pytest.approx(10, rel=0.011)
    assert Distribution.from_dict(a.as_dict()) == a
    assert distribution(100, 99).quantile(0.99) is None


def test_mean_gain_is_bounded_by_observed_variation_and_sample_count():
    result = mean_gain_evidence(distribution(100, 20), distribution(40, 20))
    assert result["repeatable_gain"]
    assert result["before_mean_ms"] == 100 and result["after_mean_ms"] == 40
    assert result["causality"] == "observational_only"
    noisy = distribution(40, 19)
    noisy.add(10000)
    assert noisy.quantile(0.95) == pytest.approx(40, rel=0.011)
    assert not mean_gain_evidence(distribution(100, 20), noisy)["repeatable_gain"]
    assert not mean_gain_evidence(distribution(100, 1), distribution(40, 1))["repeatable_gain"]
    assert mean_gain_evidence(Distribution(), Distribution())["mean_difference_margin_ms"] is None


def test_baseline_gates():
    assert baseline().eligible
    assert not baseline(count=19).eligible
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert not compare_baseline(
        Window(now, distribution(300, 30)),
        [Window(now - timedelta(hours=1), distribution(100, 1000))],
    ).eligible


def test_weekday_hour_baseline_matches_a_rolling_window_between_fixed_boundaries():
    now = datetime(2026, 10, 1, 13, 47, 12, tzinfo=UTC)
    seasonal = [
        Window(
            (now - timedelta(weeks=week)).replace(minute=minute, second=0),
            distribution(100, 40),
        )
        for week in range(1, 5) for minute in (0, 30)
    ]
    unrelated = [
        Window(now - timedelta(days=day, hours=1), distribution(1000, 100))
        for day in range(1, 8)
    ]
    result = compare_baseline(Window(now, distribution(300, 30)), seasonal + unrelated)
    assert result.seasonal and result.eligible
    assert result.windows == 8 and result.historical_count == 320
    assert result.historical_p95 == pytest.approx(100, rel=0.011)
    result = compare_baseline(Window(now, distribution(300, 30)), seasonal[2:] + unrelated)
    assert not result.seasonal


def test_all_detectors():
    facts = Facts(
        distribution(6000, 100),
        baseline(6000, 100),
        ("profile", "plan", "stats"),
        True,
        cpu_ms=70000,
        scanned_rows=1000000,
        output_rows=10,
        estimated_rows=10,
        actual_rows=10000,
        plan_changed=True,
        statistics_stale=True,
        table_growth_ratio=2,
        max_operator_ms=1000,
        median_operator_ms=100,
        operator_instance_count=5,
        queue_ms=2000,
        execution_ms=3000,
        saturated=True,
        compatible_aggregates=3,
        mv_eligible=True,
    )
    findings = detect(facts, Policy())
    assert {f.detector for f in findings} == set(Detector) - {Detector.RARE_SLOW}
    assert diagnose(findings, facts)[0].category == "RESOURCE_CONTENTION"
    ranked = {d.category: d for d in diagnose(findings, facts)}
    assert ranked["STATISTICS"].confidence < ranked["CARDINALITY_ESTIMATE"].confidence
    rare = Facts(distribution(6000, 1), baseline(6000, 1))
    assert [f.detector for f in detect(rare, Policy())] == [Detector.RARE_SLOW]
    assert priority(rare, 0).score < priority(facts, 0.9).score
    assert diagnose(detect(rare, Policy()), rare)[0].category == "UNKNOWN"


def test_age_and_temporal_change_not_causal():
    facts = Facts(
        distribution(100, 30), baseline(100), ("stats",), statistics_stale=True, plan_changed=True
    )
    assert not detect(facts, Policy())
    assert diagnose([], facts)[0].confidence == 0


def test_stale_statistics_does_not_outweigh_observed_cardinality_error():
    facts = Facts(
        distribution(300, 30),
        baseline(300),
        ("stats", "analyzed-profile"),
        statistics_stale=True,
        estimated_rows=10,
        actual_rows=10000,
    )
    diagnosis = diagnose(detect(facts, Policy()), facts)
    assert [d.category for d in diagnosis] == ["CARDINALITY_ESTIMATE", "STATISTICS"]
    assert "does not establish" in diagnosis[1].explanation


def test_sustained_regression():
    assert Detector.LATENCY_REGRESSION not in {
        f.detector for f in detect(Facts(distribution(300, 30), baseline()), Policy())
    }


def test_scope_partition():
    s = Scope(principal="alice", active_role="analyst", security_context_version=1)
    variants = [
        s,
        s.model_copy(update={"principal": "bob"}),
        s.model_copy(update={"active_role": "finance"}),
        s.model_copy(update={"security_context_version": 2}),
        s.model_copy(update={"policy_revision": "2"}),
    ]
    assert len({s.cohort_id for s in variants}) == 5


def proof(rows, ordered=False, **kwargs):
    return prove_result(
        rows, ("DECIMAL",), ordered=ordered, max_rows=100, max_bytes=10000, **kwargs
    )


def test_typed_multiplicity_and_order():
    assert equivalent(proof([[1], [2]]), proof([[2], [1]]))
    assert not equivalent(proof([[1], [1], [2]]), proof([[1], [2], [2]]))
    assert not equivalent(proof([[1], [2]], True), proof([[2], [1]], True))
    assert not equivalent(proof([[1]]), proof([[Decimal("1")]]))
    assert equivalent(proof([[1]], truncated=True), proof([[1]])) is None
    assert (
        prove_result([[1]], ("INT",), ordered=False, max_rows=1, max_bytes=1).reason
        == "comparison_byte_budget"
    )


@pytest.mark.parametrize(
    ("detector", "positive", "boundary", "missing"),
    [
        (
            Detector.ABSOLUTE_SLOW,
            {"latency": distribution(5000, 20)},
            {"latency": distribution(4999, 20)},
            {"latency": Distribution()},
        ),
        (
            Detector.LATENCY_REGRESSION,
            {"previous_regression": True},
            {"baseline": baseline(199)},
            {"baseline": baseline(count=19)},
        ),
        (
            Detector.HIGH_FREQUENCY,
            {"latency": distribution(100, 100)},
            {"latency": distribution(100, 99)},
            {"latency": Distribution()},
        ),
        (Detector.HIGH_RESOURCE, {"cpu_ms": 60000}, {"cpu_ms": 59999}, {"cpu_ms": None}),
        (
            Detector.PLAN_REGRESSION,
            {"plan_changed": True, "previous_regression": True},
            {"previous_regression": False},
            {"plan_changed": None},
        ),
        (
            Detector.CARDINALITY_ERROR,
            {"actual_rows": 1000, "estimated_rows": 100},
            {"actual_rows": 999},
            {"estimated_rows": None},
        ),
        (
            Detector.STATISTICS,
            {"statistics_stale": True, "actual_rows": 1000, "estimated_rows": 100},
            {"statistics_stale": False},
            {"actual_rows": None, "estimated_rows": None},
        ),
        (
            Detector.SCAN_AMPLIFICATION,
            {"scanned_rows": 100000, "output_rows": 1000},
            {"scanned_rows": 99999},
            {"output_rows": None},
        ),
        (
            Detector.OPERATOR_SKEW,
            {"max_operator_ms": 100, "median_operator_ms": 25, "operator_instance_count": 3},
            {"operator_instance_count": 2},
            {"median_operator_ms": None},
        ),
        (
            Detector.CONTENTION,
            {"queue_ms": 100, "execution_ms": 400, "saturated": True},
            {"queue_ms": 99},
            {"saturated": None},
        ),
        (
            Detector.MATERIALIZED_VIEW,
            {"mv_eligible": True, "compatible_aggregates": 2},
            {"compatible_aggregates": 1},
            {"mv_eligible": False},
        ),
        (
            Detector.RARE_SLOW,
            {"latency": distribution(5000, 19)},
            {"latency": distribution(5000, 20)},
            {"latency": Distribution()},
        ),
    ],
)
def test_each_detector_positive_boundary_and_missing_evidence(
    detector, positive, boundary, missing
):
    from dataclasses import replace

    facts = replace(Facts(distribution(300, 30), baseline(), evidence_ids=("fixture",)), **positive)
    assert detector in {item.detector for item in detect(facts, Policy())}
    for override in (boundary, missing):
        assert detector not in {
            item.detector for item in detect(replace(facts, **override), Policy())
        }
