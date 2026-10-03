from __future__ import annotations

from datetime import datetime

from app.modules.query_autopilot.models import (
    ActionKind,
    Candidate,
    Enrollment,
    Mode,
    Policy,
    Risk,
    State,
)

_APPROVAL = {
    ActionKind.HISTOGRAM,
    ActionKind.MATERIALIZED_VIEW,
    ActionKind.REFRESH_POLICY,
    ActionKind.PLAN_BASELINE,
    ActionKind.RESOURCE_GROUP,
    ActionKind.ADD_INDEX,
}
_RECOMMEND = {
    ActionKind.PARTITION,
    ActionKind.SORT_KEY,
    ActionKind.BUCKETING,
    ActionKind.APPLICATION_SQL,
}
_TRANSITIONS = {
    State.PROPOSED: {State.VALIDATING, State.BLOCKED, State.REJECTED},
    State.VALIDATING: {
        State.READY_AUTO,
        State.READY_APPROVAL,
        State.INCONCLUSIVE,
        State.BLOCKED,
        State.FAILED,
    },
    State.READY_AUTO: {State.APPLYING, State.BLOCKED, State.REJECTED},
    State.READY_APPROVAL: {State.APPROVED, State.REJECTED, State.BLOCKED},
    State.APPROVED: {State.APPLYING, State.BLOCKED, State.REJECTED},
    State.APPLYING: {State.APPLIED, State.FAILED, State.BLOCKED},
    State.APPLIED: {State.VERIFYING, State.BLOCKED},
    State.VERIFYING: {
        State.SUCCESS,
        State.NO_IMPROVEMENT,
        State.REGRESSED,
        State.INCONCLUSIVE,
        State.BLOCKED,
        State.FAILED,
    },
    State.REGRESSED: {State.ROLLED_BACK, State.BLOCKED},
    State.BLOCKED: {State.PROPOSED, State.REJECTED},
    State.INCONCLUSIVE: {State.PROPOSED, State.REJECTED},
}


def classify(candidate: Candidate, enrollment: Enrollment, policy: Policy) -> Risk:
    if not candidate.targets or any(
        "ACCOUNTADMIN" in target.upper() for target in candidate.targets
    ):
        return Risk.PROHIBITED
    if candidate.kind in {ActionKind.DESTRUCTIVE, ActionKind.SECURITY_POLICY}:
        return Risk.PROHIBITED
    if candidate.kind in _RECOMMEND:
        return Risk.RECOMMEND_ONLY
    if candidate.kind in _APPROVAL:
        return Risk.APPROVAL
    if candidate.kind == ActionKind.STATISTICS:
        return (
            Risk.AUTO
            if enrollment.statistics_auto and len(candidate.targets) <= policy.max_statistics_tables
            else Risk.APPROVAL
        )
    if candidate.kind == ActionKind.NATIVE_FEEDBACK:
        return Risk.AUTO if enrollment.native_feedback_auto else Risk.APPROVAL
    return Risk.PROHIBITED


def transition(candidate: Candidate, target: State, *, reason: str | None = None) -> Candidate:
    if target not in _TRANSITIONS.get(candidate.state, set()):
        raise ValueError(f"Transition {candidate.state} to {target} is not allowed")
    if target in {State.BLOCKED, State.INCONCLUSIVE, State.FAILED} and not reason:
        raise ValueError("A blocked, inconclusive, or failed action requires a reason")
    return candidate.model_copy(update={"state": target, "reason": reason})


def application_block(
    candidate: Candidate,
    enrollment: Enrollment,
    policy: Policy,
    now: datetime,
    *,
    experiment_passed: bool,
    evidence_current: bool,
    authorized: bool,
) -> str | None:
    if policy.mode == Mode.OBSERVE:
        return "observe_mode"
    if not authorized:
        return "authorization_unavailable_or_revoked"
    if not enrollment.enabled or not enrollment.replay_opt_in:
        return "enrollment_disabled"
    if candidate.scope != enrollment.scope:
        return "security_scope_changed"
    if (
        candidate.enrollment_version != enrollment.version
        or candidate.policy_version != policy.version
    ):
        return "policy_or_enrollment_changed"
    if not set(candidate.targets).issubset(enrollment.table_mapping):
        return "target_not_enrolled"
    if not evidence_current:
        return "evidence_unavailable_or_expired"
    if not experiment_passed or not candidate.experiment_id or not candidate.experiment_digest:
        return "experiment_not_passed"
    risk = classify(candidate, enrollment, policy)
    if risk in {Risk.PROHIBITED, Risk.RECOMMEND_ONLY}:
        return risk.value.lower()
    if risk == Risk.AUTO and candidate.state == State.READY_AUTO:
        return None
    if candidate.state != State.APPROVED or not candidate.approved_by:
        return "approval_required"
    if candidate.approval_digest != candidate.binding:
        return "approval_stale"
    if candidate.approval_expires_at is None or candidate.approval_expires_at <= now:
        return "approval_expired"
    return None


def can_compensate(
    candidate: Candidate, *, object_identity_matches: bool, authorized: bool
) -> bool:
    return bool(
        candidate.kind
        in {ActionKind.MATERIALIZED_VIEW, ActionKind.ADD_INDEX, ActionKind.PLAN_BASELINE}
        and candidate.owned_object
        and object_identity_matches
        and authorized
        and candidate.state == State.REGRESSED
    )
