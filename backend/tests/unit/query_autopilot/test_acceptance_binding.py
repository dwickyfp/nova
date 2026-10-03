from datetime import timedelta

import pytest

from app.modules.query_autopilot.models import ActionKind, Evidence, State, utcnow
from app.modules.query_autopilot.service import Conflict
from tests.unit.query_autopilot import test_service

setup = test_service.setup


@pytest.mark.parametrize("kind", ["MATERIALIZED_VIEW", "REFRESH_POLICY"])
@pytest.mark.parametrize("change", ["summary", "expiry", "deleted", "reference", "cohort"])
async def test_ranger_proof_changes_or_expiry_invalidate_bound_approval(setup, kind, change):
    service, repo, candidate, enrollment, _, user = setup
    proof = Evidence(
        id="ranger-proof", family_id=candidate.family_id,
        cohort_id=candidate.scope.cohort_id, kind="ranger_acceptance",
        source="patched_fe_acceptance", availability="available",
        expires_at=utcnow() + timedelta(hours=1), summary={"masking": "PASS"},
    ).model_dump(mode="json")
    await repo.put("evidence", proof["id"], proof)
    enrollment = enrollment.model_copy(update={"ranger_acceptance_ref": proof["id"]})
    await repo.put("enrollments", enrollment.id, enrollment.model_dump(mode="json"))
    candidate = candidate.model_copy(
        update={"kind": ActionKind(kind), "state": State.READY_APPROVAL}
    )
    candidate = candidate.model_copy(
        update={"evidence_digest": await service.evidence_digest(candidate)}
    )
    await service._candidate(candidate)
    assert await service.evidence_current(candidate)
    assert (await service.bound_evidence_ids(candidate))[-1] == proof["id"]
    if change == "deleted":
        repo.records.pop(("evidence", proof["id"]))
    elif change == "reference":
        await repo.put("enrollments", enrollment.id, {
            **enrollment.model_dump(mode="json"), "ranger_acceptance_ref": "different-proof",
        })
    else:
        if change == "summary":
            proof = {**proof, "summary": {"masking": "FAIL"}}
        elif change == "expiry":
            proof = {**proof, "expires_at": (utcnow() - timedelta(seconds=1)).isoformat()}
        else:
            proof = {**proof, "cohort_id": "same-role-different-principal"}
        await repo.put("evidence", proof["id"], proof)
    assert await service.evidence_current(candidate) is False
    with pytest.raises(Conflict, match="approval_evidence_or_policy_changed"):
        await service.mutate(
            candidate.id, "approve", version=candidate.version,
            idempotency_key="reject-changed-ranger-proof", user=user,
        )
    assert (await repo.get("opportunities", candidate.id))["approval_digest"] is None
    assert not any(call[-1] == "maintenance" for call in service.sql.calls)


async def test_unrelated_statistics_candidate_does_not_bind_a_materialized_view_proof(setup):
    service, repo, candidate, enrollment, _, _ = setup
    old_digest = await service.evidence_digest(candidate)
    await repo.put("enrollments", enrollment.id, {
        **enrollment.model_dump(mode="json"), "ranger_acceptance_ref": "unrelated-proof",
    })
    assert candidate.kind == "STATISTICS"
    assert await service.bound_evidence_ids(candidate) == candidate.evidence_ids
    assert await service.evidence_digest(candidate) == old_digest
    assert await service.evidence_current(candidate)
