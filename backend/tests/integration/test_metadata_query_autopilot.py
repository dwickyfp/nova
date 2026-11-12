"""Query Autopilot metadata on the real PG fixture: CAS, approvals, leases, facts, scope."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.query_autopilot.metadata_repository import TABLES, AutopilotMetadataRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg

TELEMETRY = {"web", "query", "proxy"}
READERS = {
    "policies": {"web", "query", "proxy", "automation"},
    "enrollments": {"web", "query", "proxy", "automation"},
    "evidence": {"web", "query", "proxy", "automation"},
    "observations": {"web", "query", "proxy", "automation"},
    "jobs": {"web", "scheduler", "automation"},
    **{kind: {"web", "automation"} for kind in (
        "opportunities", "experiments", "actions", "outcomes", "incidents", "families",
        "rollups", "baselines")},
}


class Autopilot:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, workload="web", *, name="ap-owner", active="ANALYST",
                          read_only=False):
        user = session(name, active=active)
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            actor = SecurityContext.from_session(user)
            async with db.transaction(actor, read_only=read_only) as conn:
                yield AutopilotMetadataRepository(conn, actor), conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None


@pytest_asyncio.fixture
async def autopilot(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="d" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Autopilot(environment, epoch)


def candidate(identifier="cand-1", **changes):
    return {"id": identifier, "version": 1, "family_id": "fam-1", "state": "PROPOSED",
            "kind": "MATERIALIZED_VIEW", "targets": ["sales.orders"], "approved_by": None,
            "approval_digest": None, "experiment_id": None, **changes}


def job(identifier, kind, state="QUEUED", **changes):
    return {"id": identifier, "kind": kind, "version": 1, "state": state,
            "candidate_id": "cand-1", "request_hash": "hash-" + identifier,
            "actor": "ap-owner", "actor_role": "ANALYST", "actor_session_id": "login-ap-owner",
            **changes}


def intent(identifier, **changes):
    return {"id": identifier, "candidate_id": "cand-1", "binding": "bind-1",
            "state": "APPLYING", "statement_digests": ["d1"], "kind": "MATERIALIZED_VIEW",
            "targets": ["sales.orders"], "actor": "ap-owner", "actor_role": "ACCOUNTADMIN",
            "actor_version": 5, **changes}


async def proposed(ap, identifier="cand-1"):
    async with ap.transaction() as (repo, _):
        return await repo.propose(identifier, candidate(identifier), family_id="fam-1",
                                  cohort_id="coh-1")


async def ready_for_approval(ap, identifier="cand-1"):
    row = await proposed(ap, identifier)
    async with ap.transaction("automation", name="svc-autopilot") as (repo, _):
        return await repo.update_candidate(
            identifier, candidate(identifier, state="READY_APPROVAL", version=2,
                                  experiment_id="exp-1"),
            expected_revision=row["metadata_revision"])


async def queued(ap, identifier, kind="apply", workload="automation", **changes):
    async with ap.transaction(workload, name="svc-autopilot" if workload != "web"
                              else "ap-owner") as (repo, _):
        row, created = await repo.create_job_once(identifier, job(identifier, kind, **changes))
    assert created
    return row


async def claimed(ap, identifier, **changes):
    await queued(ap, identifier, **changes)
    async with ap.transaction("automation", name="svc-autopilot") as (repo, _):
        return await repo.claim_job(identifier, expected_version=1)


# ── Governed definitions ──────────────────────────────────────────────────


async def test_definitions_change_only_from_the_version_read(autopilot):
    async with autopilot.transaction(name="admin-1") as (repo, _):
        row = await repo.save_definition(
            "policies", "default", {"id": "default", "version": 2, "mode": "GOVERNED"},
            expected_version=None)
    assert row["version"] == 2 and row["updated_by"] == "admin-1"
    ready = asyncio.Barrier(2)

    async def edit(mode):
        async with autopilot.transaction(name="admin-2") as (repo, _):
            await ready.wait()
            return await repo.save_definition(
                "policies", "default", {"id": "default", "version": 3, "mode": mode},
                expected_version=2)

    results = await asyncio.gather(edit("OBSERVE"), edit("AUTONOMOUS"), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    with pytest.raises(MetadataConflict):
        async with autopilot.transaction() as (repo, _):
            await repo.save_definition("policies", "default", {"id": "default", "version": 2},
                                       expected_version=None)
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction() as (_, conn):
            await conn.execute(
                "UPDATE nova_meta.query_autopilot_policies SET payload=%s WHERE id='default'",
                (Jsonb({"id": "default", "version": 3, "mode": "OBSERVE"}),))
    audit = await autopilot.admin(
        "SELECT action,metadata_revision,user_name FROM nova_meta.audit_log "
        "WHERE metadata_table='query_autopilot_policies' ORDER BY metadata_revision")
    assert [(a["action"], a["metadata_revision"]) for a in audit] == [("CREATE", 2),
                                                                      ("UPDATE", 3)]
    assert {a["user_name"] for a in audit} == {"admin-1", "admin-2"}
    for workload in ("query", "automation"):
        with pytest.raises(MetadataPermissionDenied):
            async with autopilot.transaction(workload) as (repo, _):
                await repo.save_definition(
                    "enrollments", "enr-1", {"id": "enr-1", "version": 1},
                    expected_version=None)


# ── Candidates and approvals ──────────────────────────────────────────────


async def test_candidate_changes_need_the_revision_they_read(autopilot):
    row = await proposed(autopilot)
    assert row["metadata_revision"] == 1 and row["approved_by"] is None
    ready = asyncio.Barrier(2)

    async def move(state):
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            await ready.wait()
            return await repo.update_candidate("cand-1", candidate(state=state),
                                               expected_revision=1)

    results = await asyncio.gather(move("VALIDATING"), move("INCONCLUSIVE"),
                                   return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    with pytest.raises(MetadataConflict):
        await proposed(autopilot)
    for bad in (candidate("c2", state="APPROVED"), candidate("c3", approved_by="x"),
                candidate("c4", version=2)):
        with pytest.raises(MetadataIntegrityViolation):
            async with autopilot.transaction() as (repo, _):
                await repo.propose(bad["id"], bad, family_id="fam-1", cohort_id="coh-1")
    with pytest.raises(MetadataIntegrityViolation):  # the revision cannot be skipped
        async with autopilot.transaction() as (_, conn):
            await conn.execute("UPDATE nova_meta.query_autopilot_opportunities SET "
                               "metadata_revision=metadata_revision+5 WHERE id='cand-1'")
    audit = await autopilot.admin(
        "SELECT action FROM nova_meta.audit_log WHERE metadata_table="
        "'query_autopilot_opportunities' ORDER BY metadata_revision")
    assert [a["action"] for a in audit][0] == "CREATE" and len(audit) == 2


async def test_an_approval_is_bound_to_the_approver_and_cannot_be_restored(autopilot):
    row = await ready_for_approval(autopilot)
    expires = datetime.now(UTC) + timedelta(hours=24)
    approved_payload = candidate(state="APPROVED", version=2, experiment_id="exp-1",
                                 approved_by="approver", approval_digest="dg-1")
    # The worker never approves; the payload must name the approving principal.
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction("automation", name="approver") as (repo, _):
            await repo.approve("cand-1", approved_payload, expected_revision=2,
                               approval_digest="dg-1", expires_at=expires)
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction(name="someone-else") as (repo, _):
            await repo.approve("cand-1", approved_payload, expected_revision=2,
                               approval_digest="dg-1", expires_at=expires)
    async with autopilot.transaction(name="approver", active="ACCOUNTADMIN") as (repo, _):
        approved = await repo.approve("cand-1", approved_payload, expected_revision=2,
                                      approval_digest="dg-1", expires_at=expires)
    assert (approved["approved_by"], approved["approved_role"],
            approved["approved_context_version"]) == ("approver", "ACCOUNTADMIN", 5)
    assert row["metadata_revision"] + 1 == approved["metadata_revision"] == 3
    # The approval cannot move to another principal or survive a new version.
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction("automation") as (_, conn):
            await conn.execute("UPDATE nova_meta.query_autopilot_opportunities SET "
                               "approved_by='intruder',metadata_revision=4 WHERE id='cand-1'")
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            await repo.update_candidate(
                "cand-1", {**approved_payload, "state": "VALIDATING", "version": 3},
                expected_revision=3)
    # Withdrawn (expired) approvals are not restored by setting the state back.
    async with autopilot.transaction(name="approver", active="ACCOUNTADMIN") as (repo, _):
        await repo.update_candidate(
            "cand-1", {**approved_payload, "state": "BLOCKED", "approved_by": None,
                       "approval_digest": None}, expected_revision=3, withdraw_approval=True)
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction(name="approver", active="ACCOUNTADMIN") as (repo, _):
            await repo.approve("cand-1", approved_payload, expected_revision=4,
                               approval_digest="dg-1", expires_at=expires)
    actions = await autopilot.admin(
        "SELECT action,user_name,active_role FROM nova_meta.audit_log WHERE "
        "metadata_table='query_autopilot_opportunities' AND action='APPROVED'")
    assert [(a["user_name"], a["active_role"]) for a in actions] == [
        ("approver", "ACCOUNTADMIN")]


async def test_applying_needs_a_current_approval(autopilot):
    await ready_for_approval(autopilot)
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            await repo.update_candidate(
                "cand-1", candidate(state="APPLYING", version=2, experiment_id="exp-1"),
                expected_revision=2)
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            await repo.update_candidate(
                "cand-1", candidate(state="REJECTED", version=2), expected_revision=2)


# ── Jobs and leases ───────────────────────────────────────────────────────


async def test_jobs_are_created_once_and_keys_are_not_reused(autopilot):
    async def create():
        async with autopilot.transaction() as (repo, _):
            return await repo.create_job_once("op-1", job("op-1", "experiment"))

    results = await asyncio.gather(*(create() for _ in range(4)))
    assert sum(created for _, created in results) == 1
    with pytest.raises(MetadataConflict):
        async with autopilot.transaction() as (repo, _):
            await repo.create_job_once("op-1", job("op-1", "experiment", request_hash="other"))
    # A requested operation is attributed to the bound actor, not the payload.
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction(name="ap-other") as (repo, _):
            await repo.create_job_once("op-2", job("op-2", "apply"))
    async with autopilot.transaction("scheduler", name="svc-scheduler") as (repo, _):
        _, created = await repo.create_job_once(
            "aggregate:1", {"id": "aggregate:1", "kind": "aggregate", "version": 1,
                            "state": "QUEUED"})
    assert created
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction("scheduler", name="svc-scheduler") as (repo, _):
            await repo.create_job_once("op-3", job("op-3", "apply"))


async def test_one_worker_claims_a_job(autopilot):
    await queued(autopilot, "op-claim")

    async def claim():
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            return await repo.claim_job("op-claim", expected_version=1)

    results = await asyncio.gather(*(claim() for _ in range(4)))
    assert sorted(results, key=str) == [1, None, None, None]
    with pytest.raises(MetadataIntegrityViolation):  # only the worker claims
        async with autopilot.transaction() as (_, conn):
            await conn.execute("UPDATE nova_meta.query_autopilot_jobs SET "
                               "lease_generation=lease_generation+1 WHERE id='op-claim'")


async def test_a_stale_worker_cannot_apply_or_record_an_outcome(autopilot):
    old = await claimed(autopilot, "op-apply")
    await asyncio.sleep(0.05)
    async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
        new = await repo.retake_job("op-apply", older_than_seconds=0)
    assert (old, new) == (1, 2)
    # The first worker comes back: no intent, acknowledgement, outcome or finish.
    with pytest.raises(MetadataConflict):
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            await repo.record_action_intent("op-apply", intent("op-apply"), job_id="op-apply",
                                            generation=old)
    async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
        row, created = await repo.record_action_intent(
            "op-apply", intent("op-apply"), job_id="op-apply", generation=new)
        assert created and row["state"] == "APPLYING" and row["job_generation"] == 2
        assert await repo.heartbeat_job("op-apply", generation=old) is False
    applied = intent("op-apply", state="APPLIED", owned_object_binding="obj-1",
                     applied_at="2026-11-12T00:00:00+00:00")
    with pytest.raises(MetadataConflict):
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            await repo.acknowledge_action("op-apply", applied, job_id="op-apply",
                                          generation=old)
    with pytest.raises(MetadataConflict):
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            await repo.record_outcome("op-apply", {"state": "SUCCESS"}, job_id="op-apply",
                                      generation=old, family_id="fam-1", cohort_id="coh-1",
                                      state="SUCCESS")
    async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
        assert await repo.finish_job("op-apply", job("op-apply", "apply", state="COMPLETED",
                                                     version=3), generation=old) is None
        acknowledged = await repo.acknowledge_action("op-apply", applied, job_id="op-apply",
                                                     generation=new)
        assert acknowledged["state"] == "APPLIED"
        done = await repo.finish_job("op-apply", job("op-apply", "apply", state="COMPLETED",
                                                     version=3), generation=new)
        assert done["state"] == "COMPLETED" and done["version"] == 3
    # A finished job is frozen and holds no lease: nothing more can be written.
    with pytest.raises(MetadataConflict):
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            await repo.save_experiment("op-apply", {"candidate_id": "cand-1"},
                                       job_id="op-apply", generation=new, family_id="fam-1",
                                       cohort_id="coh-1", state="SUCCESS")
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction("automation") as (_, conn):
            await conn.execute("UPDATE nova_meta.query_autopilot_jobs SET state='QUEUED',"
                               "lease_generation=lease_generation+1 WHERE id='op-apply'")


async def test_in_request_decisions_complete_without_a_lease(autopilot):
    await queued(autopilot, "op-approve", kind="approve", workload="web", state="RUNNING")
    with pytest.raises(MetadataIntegrityViolation):  # queued work needs a claim
        await queued(autopilot, "op-exp", kind="experiment", workload="web", state="RUNNING")
    async with autopilot.transaction() as (repo, _):
        done = await repo.complete_decision(
            "op-approve", job("op-approve", "approve", state="COMPLETED", version=2),
            expected_version=1)
    assert done["state"] == "COMPLETED" and done["lease_generation"] == 0
    with pytest.raises(MetadataConflict):
        async with autopilot.transaction() as (repo, _):
            await repo.complete_decision(
                "op-approve", job("op-approve", "approve", state="COMPLETED", version=2),
                expected_version=1)


# ── Facts ─────────────────────────────────────────────────────────────────


async def test_actions_are_facts_with_identical_retry(autopilot):
    generation = await claimed(autopilot, "op-act")
    async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
        await repo.record_action_intent("op-act", intent("op-act"), job_id="op-act",
                                        generation=generation)
        again, created = await repo.record_action_intent(
            "op-act", intent("op-act"), job_id="op-act", generation=generation)
        assert created is False and again["state"] == "APPLYING"
        with pytest.raises(MetadataConflict):
            await repo.record_action_intent("op-act", intent("op-act", binding="other"),
                                            job_id="op-act", generation=generation)
    for bad in (intent("op-act", state="APPLIED"),  # no verified object identity
                {**intent("op-act", state="APPLIED", owned_object_binding="o"),
                 "statement_digests": "d1"},
                {k: v for k, v in intent("op-act", state="APPLIED",
                                         owned_object_binding="o").items() if k != "binding"}):
        with pytest.raises(MetadataIntegrityViolation):
            async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
                await repo.acknowledge_action("op-act", bad, job_id="op-act",
                                              generation=generation)
    applied = intent("op-act", state="APPLIED", owned_object_binding="o-1")
    async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
        await repo.acknowledge_action("op-act", applied, job_id="op-act", generation=generation)
        assert (await repo.acknowledge_action(
            "op-act", applied, job_id="op-act", generation=generation))["state"] == "APPLIED"
        with pytest.raises(MetadataConflict):
            await repo.acknowledge_action("op-act", {**applied, "owned_object_binding": "o-2"},
                                          job_id="op-act", generation=generation)
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction("automation") as (_, conn):
            await conn.execute("UPDATE nova_meta.query_autopilot_actions SET state='APPLYING',"
                               "payload=payload||'{\"state\":\"APPLYING\"}' WHERE id='op-act'")
    with pytest.raises(MetadataPermissionDenied):
        async with autopilot.transaction("automation") as (_, conn):
            await conn.execute("DELETE FROM nova_meta.query_autopilot_actions")


async def test_outcomes_and_observations_accept_only_identical_retries(autopilot):
    generation = await claimed(autopilot, "verify:op-1", kind="verify")
    outcome = {"state": "REGRESSED", "gain": -0.3, "rollback": "pending"}
    async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
        _, created = await repo.record_outcome(
            "verify:op-1", outcome, job_id="verify:op-1", generation=generation,
            family_id="fam-1", cohort_id="coh-1", state="REGRESSED")
        assert created
        _, created = await repo.record_outcome(
            "verify:op-1", outcome, job_id="verify:op-1", generation=generation,
            family_id="fam-1", cohort_id="coh-1", state="REGRESSED")
        assert created is False
        with pytest.raises(MetadataConflict):
            await repo.record_outcome(
                "verify:op-1", {**outcome, "gain": 0.4}, job_id="verify:op-1",
                generation=generation, family_id="fam-1", cohort_id="coh-1", state="SUCCESS")
        resolved = await repo.resolve_rollback("verify:op-1", "verified_object_removed",
                                               job_id="verify:op-1", generation=generation)
        assert resolved["payload"]["rollback"] == "verified_object_removed"
        with pytest.raises(MetadataConflict):
            await repo.resolve_rollback("verify:op-1", "other", job_id="verify:op-1",
                                        generation=generation)
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction("automation") as (_, conn):
            await conn.execute("UPDATE nova_meta.query_autopilot_outcomes SET "
                               "payload=payload||'{\"gain\":9}' WHERE id='verify:op-1'")
    observed = datetime(2026, 11, 1, tzinfo=UTC)
    item = {"id": "obs-1", "family_id": "fam-1", "cohort_id": "coh-1", "state": "ok",
            "payload": {"id": "obs-1", "total_ms": 12.5, "canonical": "SELECT ?"},
            "observed_at": observed, "expires_at": observed + timedelta(days=7)}

    async def record(batch):
        async with autopilot.transaction("query", name="svc-query") as (repo, _):
            return await repo.record_observations(batch)

    assert sorted(await asyncio.gather(record([item]), record([item]))) == [0, 1]
    assert await record([item]) == 0
    with pytest.raises(MetadataConflict):
        await record([{**item, "payload": {**item["payload"], "total_ms": 99}}])
    with pytest.raises(ValueError):
        await record([item] * 129)
    rows = await autopilot.admin(
        "SELECT created_at,provenance FROM nova_meta.query_autopilot_observations")
    assert rows == [{"created_at": observed, "provenance": "runtime"}]
    with pytest.raises(MetadataPermissionDenied):
        async with autopilot.transaction("query") as (_, conn):
            await conn.execute("UPDATE nova_meta.query_autopilot_observations SET state='x'")


async def test_evidence_is_kept_until_it_expires(autopilot):
    sample = {"id": "sample-1", "family_id": "fam-1", "cohort_id": "coh-1",
              "kind": "replay_sample", "availability": "unavailable", "source": "opt_in_workload",
              "collected_at": "2026-11-12T00:00:00+00:00", "summary": {"parameter_digest": "p"},
              "payload_ref": "ref-1", "reason": "upload_pending"}
    async with autopilot.transaction("proxy", name="svc-proxy") as (repo, _):
        await repo.put_evidence("sample-1", sample, family_id="fam-1", cohort_id="coh-1")
        available = {**sample, "availability": "available", "reason": None}
        await repo.put_evidence("sample-1", available, family_id="fam-1", cohort_id="coh-1")
    for bad in ({**available, "summary": {"parameter_digest": "changed"}},
                {**available, "kind": "profile"},
                {**available, "availability": "expired"}):  # an expired payload is dropped
        with pytest.raises(MetadataIntegrityViolation):
            async with autopilot.transaction("automation") as (repo, _):
                await repo.put_evidence("sample-1", bad, family_id="fam-1", cohort_id="coh-1")
    expired = {**available, "availability": "expired", "payload_ref": None,
               "reason": "retention_expired"}
    with pytest.raises(MetadataPermissionDenied):
        async with autopilot.transaction("web") as (_, conn):
            await conn.execute("DELETE FROM nova_meta.query_autopilot_evidence")
    async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
        assert await repo.delete_expired_evidence("sample-1") is False
        await repo.put_evidence("sample-1", expired, family_id="fam-1", cohort_id="coh-1")
        await repo.put_evidence("sample-1", expired, family_id="fam-1", cohort_id="coh-1")
        with pytest.raises(psycopg.errors.CheckViolation):
            async with repo.connection.transaction():
                await repo.put_evidence("sample-1", {**expired, "availability": "available"},
                                        family_id="fam-1", cohort_id="coh-1")
        assert await repo.delete_expired_evidence("sample-1") is True


async def test_retention_never_sweeps_decisions(autopilot):
    await proposed(autopilot)
    old = datetime(2026, 1, 1, tzinfo=UTC)
    async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
        await repo.put_summary("baselines", "base-1", {"id": "base-1"}, family_id="fam-1",
                               cohort_id="coh-1", created_at=old)
        await repo.put_summary("rollups", "roll-1", {"id": "roll-1"}, family_id="fam-1",
                               cohort_id="coh-1", state="OPEN")
        await repo.record_incident_once("inc-1", {"id": "inc-1", "state": "OPEN"},
                                        family_id="fam-1", cohort_id="coh-1")
        _, created = await repo.record_incident_once("inc-1", {"id": "inc-1", "x": 1},
                                                     family_id="fam-1", cohort_id="coh-1")
        assert created is False
        assert await repo.cleanup_before("baselines", datetime(2026, 2, 1, tzinfo=UTC)) == 1
        assert await repo.cleanup_before("rollups", datetime(2026, 2, 1, tzinfo=UTC)) == 0
        with pytest.raises(ValueError):
            await repo.cleanup_before("opportunities", datetime.now(UTC))
        await repo.save_checkpoint("aggregation_cursor", {"id": "aggregation_cursor",
                                                          "state": "CHECKPOINT"})
        await repo.save_checkpoint("aggregation_cursor", {"id": "aggregation_cursor",
                                                          "state": "CHECKPOINT", "through": "t"})
    with pytest.raises(MetadataIntegrityViolation):
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            await repo.put_summary("rollups", "roll-2", {"id": "roll-2"}, family_id="fam-1",
                                   cohort_id="coh-1", state="DONE")
    for table in ("opportunities", "incidents"):
        with pytest.raises(MetadataPermissionDenied):
            async with autopilot.transaction("automation") as (_, conn):
                await conn.execute(f"DELETE FROM nova_meta.query_autopilot_{table}")
    await queued(autopilot, "cleanup:1", kind="cleanup")
    await queued(autopilot, "op-keep", kind="apply")
    async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
        generation = await repo.claim_job("cleanup:1", expected_version=1)
        await repo.finish_job("cleanup:1", job("cleanup:1", "cleanup", state="COMPLETED",
                                               version=3), generation=generation)
        purged = await repo.purge_finished_jobs(datetime.now(UTC) + timedelta(minutes=1))
        assert purged == 1
        assert await repo.get("jobs", "op-keep") is not None


# ── Workloads, maintenance, import ────────────────────────────────────────


@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(autopilot, workload):
    await proposed(autopilot)
    readable = {kind for kind, readers in READERS.items() if workload in readers}
    async with autopilot.transaction(workload, name="svc-" + workload) as (_, conn):
        for kind, table in TABLES.items():
            try:
                async with conn.transaction():
                    await conn.execute(f"SELECT count(*) FROM nova_meta.{table}")
                allowed = True
            except psycopg.errors.InsufficientPrivilege:
                allowed = False
            assert allowed is (kind in readable), kind
        sample = {"id": "ev-" + workload, "availability": "unavailable", "kind": "k",
                  "source": "s"}
        try:
            async with conn.transaction():
                await AutopilotMetadataRepository(conn, SecurityContext.from_session(
                    session("svc-" + workload))).put_evidence(
                        sample["id"], sample, family_id="f", cohort_id="c")
            evidence_writer = True
        except psycopg.errors.InsufficientPrivilege:
            evidence_writer = False
        assert evidence_writer is (workload in TELEMETRY | {"automation"})
        for table in ("opportunities", "policies", "enrollments", "experiments", "actions",
                      "outcomes", "incidents"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute(f"DELETE FROM nova_meta.query_autopilot_{table}")
    if workload != "web":
        with pytest.raises(MetadataPermissionDenied):
            async with autopilot.transaction(workload) as (repo, _):
                await repo.save_definition("policies", "default",
                                           {"id": "default", "version": 1},
                                           expected_version=None)


async def test_maintenance_blocks_writes(autopilot):
    await proposed(autopilot)
    await autopilot.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    try:
        with pytest.raises(MetadataReadOnlyMaintenance):
            async with autopilot.transaction("automation") as (repo, _):
                await repo.update_candidate("cand-1", candidate(state="VALIDATING"),
                                            expected_revision=1)
        async with autopilot.transaction(read_only=True) as (repo, _):
            assert (await repo.get("opportunities", "cand-1"))["state"] == "PROPOSED"
    finally:
        await autopilot.admin("UPDATE nova_meta.deployment_state SET maintenance=false")


async def test_import_is_maintenance_only_and_keeps_legacy_rows(autopilot):
    env = autopilot.environment
    legacy_time = datetime(2025, 3, 1, 8, 30, tzinfo=UTC)
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as conn:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                await conn.execute(
                    "INSERT INTO nova_meta.query_autopilot_families (id,family_id,cohort_id,"
                    "version,state,payload,created_at,updated_at) VALUES "
                    "('f','f','c',1,'',%s,%s,%s)", (Jsonb({}), legacy_time, legacy_time))
        await autopilot.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                for table, identifier, state, payload in (
                    ("jobs", "legacy-run", "RUNNING", job("legacy-run", "apply",
                                                          state="RUNNING", version=2)),
                    ("actions", "legacy-run", "APPLYING", intent("legacy-run")),
                    ("opportunities", "legacy-cand", "APPROVED",
                     candidate("legacy-cand", state="APPROVED", approved_by="old-admin")),
                    ("observations", "legacy-obs", "", {"weird": True}),
                ):
                    await conn.execute(
                        f"INSERT INTO nova_meta.query_autopilot_{table} (id,family_id,cohort_id,"
                        "version,state,payload,created_at,updated_at) "
                        "VALUES (%s,'fam-1','coh-1',%s,%s,%s,%s,%s)",
                        (identifier, payload.get("version", 1), state, Jsonb(payload),
                         legacy_time, legacy_time))
        finally:
            await autopilot.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    rows = await autopilot.admin(
        "SELECT id,provenance,created_at,updated_at FROM nova_meta.query_autopilot_jobs "
        "UNION ALL SELECT id,provenance,created_at,updated_at "
        "FROM nova_meta.query_autopilot_observations ORDER BY id")
    assert [(r["id"], r["provenance"], r["created_at"]) for r in rows] == [
        ("legacy-obs", "imported", legacy_time), ("legacy-run", "imported", legacy_time)]
    async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
        # An imported running job holds no live lease: it must be retaken first.
        assert await repo.finish_job("legacy-run", job("legacy-run", "apply",
                                     state="BLOCKED", version=3), generation=1) is None
        generation = await repo.retake_job("legacy-run", older_than_seconds=0)
    assert generation == 1
    # The imported intent names no job lease, so it cannot be acknowledged; it
    # stays APPLYING for reconciliation instead of being re-submitted.
    with pytest.raises(MetadataConflict):
        async with autopilot.transaction("automation", name="svc-autopilot") as (repo, _):
            await repo.acknowledge_action(
                "legacy-run", intent("legacy-run", state="APPLIED", owned_object_binding="o"),
                job_id="legacy-run", generation=generation)
    async with autopilot.transaction(read_only=True) as (repo, _):
        legacy = await repo.get("opportunities", "legacy-cand")
    assert legacy["approved_by"] is None and legacy["payload"]["approved_by"] == "old-admin"
