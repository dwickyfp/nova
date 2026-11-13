"""Task metadata and lowering against PG17, with engine timezone observations."""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import asyncmy
import pytest
import pytest_asyncio

from app.common.metadata_actors import metadata_scope
from app.common.nova_system import init_task_orchestration
from app.core.config import settings
from app.core.database import db
from app.core.metadata_db import MetadataPermissionDenied
from app.core.metadata_migrations import migrate
from app.modules.task_orchestration.repository import (
    UnknownUpdateColumnError,
    graph_ids_from,
)
from app.modules.task_orchestration.repository import (
    task_orchestration_repository as repo,
)
from tests.conftest import root_password
from tests.integration._stack import (
    engine_port,
    require_shared_stack,
)
from tests.integration._task_runs import create_graph_run, create_task_run

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = engine_port(_EXPLICIT_PORT, "NOVA_TEST_FE_MYSQL_PORT", 29030)
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD") or root_password()
_USE_SHARED_STACK = _EXPLICIT_PORT is None

#: Real engine required (real DDL, real CRUD), so this belongs to the L3 job's
#: `-m engine` selection. The skip-in-fixture behaviour stays: an unreachable
#: engine is a missing local prerequisite, and the CI job fails on
#: `skipped == collected` so an all-skip run cannot pass for green.
pytestmark = pytest.mark.engine


@pytest_asyncio.fixture
async def task_metadata_login(engine_workload):
    async with engine_workload("task"):
        yield


CREDENTIAL_SUBSTRINGS = ("password", "secret", "token", "credential")


async def _reachable() -> bool:
    try:
        conn = await asyncio.wait_for(
            asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD),
            timeout=5,
        )
    except Exception:
        return False
    conn.close()
    return True


async def _has_live_backend() -> bool:
    """An FE that accepts connections but has no BE cannot execute DDL."""
    try:
        conn = await asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD)
    except Exception:
        return False
    try:
        async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
            await cur.execute("SHOW BACKENDS")
            rows = await cur.fetchall()
        return any(str(row.get("Alive", "")).lower() == "true" for row in rows)
    except Exception:
        return False
    finally:
        conn.close()


@pytest_asyncio.fixture
async def orchestration_db(request):
    require_shared_stack(request, enabled=_USE_SHARED_STACK)
    if not await _reachable():
        pytest.skip("StarRocks not reachable")
    if not await _has_live_backend():
        pytest.skip("StarRocks FE is up but has no live backend; cannot run DDL")

    settings.STARROCKS_HOST = SR_HOST
    settings.STARROCKS_FE_MYSQL_PORT = SR_PORT
    settings.STARROCKS_ROOT_USER = SR_USER
    settings.STARROCKS_ROOT_PASSWORD = SR_PASSWORD
    await db.init_system_pool()
    yield db
    await db.close_system_pool()


async def _ensure_ddl() -> None:
    await init_task_orchestration()


class TestDdlIdempotency:
    async def test_running_migrations_twice_succeeds(self, orchestration_db, engine_metadata):
        for _ in range(2):
            await migrate(engine_metadata.role_config("nova_meta_migrator"), app_version="e" * 40)

    async def test_all_four_tables_exist(self, orchestration_db, metadata_inspect):
        names = await metadata_inspect(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='nova_meta' AND table_name LIKE 'config_task%'"
        )
        assert {
            "config_tasks",
            "config_task_edges",
            "config_task_graph_runs",
            "config_task_runs",
        } <= {row[0] for row in names}

    async def test_tables_have_primary_keys(self, orchestration_db, metadata_inspect):
        keys = await metadata_inspect(
            "SELECT table_name FROM information_schema.table_constraints "
            "WHERE table_schema='nova_meta' AND constraint_type='PRIMARY KEY'"
        )
        assert {
            "config_tasks",
            "config_task_edges",
            "config_task_graph_runs",
            "config_task_runs",
        } <= {row[0] for row in keys}


