"""Integration tests for the Phase 9 task-orchestration metadata layer.

These run against a real StarRocks instance because the acceptance criteria are
about real DDL (idempotency, Primary-Key tables, ``information_schema.columns``)
and real CRUD — none of which a mock can prove.

By default the suite brings up the project's ``docker-compose.test.yml`` stack
via the shared ``docker_services`` fixture, then connects on port 29030. To point
at an already-running engine instead (e.g. the dev engine on 9030), set the port::

    NOVA_ORCH_SR_PORT=9030 uv run pytest tests/integration/test_task_orchestration_metadata.py

Other overrides: ``NOVA_ORCH_SR_HOST``, ``NOVA_ORCH_SR_USER``,
``NOVA_ORCH_SR_PASSWORD``. When StarRocks is unreachable the module skips rather
than fails: a missing local prerequisite is not a regression.
"""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import asyncmy
import pytest
import pytest_asyncio

from app.common.nova_system import (
    TASK_ORCHESTRATION_DDL,
    migrate_task_orchestration_columns,
)
from app.core.config import settings
from app.core.database import db
from app.modules.task_orchestration.repository import (
    UnknownUpdateColumnError,
)
from app.modules.task_orchestration.repository import (
    task_orchestration_repository as repo,
)

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = int(_EXPLICIT_PORT or "29030")
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")
_USE_SHARED_STACK = _EXPLICIT_PORT is None

#: Real engine required (real DDL, real CRUD), so this belongs to the L3 job's
#: `-m engine` selection. The skip-in-fixture behaviour stays: an unreachable
#: engine is a missing local prerequisite, and the CI job fails on
#: `skipped == collected` so an all-skip run cannot pass for green.
pytestmark = pytest.mark.engine

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
        conn = await asyncmy.connect(
            host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD
        )
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
    if _USE_SHARED_STACK and "docker_services" in request.fixturenames:
        request.getfixturevalue("docker_services")
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
    """Bootstrap NOVA_SYSTEM (the test stack ships without init-nova.sql) then DDL.

    Also runs the additive column migrations, because the engine stack's
    ``init-nova.sql`` may have already created ``CONFIG_TASK*`` without the
    later ``heartbeat_at`` column — ``CREATE TABLE IF NOT EXISTS`` will not
    evolve it, and the repository selects that column (NOVA-36).
    """
    await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    for ddl in TASK_ORCHESTRATION_DDL:
        await db.execute_system(ddl)
    await migrate_task_orchestration_columns()


async def _columns(table: str) -> set[str]:
    result = await db.execute_system(
        "SELECT COLUMN_NAME FROM information_schema.columns "
        "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' AND TABLE_NAME = %s",
        [table],
    )
    return {row[0] for row in result["rows"]}


def _repo():
    """The shared repository singleton, imported lazily to keep this helper
    next to the tests that use it."""
    from app.modules.task_orchestration.repository import (
        task_orchestration_repository,
    )

    return task_orchestration_repository


class TestDdlIdempotency:
    async def test_running_ddl_twice_succeeds(self, orchestration_db):
        await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
        for _ in range(2):
            for ddl in TASK_ORCHESTRATION_DDL:
                await db.execute_system(ddl)

    async def test_all_four_tables_exist(self, orchestration_db):
        await _ensure_ddl()
        result = await db.execute_system(
            "SELECT TABLE_NAME FROM information_schema.tables "
            "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' AND TABLE_NAME LIKE 'CONFIG_TASK%'"
        )
        names = {row[0] for row in result["rows"]}
        assert {
            "CONFIG_TASKS",
            "CONFIG_TASK_EDGES",
            "CONFIG_TASK_GRAPH_RUNS",
            "CONFIG_TASK_RUNS",
        } <= names

    async def test_tables_are_primary_key_tables(self, orchestration_db):
        """Every CONFIG_TASK* table is a Primary Key table, never Duplicate Key."""
        await _ensure_ddl()
        for table in (
            "CONFIG_TASKS",
            "CONFIG_TASK_EDGES",
            "CONFIG_TASK_GRAPH_RUNS",
            "CONFIG_TASK_RUNS",
        ):
            result = await db.execute_system(f"SHOW CREATE TABLE NOVA_SYSTEM.{table}")
            ddl = str(result["rows"][0][1]).upper()
            assert "PRIMARY KEY" in ddl, f"{table} is not a Primary Key table"
            assert "DUPLICATE KEY" not in ddl, f"{table} must not be a Duplicate Key table"


