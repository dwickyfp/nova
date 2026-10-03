from datetime import timedelta

import pytest

from app.modules.query_autopilot.models import Candidate, Evidence, Observation, State, utcnow
from tests.unit.query_autopilot import test_service
from tests.unit.query_autopilot.test_aggregation import TimedRepository

setup = test_service.setup


@pytest.mark.parametrize(
    (
        "target_after",
        "control_after",
        "target_count",
        "control_count",
        "signals",
        "state",
        "reason",
    ),
    [
        (40, 100, 20, 20, True, State.SUCCESS, None),
        (99, 100, 20, 20, True, State.NO_IMPROVEMENT, "measured_gain_below_threshold"),
        (150, 100, 20, 20, True, State.REGRESSED, "measured_post_application_regression"),
        (40, 300, 20, 20, True, State.REGRESSED, "untargeted_workload_regressed"),
        (40, 100, 19, 20, True, State.INCONCLUSIVE, "insufficient_post_application_samples"),
        (40, 100, 20, 19, True, State.INCONCLUSIVE, "insufficient_control_workload_samples"),
        (
            40,
            100,
            20,
            20,
            False,
            State.INCONCLUSIVE,
            "production_plan_or_resource_evidence_unavailable",
        ),
        (
            [10000] + [40] * 19,
            100,
            20,
            20,
            True,
            State.INCONCLUSIVE,
            "post_application_gain_not_repeatable",
        ),
    ],
)
async def test_complete_window_outcomes_require_target_controls_and_measured_evidence(
    setup,
    target_after,
    control_after,
    target_count,
    control_count,
    signals,
    state,
    reason,
):
    service, original, candidate, enrollment, policy, _ = setup
    now = utcnow()
    applied = now - timedelta(minutes=31)
    started = applied - timedelta(minutes=10)
    repo = TimedRepository(now)
    for (kind, identifier), record in original.records.items():
        await repo.put(kind, identifier, record)
    service.repo = repo
    candidate = candidate.model_copy(update={"state": State.VERIFYING})
    await service._candidate(candidate)
    job = {"id": "verify:application"}
    await repo.put(
        "actions",
        "application",
        {
            "state": "APPLIED",
            "started_at": started.isoformat(),
            "applied_at": applied.isoformat(),
        },
    )
    for phase, observed in (
        ("before", started - timedelta(minutes=1)),
        ("during", started + timedelta(minutes=1)),
        ("after", applied + timedelta(minutes=1)),
    ):
        for family, latency, count in (
            (
                candidate.family_id,
                100 if phase == "before" else (10000 if phase == "during" else target_after),
                target_count,
            ),
            (
                "control",
                100 if phase == "before" else (10000 if phase == "during" else control_after),
                control_count,
            ),
        ):
            for index in range(count):
                item = Observation(
                    id=f"{phase}-{family}-{index}",
                    family_id=family,
                    scope=candidate.scope,
                    observed_at=observed,
                    source="deterministic_fixture",
                    status="success",
                    total_ms=latency[index] if isinstance(latency, list) else latency,
                )
                await repo.put(
                    "observations",
                    item.id,
                    item.model_dump(mode="json"),
                    family_id=family,
                    cohort_id=candidate.scope.cohort_id,
                    created_at=observed,
                )
        if signals:
            for kind in ("plan", "profile"):
                item = Evidence(
                    id=phase + kind,
                    family_id=candidate.family_id,
                    cohort_id=candidate.scope.cohort_id,
                    kind=kind,
                    availability="available",
                    source="deterministic_fixture",
                    collected_at=observed,
                    expires_at=now + timedelta(hours=1),
                    query_ids=(phase + "-query",),
                    summary={
                        "parameter_digest": "identical-parameters",
                        "operators": [
                            {"operator": "application-gap" if phase == "during" else "scan"}
                        ],
                        "facts": {"cpu_ms": 10000 if phase == "during" else 10},
                    },
                )
                await repo.put("evidence", item.id, item.model_dump(mode="json"))
    assert await service.verify(candidate, enrollment, policy, job) == {
        "state": "COMPLETED",
        "reason": reason,
    }
    outcome = await repo.get("outcomes", job["id"])
    assert outcome["state"] == state and outcome["reason"] == reason
    assert outcome["correctness"] == "sandbox_equivalence_only"
    assert outcome["causality"] == "observational_post_application_window"
    assert outcome["before"]["count"] == outcome["after"]["count"] == target_count
    assert outcome["windows"]["after_start"] == applied.isoformat()
    assert outcome["windows"]["before_end"] == started.isoformat()
    if signals:
        assert outcome["verification_evidence"]["resources"]["before"]["metrics"]["cpu_ms"] == {
            "mean": 10,
            "sample_count": 1,
        }
        assert outcome["verification_evidence"]["plans"][0]["before"]["operators"] == [
            {"operator": "scan"},
        ]
    if reason == "post_application_gain_not_repeatable":
        assert outcome["measured_gain"] > policy.minimum_gain
        assert outcome["gain_evidence"]["repeatable_gain"] is False
    assert outcome["rollback"] == ("not_reversible" if state == State.REGRESSED else "not_needed")
    assert (await repo.get("opportunities", candidate.id))["state"] == state
    assert not any(call[-1] == "maintenance" for call in service.sql.calls)
    calls = list(service.sql.calls)
    final = Candidate.model_validate(await repo.get("opportunities", candidate.id))
    assert (await service.verify(final, enrollment, policy, job))["state"] == "COMPLETED"
    assert service.sql.calls == calls
    assert await repo.get("outcomes", job["id"]) == outcome