class TestHeartbeatColumnMigration:
    async def test_imported_graph_without_heartbeat_can_be_read(
        self, orchestration_db, engine_metadata
    ):
        identifier = str(uuid4())
        async with engine_metadata.importer() as connection:
            await connection.execute(
                "INSERT INTO nova_meta.config_task_graph_runs (id,graph_id,trigger_type,state) "
                "VALUES (%s,%s,'manual','pending')",
                (identifier, "legacy-graph"),
            )
        row = await repo.get_graph_run(identifier)
        assert row is not None and row["heartbeat_at"] is None
        assert row["provenance"] == "imported"

    async def test_migration_retains_heartbeat_columns(
        self, orchestration_db, engine_metadata, metadata_inspect
    ):
        for _ in range(2):
            await migrate(engine_metadata.role_config("nova_meta_migrator"), app_version="e" * 40)
        rows = await metadata_inspect(
            "SELECT table_name FROM information_schema.columns WHERE table_schema='nova_meta' "
            "AND column_name='heartbeat_at' "
            "AND table_name IN ('config_task_runs','config_task_graph_runs')"
        )
        assert {row[0] for row in rows} == {"config_task_runs", "config_task_graph_runs"}


class TestNoCredentialColumns:
    async def test_control_columns_contain_no_credential_names(
        self, orchestration_db, metadata_inspect
    ):
        rows = await metadata_inspect(
            "SELECT table_name,column_name FROM information_schema.columns "
            "WHERE table_schema='nova_meta' AND table_name LIKE 'config_task%'"
        )
        assert rows
        assert [
            (table, column)
            for table, column in rows
            if any(bad in column.lower() for bad in CREDENTIAL_SUBSTRINGS)
        ] == []


class TestRepositoryCrud:
    async def test_task_create_read_update_delete(self, orchestration_db):
        await _ensure_ddl()
        created = await repo.create_task(
            {
                "name": f"etl_{uuid4().hex[:8]}",
                "timezone": "Asia/Jakarta",
                "definition": "INSERT INTO t SELECT 1",
                "schedule_kind": "cron",
                "schedule_expr": "0 2 * * *",
                "owner_role": "analyst",
            },
            created_by="alice",
        )
        assert created["id"]
        assert created["version"] == 1
        assert created["created_by"] == "alice"

        fetched = await repo.get_task(created["id"])
        assert fetched is not None
        assert fetched["schedule_expr"] == "0 2 * * *"

        updated = await repo.update_task(created["id"], {"name": "renamed_task"})
        assert updated is not None
        assert updated["name"] == "renamed_task"
        assert updated["version"] == 2

        assert await repo.delete_task(created["id"]) is True
        assert await repo.get_task(created["id"]) is None

    async def test_edge_create_read_replace_delete(self, orchestration_db):
        await _ensure_ddl()
        graph_id = f"g_{uuid4().hex[:8]}"
        created = await repo.create_edge(graph_id, {"parent_task": "A", "child_task": "B"})
        assert created["graph_id"] == graph_id

        assert await repo.list_edges(graph_id) == [created]
        async with repo.unit() as command:
            assert await command.delete_edge(created["id"])
            replacement = await command.create_edge(
                graph_id, {"parent_task": "A", "child_task": "C"}
            )
        assert await repo.list_edges(graph_id) == [replacement]
        assert replacement["id"] != created["id"]
        assert await repo.delete_edge(replacement["id"]) is True
        assert await repo.list_edges(graph_id) == []

    @pytest.mark.usefixtures("task_metadata_login")
    async def test_graph_run_claim_finish_and_retain_history(self, orchestration_db):
        await _ensure_ddl()
        graph_id = f"g_{uuid4().hex[:8]}"
        created = await create_graph_run(
            repo, {"graph_id": graph_id, "trigger_type": "schedule", "state": "running"}
        )
        assert created["state"] == "running"

        fetched = await repo.get_graph_run(created["id"])
        assert fetched is not None

        assert await repo.transition_graph_run(
            created["id"], ["running"], "success", generation=created["lease_generation"]
        )
        updated = await repo.get_graph_run(created["id"])
        assert updated is not None and updated["state"] == "success"

        assert len(await repo.list_graph_runs_page(graph_id, limit=100, offset=0)) == 1
        assert (await repo.get_graph_run(created["id"]))["state"] == "success"

    @pytest.mark.usefixtures("task_metadata_login")
    async def test_task_run_claim_finish_and_retain_history(self, orchestration_db):
        await _ensure_ddl()
        graph_run_id = f"gr_{uuid4().hex[:8]}"
        created = await create_task_run(
            repo,
            {
                "graph_run_id": graph_run_id,
                "task_id": f"t_{uuid4().hex[:8]}",
                "state": "running",
                "delegated": True,
            },
        )
        assert created["attempt"] == 1
        assert created["delegated"] in (True, 1)

        fetched = await repo.get_task_run(created["id"])
        assert fetched is not None

        assert await repo.finish_task_run(
            created["id"], "failed", error_message="boom", generation=created["lease_generation"]
        )
        updated = await repo.get_task_run(created["id"])
        assert updated is not None and updated["error_message"] == "boom"

        assert len(await repo.list_task_runs(graph_run_id)) == 1
        assert (await repo.get_task_run(created["id"]))["state"] == "failed"


