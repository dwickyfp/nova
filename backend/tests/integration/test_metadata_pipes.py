"""Pipe metadata on the real PG17 fixture (0056): role-scoped RLS, column grants,
revision CAS, the worker's fenced lease and the file-ledger lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.pipes.repository import LeaseLost, PipeRepository
from app.modules.task_orchestration.metadata_repository import TaskMetadataRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg

WORKER = SecurityContext(principal="nova-pipes", active_role="NOVA_WORKER",
                         principal_type="SERVICE")


class Pipes:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, actor=None, *, workload="web"):
        actor = actor or SecurityContext.from_session(session("loader", active="LOADER"))
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            async with db.transaction(actor) as conn:
                yield PipeRepository(conn, actor), conn
        finally:
            await db.close()

    def person(self, name="loader", role="LOADER"):
        return SecurityContext.from_session(session(name, active=role))

    async def inspect(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall()


@pytest_asyncio.fixture
async def pipes(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="e" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Pipes(environment, epoch)


def definition(**changes):
    return {
        "id": uuid4(), "database_name": "sales", "name": "orders_pipe",
        "target_table": "sales.orders", "definition": "INSERT INTO orders SELECT * FROM @s/x/*.csv",
        "source": {"form": "stage", "stage_id": "s1", "pattern": "x/*.csv"},
        "auto_ingest": True, "poll_interval": 10, "batch_size": 1024, "batch_files": 2,
        "state": "RUNNING", "owner_role": "LOADER", "created_by": "loader",
        "task_id": str(uuid4()), **changes,
    }


async def create(pipes, **changes):
    async with pipes.transaction() as (repo, _):
        return await repo.create_pipe(definition(**changes))


async def test_a_role_sees_and_changes_only_its_own_pipes(pipes):
    row = await create(pipes)
    assert row["revision"] == 1 and row["state"] == "RUNNING" and row["claim_generation"] == 0
    async with pipes.transaction(pipes.person("other", "MARKETING")) as (repo, _):
        assert await repo.get_pipe("sales", "orders_pipe") is None
        assert await repo.list_pipes() == []
        assert not await repo.delete_pipe(row["id"])
    for workload in ("web", "query"):
        async with pipes.transaction(workload=workload) as (repo, _):
            assert (await repo.get_pipe("sales", "orders_pipe"))["id"] == row["id"]
    async with pipes.transaction(pipes.person("admin", "ACCOUNTADMIN")) as (repo, _):
        assert [item["name"] for item in await repo.list_pipes("sales")] == ["orders_pipe"]
    # A person creates pipes only for their active role, as themselves.
    for changes in ({"owner_role": "MARKETING"}, {"created_by": "someone"}):
        with pytest.raises(MetadataPermissionDenied):
            await create(pipes, name="x_" + str(uuid4())[:8], **changes)
    with pytest.raises(MetadataConflict):
        await create(pipes)  # one name per database


async def test_property_changes_need_the_revision_they_read(pipes):
    row = await create(pipes)
    ready = asyncio.Barrier(2)

    async def change(seconds):
        async with pipes.transaction() as (repo, _):
            await ready.wait()
            return await repo.update_pipe(row["id"], {"poll_interval": seconds},
                                          expected_revision=1)

    results = await asyncio.gather(change(20), change(30), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in results) == 1
    assert sum(isinstance(item, MetadataConflict) for item in results) == 1
    async with pipes.transaction() as (repo, conn):
        current = await repo.get_pipe_by_id(row["id"])
        assert current["revision"] == 2 and current["poll_interval"] in {20, 30}
        with pytest.raises(MetadataConflict):
            await repo.update_pipe(row["id"], {"state": "SUSPENDED"}, expected_revision=1)
    # The definition and identity never change in place; a person cannot touch the lease.
    for statement in (
        "UPDATE nova_meta.config_pipes SET definition='INSERT INTO t SELECT 1' WHERE id=%s",
        "UPDATE nova_meta.config_pipes SET claim_generation=99 WHERE id=%s",
        "UPDATE nova_meta.config_pipes SET owner_role='LOADER' WHERE id=%s",
    ):
        with pytest.raises(MetadataPermissionDenied):
            async with pipes.transaction() as (_, conn):
                await conn.execute(statement, (row["id"],))
    with pytest.raises(MetadataIntegrityViolation):
        async with pipes.transaction() as (_, conn):
            await conn.execute(
                "UPDATE nova_meta.config_pipes SET state='SUSPENDED' WHERE id=%s", (row["id"],),
            )


async def test_two_workers_on_one_pipe_have_one_winner(pipes):
    row = await create(pipes)
    ready = asyncio.Barrier(2)

    async def claim():
        async with pipes.transaction(WORKER, workload="task") as (repo, _):
            await ready.wait()
            token = uuid4()
            claimed = await repo.claim(row["id"], token, seconds=2)
            return (token, claimed["claim_generation"]) if claimed else None

    results = await asyncio.gather(claim(), claim())
    winners = [item for item in results if item]
    assert len(winners) == 1 and winners[0][1] == 1
    token, generation = winners[0]
    async with pipes.transaction(WORKER, workload="task") as (repo, _):
        await repo.stage_files(row["id"], token, generation, [
            ("x/a.csv", "e1", 10, datetime(2026, 11, 16, tzinfo=UTC)),
            ("x/a.csv", "e1", 10, datetime(2026, 11, 16, tzinfo=UTC)),
            ("x/b.csv", "e2", 20, None),
        ])
        with pytest.raises(LeaseLost):
            await repo.fence(row["id"], uuid4(), generation)
    # The lease expires; a second worker takes it and the first records nothing more.
    async with pipes.transaction(WORKER, workload="task") as (repo, _):
        assert await repo.claim(row["id"], uuid4(), seconds=60) is None
    await asyncio.sleep(2.5)
    async with pipes.transaction(WORKER, workload="task") as (repo, _):
        newer = uuid4()
        taken = await repo.claim(row["id"], newer, seconds=60)
        assert taken["claim_generation"] == 2
    with pytest.raises(LeaseLost):
        async with pipes.transaction(WORKER, workload="task") as (repo, _):
            await repo.begin_batch(row["id"], token, generation, "nova_pipe_x",
                                   [("x/a.csv", "e1")], 10)
    # A batch row is refused below the pipe's current lease even without the fence.
    with pytest.raises(MetadataConflict):
        async with pipes.transaction(WORKER, workload="task") as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.pipe_batches "
                "(pipe_id,label,file_count,total_bytes,claim_generation) "
                "VALUES (%s,'nova_pipe_stale',1,10,1)", (row["id"],),
            )
    assert await pipes.inspect(
        "SELECT count(*) AS n FROM nova_meta.pipe_files WHERE pipe_id=%s", (row["id"],),
    ) == [{"n": 2}]


async def test_files_follow_the_load_lifecycle(pipes):
    row = await create(pipes)
    async with pipes.transaction(WORKER, workload="task") as (repo, _):
        token = uuid4()
        generation = (await repo.claim(row["id"], token, seconds=60))["claim_generation"]
        await repo.stage_files(row["id"], token, generation, [
            ("x/a.csv", "e1", 10, None), ("x/b.csv", "e2", 20, None), ("x/c.csv", "e3", 5, None),
        ])
        await repo.begin_batch(row["id"], token, generation, "nova_pipe_ab",
                               [("x/a.csv", "e1"), ("x/b.csv", "e2")], 30)
        await repo.begin_batch(row["id"], token, generation, "nova_pipe_c",
                               [("x/c.csv", "e3")], 5)
        assert [f["load_state"] for f in await repo.files_in(row["id"], "LOADING")] == [
            "LOADING"] * 3
        await repo.finish_batch(row["id"], token, generation, "nova_pipe_ab",
                                {("x/a.csv", "e1"): 3, ("x/b.csv", "e2"): 4})
        await repo.fail_batch(row["id"], token, generation, "nova_pipe_c",
                              error="Bad value in row 2", file_state="ERROR")
        # A finished batch is never loaded again under its label.
        with pytest.raises(MetadataConflict):
            await repo.begin_batch(row["id"], token, generation, "nova_pipe_ab", [], 0)
    files = {f["file_name"]: f for f in await pipes.inspect(
        "SELECT * FROM nova_meta.pipe_files WHERE pipe_id=%s", (row["id"],))}
    assert files["x/a.csv"]["load_state"] == "FINISHED" and files["x/a.csv"]["rows_loaded"] == 3
    assert files["x/a.csv"]["finish_load_time"] >= files["x/a.csv"]["start_load_time"]
    assert files["x/c.csv"]["load_state"] == "ERROR"
    assert files["x/c.csv"]["error_msg"] == "Bad value in row 2"
    # A finished file stays finished, whoever asks.
    with pytest.raises(MetadataIntegrityViolation):
        async with pipes.transaction(WORKER, workload="task") as (_, conn):
            await conn.execute("UPDATE nova_meta.pipe_files SET load_state='UNLOADED' "
                               "WHERE file_name='x/a.csv'")
    # The owner retries the failed file; it is counted and queued again.
    async with pipes.transaction() as (repo, _):
        assert await repo.retry_files(row["id"], "x/c.csv") == 1
        assert await repo.retry_files(row["id"]) == 0
        status = await repo.load_status([row["id"]])
    assert status[str(row["id"])]["FINISHED"] == 2
    assert status[str(row["id"])]["UNLOADED"] == 1
    assert status[str(row["id"])]["loaded_rows"] == 7
    retried = await pipes.inspect(
        "SELECT load_state,retry_count,error_msg FROM nova_meta.pipe_files "
        "WHERE file_name='x/c.csv'")
    assert retried == [{"load_state": "UNLOADED", "retry_count": 1, "error_msg": None}]
    # A person cannot mark a file loaded, nor stage one; another role cannot retry.
    with pytest.raises(MetadataIntegrityViolation):
        async with pipes.transaction() as (_, conn):
            await conn.execute("UPDATE nova_meta.pipe_files SET load_state='FINISHED' "
                               "WHERE file_name='x/c.csv'")
    with pytest.raises(MetadataPermissionDenied):
        async with pipes.transaction() as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.pipe_files (pipe_id,file_name,file_version,file_size) "
                "VALUES (%s,'x/d.csv','e4',1)", (row["id"],))
    async with pipes.transaction(pipes.person("other", "MARKETING")) as (repo, _):
        assert await repo.list_files([row["id"]]) == []
        assert await repo.retry_files(row["id"]) == 0


async def test_only_the_worker_actor_reaches_the_ledger(pipes):
    row = await create(pipes)
    person = pipes.person()
    # The task login acting as a person (not the pipes service) sees nothing.
    async with pipes.transaction(person, workload="task") as (repo, _):
        assert await repo.get_pipe_by_id(row["id"]) is None
    for workload in ("scheduler", "notebook", "agent"):
        with pytest.raises(MetadataPermissionDenied):
            async with pipes.transaction(person, workload=workload) as (_, conn):
                await conn.execute("SELECT 1 FROM nova_meta.config_pipes")
    with pytest.raises(MetadataPermissionDenied):
        async with pipes.transaction(WORKER, workload="task") as (_, conn):
            await conn.execute("DELETE FROM nova_meta.config_pipes WHERE id=%s", (row["id"],))
    with pytest.raises(MetadataPermissionDenied):
        async with pipes.transaction(WORKER, workload="task") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_pipes SET poll_interval=5 "
                               "WHERE id=%s", (row["id"],))


async def test_drop_removes_the_ledger_and_keeps_other_pipes(pipes):
    first = await create(pipes)
    second = await create(pipes, name="returns_pipe")
    async with pipes.transaction(WORKER, workload="task") as (repo, _):
        for row in (first, second):
            token = uuid4()
            generation = (await repo.claim(row["id"], token, seconds=60))["claim_generation"]
            await repo.stage_files(row["id"], token, generation, [("x/a.csv", "e1", 1, None)])
            await repo.begin_batch(row["id"], token, generation, "nova_pipe_a",
                                   [("x/a.csv", "e1")], 1)
    async with pipes.transaction() as (repo, _):
        assert await repo.delete_pipe(first["id"])
    remaining = await pipes.inspect(
        "SELECT pipe_id FROM nova_meta.pipe_files UNION ALL "
        "SELECT pipe_id FROM nova_meta.pipe_batches")
    assert {item["pipe_id"] for item in remaining} == {second["id"]}


async def test_the_worker_only_suspends_a_pipe_schedule(pipes):
    owner = pipes.person()
    db = MetadataDatabase(pipes.environment.role_config("nova_meta_web"))
    tasks = {}
    try:
        await db.open(schema_version=len(load_migrations()), metadata_epoch=pipes.epoch)
        async with db.transaction(owner) as conn:
            repo = TaskMetadataRepository(conn, owner)
            for handler in ("pipe.load", "sql"):
                tasks[handler] = await repo.create_task({
                    "name": "t_" + handler.replace(".", "_"), "database_name": "sales",
                    "schema_name": "__pipes", "definition": "", "schedule_kind": "interval",
                    "schedule_expr": "10 seconds", "timezone": "UTC", "owner_role": "LOADER",
                    "handler": handler, "handler_config": {"pipe_id": "p"},
                })
    finally:
        await db.close()
    async with pipes.transaction(WORKER, workload="task") as (_, conn):
        await conn.execute("UPDATE nova_meta.config_tasks SET lifecycle_state='SUSPENDED' "
                           "WHERE id=%s", (tasks["pipe.load"]["id"],))
    for task, state in ((tasks["sql"], "SUSPENDED"), (tasks["pipe.load"], "STARTED")):
        with pytest.raises(MetadataPermissionDenied):
            async with pipes.transaction(WORKER, workload="task") as (_, conn):
                await conn.execute("UPDATE nova_meta.config_tasks SET lifecycle_state=%s "
                                   "WHERE id=%s", (state, task["id"]))
    assert await pipes.inspect(
        "SELECT handler,lifecycle_state FROM nova_meta.config_tasks ORDER BY handler") == [
        {"handler": "pipe.load", "lifecycle_state": "SUSPENDED"},
        {"handler": "sql", "lifecycle_state": "STARTED"},
    ]