async def test_long_application_still_requires_a_complete_window_after_completion(setup):
    service, repo, candidate, enrollment, policy, _ = setup
    now = utcnow()
    applied = now - timedelta(minutes=5)
    candidate = candidate.model_copy(update={"state": State.VERIFYING})
    await repo.put(
        "actions",
        "application",
        {
            "state": "APPLIED",
            "started_at": (now - timedelta(minutes=35)).isoformat(),
            "applied_at": applied.isoformat(),
        },
    )
    result = await service.verify(candidate, enrollment, policy, {"id": "verify:application"})
    assert result == {
        "state": "QUEUED",
        "reason": "awaiting_complete_verification_window",
        "not_before": (applied + timedelta(minutes=30)).isoformat(),
    }
    assert await repo.get("outcomes", "verify:application") is None


@pytest.mark.parametrize(
    "action,reason",
    [
        (None, "application_completion_time_unavailable"),
        (
            {"state": "APPLIED", "started_at": "2026-10-01T10:00:00+00:00"},
            "application_completion_time_unavailable",
        ),
        (
            {"state": "APPLYING", "applied_at": "2026-10-01T10:00:00+00:00"},
            "application_completion_time_unavailable",
        ),
        (
            {"state": "APPLIED", "applied_at": "2026-10-01T10:00:00+00:00"},
            "application_timestamps_invalid",
        ),
        (
            {
                "state": "APPLIED",
                "started_at": "2026-10-01T10:00:00",
                "applied_at": "2026-10-01T10:01:00",
            },
            "application_timestamps_invalid",
        ),
        (
            {
                "state": "APPLIED",
                "started_at": "2026-10-01T10:01:00+00:00",
                "applied_at": "2026-10-01T10:00:00+00:00",
            },
            "application_timestamps_invalid",
        ),
    ],
)
async def test_missing_or_ambiguous_application_time_cannot_produce_an_outcome(
    setup,
    action,
    reason,
):
    service, repo, candidate, enrollment, policy, _ = setup
    candidate = candidate.model_copy(update={"state": State.VERIFYING})
    if action:
        await repo.put("actions", "application", action)
    result = await service.verify(candidate, enrollment, policy, {"id": "verify:application"})
    assert result == {"state": "BLOCKED", "reason": reason}
    assert await repo.get("outcomes", "verify:application") is None
    assert not any(call[-1] == "maintenance" for call in service.sql.calls)