@pytest.mark.usefixtures("task_metadata_login")
class TestIdempotentCreatePrimitives:
    """The Primary-Key table makes a plain INSERT a destructive upsert.

    These tests pin the two primitives the scheduler and worker rely on:
    ``create_graph_run_once`` must never clobber an existing run's state, and
    ``create_task_run_once`` must resolve two racing creators to one row (the
    deterministic id), so a node cannot execute twice.
    """

    async def test_create_graph_run_once_is_non_destructive(self, orchestration_db):
        await _ensure_ddl()
        run_id = f"gr_{uuid4().hex}"
        graph_id = f"g_{uuid4().hex}"
        try:
            created, is_new = await repo.create_graph_run_once(
                {
                    "id": run_id,
                    "graph_id": graph_id,
                    "trigger_type": "schedule",
                    "state": "pending",
                }
            )
            assert is_new is True and created["state"] == "pending"

            # The worker claims it; a second create for the same due instant
            # must not reset it back to pending.
            generation = await repo.claim_graph_run(run_id, ["pending"], "running")
            assert await repo.transition_graph_run(
                run_id, ["running"], "success", generation=generation
            )

            regotten, is_new2 = await repo.create_graph_run_once(
                {
                    "id": run_id,
                    "graph_id": graph_id,
                    "trigger_type": "schedule",
                    "state": "pending",
                }
            )
            assert is_new2 is False
            assert regotten["state"] == "success", (
                "a re-tick must not clobber a settled run (PK upsert would)"
            )
        finally:
            await repo.get_graph_run(run_id)

    async def test_create_task_run_once_resolves_racers_to_one_row(self, orchestration_db):
        await _ensure_ddl()
        graph_run_id = f"gr_{uuid4().hex}"
        task_id = f"t_{uuid4().hex}"
        try:
            first = await repo.create_task_run_once(graph_run_id, task_id)
            second = await repo.create_task_run_once(graph_run_id, task_id)
            assert first["id"] == second["id"], "racers must share one primary key"
            rows = await repo.list_task_runs(graph_run_id)
            assert len(rows) == 1, "a node must have exactly one row for an attempt"
        finally:
            for row in await repo.list_task_runs(graph_run_id):
                await repo.get_task_run(row["id"])

    async def test_create_task_run_once_does_not_clobber_a_running_row(self, orchestration_db):
        await _ensure_ddl()
        graph_run_id = f"gr_{uuid4().hex}"
        task_id = f"t_{uuid4().hex}"
        try:
            row = await repo.create_task_run_once(graph_run_id, task_id)
            assert await repo.claim_task_run(row["id"], ["pending"])
            await repo.create_task_run_once(graph_run_id, task_id)
            again = await repo.get_task_run(row["id"])
            assert again is not None and again["state"] == "running"
        finally:
            for r in await repo.list_task_runs(graph_run_id):
                await repo.get_task_run(r["id"])


