from datetime import timedelta
from pathlib import Path

import pytest

from app.modules.query_autopilot.enrichment import measured_facts
from app.modules.query_autopilot.evidence import structured_plan
from app.modules.query_autopilot.jobs import exclusive
from app.modules.query_autopilot.models import Evidence, utcnow
from app.modules.query_autopilot.profile import analyzed_profile_summary, profile_summary
from app.modules.query_autopilot.statistics_evidence import statistics_summary
from app.sql_frontend.autopilot import deterministic_order
from tests.unit.query_autopilot.test_service import MemoryRepository

FIXTURES = Path(__file__).parents[2] / "fixtures" / "query_autopilot"


def test_pinned_profiles_bind_cardinality_to_the_actual_engine_execution():
    raw = (FIXTURES / "analyzed-profile.txt").read_text()
    summary = analyzed_profile_summary(raw, query_id="00000000-0000-0000-0000-000000000001")
    assert summary["query_id_verified"]
    assert len(summary["operators"]) == 2
    assert summary["operators"][1]["actual_rows"] == 50000
    assert summary["operators"][1]["estimated_rows"] == 50000
    with pytest.raises(ValueError, match="identity_mismatch"):
        analyzed_profile_summary(raw, query_id="different-query")
    profile = profile_summary((FIXTURES / "profile.txt").read_text())
    assert profile["facts"]["cpu_ms"] == 15.535
    assert profile["facts"]["execution_ms"] == 54.133
    assert profile["facts"]["peak_memory_bytes"] > 350000
    assert profile["facts"]["spill_bytes"] == 0


def test_pinned_profile_exact_counts_and_frontend_pending_are_not_pipeline_waits():
    summary = profile_summary("""  Planner:
     - -- Pending[1] 2s471ms
  Execution:
     - QueryExecutionWallTime: 5s767ms
     - RawRowsRead: 50.000K (50001)
     - NumSentRows: 1
     - PendingTime: 99s
     - InputEmptyTime: 99s
""")
    assert summary["facts"] == {
        "queue_ms": 2471,
        "execution_ms": 5767,
        "scanned_rows": 50001,
        "output_rows": 1,
    }
    assert profile_summary("- -- Pending[2] 2s\n- PendingTime: 10s")["facts"] == {}


def test_plan_hash_tracks_shape_and_distribution_separately_from_estimates():
    a = "1:HASH JOIN\njoin op: INNER JOIN (BROADCAST)\ncardinality: 10"
    b = a.replace("10", "100")
    c = a.replace("BROADCAST", "PARTITIONED")
    assert structured_plan(a)["hash"] == structured_plan(b)["hash"]
    assert structured_plan(a)["hash"] != structured_plan(c)["hash"]
    assert structured_plan(a)["operators"] != structured_plan(b)["operators"]


async def test_profiles_deduplicate_but_samples_do_not_hide_profile_facts():
    repo = MemoryRepository()
    now = utcnow()
    for index, kind in enumerate(("replay_sample", "profile", "profile")):
        item = Evidence(
            id=str(index),
            family_id="f",
            cohort_id="c",
            kind=kind,
            availability="available",
            source="starrocks",
            query_ids=("q",),
            collected_at=now + timedelta(microseconds=index),
            summary={"facts": {"cpu_ms": 5}} if kind == "profile" else {},
        )
        await repo.put("evidence", item.id, item.model_dump(mode="json"))
    facts, refs = await measured_facts(
        repo, "f", "c", now - timedelta(minutes=1), now + timedelta(seconds=1)
    )
    assert facts["cpu_ms"] == 5
    assert refs == ("1",)