class TestHeartbeatColumnMigration:
    """NOVA-36 regression: an old CONFIG_TASK* table must be healed.

    ``docker/init-nova.sql`` (and any deployment created before the worker
    landed) defines ``CONFIG_TASK_RUNS`` / ``CONFIG_TASK_GRAPH_RUNS`` without
    ``heartbeat_at``. ``CREATE TABLE IF NOT EXISTS`` is a no-op on the existing
    table, so the column is absent and every repository read that selects it
    fails with "Column 'heartbeat_at' cannot be resolved" — which is exactly
    how L3 went red on PR #46. The migration must add it to a pre-existing
    old-schema table.
    """

    async def test_migration_adds_heartbeat_to_an_old_schema_table(self, orchestration_db):
        table = "CONFIG_TASK_GRAPH_RUNS"
        # Rebuild the table exactly as an older release left it: no heartbeat.
        await db.execute_system(f"DROP TABLE IF EXISTS NOVA_SYSTEM.{table}")
        await db.execute_system(
            f"""
            CREATE TABLE NOVA_SYSTEM.{table} (
                id           VARCHAR(64) NOT NULL,
                graph_id     VARCHAR(64) NOT NULL,
                trigger_type VARCHAR(32) NOT NULL,
                state        VARCHAR(32) NOT NULL,
                wal_marks    TEXT,
                started_at   DATETIME,
                finished_at  DATETIME
            ) PRIMARY KEY(id)
            DISTRIBUTED BY HASH(id) BUCKETS 1
            PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
            """
        )
        before = await _columns(table)
        assert "heartbeat_at" not in before, "fixture did not create an old-schema table"

        await migrate_task_orchestration_columns()

        after = await _columns(table)
        assert "heartbeat_at" in after, "migration did not add the heartbeat column"

        # The repository read that failed in CI must now succeed.
        repo = _repo()
        created = await repo.create_graph_run({"graph_id": f"g_{uuid4().hex[:8]}"})
        fetched = await repo.get_graph_run(created["id"])
        assert fetched is not None
        assert fetched["heartbeat_at"] is None
        await repo.delete_graph_run(created["id"])

        # Leave the schema as the rest of the suite expects.
        await _ensure_ddl()

    async def test_migration_is_idempotent_when_the_column_exists(self, orchestration_db):
        await _ensure_ddl()
        for _ in range(2):
            await migrate_task_orchestration_columns()
        assert "heartbeat_at" in await _columns("CONFIG_TASK_RUNS")