class TestListTasksByGraph:
    async def test_returns_only_member_tasks_by_name(self, orchestration_db):
        """Regression: list_tasks(graph_id) previously referenced a nonexistent
        ``task_id`` column on CONFIG_TASK_EDGES and crashed on every call.

        Edges store task *names*, and a task is a member if it is either endpoint.
        """
        await _ensure_ddl()
        graph_id = f"g_{uuid4().hex[:8]}"
        suffix = uuid4().hex[:8]
        member_a = f"a_{suffix}"
        member_b = f"b_{suffix}"
        member_c = f"c_{suffix}"
        outsider = f"z_{suffix}"

        for name in (member_a, member_b, member_c, outsider):
            await repo.create_task({"name": name, "timezone": "UTC"}, created_by="alice")
        edges = [
            await repo.create_edge(graph_id, {"parent_task": member_a, "child_task": member_b}),
            await repo.create_edge(graph_id, {"parent_task": member_b, "child_task": member_c}),
        ]

        members = await repo.list_tasks(graph_id)
        assert {t["name"] for t in members} == {member_a, member_b, member_c}
        assert all(t["id"] for t in members)
        assert outsider not in {t["name"] for t in members}

        for edge in edges:
            await repo.delete_edge(edge["id"])
        for name in (member_a, member_b, member_c, outsider):
            tasks = [t for t in await repo.list_tasks() if t["name"] == name]
            for task in tasks:
                await repo.delete_task(task["id"])

    async def test_unknown_graph_returns_empty_list_not_error(self, orchestration_db):
        await _ensure_ddl()
        assert await repo.list_tasks(f"missing_{uuid4().hex[:8]}") == []

    async def test_list_tasks_without_graph_returns_all(self, orchestration_db):
        await _ensure_ddl()
        suffix = uuid4().hex[:8]
        created = await repo.create_task(
            {"name": f"all_{suffix}", "timezone": "UTC"}, created_by="alice"
        )
        names = {t["name"] for t in await repo.list_tasks()}
        assert f"all_{suffix}" in names
        await repo.delete_task(created["id"])


class TestUpdateColumnWhitelist:
    async def test_update_task_rejects_unknown_column(self, orchestration_db):
        await _ensure_ddl()
        created = await repo.create_task(
            {"name": f"guard_{uuid4().hex[:8]}", "timezone": "UTC"}, created_by="alice"
        )
        with pytest.raises(UnknownUpdateColumnError, match="cannot update task"):
            await repo.update_task(created["id"], {"id = 1, name": "boom"})
        with pytest.raises(UnknownUpdateColumnError):
            await repo.update_task(created["id"], {"version": 99})
        await repo.delete_task(created["id"])

    async def test_update_task_rejects_empty_payload(self, orchestration_db):
        await _ensure_ddl()
        created = await repo.create_task(
            {"name": f"empty_{uuid4().hex[:8]}", "timezone": "UTC"}, created_by="alice"
        )
        with pytest.raises(UnknownUpdateColumnError):
            await repo.update_task(created["id"], {})
        await repo.delete_task(created["id"])

    async def test_edge_graph_identity_cannot_be_updated(self, orchestration_db):
        await _ensure_ddl()
        edge = await repo.create_edge(
            f"g_{uuid4().hex[:8]}", {"parent_task": "A", "child_task": "B"}
        )
        with pytest.raises(MetadataPermissionDenied):
            async with metadata_scope(repo.actor) as connection:
                await connection.execute(
                    "UPDATE nova_meta.config_task_edges SET graph_id=%s WHERE id=%s",
                    ("other", edge["id"]),
                )
        assert await repo.list_edges(edge["graph_id"]) == [edge]
        await repo.delete_edge(edge["id"])

    @pytest.mark.usefixtures("task_metadata_login")
    async def test_update_graph_run_rejects_unknown_column(self, orchestration_db):
        await _ensure_ddl()
        run = await create_graph_run(repo, {"graph_id": f"g_{uuid4().hex[:8]}"})
        with pytest.raises(TypeError):
            await repo.transition_graph_run(
                run["id"], ["pending"], "running", generation=1, graph_id="other"
            )
        await repo.get_graph_run(run["id"])

    @pytest.mark.usefixtures("task_metadata_login")
    async def test_update_task_run_rejects_unknown_column(self, orchestration_db):
        await _ensure_ddl()
        run = await create_task_run(
            repo, {"graph_run_id": f"gr_{uuid4().hex[:8]}", "task_id": "t1"}
        )
        with pytest.raises(TypeError):
            await repo.finish_task_run(run["id"], "success", generation=1, graph_run_id="other")
        await repo.get_task_run(run["id"])


