from datetime import timedelta

import pytest

from app.modules.query_autopilot.acceptance import ranger_acceptance_reason
from app.modules.query_autopilot.models import Evidence, Scope, digest, utcnow


@pytest.mark.parametrize("changed,reason", [
    ({"availability": "unavailable"}, "ranger_acceptance_unavailable"),
    ({"availability": "unauthorized"}, "ranger_acceptance_unauthorized"),
    ({"availability": "unsupported"}, "ranger_acceptance_unsupported"),
    ({"availability": "expired"}, "ranger_acceptance_expired"),
    ({"kind": "profile"}, "ranger_acceptance_provenance_unverified"),
    ({"source": "native_fixture"}, "ranger_acceptance_provenance_unverified"),
    ({"family_id": "other-family"}, "ranger_acceptance_binding_changed"),
    ({"cohort_id": "same-role-other-user"}, "ranger_acceptance_binding_changed"),
    ({"scope_principal": "other-user"}, "ranger_acceptance_binding_changed"),
    ({"scope_policy_revision": "changed-epoch"}, "ranger_acceptance_binding_changed"),
    ({"scope_security_context_version": 2}, "ranger_acceptance_binding_changed"),
    ({"snapshot_id": "other-snapshot"}, "ranger_acceptance_binding_changed"),
    ({"snapshot_state": "new-partition-version"}, "ranger_acceptance_binding_changed"),
    ({"snapshot_state": None}, "ranger_acceptance_binding_changed"),
    ({"execution_scope": {"principal": "other-replay"}}, "ranger_acceptance_binding_changed"),
    ({"candidate_shape": digest("different")}, "ranger_acceptance_binding_changed"),
    ({"row_filter": "NOT_RUN"}, "ranger_filter_or_mask_acceptance_unproven"),
    ({"masking": "FAIL"}, "ranger_filter_or_mask_acceptance_unproven"),
    ({}, None),
])
def test_acceptance_binds_actual_governed_identity_snapshot_shape_and_policy(changed, reason):
    now = utcnow()
    scope = Scope(principal="alice", active_role="marketing", security_context_version=1,
                  database="retail", policy_revision="epoch")
    execution_scope = scope.model_copy(update={"principal": "replay", "database": "snapshot"})
    replay = "SELECT SUM(amount) FROM snapshot.sales"
    proof = Evidence(
        id="proof", family_id="family", cohort_id=scope.cohort_id,
        kind="ranger_acceptance", source="patched_fe_acceptance", availability="available",
        expires_at=now + timedelta(hours=1), summary={
            "scope": scope.model_dump(mode="json"), "snapshot_id": "snapshot",
            "candidate_shape": digest(replay), "row_filter": "PASS", "masking": "PASS",
            "execution_scope": execution_scope.model_dump(mode="json"),
            "snapshot_state": "partition-version",
        },
    ).model_dump(mode="json")
    for key, value in changed.items():
        if key.startswith("scope_"):
            proof["summary"]["scope"][key.removeprefix("scope_")] = value
        elif key in {"snapshot_id", "snapshot_state", "execution_scope",
                     "candidate_shape", "row_filter", "masking"}:
            proof["summary"][key] = value
        else:
            proof[key] = value
    assert ranger_acceptance_reason(
        proof, scope, "snapshot", replay, now, family_id="family",
        execution_scope=execution_scope, snapshot_state="partition-version",
    ) == reason
    if reason is None:
        assert ranger_acceptance_reason(
            proof, scope, "snapshot", replay, now + timedelta(hours=2), family_id="family",
            execution_scope=execution_scope, snapshot_state="partition-version",
        ) == "ranger_acceptance_expired"
        assert ranger_acceptance_reason(
            None, scope, "snapshot", replay, now, family_id="family",
            execution_scope=execution_scope, snapshot_state="partition-version",
        ) == "ranger_acceptance_unavailable"
