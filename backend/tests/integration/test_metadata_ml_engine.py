"""ML engine metadata on the real PG fixture: fencing, reservations, provenance, scope."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio

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
from app.modules.ml_engine.metadata_repository import MLMetadataRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
SHA = "a" * 64
REGISTRY_READERS = {"web", "query", "proxy", "agent", "automation", "ml", "maintenance"}
TRAINERS = {"web", "query", "agent", "automation", "ml"}
EPHEMERAL_WRITERS = {"web", "query", "proxy", "agent", "automation", "ml"}
JOB_READERS = {"web", "query", "ml"}


class ML:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, workload="web", *, name="ml-owner", read_only=False,
                          service=False):
        if service:
            actor = SecurityContext(name, "ACCOUNTADMIN", principal_type="SERVICE")
        else:
            actor = SecurityContext.from_session(session(name, active="ANALYST"))
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            async with db.transaction(actor, read_only=read_only) as conn:
                yield MLMetadataRepository(conn, actor), conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None

    async def backdate_finished(self, job_id):
        """Age a finished job for the retention test, outside the runtime triggers."""
        async with self.environment.admin.transaction():
            await self.environment.admin.execute("SET LOCAL session_replication_role=replica")
            await self.environment.admin.execute(
                "UPDATE nova_meta.config_ml_jobs SET finished_at=finished_at-interval '2 days' "
                "WHERE id=%s", (job_id,))


@pytest_asyncio.fixture
async def ml(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="e" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return ML(environment, epoch)


def model(**changes):
    return {"model_name": "churn", "model_type": "classification", "tenant": "default",
            "database": "sales", "schema": None, "target_column": "churned",
            "feature_columns": ["age", "plan"], "hyperparameters": {"depth": 3},
            "training_sql": "SELECT * FROM t WHERE x = ?", **changes}


async def reserve(ml, workload="ml", **changes):
    async with ml.transaction(workload) as (repo, _):
        return await repo.reserve_version(**model(**changes))


async def publish(ml, reservation, workload="ml"):
    async with ml.transaction(workload) as (repo, _):
        return await repo.publish_version(
            model_id=reservation["model_id"], version=reservation["version"],
            reservation_token=reservation["reservation_token"], feature_columns=["age"],
            fields={"artifact_uri": "s3://models/churn/1", "artifact_sha256": SHA,
                    "artifact_size": 42, "metrics": {"auc": 0.9}, "framework": "sklearn"})


async def queue_job(ml, job_id="job-1", workload="web", name="ml-owner"):
    async with ml.transaction(workload, name=name) as (repo, _):
        return await repo.create_job(job_id=job_id, fingerprint="f" * 64, tenant="default",
                                     request={"model_name": "churn"}, encrypted_sql="gAAAA-cipher")


# ── Jobs ─────────────────────────────────────────────────────────────────

async def test_jobs_are_created_once_for_the_bound_actor(ml):
    results = await asyncio.gather(*(queue_job(ml) for _ in range(4)))
    assert sum(created for _, created in results) == 1
    job = results[0][0]
    assert job["actor"] == "ml-owner" and job["active_role"] == "ANALYST"
    assert job["security_context_version"] == 5 and job["status"] == "queued"
    assert "encrypted_sql" not in job
    with pytest.raises(MetadataConflict):
        await queue_job(ml, name="someone-else")
    # The submitting API process never reads the ciphertext back.
    with pytest.raises(MetadataPermissionDenied):
        async with ml.transaction("web") as (repo, _):
            await repo.get_job_for_worker("job-1")
    async with ml.transaction("ml") as (repo, _):
        assert (await repo.get_job_for_worker("job-1"))["encrypted_sql"] == "gAAAA-cipher"
    # A job cannot be queued in another person's name.
    with pytest.raises(MetadataIntegrityViolation):
        async with ml.transaction("web") as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_ml_jobs (id,actor,session_fingerprint,active_role,"
                "security_context_version,tenant,request_json,encrypted_sql,status) VALUES "
                "('job-x','other','f','ANALYST',5,'default','{}','c','queued')")


async def test_one_worker_wins_a_claim_and_a_stale_worker_is_fenced(ml):
    await queue_job(ml)
    ready = asyncio.Barrier(3)

    async def claim():
        async with ml.transaction("ml") as (repo, _):
            await ready.wait()
            return await repo.claim_job("job-1")

    results = await asyncio.gather(*(claim() for _ in range(3)))
    assert sorted(results, key=str) == [1, None, None]
    async with ml.transaction("ml") as (repo, _):
        assert await repo.heartbeat_job("job-1", generation=1) == (True, False)
        assert await repo.heartbeat_job("job-1", generation=2) == (False, False)
        assert await repo.finish_job("job-1", "failed", generation=2) is False
    async with ml.transaction("web") as (repo, _):
        assert await repo.request_cancel("job-1") is True
    async with ml.transaction("ml") as (repo, _):
        assert await repo.heartbeat_job("job-1", generation=1) == (True, True)
        assert await repo.finish_job("job-1", "cancelled", generation=1,
                                     error_message="Cancelled") is True
        assert await repo.finish_job("job-1", "succeeded", generation=1) is False
        done = await repo.get_job("job-1")
    assert done["status"] == "cancelled" and done["finished_at"] is not None
    actions = await ml.admin("SELECT action FROM nova_meta.audit_log "
                             "WHERE metadata_table='config_ml_jobs' ORDER BY event_time")
    assert [row["action"] for row in actions] == ["CREATE", "CANCEL"]
    # A finished job is frozen, and a lease never moves backwards.
    with pytest.raises(MetadataIntegrityViolation):
        async with ml.transaction("ml") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_ml_jobs SET status='running' "
                               "WHERE id='job-1'")


async def test_an_interrupted_worker_cannot_finish_and_its_job_is_retained(ml):
    await queue_job(ml)
    async with ml.transaction("ml") as (repo, _):
        generation = await repo.claim_job("job-1")
    await asyncio.sleep(0.05)
    async with ml.transaction("ml", name="nova-ml-worker", service=True) as (repo, _):
        assert await repo.interrupt_stale_jobs(older_than_seconds=0) == ["job-1"]
    async with ml.transaction("ml") as (repo, _):
        assert await repo.finish_job("job-1", "succeeded", generation=generation,
                                     result={"model_id": "m"}) is False
    await ml.backdate_finished("job-1")
    await queue_job(ml, "job-2")
    async with ml.transaction("web") as (repo, _):
        assert await repo.withdraw_queued_job("job-2", error_kind="worker_unavailable",
                                              error_message="No worker") is True
    await ml.backdate_finished("job-2")
    async with ml.transaction("ml", name="nova-ml-worker", service=True) as (repo, _):
        assert await repo.prune_finished_jobs() == 1
        assert await repo.get_job("job-1") is not None
        assert await repo.get_job("job-2") is None


async def test_the_waiting_caller_cannot_drive_the_worker_lifecycle(ml):
    await queue_job(ml)
    with pytest.raises(MetadataIntegrityViolation):
        async with ml.transaction("web") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_ml_jobs SET status='running' "
                               "WHERE id='job-1'")
    with pytest.raises(MetadataPermissionDenied):
        async with ml.transaction("web") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_ml_jobs SET result_json='{}' "
                               "WHERE id='job-1'")
    async with ml.transaction("web") as (repo, _):
        await repo.request_cancel("job-1")
    async with ml.transaction("ml") as (repo, _):
        assert await repo.claim_job("job-1") is None


# ── Model registry ───────────────────────────────────────────────────────

async def test_concurrent_reservations_get_distinct_versions_of_one_model(ml):
    ready = asyncio.Barrier(4)

    async def one():
        async with ml.transaction("ml") as (repo, _):
            await ready.wait()
            return await repo.reserve_version(**model())

    results = await asyncio.gather(*(one() for _ in range(4)))
    assert len({r["model_id"] for r in results}) == 1
    assert sorted(r["version"] for r in results) == [1, 2, 3, 4]
    rows = await ml.admin("SELECT metadata_revision,created_by FROM nova_meta.ml_models")
    assert rows == [{"metadata_revision": 4, "created_by": "ml-owner"}]
    # Another person training the same name gets their own model.
    async with ml.transaction("ml", name="other") as (repo, _):
        other = await repo.reserve_version(**model())
    assert other["model_id"] != results[0]["model_id"] and other["version"] == 1


async def test_publishing_happens_once_and_published_versions_are_immutable(ml):
    reservation = await reserve(ml)
    ready = asyncio.Barrier(2)

    async def attempt():
        async with ml.transaction("ml") as (repo, _):
            await ready.wait()
            return await repo.publish_version(
                model_id=reservation["model_id"], version=reservation["version"],
                reservation_token=reservation["reservation_token"], feature_columns=["age"],
                fields={"artifact_uri": "s3://m/1", "artifact_sha256": SHA, "artifact_size": 1})

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    winner = next(r for r in results if isinstance(r, dict))
    assert winner["status"] == "READY" and winner["ready_at"] is not None
    async with ml.transaction("ml", read_only=True) as (repo, _):
        listed = await repo.list_models(owner_name="ml-owner", database_name="sales")
        assert listed[0]["current_version"] == 1 and listed[0]["latest_status"] == "READY"
    async with ml.transaction("ml") as (repo, _):
        assert await repo.abort_version(reservation["model_id"], 1) is False
    for change in ("artifact_uri='s3://evil'", "status='FAILED',failure_reason='x'",
                   "metrics='{}'"):
        with pytest.raises(MetadataIntegrityViolation):
            async with ml.transaction("ml") as (_, conn):
                await conn.execute(f"UPDATE nova_meta.ml_model_versions SET {change}")
    with pytest.raises(MetadataConflict):
        async with ml.transaction("ml") as (repo, _):
            await repo.publish_version(
                model_id=reservation["model_id"], version=1, reservation_token="wrong",
                feature_columns=[], fields={"artifact_uri": "s3://x", "artifact_sha256": SHA,
                                            "artifact_size": 1})


@pytest.mark.parametrize("fields", [
    {"artifact_uri": "s3://m/1", "artifact_sha256": "not-a-digest", "artifact_size": 1},
    {"artifact_uri": " ", "artifact_sha256": SHA, "artifact_size": 1},
    {"artifact_uri": "s3://m/1", "artifact_sha256": SHA, "artifact_size": -1},
])
async def test_a_version_publishes_only_with_its_verified_artifact(ml, fields):
    reservation = await reserve(ml)
    with pytest.raises(MetadataIntegrityViolation):
        async with ml.transaction("ml") as (repo, _):
            await repo.publish_version(
                model_id=reservation["model_id"], version=1,
                reservation_token=reservation["reservation_token"], feature_columns=[],
                fields=fields)


async def test_the_sweep_abandons_reservations_but_cannot_publish(ml):
    reservation = await reserve(ml)
    with pytest.raises(MetadataIntegrityViolation):
        async with ml.transaction("maintenance", name="nova-maintenance", service=True) as (_, c):
            await c.execute("UPDATE nova_meta.ml_model_versions SET status='READY',"
                            "failure_reason=NULL")
    async with ml.transaction("maintenance", name="nova-maintenance", service=True) as (repo, _):
        assert await repo.abort_version(reservation["model_id"], 1) is True
    with pytest.raises(MetadataConflict):
        await publish(ml, reservation)
    actions = await ml.admin("SELECT action,user_name FROM nova_meta.audit_log "
                             "WHERE metadata_table='ml_model_versions' ORDER BY event_time")
    assert [(a["action"], a["user_name"]) for a in actions] == [
        ("RESERVE", "ml-owner"), ("ABANDON", "nova-maintenance")]


async def test_model_changes_need_the_next_revision_and_keep_identity(ml):
    reservation = await reserve(ml)
    await publish(ml, reservation)
    for change in ("hyperparameters='{}'", "created_by='mallory'",
                   "current_version=0,metadata_revision=metadata_revision+1",
                   "current_version=7,metadata_revision=metadata_revision+1",
                   "model_type='poetry',metadata_revision=metadata_revision+1"):
        with pytest.raises((MetadataIntegrityViolation, MetadataPermissionDenied)):
            async with ml.transaction("ml") as (_, conn):
                await conn.execute(f"UPDATE nova_meta.ml_models SET {change}")


async def test_aliases_move_from_the_revision_read_and_only_to_published_versions(ml):
    first = await reserve(ml, workload="web")
    await publish(ml, first, workload="web")
    second = await reserve(ml, workload="web")
    async with ml.transaction("web") as (repo, _):
        alias = await repo.set_alias("prod", first["model_id"], 1, database_name="sales")
        assert alias["metadata_revision"] == 1
    with pytest.raises(MetadataIntegrityViolation):  # version 2 is still training
        async with ml.transaction("web") as (repo, _):
            await repo.set_alias("prod", second["model_id"], 2, database_name="sales",
                                 expected_revision=1)
    await publish(ml, second, workload="web")
    ready = asyncio.Barrier(2)

    async def move():
        async with ml.transaction("web") as (repo, _):
            await ready.wait()
            return await repo.set_alias("prod", second["model_id"], 2, database_name="sales",
                                        expected_revision=1)

    results = await asyncio.gather(move(), move(), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    async with ml.transaction("proxy", read_only=True) as (repo, _):
        resolved = await repo.resolve_alias("prod", owner_name="ml-owner", database_name="sales")
    assert resolved["version"] == 2 and resolved["artifact_sha256"] == SHA
    with pytest.raises(MetadataConflict):  # creating an existing alias is not a move
        async with ml.transaction("web") as (repo, _):
            await repo.set_alias("prod", first["model_id"], 1, database_name="sales")
    # Another person cannot point an alias at this owner's model.
    with pytest.raises(MetadataIntegrityViolation):
        async with ml.transaction("web", name="mallory") as (repo, _):
            await repo.set_alias("steal", first["model_id"], 1, database_name="sales")


async def test_only_the_owner_deletes_a_model_and_its_aliases_and_versions(ml):
    reservation = await reserve(ml, workload="web")
    await publish(ml, reservation, workload="web")
    async with ml.transaction("web") as (repo, _):
        await repo.set_alias("prod", reservation["model_id"], 1, database_name="sales")
    async with ml.transaction("web", name="mallory") as (repo, _):
        assert await repo.delete_model(reservation["model_id"]) is False
    with pytest.raises(MetadataIntegrityViolation):  # versions go before their model
        async with ml.transaction("web") as (_, conn):
            await conn.execute("DELETE FROM nova_meta.ml_models")
    async with ml.transaction("web") as (repo, _):
        assert await repo.delete_model(reservation["model_id"]) is True
        assert await repo.list_models(owner_name="ml-owner") == []
    assert await ml.admin("SELECT 1 FROM nova_meta.ml_model_aliases") == []


# ── Ephemeral descriptors and runs ───────────────────────────────────────

async def test_ephemeral_descriptors_stay_in_their_scope_and_expire(ml):
    now = time.time()
    async with ml.transaction("query") as (repo, _):
        await repo.put_ephemeral(run_id="r1", scope_key="scope-a", fingerprint="f1",
                                 descriptor_json='{"kind":"pending"}', expires_epoch=now + 60)
        await repo.put_ephemeral(run_id="r1", scope_key="scope-a", fingerprint="f1",
                                 descriptor_json='{"kind":"model"}', expires_epoch=now + 60)
        assert (await repo.get_ephemeral("r1", "scope-a"))["descriptor_json"] == '{"kind":"model"}'
        assert await repo.get_ephemeral("r1", "scope-b") is None
        await repo.put_ephemeral(run_id="r2", scope_key="scope-a", fingerprint="f2",
                                 descriptor_json="{}", expires_epoch=now - 1)
    with pytest.raises(MetadataConflict):
        async with ml.transaction("query") as (repo, _):
            await repo.put_ephemeral(run_id="r1", scope_key="scope-b", fingerprint="f",
                                     descriptor_json="{}", expires_epoch=now + 60)
    sweep = {"name": "nova-maintenance", "service": True}
    async with ml.transaction("maintenance", **sweep) as (repo, conn):
        assert [row["run_id"] for row in await repo.expired_ephemeral()] == ["r2"]
        await conn.execute("DELETE FROM nova_meta.ml_ephemeral_runs")  # live rows survive
        assert await repo.delete_expired_ephemeral("r2", "scope-a") is False
    rows = await ml.admin("SELECT run_id FROM nova_meta.ml_ephemeral_runs")
    assert [row["run_id"] for row in rows] == ["r1"]
    async with ml.transaction("proxy") as (repo, _):
        assert await repo.remove_ephemeral("r1", "scope-a") is True


async def test_run_records_are_created_once_and_finish_once(ml):
    run = {"run_id": "sim-1", "task": "simulation", "mode": "best", "status": "RUNNING",
           "telemetry": {"operation_id": "op"}}

    async def record(status):
        async with ml.transaction("agent") as (repo, _):
            return await repo.record_run({**run, "status": status}, ttl_seconds=1800)

    assert (await record("RUNNING"))["owner_name"] == "ml-owner"
    results = await asyncio.gather(record("COMPLETE"), record("FAILED"), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    async with ml.transaction("agent", name="mallory") as (repo, _):
        assert await repo.get_run("sim-1") is None
        with pytest.raises(MetadataConflict):
            await repo.record_run({**run, "status": "COMPLETE"}, ttl_seconds=1)


# ── Access, maintenance and import ───────────────────────────────────────

@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(ml, workload):
    reservation = await reserve(ml)
    await publish(ml, reservation)
    await queue_job(ml)

    async def allowed(action):
        async with ml.transaction(workload) as (repo, _):
            return await action(repo)

    async def refused(action):
        with pytest.raises(MetadataPermissionDenied):
            await allowed(action)

    async def read_models(repo):
        return await repo.list_models(owner_name="ml-owner")

    async def read_job(repo):
        return await repo.get_job("job-1")

    async def reserve_again(repo):
        return await repo.reserve_version(**model())

    async def put(repo):
        return await repo.put_ephemeral(run_id="p-" + workload, scope_key="s", fingerprint="f",
                                        descriptor_json="{}", expires_epoch=time.time() + 60)

    async def alias(repo):
        return await repo.set_alias("a-" + workload, reservation["model_id"], 1,
                                    database_name="sales")

    async def resolve(repo):
        return await repo.resolve_alias("none", owner_name="ml-owner", database_name="sales")

    async def claim(repo):
        return await repo.claim_job("job-1")

    await (allowed if workload in REGISTRY_READERS else refused)(read_models)
    await (allowed if workload in JOB_READERS else refused)(read_job)
    await (allowed if workload in TRAINERS else refused)(reserve_again)
    await (allowed if workload in EPHEMERAL_WRITERS else refused)(put)
    await (allowed if workload in REGISTRY_READERS - {"ml", "maintenance"} else refused)(resolve)
    await (allowed if workload == "web" else refused)(alias)
    await (allowed if workload == "ml" else refused)(claim)


async def test_maintenance_blocks_writes_but_not_reads(ml):
    reservation = await reserve(ml)
    await ml.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        await publish(ml, reservation)
    with pytest.raises(MetadataReadOnlyMaintenance):
        await queue_job(ml)
    async with ml.transaction(read_only=True) as (repo, _):
        assert await repo.list_models(owner_name="ml-owner")


async def test_import_keeps_legacy_rows_only_under_maintenance(ml):
    env = ml.environment
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as conn:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                await conn.execute(
                    "INSERT INTO nova_meta.ml_runs (run_id,task,mode,status,owner_name,"
                    "tenant_name,created_at,updated_at) VALUES ('x','t','m','succeeded','u',"
                    "'default',now(),now())")
        await ml.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE nova_meta_owner")
            for model_id in ("legacy-a", "legacy-b"):  # duplicate scope, kept as found
                await conn.execute(
                    "INSERT INTO nova_meta.ml_models (model_id,model_type,model_name,"
                    "database_name,created_at,created_by,current_version,tenant_name,updated_at,"
                    "metadata_revision) VALUES (%s,'kmeans_legacy','churn','sales',"
                    "'2026-01-01','ml-owner',3,'default','2026-01-02',1)", (model_id,))
            await conn.execute(
                "INSERT INTO nova_meta.ml_model_versions (model_id,version,status,model_binary,"
                "created_at,created_by) VALUES ('legacy-a',3,'READY','pickled','2026-01-01',NULL)")
            await conn.execute(
                "INSERT INTO nova_meta.ml_model_aliases (alias_name,owner_name,database_name,"
                "model_id,version,created_at,updated_at) VALUES ('old','__legacy__','',"
                "'legacy-a',3,'2026-01-01','2026-01-01')")
            await conn.execute(
                "INSERT INTO nova_meta.config_ml_jobs (id,actor,session_fingerprint,"
                "request_json,encrypted_sql,status,created_at) VALUES "
                "('legacy-job','ml-owner','f','{}','c','running','2026-01-01')")
        await ml.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    rows = await ml.admin("SELECT model_id,provenance,created_at FROM nova_meta.ml_models "
                          "ORDER BY model_id")
    assert [(r["model_id"], r["provenance"]) for r in rows] == [
        ("legacy-a", "imported"), ("legacy-b", "imported")]
    assert rows[0]["created_at"].year == 2026 and rows[0]["created_at"].month == 1
    async with ml.transaction("ml") as (repo, _):
        # The newest imported model of the scope continues its version line.
        reservation = await repo.reserve_version(**model())
        assert reservation["version"] == 4
        # An imported running job has no live lease: it can only be interrupted.
        assert await repo.finish_job("legacy-job", "succeeded", generation=1) is False
    # An imported published version is provenance: the sweep cannot abandon it.
    async with ml.transaction("maintenance", name="nova-maintenance", service=True) as (repo, _):
        assert await repo.abort_version("legacy-a", 3) is False
    async with ml.transaction("proxy", read_only=True) as (repo, _):
        legacy = await repo.resolve_alias("old", owner_name="__legacy__", database_name=None)
    assert legacy["model_binary"] == "pickled"