class TestWalMarksRoundTrip:
    @pytest.mark.usefixtures("task_metadata_login")
    async def test_wal_marks_survive_round_trip_as_json_metadata(
        self, orchestration_db, metadata_inspect
    ):
        await _ensure_ddl()
        marks = {"p1": 2, "p2": 7, "p2026_09": 42}
        created = await create_graph_run(
            repo, {"graph_id": f"g_{uuid4().hex[:8]}", "wal_marks": marks}
        )
        assert created["wal_marks"] == marks

        fetched = await repo.get_graph_run(created["id"])
        assert fetched is not None
        assert fetched["wal_marks"] == marks

        raw = await metadata_inspect(
            "SELECT wal_marks FROM nova_meta.config_task_graph_runs WHERE id = %s",
            [created["id"]],
        )
        assert json.loads(raw[0][0]) == marks

        await repo.get_graph_run(created["id"])

    async def test_task_definition_never_persists_a_credential_value(self, orchestration_db):
        """A task body references a credential by *name*, never by value."""
        await _ensure_ddl()
        created = await repo.create_task(
            {
                "name": f"cred_{uuid4().hex[:8]}",
                "timezone": "UTC",
                "definition": "INSERT INTO t SELECT * FROM @stage1.data.csv",
                "owner_role": "storage_named_connection",
            },
            created_by="alice",
        )
        serialized = json.dumps(created, default=str).lower()
        for bad in CREDENTIAL_SUBSTRINGS:
            assert f"{bad}=" not in serialized
        await repo.delete_task(created["id"])


