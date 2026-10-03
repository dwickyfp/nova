from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query_autopilot.engine_state import ObjectState
from app.modules.query_autopilot.models import (
    ActionKind,
    Candidate,
    Evidence,
    Scope,
    State,
    digest,
    utcnow,
)
from app.sql_frontend.autopilot import clone_materialized_view
from tests.unit.query_autopilot import test_service

setup = test_service.setup

DEFINITION = (
    "CREATE MATERIALIZED VIEW nova_ap_original (`id`, `s`) DISTRIBUTED BY RANDOM "
    "REFRESH MANUAL PROPERTIES ('replication_num'='1') AS "
    "SELECT id,SUM(amount) AS s FROM retail.orders GROUP BY id"
)


def test_clone_uses_snapshot_query_and_schedule_without_copying_properties():
    cloned = clone_materialized_view(
        DEFINITION, "nova_ap_trial", {"orders": "snapshot.orders"}, "retail", "snapshot"
    )
    assert cloned == (
        "CREATE MATERIALIZED VIEW `nova_ap_trial` (`id`,`s`) DISTRIBUTED BY RANDOM "
        "REFRESH MANUAL AS SELECT id,SUM(amount) AS s FROM `snapshot`.`orders` GROUP BY id"
    )
    assert "replication_num" not in cloned
    async_definition = DEFINITION.replace(
        "REFRESH MANUAL", "REFRESH ASYNC EVERY(INTERVAL 10 MINUTE)"
    )
    assert "REFRESH ASYNC EVERY(INTERVAL 10 MINUTE)" in clone_materialized_view(
        async_definition, "nova_ap_trial", {"orders": "snapshot.orders"}, "retail", "snapshot"
    )


@pytest.mark.parametrize(
    "definition",
    [
        DEFINITION.replace("RANDOM", "HASH(id)"),
        DEFINITION.replace("REFRESH MANUAL", "REFRESH INCREMENTAL"),
        DEFINITION.replace(
            "REFRESH MANUAL", "REFRESH ASYNC START('2026-01-01') EVERY(INTERVAL 10 MINUTE)"
        ),
        DEFINITION.replace("GROUP BY id", "GROUP BY id HAVING rand()>0.5"),
    ],
)
def test_unsupported_clone_cannot_be_executed(definition):
    with pytest.raises(ValueError):
        clone_materialized_view(
            definition, "nova_ap_trial", {"orders": "snapshot.orders"}, "retail", "snapshot"
        )


