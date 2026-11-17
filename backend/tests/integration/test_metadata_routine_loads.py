"""Routine-load metadata on the real PG17 fixture (0057): Kafka connection role
grants, job ownership RLS, column grants, the lifecycle guard, revision CAS and
the reconciler's SERVICE actor on the maintenance login."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataOperationFailed,
    MetadataPermissionDenied,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.catalog.metadata_repository import CatalogMetadataRepository
from app.modules.routine_load.repository import RECONCILER, RoutineLoadRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg

REFUSED = (MetadataPermissionDenied, MetadataIntegrityViolation, MetadataOperationFailed)


class Store:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, actor, *, workload="web"):
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            async with db.transaction(actor) as conn:
                yield RoutineLoadRepository(conn, actor), CatalogMetadataRepository(conn, actor)
        finally:
            await db.close()

    @staticmethod
    def person(name="alice", role="MARKETING"):
        return SecurityContext.from_session(session(name, active=role))


@pytest_asyncio.fixture
async def store(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="e" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    store = Store(environment, epoch)
    async with store.transaction(store.person("admin", "ACCOUNTADMIN")) as (_, catalog):
        await catalog.create_connection({
            "name": "events_kafka", "connector_type": "kafka", "host": "kafka:9092",
            "port": 9092, "options": {"security_protocol": "PLAINTEXT"},
            "secret_ref": "nova://events_kafka",
        })
    return store


def job(**changes):
    return {
        "id": str(uuid4()), "database_name": "sales", "job_name": "orders_job",
        "table_name": "orders", "connection_name": "events_kafka", "topic": "orders",
        "data_format": "csv", "owner_principal": "alice", "owner_role": "MARKETING",
        "security_context_version": 3, **changes,
    }


async def test_only_security_admins_grant_connection_roles(store):
    for workload in ("web", "query", "proxy"):
        with pytest.raises(REFUSED):
            async with store.transaction(store.person(), workload=workload) as (repo, _):
                await repo.set_connection_roles("events_kafka", ["MARKETING"])
    async with store.transaction(store.person("admin", "SECURITYADMIN")) as (repo, _):
        assert await repo.set_connection_roles("events_kafka", ["MARKETING", "FINANCE"]) == [
            "FINANCE", "MARKETING"]
    async with store.transaction(store.person("admin", "SECURITYADMIN")) as (repo, _):
        await repo.set_connection_roles("events_kafka", ["MARKETING"])
    rows = await (await store.environment.admin.execute(
        "SELECT role_name,granted_by FROM nova_meta.catalog_connection_roles")).fetchall()
    assert [(r["role_name"], r["granted_by"]) for r in rows] == [("MARKETING", "admin")]
    for workload in ("web", "query", "proxy"):
        async with store.transaction(store.person(), workload=workload) as (repo, _):
            assert await repo.connection_roles("events_kafka") == ["MARKETING"]
    async with store.transaction(RECONCILER, workload="maintenance") as (repo, _):
        assert await repo.connection_roles("events_kafka") == ["MARKETING"]
    # Deleting the connection removes its grants in the same transaction.
    async with store.transaction(store.person("admin", "ACCOUNTADMIN")) as (_, catalog):
        assert await catalog.delete_connection("events_kafka")
    rows = await (await store.environment.admin.execute(
        "SELECT count(*) AS n FROM nova_meta.catalog_connection_roles")).fetchone()
    assert rows["n"] == 0


async def test_owner_role_and_admins_change_jobs_everyone_reads_them(store):
    async with store.transaction(store.person()) as (repo, _):
        row = await repo.create_job(job())
    assert row["state"] == "CREATING" and row["revision"] == 1
    # A person creates jobs only for their own role, as themselves.
    for changes in ({"owner_role": "FINANCE"}, {"owner_principal": "bob"}):
        with pytest.raises(REFUSED):
            async with store.transaction(store.person()) as (repo, _):
                await repo.create_job(job(job_name="x_" + uuid4().hex[:6], **changes))
    # Another role sees the job (so Nova refuses it) but cannot change it.
    async with store.transaction(store.person("bob", "FINANCE"), workload="query") as (repo, _):
        assert (await repo.live_job("sales", "orders_job"))["owner_role"] == "MARKETING"
        with pytest.raises(MetadataConflict):
            await repo.update_job(row["id"], {"state": "ACTIVE"}, expected_revision=1)
    async with store.transaction(store.person("root_like", "ACCOUNTADMIN"),
                                 workload="proxy") as (repo, _):
        updated = await repo.update_job(row["id"], {"state": "ACTIVE", "engine_job_id": "42"},
                                        expected_revision=1)
    assert updated["state"] == "ACTIVE" and updated["revision"] == 2
    # One live job per engine name.
    with pytest.raises(MetadataConflict):
        async with store.transaction(store.person()) as (repo, _):
            await repo.create_job(job())


async def test_lifecycle_guard_and_compare_and_swap(store):
    async with store.transaction(store.person()) as (repo, _):
        row = await repo.create_job(job())
    with pytest.raises(REFUSED):  # CREATING cannot jump to PAUSED_BY_NOVA
        async with store.transaction(store.person()) as (repo, _):
            await repo.update_job(row["id"], {"state": "PAUSED_BY_NOVA", "pause_reason": "x"},
                                  expected_revision=1)
    ready = asyncio.Barrier(2)

    async def activate(engine_id):
        async with store.transaction(store.person()) as (repo, _):
            await ready.wait()
            return await repo.update_job(row["id"], {"state": "ACTIVE", "engine_job_id": engine_id},
                                         expected_revision=1)

    results = await asyncio.gather(activate("1"), activate("2"), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in results) == 1
    assert sum(isinstance(item, MetadataConflict) for item in results) == 1
    async with store.transaction(store.person()) as (repo, _):
        current = await repo.get_job(row["id"])
    assert current["revision"] == 2
    with pytest.raises(REFUSED):  # the engine job id never changes once set
        async with store.transaction(store.person()) as (repo, _):
            await repo.update_job(row["id"], {"engine_job_id": "999"}, expected_revision=2)
    # Identity columns are not even granted for update.
    with pytest.raises(REFUSED):
        async with store.transaction(store.person()) as (repo, _):
            await repo.connection.execute(
                "UPDATE nova_meta.routine_load_jobs SET owner_role='FINANCE' WHERE id=%s",
                (row["id"],))
    # Only the owner (principal and role) renews the security-context version.
    with pytest.raises(REFUSED):
        async with store.transaction(store.person("admin", "ACCOUNTADMIN")) as (repo, _):
            await repo.update_job(row["id"], {"security_context_version": 9},
                                  expected_revision=2)
    async with store.transaction(store.person()) as (repo, _):
        renewed = await repo.update_job(row["id"], {"security_context_version": 4},
                                        expected_revision=2)
    assert renewed["security_context_version"] == 4
    async with store.transaction(store.person()) as (repo, _):
        stopped = await repo.update_job(row["id"], {"state": "STOPPED"}, expected_revision=3)
    assert stopped["state"] == "STOPPED"
    with pytest.raises(REFUSED):  # STOPPED is final
        async with store.transaction(store.person()) as (repo, _):
            await repo.update_job(row["id"], {"state": "ACTIVE"}, expected_revision=4)
    # A stopped job's name can be used again.
    async with store.transaction(store.person()) as (repo, _):
        again = await repo.create_job(job())
    assert again["state"] == "CREATING"


async def test_reconciler_pauses_and_records_through_the_maintenance_login(store):
    async with store.transaction(store.person()) as (repo, _):
        row = await repo.create_job(job())
        row = await repo.update_job(row["id"], {"state": "ACTIVE", "engine_job_id": "7"},
                                    expected_revision=1)
    async with store.transaction(RECONCILER, workload="maintenance") as (repo, _):
        assert [item["id"] for item in await repo.live_jobs()] == [row["id"]]
        paused = await repo.update_job(
            row["id"], {"state": "PAUSED_BY_NOVA", "pause_reason": "Role lost INSERT",
                        "last_checked_at": True},
            expected_revision=row["revision"],
        )
    assert paused["paused_at"] is not None and paused["last_checked_at"] is not None
    # The reconciler cannot create jobs or renew an owner's security context.
    with pytest.raises(REFUSED):
        async with store.transaction(RECONCILER, workload="maintenance") as (repo, _):
            await repo.create_job(job(job_name="other"))
    with pytest.raises(REFUSED):
        async with store.transaction(RECONCILER, workload="maintenance") as (repo, _):
            await repo.update_job(row["id"], {"security_context_version": 8},
                                  expected_revision=paused["revision"])
    # Another SERVICE principal on the maintenance login sees nothing.
    other = SecurityContext("catalog-refresher", "SECURITYADMIN", principal_type="SERVICE")
    async with store.transaction(other, workload="maintenance") as (repo, _):
        assert await repo.live_jobs() == []
    # The reconciler on the web login changes nothing (RLS hides the row).
    with pytest.raises((*REFUSED, MetadataConflict)):
        async with store.transaction(RECONCILER, workload="web") as (repo, _):
            await repo.update_job(row["id"], {"state": "ACTIVE"},
                                  expected_revision=paused["revision"])
    # Resuming clears the pause reason.
    async with store.transaction(store.person()) as (repo, _):
        resumed = await repo.update_job(row["id"], {"state": "ACTIVE"},
                                        expected_revision=paused["revision"])
    assert resumed["pause_reason"] is None and resumed["paused_at"] is None


async def test_writes_need_an_active_epoch(store):
    await store.environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(Exception):  # noqa: B017,PT011 - maintenance refusal category
        async with store.transaction(store.person()) as (repo, _):
            await repo.create_job(job())
    await store.environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET maintenance=false")
    rows = await (await store.environment.admin.execute(
        "SELECT count(*) AS n FROM nova_meta.routine_load_jobs")).fetchone()
    assert rows["n"] == 0