async def test_counter_pairs_and_resource_saturation_require_the_same_execution():
    repo = MemoryRepository()
    now = utcnow()
    records = (
        (
            "first",
            "profile",
            ("q1",),
            {"queue_ms": 1000, "execution_ms": 10, "scanned_rows": 100000, "output_rows": 1},
        ),
        ("second", "profile", ("q2",), {"execution_ms": 20, "output_rows": 100}),
        ("unrelated", "resource", ("q1",), {"saturated": True}),
    )
    for index, (identifier, kind, queries, facts) in enumerate(records):
        item = Evidence(
            id=identifier,
            family_id="f",
            cohort_id="c",
            kind=kind,
            availability="available",
            source="starrocks",
            query_ids=queries,
            collected_at=now + timedelta(microseconds=index),
            summary={"facts": facts},
        )
        await repo.put("evidence", identifier, item.model_dump(mode="json"))
    facts, _ = await measured_facts(repo, "f", "c", now, now + timedelta(seconds=1))
    assert facts == {"execution_ms": 20, "output_rows": 100}
    item = Evidence(
        id="bound",
        family_id="f",
        cohort_id="c",
        kind="resource",
        availability="available",
        source="query_bound_runtime_snapshot",
        query_ids=("q2",),
        collected_at=now,
        summary={"facts": {"saturated": True}},
    )
    await repo.put("evidence", item.id, item.model_dump(mode="json"))
    facts, refs = await measured_facts(repo, "f", "c", now, now + timedelta(seconds=1))
    assert facts["saturated"] is True and "bound" in refs
    assert "queue_ms" not in facts


def test_statistics_missing_health_and_growth_are_distinct():
    assert statistics_summary([], [])["facts"] == {"statistics_missing": True}
    summary = statistics_summary(
        ["Database", "Table", "Healthy", "TableHealthyMetrics"],
        [["retail", "orders", "40%", "[tableRowCount=5000, tableRowCountInStatistics=1000]"]],
    )
    assert summary["facts"] == {
        "statistics_missing": False,
        "statistics_stale": True,
        "table_growth_ratio": 5,
    }


def test_order_proof_rejects_ambiguous_ties_without_guessing():
    assert deterministic_order(
        "SELECT id, value FROM t ORDER BY id", ["id", "value"], [[1, "a"], [2, "b"]]
    )
    assert not deterministic_order("SELECT id FROM t", ["id"], [[1]])
    with pytest.raises(ValueError, match="ties"):
        deterministic_order(
            "SELECT id, value FROM t ORDER BY id", ["id", "value"], [[1, "a"], [1, "b"]]
        )


class Redis:
    def __init__(self):
        self.values = {}

    async def set(self, key, token, **_):
        if key in self.values:
            return False
        self.values[key] = token
        return True

    async def eval(self, script, _, key, token, *args):
        if self.values.get(key) != token:
            return 0
        if not args:
            self.values.pop(key)
        return 1


async def test_candidate_and_object_exclusivity_releases_partial_acquisitions():
    redis = Redis()
    async with exclusive(redis, ("candidate:a", "object:orders")):
        before = dict(redis.values)
        with pytest.raises(ValueError, match="operation_in_progress"):
            async with exclusive(redis, ("candidate:b", "object:orders")):
                pytest.fail("overlapping writer acquired the same object")
        assert redis.values == before
    assert redis.values == {}


async def test_worker_delayed_front_page_does_not_starve_ready_work():
    from unittest.mock import AsyncMock

    from app.modules.query_autopilot.jobs import AutopilotWorker

    repo = MemoryRepository()
    future = (utcnow() + timedelta(hours=1)).isoformat()
    for index in range(101):
        identifier = f"job:{index:03}"
        await repo.put(
            "jobs",
            identifier,
            {
                "id": identifier,
                "kind": "aggregate",
                "state": "QUEUED",
                "version": 1,
                "not_before": future if index < 100 else None,
            },
        )
    service = AsyncMock()
    service.run_operation.return_value = {"state": "COMPLETED"}
    worker = AutopilotWorker(Redis(), service, repo)
    await worker.run_once()
    service.run_operation.assert_not_awaited()
    await worker.run_once()
    service.run_operation.assert_awaited_once()
    assert (await repo.get("jobs", "job:100"))["state"] == "COMPLETED"
    await worker.run_once()
    service.run_operation.assert_awaited_once()