@pytest.mark.parametrize("invalidate", [False, True])
async def test_refresh_trial_clones_owned_view_binds_source_and_cleans_up(setup, invalidate):
    service, repo, candidate, enrollment, _, user = setup
    enrollment = enrollment.model_copy(update={"ranger_acceptance_ref": "ranger-proof"})
    await repo.put("enrollments", enrollment.id, enrollment.model_dump(mode="json"))
    proof = Evidence(
        id="ranger-proof",
        family_id=candidate.family_id,
        cohort_id=candidate.scope.cohort_id,
        kind="ranger_acceptance",
        availability="available",
        source="patched_fe_acceptance",
        expires_at=utcnow() + timedelta(hours=1),
        summary={
            "scope": candidate.scope.model_dump(mode="json"),
            "snapshot_id": enrollment.snapshot_id,
            "snapshot_state": digest([enrollment.snapshot_id, [["snapshot.orders", [[1, 7]]]]]),
            "execution_scope": Scope(
                principal=enrollment.execution_principal, active_role=enrollment.execution_role,
                security_context_version=enrollment.version, database=enrollment.sandbox_database,
                policy_revision=candidate.scope.policy_revision,
            ).model_dump(mode="json"),
            "row_filter": "PASS",
            "masking": "PASS",
            "candidate_shape": digest("SELECT id FROM `snapshot`.`orders`"),
        },
    )
    await repo.put("evidence", proof.id, proof.model_dump(mode="json"))
    candidate = candidate.model_copy(
        update={
            "kind": ActionKind.REFRESH_POLICY,
            "parameters": {"name": "nova_ap_original", "interval_minutes": 10},
        }
    )
    candidate = candidate.model_copy(
        update={"evidence_digest": await service.evidence_digest(candidate)}
    )
    await service._candidate(candidate)
    source = ObjectState(True, "7", digest(DEFINITION), True)
    await repo.put(
        "actions",
        "original",
        {
            "id": "original",
            "kind": "MATERIALIZED_VIEW",
            "state": "APPLIED",
            "family_id": candidate.family_id,
            "cohort_id": candidate.scope.cohort_id,
            "owned_object_binding": source.binding,
        },
    )
    original_execute = service.sql.execute
    temporary = None
    changed = False
    trace = []

    async def execute(statement, scope, **options):
        nonlocal temporary, changed
        trace.append((statement, scope.database))
        if statement.startswith("SHOW CREATE MATERIALIZED VIEW"):
            return QueryResult(
                columns=["name", "Create Materialized View"],
                rows=[
                    ["nova_ap_original", DEFINITION],
                ],
            )
        if statement.startswith("SHOW MATERIALIZED VIEWS"):
            row = ["nova_ap_original", "7", DEFINITION, "true", "SUCCESS"]
            if scope.database == enrollment.sandbox_database:
                row = [temporary, "8", "changed" if changed else "initial", "true", "SUCCESS"]
            return QueryResult(
                columns=["name", "id", "text", "is_active", "last_refresh_state"],
                rows=[row] if row[0] else [],
            )
        if statement.startswith("EXPLAIN") and temporary and changed:
            return QueryResult(columns=["plan"], rows=[["0:OlapScanNode"], ["TABLE: " + temporary]])
        if statement.startswith("CREATE MATERIALIZED VIEW"):
            durable = await repo.get("experiments", job["id"])
            assert durable["state"] == "APPLYING"
            assert durable["refresh_source"]["identity"] == "7"
            temporary = statement.split("`")[1]
        if statement.startswith("ALTER MATERIALIZED VIEW"):
            assert temporary and f"`{temporary}`" in statement
            durable = await repo.get("experiments", job["id"])
            assert durable["owned_object"]["identity"] == "8"
            service.sql.optimized = True
            changed = True
            if invalidate:
                await repo.put("actions", "original", {"id": "original", "state": "FAILED"})
        if statement.startswith("DROP MATERIALIZED VIEW"):
            assert temporary and f"`{temporary}`" in statement
            assert (await repo.get("actions", "trial-cleanup:" + job["id"]))["state"] == "APPLYING"
            temporary = None
        return await original_execute(statement, scope, **options)

    service.sql.execute = execute
    job = await service.mutate(
        candidate.id, "experiment", version=1, idempotency_key="refresh", user=user
    )
    assert (await service.run_operation(await repo.get("jobs", job["id"])))["state"] == "COMPLETED"
    latest = Candidate.model_validate(await repo.get("opportunities", candidate.id))
    assert latest.state == (State.INCONCLUSIVE if invalidate else State.READY_APPROVAL)
    record = await repo.get("experiments", job["id"])
    assert record["refresh_source"] == source.__dict__
    assert record["owned_object"]["definition_digest"] == digest("changed")
    assert record["cleanup"] == "verified_object_removed" and temporary is None
    assert all(
        database == enrollment.sandbox_database
        for sql, database in trace
        if sql.startswith(
            (
                "CREATE",
                "ALTER",
                "DROP",
                "REFRESH",
            )
        )
    )


async def test_failed_action_record_does_not_authorize_refresh_ownership(setup, monkeypatch):
    from app.modules.query_autopilot import service as module

    service, repo, candidate, _, _, _ = setup
    owned = ObjectState(True, "7", "definition", True)
    monkeypatch.setattr(module, "inspect_object", AsyncMock(return_value=owned))
    for state, kind in (("APPLYING", "MATERIALIZED_VIEW"), ("APPLIED", "EXPERIMENT_CLEANUP")):
        await repo.put(
            "actions",
            "untrusted",
            {
                "id": "untrusted",
                "state": state,
                "kind": kind,
                "owned_object_binding": owned.binding,
                "family_id": candidate.family_id,
                "cohort_id": candidate.scope.cohort_id,
            },
        )
        with pytest.raises(ValueError, match="ownership_unproven"):
            await service.owned_refresh_object(candidate, candidate.scope, object())
