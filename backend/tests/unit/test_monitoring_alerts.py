"""Unit tests for the production alert rule engine (monitoring/alerts.py).

No engine: the rule engine is a pure function over an ``AlertMetrics``
snapshot, so these tests pin the semantics that matter operationally:

* a threshold crossing fires the right rule at the right severity;
* ``None`` (unproven metric) yields ``unknown``, never a silent ``ok`` — the
  same "no data is not zero" contract the cluster view uses;
* an unreachable engine marks every rule unknown instead of pretending health;
* the summary verdict escalates critical > warning > degraded > healthy.
"""

from __future__ import annotations

from app.modules.monitoring.alerts import (
    ALERT_RULES,
    AlertMetrics,
    evaluate_rules,
    summarize,
)


def _by_id(results):
    return {r.id: r for r in results}


class TestRuleCoverage:
    def test_rule_ids_are_unique(self):
        ids = [r.id for r in ALERT_RULES]
        assert len(ids) == len(set(ids))

    def test_every_rule_has_a_known_severity(self):
        assert {r.severity for r in ALERT_RULES} <= {"critical", "warning", "info"}

    def test_rule_order_is_deterministic(self):
        first = [r.id for r in evaluate_rules(AlertMetrics())]
        second = [r.id for r in evaluate_rules(AlertMetrics())]
        assert first == second


class TestUnknownIsNotHealthy:
    def test_all_none_metrics_are_unknown(self):
        results = evaluate_rules(AlertMetrics())
        assert all(r.status == "unknown" for r in results)

    def test_unreachable_engine_forces_unknown_even_with_values(self):
        metrics = AlertMetrics(engine_reachable=False, fe_alive=0, be_alive=0)
        results = evaluate_rules(metrics)
        assert all(r.status == "unknown" for r in results)

    def test_absent_metric_does_not_fire(self):
        # be_alive unset => unknown, not a spurious "BE down" critical.
        results = _by_id(evaluate_rules(AlertMetrics(fe_alive=3)))
        assert results["starrocks_be_down"].status == "unknown"


class TestMembershipRules:
    def test_zero_alive_fe_is_critical(self):
        results = _by_id(evaluate_rules(AlertMetrics(fe_alive=0)))
        assert results["starrocks_fe_down"].status == "firing"
        assert results["starrocks_fe_down"].severity == "critical"

    def test_one_alive_fe_fires_quorum_risk(self):
        results = _by_id(evaluate_rules(AlertMetrics(fe_alive=1)))
        assert results["starrocks_fe_quorum_lost"].status == "firing"

    def test_three_alive_fe_quorum_ok(self):
        results = _by_id(evaluate_rules(AlertMetrics(fe_alive=3)))
        assert results["starrocks_fe_quorum_lost"].status == "ok"

    def test_no_leader_fires(self):
        results = _by_id(evaluate_rules(AlertMetrics(fe_leader_count=0)))
        assert results["starrocks_fe_leader_missing"].status == "firing"

    def test_two_bes_fires_replica_risk_warning(self):
        results = _by_id(evaluate_rules(AlertMetrics(be_alive=2)))
        assert results["starrocks_be_replica_risk"].status == "firing"
        assert results["starrocks_be_replica_risk"].severity == "warning"

    def test_three_bes_clears_replica_risk(self):
        results = _by_id(evaluate_rules(AlertMetrics(be_alive=3)))
        assert results["starrocks_be_replica_risk"].status == "ok"


class TestThresholdRules:
    def test_journal_lag_above_1000_fires(self):
        results = _by_id(evaluate_rules(AlertMetrics(fe_max_journal_lag=1001)))
        assert results["starrocks_fe_journal_lag_high"].status == "firing"

    def test_journal_lag_at_1000_is_ok(self):
        results = _by_id(evaluate_rules(AlertMetrics(fe_max_journal_lag=1000)))
        assert results["starrocks_fe_journal_lag_high"].status == "ok"

    def test_query_error_rate_above_10pct_fires(self):
        results = _by_id(evaluate_rules(AlertMetrics(query_err_rate=0.11)))
        assert results["starrocks_query_error_rate_high"].status == "firing"

    def test_query_latency_p95_over_5s_fires(self):
        results = _by_id(evaluate_rules(AlertMetrics(query_latency_p95_ms=5001)))
        assert results["starrocks_query_latency_high"].status == "firing"

    def test_heap_ratio_over_90pct_fires(self):
        results = _by_id(evaluate_rules(AlertMetrics(fe_heap_used_ratio=0.95)))
        assert results["starrocks_fe_heap_high"].status == "firing"

    def test_compaction_score_over_100_fires(self):
        results = _by_id(evaluate_rules(AlertMetrics(max_compaction_score=101)))
        assert results["starrocks_compaction_pressure"].status == "firing"

    def test_repeated_compaction_failures_fire(self):
        results = _by_id(evaluate_rules(AlertMetrics(be_compaction_failures=4)))
        assert results["starrocks_compaction_failure"].status == "firing"


class TestDiskRules:
    def test_disk_at_95_is_critical(self):
        results = _by_id(evaluate_rules(AlertMetrics(be_disk_used_pct_max=95.0)))
        assert results["starrocks_be_disk_high"].status == "firing"
        assert results["starrocks_be_disk_high"].severity == "critical"

    def test_disk_90_to_95_is_elevated_warning_only(self):
        results = _by_id(evaluate_rules(AlertMetrics(be_disk_used_pct_max=92.0)))
        assert results["starrocks_be_disk_soft"].status == "firing"
        assert results["starrocks_be_disk_high"].status == "ok"

    def test_disk_under_90_is_ok(self):
        results = _by_id(evaluate_rules(AlertMetrics(be_disk_used_pct_max=80.0)))
        assert results["starrocks_be_disk_soft"].status == "ok"
        assert results["starrocks_be_disk_high"].status == "ok"


class TestSummary:
    def test_healthy_when_all_ok(self):
        metrics = AlertMetrics(
            fe_alive=3,
            fe_leader_count=1,
            be_alive=3,
            fe_max_journal_lag=0,
            fe_meta_log_count=10,
            query_err_rate=0.0,
            query_latency_p95_ms=100,
            fe_heap_used_ratio=0.1,
            max_compaction_score=1,
            be_compaction_failures=0,
            be_disk_used_pct_max=10.0,
        )
        results = evaluate_rules(metrics)
        summary = summarize(results)
        assert summary["overall"] == "healthy"
        assert summary["firing"] == 0

    def test_critical_beats_warning(self):
        metrics = AlertMetrics(
            fe_alive=3,
            be_alive=0,  # critical be_down
            be_disk_used_pct_max=91.0,  # warning disk soft
        )
        summary = summarize(evaluate_rules(metrics))
        assert summary["overall"] == "critical"
        assert summary["counts"]["critical"] >= 1
        assert summary["counts"]["warning"] >= 1

    def test_warning_when_no_critical(self):
        metrics = AlertMetrics(be_alive=3, fe_alive=3, be_disk_used_pct_max=91.0)
        summary = summarize(evaluate_rules(metrics))
        assert summary["overall"] == "warning"

    def test_degraded_when_only_unknown(self):
        summary = summarize(evaluate_rules(AlertMetrics()))
        assert summary["overall"] == "degraded"

    def test_unreachable_reports_unavailable(self):
        results = evaluate_rules(AlertMetrics(engine_reachable=False))
        summary = summarize(results, engine_reachable=False)
        assert summary["overall"] == "unavailable"