@pytest.mark.parametrize("changed", [
    None, "rewrite_missing", "freshness", "binding", "missing_session", "changed_actor",
    "changed_version", "changed_role", "expired_session", "metadata_unavailable",
])
async def test_measured_mv_gain_requires_the_owned_fresh_production_scan_binding(
    setup,
    monkeypatch,
    changed,
):
    from unittest.mock import AsyncMock

    from app.modules.query.repository import QueryResult
    from app.modules.query_autopilot.engine_state import ObjectState, object_name
    from app.modules.query_autopilot.models import ActionKind

    service, original, candidate, enrollment, policy, user = setup
    now = utcnow()
    applied = now - timedelta(minutes=31)
    repo = TimedRepository(now)
    for (kind, identifier), record in original.records.items():
        await repo.put(kind, identifier, record)
    service.repo = repo
    candidate = candidate.model_copy(
        update={"kind": ActionKind.MATERIALIZED_VIEW, "state": State.VERIFYING}
    )
    initial = ObjectState(True, "8", "definition", True)
    current = (
        ObjectState(True, "8", "definition", False)
        if changed == "freshness"
        else ObjectState(True, "9", "definition", True)
        if changed == "binding"
        else initial
    )
    monkeypatch.setattr(
        "app.modules.query_autopilot.service.inspect_object", AsyncMock(return_value=current)
    )
    ownership_probe = __import__(
        "app.modules.query_autopilot.service", fromlist=["inspect_object"],
    ).inspect_object
    from contextlib import asynccontextmanager

    from app.modules.query_autopilot.runtime import AuthorizationUnavailable, EvidenceUnavailable

    original_connection = service.sql.connection
    owner_connections = []

    @asynccontextmanager
    async def guarded_connection(scope, **kwargs):
        if scope.principal == user["username"]:
            owner_connections.append((scope, kwargs))
            if changed == "expired_session":
                raise AuthorizationUnavailable("private session state")
            if changed == "metadata_unavailable":
                raise EvidenceUnavailable("private engine address")
        async with original_connection(scope, **kwargs) as connection:
            yield connection

    service.sql.connection = guarded_connection
    execute = service.sql.execute
    query_scopes = []

    async def actual_sql(statement, scope, **kwargs):
        if statement.startswith("EXPLAIN"):
            query_scopes.append(scope)
            return QueryResult(
                columns=["plan"],
                rows=[
                    ["0:OlapScanNode"],
                    [
                        "TABLE: orders"
                        if changed == "rewrite_missing"
                        else "TABLE: " + object_name(candidate)
                    ],
                ],
            )
        return await execute(statement, scope, **kwargs)

    service.sql.execute = actual_sql
    await repo.put(
        "actions",
        "application",
        {
            "state": "APPLIED",
            "started_at": applied.isoformat(),
            "applied_at": applied.isoformat(),
            "owned_object_binding": initial.binding,
            "actor": user["username"], "actor_role": "ACCOUNTADMIN", "actor_version": 1,
        },
    )
    for phase, timestamp in (
        ("before", applied - timedelta(minutes=1)),
        ("after", applied + timedelta(minutes=1)),
    ):
        for family in (candidate.family_id, enrollment.control_families[0]):
            for index in range(20):
                item = Observation(
                    id=f"{phase}-{family}-{index}",
                    family_id=family,
                    scope=candidate.scope,
                    observed_at=timestamp,
                    source="deterministic_object_verification_fixture",
                    status="success",
                    total_ms=40 if phase == "after" and family == candidate.family_id else 100,
                )
                await repo.put(
                    "observations",
                    item.id,
                    item.model_dump(mode="json"),
                    family_id=family,
                    cohort_id=candidate.scope.cohort_id,
                    created_at=timestamp,
                )
        for kind in ("plan", "profile"):
            evidence = Evidence(
                id=phase + kind,
                family_id=candidate.family_id,
                cohort_id=candidate.scope.cohort_id,
                kind=kind,
                availability="available",
                collected_at=timestamp,
                source="deterministic_object_verification_fixture",
                query_ids=(phase,),
                summary={
                    "parameter_digest": "same",
                    "operators": [{"operator": "scan"}],
                    "facts": {"cpu_ms": 10},
                },
            )
            await repo.put("evidence", evidence.id, evidence.model_dump(mode="json"))
    job = {
        "id": "verify:application", "actor": user["username"], "actor_role": "ACCOUNTADMIN",
        "actor_version": 1, "actor_session_id": user["session_id"],
    }
    if changed == "missing_session":
        job.pop("actor_session_id")
    elif changed == "changed_actor":
        job["actor"] = "different-owner"
    elif changed == "changed_version":
        job["actor_version"] = 2
    elif changed == "changed_role":
        job["actor_role"] = "analyst"
    result = await service.verify(candidate, enrollment, policy, job)
    unavailable = changed in {
        "missing_session", "changed_actor", "changed_version", "changed_role", "expired_session",
        "metadata_unavailable",
    }
    expected = (
        "production_mv_metadata_identity_unavailable" if unavailable
        else None if changed is None else "production_mv_rewrite_freshness_or_binding_unproven"
    )
    assert result == {"state": "COMPLETED", "reason": expected}
    outcome = await repo.get("outcomes", "verify:application")
    assert outcome["state"] == (State.SUCCESS if changed is None else State.INCONCLUSIVE)
    if unavailable:
        assert outcome["object_evidence"]["availability"] == (
            "unavailable" if changed == "metadata_unavailable" else "unauthorized"
        )
        ownership_probe.assert_not_awaited()
    else:
        assert outcome["object_evidence"]["binding"] == current.binding
        assert ownership_probe.await_args.args[2].principal == user["username"]
        assert ownership_probe.await_args.args[2].active_role == "ACCOUNTADMIN"
        assert owner_connections[0][1]["session_id"] == user["session_id"]
    assert query_scopes == [candidate.scope]
    assert not any(call[-1] == "maintenance" for call in service.sql.calls)
