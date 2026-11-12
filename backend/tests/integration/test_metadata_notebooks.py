"""Notebook runtime, execution and package metadata on the real PG fixture."""

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
from app.modules.notebooks.metadata_repository import NotebookMetadataRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
ALICE, BOB = "notebook-alice", "notebook-bob"
NOTEBOOK = "nb-shared"
NOW = int(time.time())


class Notebooks:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, name=ALICE, *, workload="notebook", read_only=False):
        user = session(name)
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            actor = SecurityContext.from_session(user)
            async with db.transaction(actor, read_only=read_only) as conn:
                yield NotebookMetadataRepository(conn, actor), conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None


@pytest_asyncio.fixture
async def notebooks(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="d" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Notebooks(environment, epoch)


def runtime(name=ALICE, **changes):
    return {"id": f"rt-{name}", "notebook_id": NOTEBOOK, "user_name": name, "status": "READY",
            "active_role": "ANALYST", "generation": 1, "created_at": NOW - 10,
            "ready_at": NOW - 5, **changes}


def execution(name=ALICE, **changes):
    return {"id": f"ex-{name}-{uuid4().hex[:6]}", "notebook_id": NOTEBOOK, "user_name": name,
            "runtime_id": f"rt-{name}", "run_id": "run-1", "cell_id": "c1", "cell_kind": "sql",
            "sql_engine": "duckdb", "source_revision": "rev-1", "generation": 1,
            "execution_count": 1, "status": "succeeded", "active_role": "ANALYST",
            "summary": {"rows": 1}, "queued_at": NOW - 3, "started_at": NOW - 2,
            "ended_at": NOW - 1, "duration_ms": 900, **changes}


async def test_people_sharing_a_notebook_keep_separate_manifests(notebooks):
    """R21: the source keyed manifests by notebook only, so one person's save erased another's."""
    for name, package in ((ALICE, "pandas"), (BOB, "polars")):
        async with notebooks.transaction(name, workload="web") as (repo, _):
            assert await repo.replace_packages(
                NOTEBOOK, [{"name": package}], expected_revision=0) == 1
    for name, package in ((ALICE, "pandas"), (BOB, "polars")):
        async with notebooks.transaction(name) as (repo, _):
            manifest, revision = await repo.packages(NOTEBOOK)
            assert manifest == [{"name": package}] and revision == 1
    events = await notebooks.admin(
        "SELECT metadata_key,user_name FROM nova_meta.audit_log "
        "WHERE metadata_table='config_notebook_packages' ORDER BY log_id")
    assert [(e["metadata_key"]["user_name"], e["user_name"]) for e in events] == [
        (ALICE, ALICE), (BOB, BOB)]


async def test_a_worker_install_and_a_web_removal_both_survive(notebooks):
    async with notebooks.transaction(workload="web") as (repo, _):
        await repo.replace_packages(NOTEBOOK, [{"name": "a"}], expected_revision=0)
    ready = asyncio.Barrier(2)

    async def change(workload, edit):
        async with notebooks.transaction(workload=workload) as (repo, _):
            await ready.wait()
            return await repo.modify_packages(NOTEBOOK, edit)

    await asyncio.gather(
        change("notebook", lambda items: [*items, {"name": "b"}]),
        change("web", lambda items: [*items, {"name": "c"}]),
    )
    async with notebooks.transaction() as (repo, _):
        manifest, revision = await repo.packages(NOTEBOOK)
    assert sorted(item["name"] for item in manifest) == ["a", "b", "c"] and revision == 3


async def test_stale_or_duplicate_manifest_writes_conflict(notebooks):
    async with notebooks.transaction(workload="web") as (repo, _):
        await repo.replace_packages(NOTEBOOK, [], expected_revision=0)
    for revision in (0, 5):
        with pytest.raises(MetadataConflict):
            async with notebooks.transaction(workload="web") as (repo, _):
                await repo.replace_packages(NOTEBOOK, [{"name": "x"}], expected_revision=revision)


@pytest.mark.parametrize("manifest", [{"name": "x"}, ["pandas"], "pandas"])
async def test_a_manifest_must_be_a_list_of_packages(notebooks, manifest):
    async with notebooks.transaction(workload="web") as (repo, _):
        with pytest.raises(ValueError):
            await repo.replace_packages(NOTEBOOK, manifest, expected_revision=0)
    with pytest.raises(MetadataIntegrityViolation):
        async with notebooks.transaction(workload="web") as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_notebook_packages "
                "(notebook_id,user_name,requirements_json) VALUES (%s,%s,'{\"a\":1}')",
                (NOTEBOOK, ALICE))


async def test_runtime_lifecycle_grows_and_then_freezes(notebooks):
    async with notebooks.transaction() as (repo, _):
        first = await repo.record_runtime(runtime())
        assert first["metadata_revision"] == 1 and first["created_at"].tzinfo is not None
        restarted = await repo.record_runtime(runtime(generation=2))
        assert restarted["generation"] == 2 and restarted["metadata_revision"] == 2
        ended = await repo.record_runtime(runtime(generation=2, status="STOPPED",
                                                  ended_at=str(NOW), end_reason="user"))
        assert ended["ended_at"] is not None
    for change in ({"status": "READY", "generation": 3}, {"status": "LOST", "ended_at": NOW}):
        with pytest.raises(MetadataIntegrityViolation):
            async with notebooks.transaction() as (repo, _):
                await repo.record_runtime(runtime(**change))
    async with notebooks.transaction() as (repo, _):
        await repo.record_runtime(runtime(id="rt-two", generation=3))
    with pytest.raises(MetadataIntegrityViolation):
        async with notebooks.transaction() as (repo, _):
            await repo.record_runtime(runtime(id="rt-two", generation=2))


