from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from tests.benchmark.query_autopilot.regression_live import regression_case


def windows():
    anchor = datetime(2026, 10, 1, 10, tzinfo=UTC)
    phases = {}
    for phase, size, batches, offset, latency in (
        ("uncontended", 40, 3, 0, 10),
        ("contended", 30, 2, 3, 1000),
    ):
        phases[phase] = {
            "measured_latencies_ms": [latency] * (size * batches),
            "results_equivalent": True,
            "observations": [
                {
                    "query_id": str(index),
                    "observed_at": (
                        anchor
                        + timedelta(minutes=30 * (offset + index // size), seconds=index % size)
                    ).isoformat(),
                }
                for index in range(size * batches)
            ],
        }
    return {"wall_clock_history": True, "history_anchor": anchor.isoformat(), "phases": phases}


def test_complete_observed_windows_and_negative_controls():
    result = regression_case(windows())
    assert result["status"] == "PASS"
    assert result["wall_clock_historical_acceptance"] == "PASS"
    assert all(result["negative_variants"].values())


@pytest.mark.parametrize("change", ["wrong_window", "missing_observation", "changed_results"])
def test_natural_window_claim_requires_each_observation(change):
    value = deepcopy(windows())
    before = value["phases"]["uncontended"]
    if change == "wrong_window":
        before["observations"][0]["observed_at"] = value["phases"]["contended"]["observations"][0][
            "observed_at"
        ]
    elif change == "missing_observation":
        before["observations"].pop()
    else:
        before["results_equivalent"] = False
    assert regression_case(value)["status"] == "FAIL"


def test_controlled_clock_never_satisfies_wall_clock_acceptance():
    value = windows()
    value["wall_clock_history"] = False
    result = regression_case(value)
    assert result["status"] == "PASS"
    assert result["wall_clock_historical_acceptance"] == "NOT_RUN"


@pytest.mark.parametrize("change", [
    None, "old_window", "wrong_family", "wrong_cohort", "other_detector",
    "missing_current_finding", "ineligible_baseline", "invalid_window",
])
def test_durable_acceptance_cannot_use_a_stale_or_unrelated_incident(change):
    from tests.benchmark.query_autopilot.contention_live import current_regression_incidents

    family = {
        "family_id": "shape", "cohort_id": "scope", "window": "2026-10-02T10:00:05+00:00",
        "baseline": {"eligible": True}, "findings": [{"detector": "latency_regression"}],
    }
    incident = {
        "id": "incident", "family_id": "shape", "cohort_id": "scope",
        "window": "2026-10-02T10:00:00+00:00", "detector": "latency_regression",
    }
    if change == "old_window":
        incident["window"] = "2026-10-02T09:30:00+00:00"
    elif change == "wrong_family":
        incident["family_id"] = "other"
    elif change == "wrong_cohort":
        incident["cohort_id"] = "other"
    elif change == "other_detector":
        incident["detector"] = "contention"
    elif change == "missing_current_finding":
        family["findings"] = []
    elif change == "ineligible_baseline":
        family["baseline"]["eligible"] = False
    elif change == "invalid_window":
        incident["window"] = "unknown"
    assert current_regression_incidents(family, [incident]) == (
        [incident] if change is None else []
    )


def retained_fixture():
    value = windows()
    value.update(
        database="autopilot_fixture", statement="SELECT SUM(total) FROM orders",
        state="COLLECTION_FAILED_CONFIGURATION_RESTORED", snapshot_state="unchanged",
        durable_execution_ids=[str(i) for i in range(120)],
    )
    for index, observation in enumerate(value["phases"]["uncontended"]["observations"]):
        observation["nova_execution_id"] = str(index)
    return value


@pytest.mark.parametrize("state", [
    "COLLECTION_FAILED_CONFIGURATION_RESTORED", "CONFIGURATION_RESTORED",
    "DETECTION_COMPLETED_CONFIGURATION_RESTORED",
])
def test_resume_preserves_real_history_across_a_gap(state):
    from tests.benchmark.query_autopilot.contention_live import retained_history

    value = retained_fixture()
    value["state"] = state
    now = datetime.fromisoformat(value["history_anchor"]) + timedelta(hours=8)
    assert retained_history(value, "autopilot_fixture", now)["results_equivalent"]
    value["recent_anchor"] = now.isoformat()
    for observation in value["phases"]["contended"]["observations"]:
        observed = datetime.fromisoformat(observation["observed_at"])
        observation["observed_at"] = (observed + timedelta(hours=6, minutes=30)).isoformat()
    assert regression_case(value)["wall_clock_historical_acceptance"] == "PASS"


@pytest.mark.parametrize("change", [
    "unrestored", "expired", "wrong_database", "wrong_id", "wrong_window", "nonfinite",
    "insufficient", "changed_results",
])
def test_resume_rejects_unverifiable_history(change):
    from tests.benchmark.query_autopilot.contention_live import retained_history

    value = retained_fixture()
    now = datetime.fromisoformat(value["history_anchor"]) + timedelta(hours=8)
    if change == "unrestored":
        value["state"] = "RESTORATION_FAILED"
    elif change == "expired":
        now += timedelta(days=7)
    elif change == "wrong_database":
        value["database"] = "autopilot_other"
    elif change == "wrong_id":
        value["durable_execution_ids"][0] = "other"
    elif change == "wrong_window":
        value["phases"]["uncontended"]["observations"][0]["observed_at"] = now.isoformat()
    elif change == "nonfinite":
        value["phases"]["uncontended"]["measured_latencies_ms"][0] = float("nan")
    elif change == "insufficient":
        value["durable_execution_ids"].pop()
    else:
        value["phases"]["uncontended"]["results_equivalent"] = False
    with pytest.raises(ValueError, match="retained_history"):
        retained_history(value, "autopilot_fixture", now)


async def test_persisted_workload_rejects_controlled_clock_before_sql(monkeypatch):
    from unittest.mock import AsyncMock

    import pytest

    from tests.benchmark.query_autopilot.contention_live import contention_case

    monkeypatch.setenv("NOVA_AUTOPILOT_FIXTURE_STACK", "1")
    execute = AsyncMock()
    monkeypatch.setattr("tests.benchmark.query_autopilot.contention_live.db.user_conn", execute)
    with pytest.raises(ValueError, match="durable_workload_requires_natural_windows"):
        await contention_case("autopilot_fixture", persist_workload=True)
    execute.assert_not_called()


@pytest.mark.parametrize("ranger,flag,sales,customers,reason", [
    (False, "1", 50000, 200, "Explicit isolated patched-FE fixture required"),
    (True, "0", 50000, 200, "Explicit isolated patched-FE fixture required"),
    (True, "1", 999, 200, "fixture_row_budget"),
    (True, "1", 1000001, 200, "fixture_row_budget"),
    (True, "1", 50000, 99, "fixture_row_budget"),
    (True, "1", 50000, 1001, "fixture_row_budget"),
])
async def test_governed_cycle_requires_an_explicit_fixture_and_bounded_rows_before_database(
    monkeypatch, tmp_path, ranger, flag, sales, customers, reason,
):
    from unittest.mock import AsyncMock

    from app.core.config import settings
    from app.core.database import db
    from scripts import verify_autopilot_governed_cycle as cycle

    monkeypatch.setattr(cycle, "_inherit_pid_one_environment", lambda: None)
    monkeypatch.setattr(settings, "RANGER_ENABLED", ranger)
    monkeypatch.setenv("NOVA_AUTOPILOT_FIXTURE_STACK", flag)
    connect = AsyncMock()
    monkeypatch.setattr(db, "init_system_pool", connect)
    with pytest.raises(ValueError, match=reason):
        await cycle.run(tmp_path / "not-created", sales, customers)
    connect.assert_not_awaited()
    assert not (tmp_path / "not-created").exists()


async def test_late_recent_batches_wait_for_populated_rolling_windows(monkeypatch):
    from unittest.mock import AsyncMock

    from app.modules.query_autopilot.aggregation import aggregate
    from app.modules.query_autopilot.models import Observation, Scope, digest
    from tests.benchmark.query_autopilot.contention_live import rolling_comparison_ready_at
    from tests.unit.query_autopilot.test_aggregation import TimedRepository

    now = datetime(2026, 10, 1, 13, 1, tzinfo=UTC)
    repo = TimedRepository(now)
    scope = Scope(principal="replay", active_role="analyst", security_context_version=1)
    monkeypatch.setattr(
        "app.modules.query_autopilot.enrichment.measured_facts",
        AsyncMock(return_value=({}, ())),
    )
    recent = []
    for batch, (start, count, latency) in enumerate([
        (now.replace(hour=8, minute=0), 40, 10),
        (now.replace(hour=8, minute=30), 40, 10),
        (now.replace(hour=9, minute=0), 40, 10),
        (now.replace(hour=12, minute=44), 30, 1000),
        (now.replace(hour=13, minute=0), 30, 1000),
    ]):
        for index in range(count):
            observed = start + timedelta(seconds=index)
            item = Observation(
                id=f"batch-{batch}-{index}", family_id="regression", scope=scope,
                observed_at=observed, source="http", status="success", total_ms=latency,
            )
            await repo.put(
                "observations", item.id, item.model_dump(mode="json"),
                family_id=item.family_id, cohort_id=scope.cohort_id, created_at=observed,
            )
            if batch >= 3:
                recent.append({"observed_at": observed.isoformat()})
    await aggregate(repo, now=now)
    key = digest(["regression", scope.cohort_id])
    before = await repo.get("families", key)
    assert not any(f["detector"] == "latency_regression" for f in before["findings"])
    deadline = rolling_comparison_ready_at(recent)
    assert deadline > now
    repo.now = deadline
    await aggregate(repo, now=deadline)
    after = await repo.get("families", key)
    assert after["baseline"]["current_count"] == 30
    assert after["baseline"]["historical_count"] == 120
    assert after["baseline"]["eligible"] is True
    assert any(f["detector"] == "latency_regression" for f in after["findings"])


@pytest.mark.parametrize("change", ["missing", "reordered", "overlap", "expired"])
def test_rolling_comparison_rejects_missing_or_incompatible_recent_batches(change):
    from tests.benchmark.query_autopilot.contention_live import rolling_comparison_ready_at

    observations = deepcopy(windows()["phases"]["contended"]["observations"])
    if change == "missing":
        observations.pop()
    elif change == "reordered":
        observations.reverse()
    elif change == "overlap":
        observations[30]["observed_at"] = observations[0]["observed_at"]
    else:
        observations[-1]["observed_at"] = (
            datetime.fromisoformat(observations[-1]["observed_at"]) + timedelta(hours=2)
        ).isoformat()
    with pytest.raises(ValueError):
        rolling_comparison_ready_at(observations)


def test_rolling_comparison_waits_for_the_last_second_batch_completion():
    from tests.benchmark.query_autopilot.contention_live import rolling_comparison_ready_at

    observations = deepcopy(windows()["phases"]["contended"]["observations"])
    last_first = datetime.fromisoformat(observations[29]["observed_at"])
    last_second = last_first + timedelta(minutes=30, seconds=10)
    observations[-1]["observed_at"] = last_second.isoformat()
    deadline = rolling_comparison_ready_at(observations)
    assert deadline == last_second + timedelta(seconds=1)
    assert all(
        deadline - timedelta(minutes=60) <= datetime.fromisoformat(value["observed_at"])
        < deadline - timedelta(minutes=30) for value in observations[:30]
    )
    assert all(
        deadline - timedelta(minutes=30) <= datetime.fromisoformat(value["observed_at"]) < deadline
        for value in observations[30:]
    )


@pytest.mark.parametrize("seconds", [0, 11, -1, True, 1.5, "1); DROP TABLE orders"])
async def test_contention_budget_is_checked_before_any_database_operation(monkeypatch, seconds):
    from unittest.mock import AsyncMock

    from tests.benchmark.query_autopilot.contention_live import contention_case

    monkeypatch.setenv("NOVA_AUTOPILOT_FIXTURE_STACK", "1")
    connect = AsyncMock()
    monkeypatch.setattr("tests.benchmark.query_autopilot.contention_live.db.user_conn", connect)
    with pytest.raises(ValueError, match="fixture_blocker_budget"):
        await contention_case("autopilot_fixture", blocker_seconds=seconds)
    connect.assert_not_called()


@pytest.mark.parametrize("seconds", [1, 5, 10])
def test_bounded_blocker_preserves_the_fixture_query_shape(seconds):
    from app.sql_frontend.fingerprint import fingerprint
    from tests.benchmark.query_autopilot.contention_live import blocker_statement

    assert fingerprint(blocker_statement(seconds)).family_id == fingerprint(
        "SELECT sleep(1) FROM orders LIMIT 1"
    ).family_id


@pytest.mark.parametrize("bound", [False, True])
def test_regression_diagnosis_requires_query_bound_queue_and_slot_evidence(bound):
    value = windows()
    observation = value["phases"]["contended"]["observations"][0]
    observation.update(
        pending_observed=True, limit_occupied=bound,
        facts={"queue_ms": 950, "execution_ms": 10},
    )
    result = regression_case(value)
    assert result["status"] == "PASS"
    assert result["diagnosis"][0]["category"] == (
        "RESOURCE_CONTENTION" if bound else "UNKNOWN"
    )
    assert result["facts"]["counter_pair_sample_count"] == int(bound)
    if bound:
        assert result["diagnosis"][0]["evidence_ids"] == (observation["query_id"],)
    else:
        assert "queue_ms" not in result["facts"]


@pytest.mark.parametrize("duration,absolute_expected", [(4999, False), (5000, True), (9000, True)])
def test_natural_regression_retains_the_absolute_latency_boundary(duration, absolute_expected):
    value = windows()
    value["phases"]["contended"]["measured_latencies_ms"] = [duration] * 60
    result = regression_case(value)
    assert result["status"] == "PASS"
    assert result["absolute_slow_expected"] is absolute_expected
    assert any(f["detector"] == "absolute_slow" for f in result["findings"]) is absolute_expected


@pytest.mark.parametrize("minute,second,expected_hour,expected_minute", [
    (19, 59, 13, 0), (20, 0, 13, 30), (49, 59, 13, 30), (50, 0, 14, 0),
])
def test_resume_waits_for_a_window_with_ten_minutes_of_collection_headroom(
    minute, second, expected_hour, expected_minute,
):
    from tests.benchmark.query_autopilot.contention_live import recent_window_anchor

    now = datetime(2026, 10, 2, 13, minute, second, tzinfo=UTC)
    anchor = recent_window_anchor(now)
    assert anchor == now.replace(hour=expected_hour, minute=expected_minute, second=0)
    assert anchor + timedelta(minutes=30) - max(now, anchor) >= timedelta(minutes=10)


@pytest.mark.parametrize("repetitions,reuse,reason", [
    (29, None, "fixture_repetition_budget"), (101, None, "fixture_repetition_budget"),
    (30, "foreign-database", "registered_fixture_identity_invalid"),
])
async def test_governed_repetition_and_reuse_budgets_fail_before_database(
    monkeypatch, tmp_path, repetitions, reuse, reason,
):
    from unittest.mock import AsyncMock

    from app.core.config import settings
    from app.core.database import db
    from scripts import verify_autopilot_governed_cycle as cycle

    monkeypatch.setattr(cycle, "_inherit_pid_one_environment", lambda: None)
    monkeypatch.setattr(settings, "RANGER_ENABLED", True)
    monkeypatch.setenv("NOVA_AUTOPILOT_FIXTURE_STACK", "1")
    connect = AsyncMock()
    monkeypatch.setattr(db, "init_system_pool", connect)
    with pytest.raises(ValueError, match=reason):
        await cycle.run(tmp_path, 1000, 100, repetitions=repetitions, reuse_fixture=reuse)
    connect.assert_not_awaited()


@pytest.mark.parametrize("changed", [
    None, "unbound_failed_trial", "wrong_trial_id", "row_count", "cleanup", "state",
    "trial_owner", "scope", "budget", "source",
])
def test_fixture_reuse_requires_authoritative_registration_and_verified_trial_cleanup(changed):
    from scripts.verify_autopilot_governed_cycle import registered_fixture

    identifier = "governed-cycle-012345abcdef"
    database = "autopilot_gov_012345abcdef"
    snapshot = database + "_snapshot"
    group = "autopilot_gov_budget_012345abcdef"
    candidate = {
        "id": identifier, "state": "INCONCLUSIVE", "kind": "MATERIALIZED_VIEW",
        "scope": {"database": database}, "parameters": {"name": "nova_ap_012345abcdef"},
        "enrollment_id": identifier, "experiment_id": "trial",
    }
    enrollment = {
        "id": identifier, "scope": candidate["scope"].copy(), "sandbox_database": snapshot,
        "execution_principal": "nova_admin", "execution_role": "autopilot_gov_builder",
        "budget": {"resource_group": group},
    }
    intent = {
        "id": identifier + "-setup", "kind": "fixture_setup", "databases": [database, snapshot],
        "fixture_rows": {"sales": 1000, "customers": 100},
    }
    trial = {
        "id": "trial", "candidate_id": identifier, "state": "NO_IMPROVEMENT",
        "cleanup": "verified_object_removed", "result": {"correctness": "EQUIVALENT"},
    }
    if changed == "unbound_failed_trial":
        candidate["experiment_id"] = None
    elif changed == "wrong_trial_id":
        candidate["experiment_id"] = "different-trial"
    elif changed == "row_count":
        intent["fixture_rows"]["sales"] = 1001
    elif changed == "cleanup":
        trial["cleanup"] = "uncertain"
    elif changed == "state":
        candidate["state"] = "APPLIED"
    elif changed == "trial_owner":
        trial["candidate_id"] = "foreign"
    elif changed == "scope":
        enrollment["scope"]["database"] = "foreign"
    elif changed == "budget":
        enrollment["budget"]["resource_group"] = "unbounded"
    elif changed == "source":
        intent["databases"][0] = "production"
    if changed in {None, "unbound_failed_trial"}:
        assert registered_fixture(
            candidate, enrollment, intent, trial, sales=1000, customers=100,
        ) == (database, snapshot, group)
    else:
        with pytest.raises(ValueError, match="registered_fixture_provenance_or_cleanup"):
            registered_fixture(candidate, enrollment, intent, trial, sales=1000, customers=100)
