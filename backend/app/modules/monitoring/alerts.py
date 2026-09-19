"""Alert rule engine — production alert rules evaluated against live state.

This is Nova's in-console counterpart to a Prometheus + Alertmanager stack. The
rules and thresholds are adapted from the production StarRocks automation lab
(``abdull93/StarRocks-Production-automation``) but evaluated against engine SQL
instead of a time-series store, so they run with **zero extra infrastructure**:
every check reads ``information_schema`` views and the engine's own ``SHOW``
output on the system pool.

Design constraints
------------------

- **Read-only.** Every rule is a pure observation. Nothing here writes to
  StarRocks; the readiness audit and alerts surfaces are diagnostic.
- **No fabrication.** A metric the engine does not expose is ``None`` (unknown),
  never a manufacture zero. A rule whose inputs are unknown is reported as
  ``unknown``, not ``ok``, so "no data" can never masquerade as "healthy"
  (same contract as ``frontend/src/features/cluster/api.ts:metricValue``).
- **Severity, not just status.** ``critical`` means the cluster is serving
  degraded or will run out of capacity; ``warning`` is a threshold crossing
  worth an operator's attention. The set mirrors the lab's Alertmanager rules.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


#: Highest-first ordering used to summarise a result set.
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class AlertResult:
    """Outcome of one rule against the currently observed metrics."""

    id: str
    title: str
    category: str
    severity: str
    status: str  # "firing" | "ok" | "unknown"
    value: float | int | str | None
    threshold: str
    description: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "severity": self.severity,
            "status": self.status,
            "value": self.value,
            "threshold": self.threshold,
            "description": self.description,
            "detail": self.detail,
        }


@dataclass
class AlertMetrics:
    """Flat snapshot of every metric the rules read.

    Each attribute is ``None`` when the underlying probe failed or the engine
    does not expose it. Rules must treat ``None`` as unknown.
    """

    # Membership / liveness (SHOW FRONTENDS / SHOW BACKENDS)
    fe_total: int | None = None
    fe_alive: int | None = None
    fe_leader_count: int | None = None
    be_total: int | None = None
    be_alive: int | None = None

    # FE journal / metadata
    fe_max_journal_lag: int | None = None
    fe_meta_log_count: int | None = None

    # FE metrics (information_schema.fe_metrics)
    query_err_rate: float | None = None
    query_latency_p95_ms: float | None = None
    fe_heap_used_ratio: float | None = None

    # Storage / compaction
    max_compaction_score: float | None = None
    be_disk_used_pct_max: float | None = None

    # Error counts for cross-checks
    be_compaction_failures: int | None = None

    # Whether the engine itself was reachable while building the snapshot.
    engine_reachable: bool = True


# ── Rule definitions ─────────────────────────────────────────────────


@dataclass(frozen=True)
class AlertRule:
    """A single alert condition.

    ``evaluate`` receives the metric snapshot and returns ``(firing, detail)``.
    ``threshold`` is human-readable and shown in the UI. ``min_severity_metric``
    is the attribute whose absence makes the rule ``unknown``.
    """

    id: str
    title: str
    category: str
    severity: str
    description: str
    threshold: str
    metric: str
    evaluate: Callable[[Any], bool]
    detail: Callable[[Any], str]


def _detail_value(metric: str) -> Callable[[AlertMetrics], str]:
    def _fmt(m: AlertMetrics) -> str:
        return f"{metric}={getattr(m, metric)!r}"

    return _fmt


def _detail_be_down(m: AlertMetrics) -> str:
    return f"alive={m.be_alive} total={m.be_total}"


def _detail_disk(m: AlertMetrics) -> str:
    return f"max disk used={m.be_disk_used_pct_max}%"


#: The rule set. Order is stable and used by the API for deterministic output.
ALERT_RULES: tuple[AlertRule, ...] = (
    AlertRule(
        id="starrocks_fe_down",
        title="StarRocks FE is down",
        category="membership",
        severity="critical",
        description="No FE node is Alive and serving; the cluster cannot answer SQL.",
        threshold="alive FE >= 1",
        metric="fe_alive",
        evaluate=lambda m: m.fe_alive is not None and m.fe_alive < 1,
        detail=_detail_value("fe_alive"),
    ),
    AlertRule(
        id="starrocks_fe_leader_missing",
        title="No FE Leader elected",
        category="membership",
        severity="critical",
        description="The FE voting group has no single Leader; metadata writes stall.",
        threshold="leader count == 1",
        metric="fe_leader_count",
        evaluate=lambda m: m.fe_leader_count is not None and m.fe_leader_count != 1,
        detail=_detail_value("fe_leader_count"),
    ),
    AlertRule(
        id="starrocks_fe_quorum_lost",
        title="FE quorum at risk",
        category="membership",
        severity="critical",
        description="Fewer than two Alive voting FEs; quorum loss blocks metadata writes.",
        threshold="alive FE >= 2",
        metric="fe_alive",
        evaluate=lambda m: m.fe_alive is not None and m.fe_alive < 2,
        detail=_detail_value("fe_alive"),
    ),
    AlertRule(
        id="starrocks_be_down",
        title="StarRocks BE is down",
        category="membership",
        severity="critical",
        description="No BE node is Alive; queries fail even if the FE is healthy.",
        threshold="alive BE >= 1",
        metric="be_alive",
        evaluate=lambda m: m.be_alive is not None and m.be_alive < 1,
        detail=_detail_be_down,
    ),
    AlertRule(
        id="starrocks_be_replica_risk",
        title="BE replica headroom low",
        category="membership",
        severity="warning",
        description=(
            "Fewer than three Alive BEs: a replication_num=3 table can no longer "
            "place a full replica set and a single further loss can drop a tablet."
        ),
        threshold="alive BE >= 3",
        metric="be_alive",
        evaluate=lambda m: m.be_alive is not None and m.be_alive < 3,
        detail=_detail_be_down,
    ),
    AlertRule(
        id="starrocks_fe_journal_lag_high",
        title="FE journal replay lag high",
        category="replication",
        severity="warning",
        description="A follower is far behind the leader's replayed journal id.",
        threshold="lag <= 1000",
        metric="fe_max_journal_lag",
        evaluate=lambda m: m.fe_max_journal_lag is not None and m.fe_max_journal_lag > 1000,
        detail=_detail_value("fe_max_journal_lag"),
    ),
    AlertRule(
        id="starrocks_fe_metadata_logs_high",
        title="FE metadata log count high",
        category="replication",
        severity="warning",
        description="Metadata log count indicates checkpoint pressure on the FE.",
        threshold="meta_log_count <= 100000",
        metric="fe_meta_log_count",
        evaluate=lambda m: m.fe_meta_log_count is not None and m.fe_meta_log_count > 100000,
        detail=_detail_value("fe_meta_log_count"),
    ),
    AlertRule(
        id="starrocks_query_error_rate_high",
        title="Query error rate high",
        category="query",
        severity="warning",
        description="More than 10% of queries are failing in the current window.",
        threshold="error rate <= 0.10",
        metric="query_err_rate",
        evaluate=lambda m: m.query_err_rate is not None and m.query_err_rate > 0.10,
        detail=lambda m: f"error_rate={m.query_err_rate:.4f}"
        if m.query_err_rate is not None
        else "error_rate=?",
    ),
    AlertRule(
        id="starrocks_query_latency_high",
        title="P95 query latency high",
        category="query",
        severity="warning",
        description="The 95th-percentile query latency exceeds 5 seconds.",
        threshold="p95 <= 5000 ms",
        metric="query_latency_p95_ms",
        evaluate=lambda m: m.query_latency_p95_ms is not None and m.query_latency_p95_ms > 5000,
        detail=_detail_value("query_latency_p95_ms"),
    ),
    AlertRule(
        id="starrocks_fe_heap_high",
        title="FE JVM heap usage high",
        category="resource",
        severity="warning",
        description="FE heap is above 90% of its maximum; GC pressure will follow.",
        threshold="heap used ratio <= 0.90",
        metric="fe_heap_used_ratio",
        evaluate=lambda m: m.fe_heap_used_ratio is not None and m.fe_heap_used_ratio > 0.90,
        detail=lambda m: f"heap_used_ratio={m.fe_heap_used_ratio:.4f}"
        if m.fe_heap_used_ratio is not None
        else "heap_used_ratio=?",
    ),
    AlertRule(
        id="starrocks_compaction_pressure",
        title="Compaction pressure high",
        category="storage",
        severity="warning",
        description="A tablet's compaction score is far above normal; read amplification grows.",
        threshold="compaction score <= 100",
        metric="max_compaction_score",
        evaluate=lambda m: m.max_compaction_score is not None and m.max_compaction_score > 100,
        detail=_detail_value("max_compaction_score"),
    ),
    AlertRule(
        id="starrocks_compaction_failure",
        title="Repeated compaction failures",
        category="storage",
        severity="warning",
        description="Compactions are failing repeatedly on the BE engine.",
        threshold="failures <= 3",
        metric="be_compaction_failures",
        evaluate=lambda m: m.be_compaction_failures is not None and m.be_compaction_failures > 3,
        detail=_detail_value("be_compaction_failures"),
    ),
    AlertRule(
        id="starrocks_be_disk_high",
        title="BE disk utilization high",
        category="storage",
        severity="critical",
        description="A BE disk is at or above 95%; ingestion will be rejected shortly.",
        threshold="disk used < 95%",
        metric="be_disk_used_pct_max",
        evaluate=lambda m: m.be_disk_used_pct_max is not None and m.be_disk_used_pct_max >= 95,
        detail=_detail_disk,
    ),
    AlertRule(
        id="starrocks_be_disk_soft",
        title="BE disk utilization elevated",
        category="storage",
        severity="warning",
        description="A BE disk is at or above 90%; plan capacity or compaction work.",
        threshold="disk used < 90%",
        metric="be_disk_used_pct_max",
        evaluate=lambda m: (
            m.be_disk_used_pct_max is not None
            and 90 <= m.be_disk_used_pct_max < 95
        ),
        detail=_detail_disk,
    ),
)


def evaluate_rules(metrics: AlertMetrics) -> list[AlertResult]:
    """Evaluate every rule against the snapshot, preserving rule order."""
    results: list[AlertResult] = []
    for rule in ALERT_RULES:
        raw = getattr(metrics, rule.metric, None)
        if raw is None or not metrics.engine_reachable:
            results.append(
                AlertResult(
                    id=rule.id,
                    title=rule.title,
                    category=rule.category,
                    severity=rule.severity,
                    status="unknown",
                    value=None,
                    threshold=rule.threshold,
                    description=rule.description,
                    detail=f"{rule.metric} unavailable",
                )
            )
            continue
        try:
            firing = bool(rule.evaluate(metrics))
            detail = rule.detail(metrics)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Alert rule %s evaluation failed: %s", rule.id, exc)
            results.append(
                AlertResult(
                    id=rule.id,
                    title=rule.title,
                    category=rule.category,
                    severity=rule.severity,
                    status="unknown",
                    value=None,
                    threshold=rule.threshold,
                    description=rule.description,
                    detail=f"evaluation error: {exc}",
                )
            )
            continue
        results.append(
            AlertResult(
                id=rule.id,
                title=rule.title,
                category=rule.category,
                severity=rule.severity,
                status="firing" if firing else "ok",
                value=raw,
                threshold=rule.threshold,
                description=rule.description,
                detail=detail,
            )
        )
    return results


def summarize(results: list[AlertResult], engine_reachable: bool = True) -> dict[str, Any]:
    """Count firing alerts by severity for the UI summary card."""
    firing = [r for r in results if r.status == "firing"]
    unknown = [r for r in results if r.status == "unknown"]
    counts = {"critical": 0, "warning": 0, "info": 0}
    for r in firing:
        counts[r.severity] = counts.get(r.severity, 0) + 1
    if not engine_reachable:
        overall = "unavailable"
    elif counts["critical"]:
        overall = "critical"
    elif counts["warning"]:
        overall = "warning"
    elif unknown:
        overall = "degraded"
    else:
        overall = "healthy"
    return {
        "overall": overall,
        "firing": len(firing),
        "unknown": len(unknown),
        "counts": counts,
    }
