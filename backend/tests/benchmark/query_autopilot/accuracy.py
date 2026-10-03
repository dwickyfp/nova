"""Deterministic scenario labels are separate from live engine measurements."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from app.modules.query_autopilot.detection import Detector, Facts, detect, diagnose
from app.modules.query_autopilot.models import Policy
from app.modules.query_autopilot.statistics import Distribution, Window, compare_baseline


def distribution(ms, count):
    value = Distribution()
    value.add(ms, count)
    return value


def cases():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    history = [Window(now - timedelta(minutes=30 * i), distribution(100, 40)) for i in range(2, 5)]

    def facts(ms=100, count=30, **kwargs):
        d = distribution(ms, count)
        return Facts(d, compare_baseline(Window(now, d), history), **kwargs)

    return [
        ("A-high-frequency", facts(count=500), {Detector.HIGH_FREQUENCY}, "UNKNOWN"),
        ("B-rare-slow", facts(6000, 1), {Detector.RARE_SLOW}, "UNKNOWN"),
        ("C-parameter-family", facts(), set(), "UNKNOWN"),
        (
            "D-sustained-regression",
            facts(300, previous_regression=True),
            {Detector.LATENCY_REGRESSION},
            "UNKNOWN",
        ),
        (
            "E-cardinality-stats",
            facts(
                300,
                previous_regression=True,
                evidence_ids=("stats", "operator"),
                statistics_stale=True,
                estimated_rows=10,
                actual_rows=10000,
            ),
            {Detector.LATENCY_REGRESSION, Detector.STATISTICS, Detector.CARDINALITY_ERROR},
            "CARDINALITY_ESTIMATE",
        ),
        (
            "F-aggregate",
            facts(count=30, compatible_aggregates=3, mv_eligible=True),
            {Detector.MATERIALIZED_VIEW},
            "UNKNOWN",
        ),
        (
            "G-contention",
            facts(
                700, evidence_ids=("queue", "group"), queue_ms=500, execution_ms=100, saturated=True
            ),
            {Detector.CONTENTION},
            "RESOURCE_CONTENTION",
        ),
        ("H-negative-benefit", facts(), set(), "UNKNOWN"),
        ("I-logs-only", facts(evidence_ids=("log",)), set(), "UNKNOWN"),
        ("age-only-negative", facts(statistics_stale=True), set(), "UNKNOWN"),
        ("insufficient-current", facts(300, 19, previous_regression=True), set(), "UNKNOWN"),
        ("unsustained-negative", facts(300), set(), "UNKNOWN"),
        ("resource", facts(cpu_ms=70000), {Detector.HIGH_RESOURCE}, "UNKNOWN"),
        (
            "scan",
            facts(evidence_ids=("scan",), scanned_rows=1000000, output_rows=100),
            {Detector.SCAN_AMPLIFICATION},
            "EXCESSIVE_SCAN",
        ),
        (
            "skew",
            facts(
                2500,
                evidence_ids=("profile",),
                max_operator_ms=2000,
                median_operator_ms=100,
                operator_instance_count=5,
            ),
            {Detector.OPERATOR_SKEW},
            "OPERATOR_SKEW",
        ),
        (
            "plan",
            facts(300, previous_regression=True, evidence_ids=("plan",), plan_changed=True),
            {Detector.LATENCY_REGRESSION, Detector.PLAN_REGRESSION},
            "PLAN_CHANGE",
        ),
    ]


def scorecard():
    tp = fp = fn = top1 = topk = supported = 0
    results = []
    for name, facts, expected, root in cases():
        found = detect(facts, Policy())
        actual = {f.detector for f in found}
        tp += len(expected & actual)
        fp += len(actual - expected)
        fn += len(expected - actual)
        diagnosis = diagnose(found, facts)
        if root != "UNKNOWN":
            supported += 1
            top1 += diagnosis[0].category == root
            topk += root in [d.category for d in diagnosis[:3]]
        results.append(
            {
                "case": name,
                "expected": sorted(expected),
                "actual": sorted(actual),
                "diagnosis": [asdict(d) for d in diagnosis],
                "pass": expected == actual,
            }
        )
    return {
        "evidence_kind": "deterministic_fixture",
        "cases": results,
        "detector_counts": {"true_positive": tp, "false_positive": fp, "false_negative": fn},
        "precision": tp / max(1, tp + fp),
        "recall": tp / max(1, tp + fn),
        "rca": {
            "supported_cases": supported,
            "top1_correct": top1,
            "topk_correct": topk,
            "top1": top1 / max(1, supported),
            "topk": topk / max(1, supported),
        },
    }
