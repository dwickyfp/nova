"""Engine application boundaries with an explicitly seeded trial verdict.

The verdict is a state-machine fixture, not a measured optimization success.
Application SQL, identity, persistence and the verification wait use real services.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.core.database import db
from app.core.redis import session_store
from app.core.security import encrypt_password
from app.modules.query_autopilot.correctness import equivalent, prove_result
from app.modules.query_autopilot.engine_state import registered_snapshot_state
from app.modules.query_autopilot.models import (
    Budget,
    Candidate,
    Enrollment,
    Evidence,
    Scope,
    State,
    digest,
    utcnow,
)
from app.modules.query_autopilot.repository import repository
from app.modules.query_autopilot.runtime import AuthorizedSQL
from app.modules.query_autopilot.service import AutopilotService
from app.modules.task_orchestration.credentials import StaticCredentialProvider
from app.modules.task_orchestration.execution import DelegateExecutor
from app.sql_frontend.fingerprint import fingerprint
from tests.integration import test_query_autopilot_live

pytestmark = pytest.mark.engine
autopilot_engine = test_query_autopilot_live.autopilot_engine


async def test_approved_statistics_application_is_durable_and_verification_waits(
    autopilot_engine,
):
    import os

    scope: Scope = autopilot_engine
    prefix = "application-fixture-" + uuid4().hex
    password = os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    runtime = AuthorizedSQL(DelegateExecutor(StaticCredentialProvider({"nova_admin": password})))
    service = AutopilotService(sql=runtime)
    session_id = await session_store.create(
        "nova_admin", encrypt_password(password), ["ACCOUNTADMIN"],
        active_role="ACCOUNTADMIN",
    )
    user = {**await session_store.get(session_id), "session_id": session_id}
    statement = "SELECT SUM(amount) FROM facts"
    family = fingerprint(statement).family_id
    policy = await service.policy()
    enrollment = Enrollment(
        id=prefix, scope=scope, sandbox_database=scope.database + "_snapshot",
        table_mapping={"facts": scope.database + "_snapshot.facts"},
        snapshot_id="explicit-state-machine-fixture", snapshot_created_at=utcnow(),
        snapshot_frozen=True, execution_principal="nova_admin", execution_role=scope.active_role,
        budget=Budget(resource_group="fixture"), replay_opt_in=True,
        statistics_auto=False,
    )
    sample_id = prefix + "-sample"
    sample = Evidence(
        id=sample_id, family_id=family, cohort_id=scope.cohort_id,
        kind="replay_sample", availability="available", source="explicit_application_fixture",
        expires_at=utcnow() + timedelta(hours=24),
        payload_ref=service.payloads.reference(sample_id),
    )
    records = [("enrollments", prefix), ("evidence", sample_id), ("experiments", prefix),
               ("opportunities", prefix)]

    async def run(statement):
        async with runtime.connection(scope) as connection:
            return await runtime.execute(
                statement, scope, connection=connection, category="diagnostic",
            )

    def proof(result):
        return prove_result(
            result.rows, result.column_types, ordered=False,
            max_rows=100, max_bytes=10000, truncated=result.truncated,
        )

    try:
        await db.execute_system(f"CREATE DATABASE {enrollment.sandbox_database}")
        await db.execute_system(
            f"GRANT ALL ON {enrollment.sandbox_database}.* TO ROLE {scope.active_role}"
        )
        await db.execute_system(
            f"CREATE TABLE {enrollment.sandbox_database}.facts LIKE {scope.database}.facts"
        )
        await db.execute_system(
            f"INSERT INTO {enrollment.sandbox_database}.facts "
            f"SELECT * FROM {scope.database}.facts"
        )
        before = proof(await run(statement))
        await repository.put("enrollments", prefix, enrollment.model_dump(mode="json"))
        await repository.put(
            "evidence", sample_id, sample.model_dump(mode="json"),
            family_id=family, cohort_id=scope.cohort_id,
        )
        await service.payloads.put(sample_id, statement)
        candidate = Candidate(
            id=prefix, family_id=family, scope=scope, kind="STATISTICS", targets=("facts",),
            evidence_ids=(sample_id,), enrollment_id=prefix, enrollment_version=1,
            policy_version=policy.version, state=State.READY_APPROVAL,
            experiment_id=prefix, experiment_digest=digest("explicit-trial-verdict-fixture"),
        )
        candidate = candidate.model_copy(
            update={"evidence_digest": await service.evidence_digest(candidate)}
        )
        async with runtime.connection(enrollment.scope.model_copy(
            update={"database": enrollment.sandbox_database},
        )) as snapshot_connection:
            snapshot_state = await registered_snapshot_state(
                runtime, enrollment, enrollment.scope.model_copy(
                    update={"database": enrollment.sandbox_database},
                ), snapshot_connection,
            )
        assert snapshot_state
        await repository.put("experiments", prefix, {
            "id": prefix, "state": "SUCCESS", "result_digest": candidate.experiment_digest,
            "candidate_binding": candidate.proposal_binding, "sample_id": sample_id,
            "provenance": "seeded_trial_verdict_for_application_boundary_only",
            "result": {"snapshot": snapshot_state},
            "application_definition_digest": digest(("ANALYZE TABLE `facts` WITH SYNC MODE",)),
        })
        await service._candidate(candidate)
        approved = await service.mutate(
            prefix, "approve", version=1, idempotency_key=prefix + "-approve", user=user,
        )
        records.append(("jobs", approved["id"]))
        assert approved["state"] == "COMPLETED"
        candidate = Candidate.model_validate(await repository.get("opportunities", prefix))
        assert candidate.state == State.APPROVED and candidate.approval_digest == candidate.binding
        submitted = await service.mutate(
            prefix, "apply", version=1, idempotency_key=prefix + "-apply", user=user,
        )
        records.extend([("jobs", submitted["id"]), ("actions", submitted["id"]),
                        ("jobs", "verify:" + submitted["id"])])
        job = await repository.get("jobs", submitted["id"])
        assert (await service.run_operation(job))["state"] == "COMPLETED"
        action = await repository.get("actions", submitted["id"])
        assert action["state"] == "APPLIED" and action["actor"] == "nova_admin"
        assert action["applied_at"] >= action["started_at"]
        assert len(action["statement_digests"]) == 1
        assert equivalent(before, proof(await run(statement))) is True
        stats = await run(
            f"SHOW STATS META WHERE `Table`='facts' AND `Database`='{scope.database}'"
        )
        assert stats.rows
        candidate = Candidate.model_validate(await repository.get("opportunities", prefix))
        assert candidate.state == State.VERIFYING
        verify_job = await repository.get("jobs", "verify:" + submitted["id"])
        waiting = await service.verify(candidate, enrollment, policy, verify_job)
        assert waiting["state"] == "QUEUED"
        assert waiting["reason"] == "awaiting_complete_verification_window"
        assert waiting["not_before"] == (
            datetime.fromisoformat(action["applied_at"])
            + timedelta(minutes=30)
        ).isoformat()
        assert await repository.get("outcomes", verify_job["id"]) is None
        assert (await service.run_operation(job))["state"] == "COMPLETED"
        assert await repository.get("actions", submitted["id"]) == action
        assert (await repository.get("opportunities", prefix))["state"] == "VERIFYING"
    finally:
        for kind, identifier in reversed(records):
            await repository.delete(kind, identifier)
        await service.payloads.delete(sample.payload_ref)
        await session_store.delete(session_id)
        await db.execute_system(f"DROP DATABASE IF EXISTS {enrollment.sandbox_database}")
        # Statistics refresh has no rollback claim; the fixture owns the table.
        assert (await db.execute_system(f"SHOW TABLES FROM {scope.database}"))["rows"]