def test_judge_counts_referenced_evidence_without_leaking_text_or_nan():
    from app.modules.query_autopilot.judge import reduced_evidence

    value = reduced_evidence(
        {
            "findings": [
                {
                    "measured": {"cpu_ms": float("nan"), "queue_ms": 123},
                    "evidence_ids": ["private-query-id"],
                }
            ],
            "diagnosis": [
                {"category": "RESOURCE_CONTENTION", "evidence_ids": ["private-query-id"]}
            ],
            "sql": "secret source SQL",
            "credential": "private",
        }
    )
    assert value["evidence_count"] == 1
    assert value["measurements"] == {"queue_ms": 123}
    assert "private" not in str(value) and "secret" not in str(value)


def test_log_diagnosis_requires_exact_execution_error_not_temporal_proximity():
    from app.modules.query_autopilot.detection import Facts, detect, diagnose
    from app.modules.query_autopilot.log_evidence import log_summary
    from app.modules.query_autopilot.models import Policy
    from tests.benchmark.query_autopilot.accuracy import cases

    query = "00000000-0000-0000-0000-000000000001"
    other = "00000000-0000-0000-0000-000000000002"
    unrelated = log_summary(f"ERROR query_id={other} Memory limit exceeded", query)
    assert unrelated["facts"] == {}
    assert unrelated["correlated_line_count"] == 0
    correlated = log_summary(f"ERROR query_id={query} Memory limit exceeded", query)
    template = cases()[0][1]
    facts = Facts(
        template.latency, template.baseline, evidence_ids=("log-proof",), **correlated["facts"]
    )
    diagnosis = diagnose(detect(facts, Policy()), facts)
    assert diagnosis[0].category == "ENGINE_MEMORY_LIMIT"
    assert diagnosis[0].evidence_ids == ("log-proof",)
    neutral = log_summary(f"INFO query_id={query} finished; memory usage 20MB", query)
    assert neutral["facts"] == {}