class TestCreateTaskLoweringAgainstEngine:
    """NOVA-54 / PR 3a: `CREATE TASK` lowers to real CONFIG_TASK* rows.

    This is the acceptance path end to end against a real engine: the surface is
    parsed, validated and persisted, and the engine is asked for nothing. The
    metadata assertions are the same ones the unit suite makes, but here they run
    against StarRocks columns rather than a fake, so a schema/persistence drift
    is caught.
    """

    async def test_create_task_writes_task_and_edges(self, orchestration_db):
        await _ensure_ddl()
        from app.modules.task_orchestration.ddl import parse_create_task
        from app.modules.task_orchestration.lowering import persist_lowered_task

        suffix = uuid4().hex[:8]
        name = f"etl_{suffix}"
        task = parse_create_task(
            f"CREATE TASK NOVA_DEMO.default.{name} AFTER parent_a, parent_b "
            f"FINALIZE notify_task WHEN x > 1 AND y < 2 "
            f"OVERLAP_POLICY = 'QUEUE' SCHEDULE = '0 2 * * * UTC' "
            f"AS INSERT INTO t SELECT 1",
            timezone="Asia/Jakarta",
        )
        persisted = await persist_lowered_task(task, created_by="alice")
        graph_id = str(persisted.edges[0]["graph_id"])
        try:
            row = persisted.task
            assert row["name"] == name
            assert row["database_name"] == "NOVA_DEMO"
            assert row["schema_name"] == "default"
            assert row["schedule_kind"] == "cron"
            assert row["schedule_expr"] == "0 2 * * *"
            assert row["timezone"] == "UTC"
            assert row["when_expr"] == "x > 1 AND y < 2"
            assert row["overlap_policy"] == "queue"
            assert row["created_by"] == "alice"

            edges = await repo.list_edges(graph_id)
            by_kind = {(e["parent_task"], e["edge_kind"]) for e in edges}
            assert ("parent_a", "after") in by_kind
            assert ("parent_b", "after") in by_kind
            assert ("notify_task", "finalize") in by_kind
        finally:
            for edge in await repo.list_edges(graph_id):
                await repo.delete_edge(edge["id"])
            await repo.delete_task(row["id"])

    async def test_same_name_in_two_schemas_are_distinct_tasks(self, orchestration_db):
        """A task is scoped to database.schema; name alone is not an identity.

        Two schemas may each hold a task named the same. They must be two rows,
        each owned by its own schema, so an explorer node for one schema never
        shows the other's task.
        """
        await _ensure_ddl()
        from app.modules.task_orchestration.ddl import parse_create_task
        from app.modules.task_orchestration.lowering import persist_lowered_task

        suffix = uuid4().hex[:8]
        name = f"etl_{suffix}"
        silver = await persist_lowered_task(
            parse_create_task(
                f"CREATE TASK NOVA_DEMO.silver.{name} AS INSERT INTO t SELECT 1",
                timezone="UTC",
            ),
            created_by="alice",
        )
        gold = await persist_lowered_task(
            parse_create_task(
                f"CREATE TASK NOVA_DEMO.gold.{name} AS INSERT INTO t SELECT 1",
                timezone="UTC",
            ),
            created_by="alice",
        )
        try:
            assert silver.task["id"] != gold.task["id"]
            assert (silver.task["database_name"], silver.task["schema_name"]) == (
                "NOVA_DEMO",
                "silver",
            )
            assert (gold.task["database_name"], gold.task["schema_name"]) == (
                "NOVA_DEMO",
                "gold",
            )
            silver_rows = await repo.list_tasks(silver.task["database_name"] + ".silver." + name)
            assert [r["id"] for r in silver_rows if r["name"] == name] == [silver.task["id"]]
            gold_rows = await repo.list_tasks(gold.task["database_name"] + ".gold." + name)
            assert [r["id"] for r in gold_rows if r["name"] == name] == [gold.task["id"]]
        finally:
            await repo.delete_task(silver.task["id"])
            await repo.delete_task(gold.task["id"])

    async def test_finalize_edge_does_not_become_a_dependency_in_the_graph(self, orchestration_db):
        """The finalizer is stored but excluded from the dependency adjacency.

        Folding it in would make the finalizer run as an ordinary child at the
        wrong time, which is the failure mode PR 3b's wiring must not inherit.
        """
        await _ensure_ddl()
        from app.modules.task_orchestration.dag import graph_from_task_rows

        graph = graph_from_task_rows(
            ["a", "b"],
            [
                {"parent_task": "a", "child_task": "b", "edge_kind": "after"},
                {"parent_task": "b", "child_task": "a", "edge_kind": "finalize"},
            ],
        )
        assert graph.adjacency.get("a") == ["b"]
        assert graph.adjacency.get("b") == []

    async def test_cycle_against_stored_rows_is_rejected_and_rolled_back(self, orchestration_db):
        """A statement that closes a cycle must leave no partial task behind.

        `b AFTER a` is stored under graph `b`; `a AFTER b` is then attempted
        under graph `a`. The component-spanning check must catch the loop and
        roll the second statement back, leaving only the first task.
        """
        await _ensure_ddl()
        from app.modules.task_orchestration.ddl import parse_create_task
        from app.modules.task_orchestration.lowering import (
            TaskLoweringError,
            persist_lowered_task,
        )

        suffix = uuid4().hex[:8]
        first = f"cyc_a_{suffix}"
        second = f"cyc_b_{suffix}"

        # `a` first, so `b AFTER a` is a valid statement.
        a = parse_create_task(
            f"CREATE TASK {first} AS INSERT INTO t SELECT 1",
            database="NOVA_DEMO",
            timezone="UTC",
        )
        persisted_a = await persist_lowered_task(a, created_by="alice")
        b = parse_create_task(
            f"CREATE TASK {second} AFTER {first} AS INSERT INTO t SELECT 1",
            database="NOVA_DEMO",
            timezone="UTC",
        )
        persisted_b = await persist_lowered_task(b, created_by="alice")

        try:
            # Now close the loop under a different graph key.
            loop = parse_create_task(
                f"CREATE TASK {first} AFTER {second} AS INSERT INTO t SELECT 1",
                database="NOVA_DEMO",
                timezone="UTC",
            )
            with pytest.raises(TaskLoweringError):
                await persist_lowered_task(loop, created_by="alice")

            assert await repo.list_edges(loop.name) == [], (
                "a rejected cycle must not leave edges behind"
            )
        finally:
            for row in (persisted_b, persisted_a):
                for edge in await repo.list_edges(row.task["name"]):
                    await repo.delete_edge(edge["id"])
                await repo.delete_task(row.task["id"])