@pytest.mark.parametrize("bad", [
    {"status": "BUSY"}, {"status": "FAILED"}, {"status": "READY", "ended_at": NOW},
])
async def test_invalid_runtime_states_are_refused(notebooks, bad):
    with pytest.raises(MetadataIntegrityViolation):
        async with notebooks.transaction() as (repo, _):
            await repo.record_runtime(runtime(**bad))


async def test_a_worker_cannot_write_another_persons_rows(notebooks):
    async with notebooks.transaction(BOB) as (repo, conn):
        for call in (lambda: repo.record_runtime(runtime(ALICE)),
                     lambda: repo.record_execution(execution(ALICE))):
            with pytest.raises(MetadataPermissionDenied):
                await call()
    with pytest.raises(MetadataPermissionDenied):
        async with notebooks.transaction(BOB) as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_notebook_runtimes (id,notebook_id,user_name,status,"
                "created_at) VALUES ('rt-forged',%s,%s,'READY',now())", (NOTEBOOK, ALICE))


async def test_executions_are_written_once_and_retries_are_idempotent(notebooks):
    row = execution()
    async with notebooks.transaction() as (repo, _):
        first = await repo.record_execution(row)
        again = await repo.record_execution(row)
        assert first["id"] == again["id"]
    with pytest.raises(MetadataConflict):
        async with notebooks.transaction() as (repo, _):
            await repo.record_execution({**row, "status": "failed"})
    with pytest.raises(MetadataPermissionDenied):
        async with notebooks.transaction() as (_, conn):
            await conn.execute(
                "UPDATE nova_meta.config_notebook_executions SET status='failed' WHERE id=%s",
                (row["id"],))
    rows = await notebooks.admin(
        "SELECT count(*) AS n FROM nova_meta.audit_log "
        "WHERE metadata_table='config_notebook_executions'")
    assert rows == [{"n": 0}]


@pytest.mark.parametrize("bad", [
    {"status": "running"}, {"cell_kind": "r"}, {"ended_at": None},
    {"started_at": NOW, "ended_at": NOW - 5}, {"duration_ms": -1},
])
async def test_invalid_executions_are_refused(notebooks, bad):
    with pytest.raises(MetadataIntegrityViolation):
        async with notebooks.transaction() as (repo, _):
            await repo.record_execution(execution(**bad))


async def test_latest_execution_per_cell_is_personal(notebooks):
    async with notebooks.transaction() as (repo, _):
        await repo.record_execution(execution(started_at=NOW - 31, ended_at=NOW - 30,
                                              status="failed"))
        newest = await repo.record_execution(execution(ended_at=NOW))
        other = await repo.record_execution(execution(cell_id="c2"))
    async with notebooks.transaction(workload="web", read_only=True) as (repo, _):
        latest = {row["cell_id"]: row for row in await repo.latest_executions(NOTEBOOK)}
        assert latest["c1"]["id"] == newest["id"] and latest["c2"]["id"] == other["id"]
        assert latest["c1"]["summary"] == {"rows": 1}
        assert (await repo.get_execution(NOTEBOOK, newest["id"]))["status"] == "succeeded"
    async with notebooks.transaction(BOB, workload="web", read_only=True) as (repo, _):
        assert await repo.latest_executions(NOTEBOOK) == []
        assert await repo.get_execution(NOTEBOOK, newest["id"]) is None


@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(notebooks, workload):
    async with notebooks.transaction() as (repo, _):
        await repo.record_runtime(runtime())
    if workload not in {"web", "notebook"}:
        with pytest.raises(MetadataPermissionDenied):
            async with notebooks.transaction(workload=workload, read_only=True) as (repo, _):
                await repo.packages(NOTEBOOK)
        return
    async with notebooks.transaction(workload=workload, read_only=True) as (repo, _):
        assert await repo.packages(NOTEBOOK) == ([], 0)
    if workload == "web":
        with pytest.raises(MetadataPermissionDenied):
            async with notebooks.transaction(workload="web") as (repo, _):
                await repo.record_runtime(runtime(generation=2))


async def test_maintenance_blocks_writes_but_not_reads(notebooks):
    await notebooks.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with notebooks.transaction() as (repo, _):
            await repo.record_execution(execution())
    async with notebooks.transaction(read_only=True) as (repo, _):
        assert await repo.latest_executions(NOTEBOOK) == []


async def test_import_preserves_legacy_rows_under_maintenance(notebooks):
    env = notebooks.environment
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as conn:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                await conn.execute(
                    "INSERT INTO nova_meta.config_notebook_packages "
                    "(notebook_id,user_name,requirements_json) VALUES ('x','y','[]')")
        await notebooks.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE nova_meta_owner")
            # The surviving source row of an overwritten manifest, and a legacy
            # runtime with a value the runtime guard would refuse today.
            await conn.execute(
                "INSERT INTO nova_meta.config_notebook_packages "
                "(notebook_id,user_name,requirements_json,updated_at) "
                "VALUES (%s,%s,'not json',NULL)", (NOTEBOOK, ALICE))
            await conn.execute(
                "INSERT INTO nova_meta.config_notebook_runtimes "
                "(id,notebook_id,user_name,status,created_at) VALUES ('rt-old',%s,%s,'BUSY',NULL)",
                (NOTEBOOK, ALICE))
        await notebooks.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    async with notebooks.transaction() as (repo, conn):
        assert await repo.packages(NOTEBOOK) == ([], 1)
        cursor = await conn.execute(
            "SELECT status,created_at FROM nova_meta.config_notebook_runtimes WHERE id='rt-old'")
        assert await cursor.fetchone() == {"status": "BUSY", "created_at": None}
