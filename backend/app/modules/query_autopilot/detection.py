"""Detectors and diagnoses consume measured, scoped facts; they never execute SQL."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import StrEnum

from app.modules.query_autopilot.guidance import explain
from app.modules.query_autopilot.models import Policy
from app.modules.query_autopilot.statistics import Baseline, Distribution


class Detector(StrEnum):
    ABSOLUTE_SLOW = "absolute_slow"
    LATENCY_REGRESSION = "latency_regression"
    HIGH_FREQUENCY = "high_frequency"
    HIGH_RESOURCE = "high_resource"
    PLAN_REGRESSION = "plan_regression"
    CARDINALITY_ERROR = "cardinality_error"
    STATISTICS = "statistics"
    SCAN_AMPLIFICATION = "scan_amplification"
    OPERATOR_SKEW = "operator_skew"
    CONTENTION = "contention"
    MATERIALIZED_VIEW = "materialized_view"
    RARE_SLOW = "rare_slow"


@dataclass(frozen=True)
class Facts:
    latency: Distribution
    baseline: Baseline
    evidence_ids: tuple[str, ...] = ()
    previous_regression: bool = False
    cpu_ms: float | None = None
    scanned_rows: int | None = None
    output_rows: int | None = None
    estimated_rows: float | None = None
    actual_rows: float | None = None
    plan_changed: bool | None = None
    statistics_stale: bool | None = None
    statistics_missing: bool | None = None
    table_growth_ratio: float | None = None
    max_operator_ms: float | None = None
    median_operator_ms: float | None = None
    operator_instance_count: int = 0
    queue_ms: float | None = None
    execution_ms: float | None = None
    saturated: bool | None = None
    compatible_aggregates: int = 0
    mv_eligible: bool = False
    negative_evidence: tuple[str, ...] = ()
    memory_limit_exceeded: bool = False
    query_timed_out: bool = False


@dataclass(frozen=True)
class Finding:
    detector: Detector
    severity: str
    measured: dict[str, float | int | bool]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Diagnosis:
    category: str
    confidence: float
    evidence_ids: tuple[str, ...]
    counterevidence: tuple[str, ...]
    explanation: str


def detect(facts: Facts, policy: Policy) -> list[Finding]:
    results: list[Finding] = []
    n = facts.latency.count
    p95 = facts.latency.quantile(0.95) or 0
    b = facts.baseline
    regression = bool(
        b.eligible
        and b.current_p95 is not None
        and b.historical_p95 is not None
        and b.upper_envelope is not None
        and b.current_p95 >= b.historical_p95 * policy.regression_ratio
        and b.current_p95 - b.historical_p95 >= policy.regression_absolute_ms
        and b.current_p95 > b.upper_envelope
    )

    def add(kind: Detector, condition: bool, values: dict, severity: str = "warning") -> None:
        if condition:
            results.append(Finding(kind, severity, values, facts.evidence_ids))

    add(
        Detector.ABSOLUTE_SLOW,
        n >= 20 and p95 >= policy.absolute_slow_ms,
        {"p95_ms": p95, "count": n},
    )
    add(
        Detector.LATENCY_REGRESSION,
        regression and facts.previous_regression,
        {"current_p95_ms": p95, "baseline_p95_ms": b.historical_p95 or 0},
        "critical",
    )
    add(Detector.HIGH_FREQUENCY, n >= policy.high_frequency, {"count": n}, "info")
    add(Detector.HIGH_RESOURCE, (facts.cpu_ms or 0) >= 60000, {"cpu_ms": facts.cpu_ms or 0})
    add(
        Detector.PLAN_REGRESSION,
        facts.plan_changed is True and regression and facts.previous_regression,
        {"plan_changed": True, "p95_ms": p95},
    )
    cardinality = max(
        (facts.actual_rows or 0) / max(1, facts.estimated_rows or 0),
        (facts.estimated_rows or 0) / max(1, facts.actual_rows or 0),
    )
    cardinality_known = facts.actual_rows is not None and facts.estimated_rows is not None
    add(
        Detector.CARDINALITY_ERROR,
        cardinality_known
        and cardinality >= 10
        and max(facts.actual_rows or 0, facts.estimated_rows or 0) >= 1000,
        {"q_error": cardinality},
    )
    stats_support = (cardinality_known and cardinality >= 10) or (
        (facts.table_growth_ratio or 0) >= 1.5 and regression
    )
    add(
        Detector.STATISTICS,
        (facts.statistics_stale is True or facts.statistics_missing is True) and stats_support,
        {"q_error": cardinality, "growth_ratio": facts.table_growth_ratio or 0},
    )
    scan = (facts.scanned_rows or 0) / max(1, facts.output_rows or 0)
    add(
        Detector.SCAN_AMPLIFICATION,
        facts.output_rows is not None and (facts.scanned_rows or 0) >= 100000 and scan >= 100,
        {"scan_ratio": scan},
    )
    skew = (facts.max_operator_ms or 0) / max(1, facts.median_operator_ms or 0)
    add(
        Detector.OPERATOR_SKEW,
        facts.operator_instance_count >= 3
        and facts.median_operator_ms is not None
        and skew >= 4
        and (facts.max_operator_ms or 0) >= 100,
        {"operator_skew": skew, "operator_instance_count": facts.operator_instance_count},
    )
    add(
        Detector.CONTENTION,
        facts.saturated is True
        and (facts.queue_ms or 0) >= 100
        and facts.execution_ms is not None
        and (facts.queue_ms or 0) >= facts.execution_ms * 0.25,
        {"queue_ms": facts.queue_ms or 0, "execution_ms": facts.execution_ms or 0},
    )
    add(
        Detector.MATERIALIZED_VIEW,
        facts.mv_eligible and facts.compatible_aggregates >= 2 and n >= 20,
        {"compatible_aggregates": facts.compatible_aggregates, "count": n},
        "info",
    )
    add(
        Detector.RARE_SLOW,
        0 < n < 20 and facts.latency.maximum >= policy.absolute_slow_ms,
        {"max_ms": facts.latency.maximum, "count": n},
        "info",
    )
    return results


def diagnose(findings: list[Finding], facts: Facts) -> list[Diagnosis]:
    measurements = {
        **asdict(facts),
        "count": facts.latency.count,
        "p95_ms": facts.latency.quantile(0.95),
    }
    baseline = asdict(facts.baseline)
    types = {f.detector for f in findings}
    hypotheses = [
        (Detector.STATISTICS, "STATISTICS", 0.7),
        (Detector.CONTENTION, "RESOURCE_CONTENTION", 0.9),
        (Detector.OPERATOR_SKEW, "OPERATOR_SKEW", 0.85),
        (Detector.PLAN_REGRESSION, "PLAN_CHANGE", 0.8),
        (Detector.SCAN_AMPLIFICATION, "EXCESSIVE_SCAN", 0.75),
        (Detector.CARDINALITY_ERROR, "CARDINALITY_ESTIMATE", 0.75),
    ]
    ranked = [
        Diagnosis(
            category,
            max(0.1, confidence - 0.15 * len(facts.negative_evidence)),
            facts.evidence_ids,
            facts.negative_evidence,
            explain(category, measurements, baseline),
        )
        for detector, category, confidence in hypotheses
        if detector in types and facts.evidence_ids
    ]
    for present, category in (
        (
            facts.memory_limit_exceeded,
            "ENGINE_MEMORY_LIMIT",
        ),
        (
            facts.query_timed_out,
            "ENGINE_TIMEOUT",
        ),
    ):
        if present and facts.evidence_ids:
            ranked.append(
                Diagnosis(
                    category,
                    0.9,
                    facts.evidence_ids,
                    facts.negative_evidence,
                    explain(category, measurements, baseline),
                )
            )
    return sorted(ranked, key=lambda d: (-d.confidence, d.category)) or [
        Diagnosis(
            "UNKNOWN",
            0,
            facts.evidence_ids,
            facts.negative_evidence,
            explain("UNKNOWN", measurements, baseline),
        )
    ]


@dataclass(frozen=True)
class Priority:
    score: float
    contributions: dict[str, float]
    estimated_gain: float
    historical_outcomes: int
    estimate_source: str = "heuristic"


def priority(
    facts: Facts,
    confidence: float,
    *,
    expected_gain: float = 0.2,
    cost: float = 0.5,
    risk: float = 0.5,
    outcomes: list[float] | None = None,
) -> Priority:
    history = outcomes or []
    if history:
        expected_gain = sum(max(-1, min(1, v)) for v in history) / len(history)

    def bounded(value):
        return max(0, min(1, value))

    baseline = facts.baseline.historical_p95 or 1
    current = facts.baseline.current_p95 or baseline
    contributions = {
        "workload_impact": 25 * bounded(facts.latency.total / 300000),
        "frequency": 15 * bounded(math.log1p(facts.latency.count) / math.log1p(1000)),
        "degradation": 20 * bounded((current / baseline - 1) / 2),
        "expected_gain": 15 * bounded(expected_gain),
        "confidence": 25 * bounded(confidence),
        "implementation_cost": -10 * bounded(cost),
        "risk": -20 * bounded(risk),
    }
    if facts.latency.count < 20:
        contributions["rare_workload_discount"] = -30
    return Priority(
        round(max(0, sum(contributions.values())), 2),
        {k: round(v, 2) for k, v in contributions.items()},
        expected_gain,
        len(history),
        "observed_outcomes" if history else "heuristic",
    )


def serialize_findings(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]