async def test_local_log_adapter_binds_cohort_and_hides_configured_paths(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from app.core.config import settings
    from app.modules.query.repository import QueryResult
    from app.modules.query_autopilot.evidence import EvidenceCollector
    from app.modules.query_autopilot.models import Scope
    from tests.unit.query_autopilot.test_service import Payloads

    query = "00000000-0000-0000-0000-000000000001"
    path = tmp_path / "fixture.log"
    path.write_text(f"ERROR query_id={query} Memory limit exceeded\n")
    scope = Scope(principal="alice", active_role="analyst", security_context_version=1)
    monkeypatch.setattr(settings, "QUERY_AUTOPILOT_LOCAL_LOG_FIXTURES", {scope.cohort_id: [path]})
    sql = AsyncMock()
    sql.execute.return_value = QueryResult(columns=["value"], rows=[[1]])
    evidence = await EvidenceCollector(sql, MemoryRepository(), Payloads()).logs(
        "f", scope, query, object()
    )
    assert evidence.availability == "available"
    assert evidence.source == "configured_local_fixture"
    assert evidence.summary["facts"] == {"memory_limit_exceeded": True}
    assert str(path) not in evidence.model_dump_json()
    assert sql.execute.call_args.args[0] == "SELECT 1"


async def test_lost_lease_interrupts_work_without_killing_the_worker_task():
    import asyncio

    from app.modules.query_autopilot.jobs import LeaseLost

    class LostRedis(Redis):
        async def eval(self, script, _, key, token, *args):
            self.values.pop(key, None)
            return 0

    task = asyncio.current_task()
    count = task.cancelling()
    with pytest.raises(LeaseLost, match="outcome_uncertain"):
        async with exclusive(LostRedis(), ("object:orders",), renew_interval=0):
            await asyncio.Event().wait()
    assert task.cancelling() == count
    await asyncio.sleep(0)


async def test_verification_pairs_parameters_and_rejects_expired_or_unmeasured_evidence():
    from datetime import timedelta

    from app.modules.query_autopilot.models import Evidence, utcnow
    from app.modules.query_autopilot.verification import verification_signals
    from tests.unit.query_autopilot.test_service import MemoryRepository

    repo = MemoryRepository()
    now = utcnow()
    applied = now - timedelta(minutes=31)
    for phase, observed in (
        ("before", applied - timedelta(minutes=1)),
        ("after", applied + timedelta(minutes=1)),
    ):
        for kind in ("plan", "profile"):
            record = Evidence(
                id=phase + kind,
                family_id="f",
                cohort_id="c",
                kind=kind,
                availability="available",
                source="real_engine",
                collected_at=observed,
                expires_at=now + timedelta(hours=1),
                query_ids=(phase + "-query",),
                summary={
                    "parameter_digest": "same-parameters",
                    "operators": [{"operator": "scan"}],
                    "facts": {"cpu_ms": 10 if phase == "before" else 4, "estimated_rows": 100000},
                },
            )
            await repo.put("evidence", record.id, record.model_dump(mode="json"))
    result = await verification_signals(repo, "f", "c", applied)
    assert result["availability"] == "available"
    assert len(result["plans"]) == 1
    assert result["resources"]["after"]["metrics"] == {"cpu_ms": {"mean": 4, "sample_count": 1}}
    plan = repo.records["evidence", "afterplan"]
    plan["summary"]["parameter_digest"] = "different-parameters"
    result = await verification_signals(repo, "f", "c", applied)
    assert result["availability"] == "unavailable" and not result["plans"]
    plan["summary"]["parameter_digest"] = "same-parameters"
    repo.records["evidence", "afterprofile"]["expires_at"] = (
        now - timedelta(seconds=1)
    ).isoformat()
    result = await verification_signals(repo, "f", "c", applied)
    assert "production_resource_comparison_unavailable" in result["reasons"]


def test_skew_requires_multiple_instances_of_the_same_operator():
    from dataclasses import replace

    from app.modules.query_autopilot.detection import Detector, detect
    from app.modules.query_autopilot.models import Policy
    from tests.benchmark.query_autopilot.accuracy import cases

    facts = next(facts for name, facts, _, _ in cases() if name == "skew")
    assert Detector.OPERATOR_SKEW in {f.detector for f in detect(facts, Policy())}
    assert Detector.OPERATOR_SKEW not in {
        f.detector for f in detect(replace(facts, operator_instance_count=0), Policy())
    }
    unrelated = profile_summary("""SCAN (plan_node_id=1):
 - OperatorTotalTime: 2s
AGGREGATE (plan_node_id=2):
 - OperatorTotalTime: 1ms
""")
    assert "max_operator_ms" not in unrelated["facts"]


@pytest.mark.parametrize("reason,expected", [
    ("ranger_policy_revision_changed", "ranger_policy_revision_changed"),
    ("ranger_policy_revision_unavailable", "ranger_policy_revision_unavailable"),
    ("password=private", "authorization_unavailable"),
])
async def test_worker_retains_closed_authorization_reason_without_secret_leak(reason, expected):
    from unittest.mock import AsyncMock

    from app.modules.query_autopilot.jobs import AutopilotWorker
    from app.modules.query_autopilot.runtime import AuthorizationUnavailable

    repo = MemoryRepository()
    await repo.put("jobs", "revision-job", {
        "id": "revision-job", "kind": "experiment", "state": "QUEUED", "version": 1,
    })
    service = AsyncMock()
    service.run_operation.side_effect = AuthorizationUnavailable(reason)
    await AutopilotWorker(Redis(), service, repo).run_once()
    result = await repo.get("jobs", "revision-job")
    assert result["state"] == "BLOCKED" and result["reason"] == expected
    assert "private" not in str(result)