class TestNoCredentialColumns:
    async def test_warehouse_columns_contain_no_credential_names(self, orchestration_db):
        await _ensure_ddl()
        result = await db.execute_system(
            "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.columns "
            "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' AND TABLE_NAME LIKE 'CONFIG_TASK%'"
        )
        assert result["rows"], "no CONFIG_TASK% columns found — DDL did not run"

        offenders = [
            (table, column)
            for table, column in result["rows"]
            if any(bad in column.lower() for bad in CREDENTIAL_SUBSTRINGS)
        ]
        assert offenders == [], f"credential-bearing columns found: {offenders}"


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

    async def test_edge_create_read_update_delete(self, orchestration_db):
        await _ensure_ddl()
        graph_id = f"g_{uuid4().hex[:8]}"
        created = await repo.create_edge(graph_id, {"parent_task": "A", "child_task": "B"})
        assert created["graph_id"] == graph_id

        fetched = await repo.get_edge(created["id"])
        assert fetched is not None and fetched["parent_task"] == "A"

        updated = await repo.update_edge(created["id"], {"child_task": "C"})
        assert updated is not None and updated["child_task"] == "C"

        assert len(await repo.list_edges(graph_id)) == 1
        assert await repo.delete_edge(created["id"]) is True
        assert await repo.get_edge(created["id"]) is None

    async def test_graph_run_create_read_update_delete(self, orchestration_db):
        await _ensure_ddl()
        graph_id = f"g_{uuid4().hex[:8]}"
        created = await repo.create_graph_run(
            {"graph_id": graph_id, "trigger_type": "schedule", "state": "running"}
        )
        assert created["state"] == "running"

        fetched = await repo.get_graph_run(created["id"])
        assert fetched is not None

        updated = await repo.update_graph_run(created["id"], {"state": "success"})
        assert updated is not None and updated["state"] == "success"

        assert len(await repo.list_graph_runs(graph_id)) == 1
        assert await repo.delete_graph_run(created["id"]) is True
        assert await repo.get_graph_run(created["id"]) is None

    async def test_task_run_create_read_update_delete(self, orchestration_db):
        await _ensure_ddl()
        graph_run_id = f"gr_{uuid4().hex[:8]}"
        created = await repo.create_task_run(
            {
                "graph_run_id": graph_run_id,
                "task_id": f"t_{uuid4().hex[:8]}",
                "state": "running",
                "delegated": True,
            }
        )
        assert created["attempt"] == 1
        assert created["delegated"] in (True, 1)

        fetched = await repo.get_task_run(created["id"])
        assert fetched is not None

        updated = await repo.update_task_run(
            created["id"], {"state": "failed", "error_message": "boom"}
        )
        assert updated is not None and updated["error_message"] == "boom"

        assert len(await repo.list_task_runs(graph_run_id)) == 1
        assert await repo.delete_task_run(created["id"]) is True
        assert await repo.get_task_run(created["id"]) is None


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
            await repo.create_task(
                {"name": name, "timezone": "UTC"}, created_by="alice"
            )
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
        with pytest.raises(ValueError, match="no fields to update"):
            await repo.update_task(created["id"], {})
        await repo.delete_task(created["id"])

    async def test_update_edge_rejects_unknown_column(self, orchestration_db):
        await _ensure_ddl()
        edge = await repo.create_edge(
            f"g_{uuid4().hex[:8]}", {"parent_task": "A", "child_task": "B"}
        )
        with pytest.raises(UnknownUpdateColumnError):
            await repo.update_edge(edge["id"], {"graph_id": "other"})
        await repo.delete_edge(edge["id"])

    async def test_update_graph_run_rejects_unknown_column(self, orchestration_db):
        await _ensure_ddl()
        run = await repo.create_graph_run({"graph_id": f"g_{uuid4().hex[:8]}"})
        with pytest.raises(UnknownUpdateColumnError):
            await repo.update_graph_run(run["id"], {"graph_id": "other"})
        await repo.delete_graph_run(run["id"])

    async def test_update_task_run_rejects_unknown_column(self, orchestration_db):
        await _ensure_ddl()
        run = await repo.create_task_run(
            {"graph_run_id": f"gr_{uuid4().hex[:8]}", "task_id": "t1"}
        )
        with pytest.raises(UnknownUpdateColumnError):
            await repo.update_task_run(run["id"], {"graph_run_id": "other"})
        await repo.delete_task_run(run["id"])


class TestWalMarksRoundTrip:
    async def test_wal_marks_survive_round_trip_as_json_metadata(self, orchestration_db):
        await _ensure_ddl()
        marks = {"p1": 2, "p2": 7, "p2026_09": 42}
        created = await repo.create_graph_run(
            {"graph_id": f"g_{uuid4().hex[:8]}", "wal_marks": marks}
        )
        assert created["wal_marks"] == marks

        fetched = await repo.get_graph_run(created["id"])
        assert fetched is not None
        assert fetched["wal_marks"] == marks

        raw = await db.execute_system(
            "SELECT wal_marks FROM NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS WHERE id = %s",
            [created["id"]],
        )
        assert json.loads(raw["rows"][0][0]) == marks

        await repo.delete_graph_run(created["id"])

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
