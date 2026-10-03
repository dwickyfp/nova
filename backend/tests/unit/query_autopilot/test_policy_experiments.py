from datetime import timedelta

import pytest

from app.modules.query_autopilot.correctness import prove_result
from app.modules.query_autopilot.experiments import Experiment, Measurement
from app.modules.query_autopilot.models import (
    ActionKind,
    Budget,
    Candidate,
    Enrollment,
    Mode,
    Policy,
    Scope,
    State,
    utcnow,
)
from app.modules.query_autopilot.policy import (
    application_block,
    can_compensate,
    classify,
    transition,
)


def fixtures():
    scope = Scope(
        principal="analyst", active_role="finance", security_context_version=1, database="retail"
    )
    enrollment = Enrollment(
        id="e",
        scope=scope,
        sandbox_database="snapshot",
        table_mapping={"orders": "snapshot.orders"},
        snapshot_id="frozen-1",
        snapshot_created_at=utcnow(),
        snapshot_frozen=True,
        execution_principal="replay",
        execution_role="sandbox_reader",
        budget=Budget(resource_group="sandbox"),
        replay_opt_in=True,
        statistics_auto=True,
    )
    candidate = Candidate(
        id="c",
        family_id="f",
        scope=scope,
        kind=ActionKind.STATISTICS,
        targets=("orders",),
        evidence_ids=("ev",),
        enrollment_id="e",
        enrollment_version=1,
        policy_version=1,
        experiment_id="experiment",
        experiment_digest="result",
        state=State.READY_AUTO,
    )
    return candidate, enrollment, Policy()


def test_policy_matrix_and_mode_cannot_expand_authorization():
    candidate, enrollment, policy = fixtures()
    for mode in Mode:
        for authorized in (True, False):
            reason = application_block(
                candidate,
                enrollment,
                policy.model_copy(update={"mode": mode}),
                utcnow(),
                experiment_passed=True,
                evidence_current=True,
                authorized=authorized,
            )
            assert (reason is None) == (mode != Mode.OBSERVE and authorized)
    for kind in (ActionKind.DESTRUCTIVE, ActionKind.SECURITY_POLICY):
        assert (
            classify(candidate.model_copy(update={"kind": kind}), enrollment, policy)
            == "PROHIBITED"
        )
    for kind in (
        ActionKind.HISTOGRAM,
        ActionKind.MATERIALIZED_VIEW,
        ActionKind.ADD_INDEX,
        ActionKind.REFRESH_POLICY,
        ActionKind.PLAN_BASELINE,
        ActionKind.RESOURCE_GROUP,
    ):
        assert (
            classify(candidate.model_copy(update={"kind": kind}), enrollment, policy) == "APPROVAL"
        )
    for kind in (
        ActionKind.PARTITION,
        ActionKind.SORT_KEY,
        ActionKind.BUCKETING,
        ActionKind.APPLICATION_SQL,
    ):
        assert (
            classify(candidate.model_copy(update={"kind": kind}), enrollment, policy)
            == "RECOMMEND_ONLY"
        )


def test_approval_binds_material_facts_and_expires():
    candidate, enrollment, policy = fixtures()
    c = candidate.model_copy(update={"kind": ActionKind.HISTOGRAM, "state": State.APPROVED})
    c = c.model_copy(
        update={
            "approved_by": "admin",
            "approval_digest": c.binding,
            "approval_expires_at": utcnow() + timedelta(hours=1),
        }
    )
    kwargs = dict(experiment_passed=True, evidence_current=True, authorized=True)
    assert application_block(c, enrollment, policy, utcnow(), **kwargs) is None
    for update in (
        {"parameters": {"buckets": 128}},
        {"experiment_digest": "changed"},
        {"evidence_ids": ("new",)},
        {"version": 2},
    ):
        assert (
            application_block(c.model_copy(update=update), enrollment, policy, utcnow(), **kwargs)
            == "approval_stale"
        )
    assert (
        application_block(c, enrollment, policy, utcnow() + timedelta(hours=2), **kwargs)
        == "approval_expired"
    )
    assert (
        application_block(
            c, enrollment, policy.model_copy(update={"version": 2}), utcnow(), **kwargs
        )
        == "policy_or_enrollment_changed"
    )


def test_transitions_and_statistics_rollback():
    c, _, _ = fixtures()
    with pytest.raises(ValueError):
        transition(c, State.SUCCESS)
    with pytest.raises(ValueError):
        transition(c, State.BLOCKED)
    assert transition(c, State.BLOCKED, reason="evidence_expired").reason == "evidence_expired"
    c = c.model_copy(update={"state": State.REGRESSED, "owned_object": "stats"})
    assert not can_compensate(c, object_identity_matches=True, authorized=True)


async def trial(
    *, changed=False, truncated=False, drift=False, control_regression=False, gain=True
):
    calls = []

    async def measure(phase, control):
        calls.append((phase, control))
        value = 2 if phase == "after" and changed and not control else 1
        proof = prove_result(
            [[value]], ("INT",), ordered=False, max_rows=100, max_bytes=1000, truncated=truncated
        )
        duration = 100 if phase == "before" or control or not gain else 50
        if control and phase == "after" and control_regression:
            duration = 200
        return Measurement(duration, proof)

    states = iter(["snapshot", "snapshot", "different" if drift else "snapshot"])

    async def snapshot():
        return next(states)

    async def apply():
        calls.append(("apply", False))

    async def authorized():
        return True

    result = await Experiment(Budget(resource_group="test")).run(
        measure=measure, snapshot=snapshot, apply=apply, revalidate=authorized
    )
    return result, calls


@pytest.mark.asyncio
async def test_full_trial_and_controls():
    result, calls = await trial()
    assert result.status == "SUCCESS"
    assert result.before["count"] == result.after["count"] == 30
    assert len(calls) == 133
    assert result.improvement == 0.5
    assert result.correctness == "EQUIVALENT"
    proof = result.result_comparison
    assert proof["checked_target_samples"] == proof["checked_control_samples"] == 60
    assert proof["target_equivalent"] and proof["control_equivalent"]
    assert proof["snapshot_verified"]
    assert proof["before_proof"]["digest"] == proof["after_proof"]["digest"]
    assert proof["before_proof"]["row_count"] == proof["after_proof"]["row_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,status,reason",
    [
        ({"changed": True}, "FAILED", "result_changed"),
        ({"truncated": True}, "INCONCLUSIVE", "truncated_result"),
        ({"drift": True}, "INCONCLUSIVE", "snapshot_changed"),
        ({"control_regression": True}, "REGRESSED", "control_workload_regressed"),
        ({"gain": False}, "NO_IMPROVEMENT", "benefit_not_repeatable_or_below_threshold"),
    ],
)
async def test_negative_trials_cannot_pass(kwargs, status, reason):
    result, _ = await trial(**kwargs)
    assert (result.status, result.reason) == (status, reason)
    if kwargs.get("changed"):
        assert result.result_comparison["target_equivalent"] is False
        assert result.result_comparison["before_proof"]["digest"] != (
            result.result_comparison["after_proof"]["digest"]
        )
    if kwargs.get("truncated") or kwargs.get("drift"):
        assert result.result_comparison is None