@pytest.mark.usefixtures("task_metadata_login")
class TestOverlapPolicyColumnRoundTrip:
    """NOVA-54 / stage 3b: the graph-run overlap policy survives a real engine.

    The overlap decision is enforced in the scheduler and the worker from this
    column, so a schema drift here silently disables the policy. This pins the
    real round-trip: write the three policies, read them back, and confirm a
    legacy row (written without the column) defaults to the strict `skip`.
    """

    async def test_policy_round_trips(self, orchestration_db):
        await _ensure_ddl()
        graph_id = f"ov_{uuid4().hex[:8]}"
        created_ids = []
        try:
            for policy in ("skip", "queue", "allow"):
                run = await create_graph_run(
                    repo,
                    {
                        "graph_id": graph_id,
                        "trigger_type": "schedule",
                        "state": "pending",
                        "overlap_policy": policy,
                    },
                )
                created_ids.append(run["id"])
                fetched = await repo.get_graph_run(run["id"])
                assert fetched is not None
                assert fetched["overlap_policy"] == policy
        finally:
            for run_id in created_ids:
                await repo.get_graph_run(run_id)

    async def test_default_policy_is_skip(self, orchestration_db):
        await _ensure_ddl()
        graph_id = f"ovd_{uuid4().hex[:8]}"
        run = await create_graph_run(
            repo, {"graph_id": graph_id, "trigger_type": "schedule", "state": "pending"}
        )
        try:
            fetched = await repo.get_graph_run(run["id"])
            assert fetched is not None
            assert fetched["overlap_policy"] == "skip"
        finally:
            await repo.get_graph_run(run["id"])

    async def test_active_graph_runs_excludes_terminal_states(self, orchestration_db):
        await _ensure_ddl()
        graph_id = f"ova_{uuid4().hex[:8]}"
        pending = await create_graph_run(
            repo, {"graph_id": graph_id, "trigger_type": "schedule", "state": "pending"}
        )
        finished = await create_graph_run(
            repo, {"graph_id": graph_id, "trigger_type": "schedule", "state": "pending"}
        )
        generation = await repo.claim_graph_run(finished["id"], ["pending"], "running")
        assert await repo.transition_graph_run(
            finished["id"], ["running"], "success", generation=generation
        )
        try:
            active = await repo.list_active_graph_runs(graph_id)
            active_ids = {r["id"] for r in active}
            assert pending["id"] in active_ids
            assert finished["id"] not in active_ids
        finally:
            await repo.get_graph_run(pending["id"])
            await repo.get_graph_run(finished["id"])


class TestFinalizerEdgeShape:
    """`edge_kind='finalize'` is excluded from dependency adjacency on real rows."""

    async def test_finalize_edge_is_stored_but_not_a_dependency(self, orchestration_db):
        await _ensure_ddl()
        from app.modules.task_orchestration.dag import (
            finalizer_targets,
            graph_from_task_rows,
        )

        graph_id = f"fin_{uuid4().hex[:8]}"
        edges = [
            await repo.create_edge(
                graph_id,
                {"parent_task": "a", "child_task": "b", "edge_kind": "after"},
            ),
            await repo.create_edge(
                graph_id,
                {"parent_task": "a", "child_task": "f", "edge_kind": "finalize"},
            ),
        ]
        try:
            rows = await repo.list_edges(graph_id)
            assert finalizer_targets(rows) == {"f": "a"}
            graph = graph_from_task_rows(["a", "b", "f"], rows)
            assert "f" not in graph.nodes
            assert graph.finalizer_nodes == {"f"}
            assert graph.adjacency.get("a") == ["b"]
        finally:
            for edge in edges:
                await repo.delete_edge(edge["id"])


