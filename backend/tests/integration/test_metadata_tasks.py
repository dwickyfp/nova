"""Task graph metadata on the real PG fixture: CAS, fencing, idempotency, scope."""

from __future__ import annotations

import asyncio
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
from app.modules.task_orchestration.metadata_repository import TaskMetadataRepository, node_run_id
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
READERS = {"web", "query", "scheduler", "task"}


class Tasks:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, workload="web", *, name="task-owner", read_only=False):
        user = session(name, active="ANALYST")
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            actor = SecurityContext.from_session(user)
            async with db.transaction(actor, read_only=read_only) as conn:
                yield TaskMetadataRepository(conn, actor), conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None


@pytest_asyncio.fixture
async def tasks(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="d" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Tasks(environment, epoch)


def task(**changes):
    return {"name": "load_orders", "database_name": "sales", "schema_name": None,
            "definition": "INSERT INTO t SELECT 1", "schedule_kind": "interval",
            "schedule_expr": "5 MINUTE", "timezone": "UTC", "overlap_policy": "skip",
            "handler_config": {"retries": 1}, **changes}


async def create_task(tasks, **changes):
    async with tasks.transaction() as (repo, _):
        return await repo.create_task(task(**changes))


async def test_definition_changes_need_the_version_they_read(tasks):
    row = await create_task(tasks)
    assert row["version"] == 1 and row["created_by"] == "task-owner"
    assert row["handler_config"] == {"retries": 1}
    ready = asyncio.Barrier(2)

    async def edit(expr):
        async with tasks.transaction() as (repo, _):
            await ready.wait()
            return await repo.update_task(row["id"], {"schedule_expr": expr}, expected_version=1)

    results = await asyncio.gather(edit("1 MINUTE"), edit("2 MINUTE"), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    winner = next(r for r in results if isinstance(r, dict))
    assert winner["version"] == 2
    with pytest.raises(MetadataIntegrityViolation):
        async with tasks.transaction() as (_, conn):
            await conn.execute("UPDATE nova_meta.config_tasks SET definition='x' WHERE id=%s",
                               (row["id"],))


async def test_failure_counter_is_atomic_and_not_a_definition_change(tasks):
    row = await create_task(tasks)

    async def fail():
        async with tasks.transaction("task") as (repo, _):
            return await repo.increment_consecutive_failures(row["id"])

    results = await asyncio.gather(*(fail() for _ in range(6)))
    assert sorted(results) == [1, 2, 3, 4, 5, 6]
    async with tasks.transaction("task") as (repo, _):
        await repo.reset_consecutive_failures(row["id"])
        current = await repo.get_task(row["id"])
    assert current["consecutive_fail_count"] == 0 and current["version"] == 1
    actions = await tasks.admin(
        "SELECT action FROM nova_meta.audit_log WHERE metadata_table='config_tasks'")
    assert [a["action"] for a in actions] == ["CREATE"]
    with pytest.raises(MetadataPermissionDenied):
        async with tasks.transaction("task") as (repo, _):
            await repo.update_task(row["id"], {"schedule_expr": "x"}, expected_version=1)


async def test_scoped_names_compare_null_scopes_deliberately(tasks):
    await create_task(tasks, schema_name=None)
    await create_task(tasks, schema_name="public")
    with pytest.raises(MetadataConflict):
        await create_task(tasks, schema_name=None)
    async with tasks.transaction(read_only=True) as (repo, _):
        assert len(await repo.find_task("load_orders", "sales", None)) == 1
        assert len(await repo.find_task("load_orders", "sales", "public")) == 1
        assert await repo.find_task("load_orders", None, None) == []


@pytest.mark.parametrize("bad", [
    {"schedule_kind": "hourly"}, {"overlap_policy": "replace"}, {"handler": "java"},
    {"lifecycle_state": "PAUSED"}, {"name": " "},
])
async def test_invalid_definitions_are_refused(tasks, bad):
    with pytest.raises(MetadataIntegrityViolation):
        await create_task(tasks, **bad)


async def test_edges_are_unique_scoped_and_not_self_loops(tasks):
    async with tasks.transaction() as (repo, _):
        edge = await repo.create_edge("g1", {"parent_task": "a", "child_task": "b"})
        await repo.create_edge("g1", {"parent_task": "a", "child_task": "b",
                                      "edge_kind": "finalize"})
        assert len(await repo.list_edges("g1")) == 2
    with pytest.raises(MetadataConflict):
        async with tasks.transaction() as (repo, _):
            await repo.create_edge("g1", {"parent_task": "a", "child_task": "b"})
    for bad in ({"parent_task": "a", "child_task": "a"},
                {"parent_task": "a", "child_task": "c", "edge_kind": "before"}):
        with pytest.raises(MetadataIntegrityViolation):
            async with tasks.transaction() as (repo, _):
                await repo.create_edge("g1", bad)
    async with tasks.transaction() as (repo, _):
        assert await repo.delete_edge(edge["id"]) is True


async def test_graph_runs_are_created_once_and_never_reset(tasks):
    async def create():
        async with tasks.transaction("scheduler") as (repo, _):
            return await repo.create_graph_run_once({"id": "run-1", "graph_id": "g1",
                                                     "trigger_type": "schedule"})

    results = await asyncio.gather(*(create() for _ in range(4)))
    assert sum(created for _, created in results) == 1
    async with tasks.transaction("task") as (repo, _):
        generation = await repo.claim_graph_run("run-1", ["pending"], "running")
    assert generation == 1
    async with tasks.transaction("scheduler") as (repo, _):
        again, created = await repo.create_graph_run_once({"id": "run-1", "graph_id": "g1"})
    assert created is False and again["state"] == "running"
    with pytest.raises(MetadataConflict):
        async with tasks.transaction("scheduler") as (repo, _):
            await repo.create_graph_run_once({"id": "run-1", "graph_id": "other"})


async def test_a_stale_graph_worker_cannot_move_or_heartbeat_the_run(tasks):
    async with tasks.transaction("scheduler") as (repo, _):
        await repo.create_graph_run_once({"id": "run-2", "graph_id": "g1"})
    async with tasks.transaction("task") as (repo, _):
        old = await repo.claim_graph_run("run-2", ["pending"], "running")
        new = await repo.claim_graph_run("run-2", ["running"], "running")
        assert (old, new) == (1, 2)
        assert await repo.heartbeat_graph_run("run-2", generation=old) is False
        assert await repo.transition_graph_run("run-2", ["running"], "failed",
                                               generation=old) is False
        assert await repo.transition_graph_run("run-2", ["running"], "success",
                                               generation=new) is True
        done = await repo.get_graph_run("run-2")
        assert done["state"] == "success" and done["finished_at"] is not None
        assert await repo.claim_graph_run("run-2", ["success"], "running") is None


async def test_node_runs_use_the_source_ids_and_fence_stale_workers(tasks):
    async def create():
        async with tasks.transaction("task") as (repo, _):
            return await repo.create_task_run_once("run-3", "task-a", 1)

    results = await asyncio.gather(*(create() for _ in range(3)))
    assert sum(created for _, created in results) == 1
    assert results[0][0]["id"] == node_run_id("run-3", "task-a", 1)
    run_id = results[0][0]["id"]
    async with tasks.transaction("task") as (repo, _):
        first = await repo.claim_task_run(run_id, ["pending"])
        assert await repo.claim_task_run(run_id, ["pending"]) is None
    await asyncio.sleep(0.05)  # the heartbeat lapses for a zero-second threshold
    async with tasks.transaction("task") as (repo, _):
        assert await repo.abandon_stale_task_runs(older_than_seconds=0) == [run_id]
        second = await repo.claim_task_run(run_id, ["abandoned"])
        assert second == first + 1
        # The first worker comes back late: it can neither heartbeat nor finish.
        assert await repo.heartbeat_task_run(run_id, generation=first) is False
        assert await repo.finish_task_run(run_id, "failed", generation=first,
                                          error_message="late") is False
        assert await repo.finish_task_run(run_id, "success", generation=second,
                                          query_id="q-1") is True
        node = (await repo.list_task_runs("run-3"))[0]
        assert node["state"] == "success" and node["starrocks_query_id"] == "q-1"
        assert node["error_message"] is None and node["finished_at"] is not None
    with pytest.raises(MetadataIntegrityViolation):
        async with tasks.transaction("task") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_task_runs SET state='running',"
                               "lease_generation=lease_generation+1 WHERE id=%s", (run_id,))


async def test_role_bindings_are_revisioned_and_attributed(tasks):
    async with tasks.transaction(name="admin-1") as (repo, _):
        first = await repo.bind_role_execution_user("ANALYST", "svc_analyst")
        second = await repo.bind_role_execution_user("ANALYST", "svc_analyst_2")
        assert (first["metadata_revision"], second["metadata_revision"]) == (1, 2)
        assert second["configured_by"] == "admin-1"
        assert await repo.get_role_execution_user("ANALYST") == "svc_analyst_2"
    with pytest.raises(MetadataPermissionDenied):
        async with tasks.transaction("task") as (repo, _):
            await repo.bind_role_execution_user("ANALYST", "other")


@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(tasks, workload):
    row = await create_task(tasks)
    if workload not in READERS:
        with pytest.raises(MetadataPermissionDenied):
            async with tasks.transaction(workload, read_only=True) as (repo, _):
                await repo.get_task(row["id"])
        return
    async with tasks.transaction(workload, read_only=True) as (repo, _):
        assert (await repo.get_task(row["id"]))["name"] == "load_orders"
    if workload in {"scheduler", "task"}:
        with pytest.raises(MetadataPermissionDenied):
            async with tasks.transaction(workload) as (repo, _):
                await repo.delete_task(row["id"])
    if workload in {"web", "query", "scheduler"}:
        with pytest.raises(MetadataPermissionDenied):
            async with tasks.transaction(workload) as (repo, _):
                await repo.create_task_run_once("run-x", row["id"])


async def test_run_history_cannot_be_deleted(tasks):
    async with tasks.transaction("scheduler") as (repo, _):
        await repo.create_graph_run_once({"id": "run-4", "graph_id": "g1"})
    for workload in ("web", "task"):
        with pytest.raises(MetadataPermissionDenied):
            async with tasks.transaction(workload) as (_, conn):
                await conn.execute("DELETE FROM nova_meta.config_task_graph_runs")


async def test_maintenance_blocks_writes(tasks):
    row = await create_task(tasks)
    await tasks.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with tasks.transaction("task") as (repo, _):
            await repo.increment_consecutive_failures(row["id"])
    async with tasks.transaction(read_only=True) as (repo, _):
        assert await repo.get_task(row["id"])


async def test_import_keeps_legacy_rows_including_ambiguous_names(tasks):
    env = tasks.environment
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as conn:
        await tasks.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE nova_meta_owner")
            for identity in ("legacy-1", "legacy-2"):
                await conn.execute(
                    "INSERT INTO nova_meta.config_tasks (id,name,database_name,schema_name,"
                    "schedule_kind,timezone,version,created_at) "
                    "VALUES (%s,'dup','sales',NULL,'manual','UTC',7,NULL)", (identity,))
            await conn.execute(
                "INSERT INTO nova_meta.config_task_runs (id,graph_run_id,task_id,state) "
                "VALUES ('legacy-node','run-old','legacy-1','running')")
        await tasks.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    async with tasks.transaction() as (repo, _):
        assert [row["id"] for row in await repo.find_task("dup", "sales", None)] == [
            "legacy-1", "legacy-2"]
        updated = await repo.update_task("legacy-1", {"definition": "x"}, expected_version=7)
        assert updated["version"] == 8
    async with tasks.transaction("task") as (repo, _):
        # An imported running node has no live lease: it must be retaken first.
        assert await repo.finish_task_run("legacy-node", "success", generation=1) is False
        assert await repo.claim_task_run("legacy-node", ["running"]) == 1