class TestReadApiRepositoryAgainstEngine:
    """NOVA-54 / PR 4a: the read-model queries work on real StarRocks.

    The API's four endpoints are thin over these repository methods, so proving
    them here proves the endpoint data. A fake cannot catch a SQL/column drift,
    which is exactly what a read model is prone to.
    """

    async def test_create_task_then_read_graph_edges_and_runs(self, orchestration_db):
        await _ensure_ddl()
        from app.modules.task_orchestration.ddl import parse_create_task
        from app.modules.task_orchestration.lowering import persist_lowered_task

        suffix = uuid4().hex[:8]
        parent = f"api_a_{suffix}"
        child = f"api_b_{suffix}"
        finalizer = f"api_f_{suffix}"

        parent_task = parse_create_task(
            f"CREATE TASK NOVA_DEMO.default.{parent} AS INSERT INTO t SELECT 1",
            timezone="UTC",
        )
        persisted_a = await persist_lowered_task(parent_task, created_by="alice")
        child_task = parse_create_task(
            f"CREATE TASK NOVA_DEMO.default.{child} AFTER {parent} AS INSERT INTO t SELECT 1",
            timezone="UTC",
        )
        persisted_b = await persist_lowered_task(child_task, created_by="alice")
        finalizer_task = parse_create_task(
            f"CREATE TASK NOVA_DEMO.default.{finalizer} FINALIZE {parent} "
            "AS INSERT INTO t SELECT 1",
            timezone="UTC",
        )
        persisted_f = await persist_lowered_task(finalizer_task, created_by="alice")

        child_graph = str(persisted_b.edges[0]["graph_id"])
        finalizer_graph = str(persisted_f.edges[0]["graph_id"])
        try:
            # list_graph_ids must surface the graph (qualified).
            graph_ids = graph_ids_from(await repo.list_all_edges(), await repo.list_tasks())
            assert child_graph in graph_ids or f"NOVA_DEMO.default.{parent}" in graph_ids

            # The graph's tasks, resolvable by name (the API's detail path).
            names = [parent, child, finalizer]
            resolved = await repo.list_tasks(child_graph)
            assert {str(t["name"]) for t in resolved} == set(names)

            # Edges carry their kind so the UI can distinguish the finalizer.
            edges = await repo.list_edges(child_graph)
            kinds = {(e["parent_task"], e["edge_kind"]) for e in edges}
            assert (parent, "after") in kinds

            fin_edges = await repo.list_edges(finalizer_graph)
            assert (parent, "finalize") in {(e["parent_task"], e["edge_kind"]) for e in fin_edges}

            # The read model for runs: no run yet, so latest is None and the
            # per-graph task-run query returns an empty list, not an error.
            assert await repo.get_latest_graph_run(child_graph) is None
            assert await repo.list_task_runs_for_graph(child_graph) == []
        finally:
            for row, graph_id in (
                (persisted_f, finalizer_graph),
                (persisted_b, child_graph),
                (persisted_a, f"NOVA_DEMO.default.{parent}"),
            ):
                for edge in await repo.list_edges(graph_id):
                    await repo.delete_edge(edge["id"])
                await repo.delete_task(row.task["id"])

    @pytest.mark.usefixtures("task_metadata_login")
    async def test_graph_run_read_model_round_trips(self, orchestration_db):
        await _ensure_ddl()
        graph_id = f"api_r_{uuid4().hex[:8]}"
        run = await create_graph_run(
            repo,
            {
                "graph_id": graph_id,
                "trigger_type": "schedule",
                "state": "running",
                "overlap_policy": "queue",
            },
        )
        task_id = f"id_{uuid4().hex[:8]}"
        node = await create_task_run(
            repo,
            {
                "graph_run_id": run["id"],
                "task_id": task_id,
                "state": "running",
                "delegated": True,
            },
        )
        try:
            latest = await repo.get_latest_graph_run(graph_id)
            assert latest is not None
            assert latest["id"] == run["id"]
            assert latest["overlap_policy"] == "queue"

            runs = await repo.list_graph_runs_page(graph_id, limit=100, offset=0)
            assert [r["id"] for r in runs] == [run["id"]]

            node_runs = await repo.list_task_runs(run["id"])
            assert [n["id"] for n in node_runs] == [node["id"]]

            for_graph = await repo.list_task_runs_for_graph(graph_id)
            assert {n["id"] for n in for_graph} == {node["id"]}
        finally:
            await repo.get_task_run(node["id"])
            await repo.get_graph_run(run["id"])
