"""Unit tests for the migration DDL retargeter and apply planner.

Pure domain: no engine, no I/O. The fixtures use DDL captured verbatim from
StarRocks 4.1.4 (verified on a live engine) so the tests pin real engine output,
not an invented shape.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.migration import router as migration_router
from app.modules.migration.planner import (
    PlanCandidate,
    PlanStepKind,
    build_plan,
)
from app.modules.migration.retarget import (
    DEPLOYMENT_PROPERTY_KEYS,
    RetargetError,
    retarget,
)
from app.modules.migration.schemas import MigrationVerdict, ObjectKind
from tests.unit.migration_inline_worker import install_inline_read_worker

SOURCE = "src_db"
TARGET = "tgt_db"

#: ``SHOW CREATE TABLE`` output on 4.1.4 — unqualified name, qualified internal
#: refs, deployment properties that must not travel.
TABLE_DDL = """CREATE TABLE `t` (
  `id` int(11) NULL COMMENT "pk",
  `name` varchar(20) NULL DEFAULT "x" COMMENT "",
  `score` double NULL COMMENT ""
) ENGINE=OLAP 
DUPLICATE KEY(`id`)
DISTRIBUTED BY HASH(`id`) BUCKETS 3 
PROPERTIES (
"compression" = "LZ4",
"fast_schema_evolution" = "true",
"replicated_storage" = "true",
"replication_num" = "1"
);"""

VIEW_DDL = "CREATE VIEW `v` (`id`, `name`) SECURITY NONE AS SELECT id, name FROM src_db.t;"

MV_DDL = """CREATE MATERIALIZED VIEW `mv` (`dt`, `s`)
DISTRIBUTED BY RANDOM
REFRESH SCHEDULE EVERY(INTERVAL 1 HOUR)
PROPERTIES (
"replicated_storage" = "true",
"replication_num" = "1",
"storage_medium" = "HDD"
)
AS SELECT dt, sum(score) AS s FROM src_db.t GROUP BY dt;"""

FUNCTION_DDL = "CREATE FUNCTION `f_add`(`x` INT, `y` INT) RETURNS `x` + `y`"

GLOBAL_FUNCTION_DDL = "CREATE GLOBAL FUNCTION `g_upper`(`s` VARCHAR) RETURNS upper(`s`)"


class TestRetargetTable:
    def test_qualifies_object_with_target(self):
        result = retarget(
            TABLE_DDL,
            kind="table",
            object_name="t",
            source_database=SOURCE,
            target_database=TARGET,
        )
        assert result.statement.startswith("CREATE TABLE `tgt_db`.`t`")

    def test_drops_deployment_properties(self):
        result = retarget(
            TABLE_DDL,
            kind="table",
            object_name="t",
            source_database=SOURCE,
            target_database=TARGET,
        )
        for key in (
            "replication_num",
            "replicated_storage",
            "compression",
            "fast_schema_evolution",
        ):
            assert key not in result.statement
            assert key in result.dropped_properties
        # A semantic clause survives untouched.
        assert "DUPLICATE KEY(`id`)" in result.statement
        assert "DISTRIBUTED BY HASH(`id`)" in result.statement

    def test_no_properties_block_left_behind(self):
        result = retarget(
            TABLE_DDL,
            kind="table",
            object_name="t",
            source_database=SOURCE,
            target_database=TARGET,
        )
        assert "PROPERTIES" not in result.statement.upper()

    def test_target_replication_num_is_applied_not_dropped(self):
        """A small target must get a satisfiable replication factor, not the
        default (which may exceed its backend count)."""
        result = retarget(
            TABLE_DDL,
            kind="table",
            object_name="t",
            source_database=SOURCE,
            target_database=TARGET,
            target_replication_num=1,
        )
        assert '"replication_num" = "1"' in result.statement
        assert "replication_num" not in result.dropped_properties
        assert result.applied_replication_num == 1
        # Other deployment properties are still stripped.
        assert "compression" not in result.statement

    def test_replication_num_dropped_when_target_unknown(self):
        result = retarget(
            TABLE_DDL,
            kind="table",
            object_name="t",
            source_database=SOURCE,
            target_database=TARGET,
        )
        assert "replication_num" not in result.statement
        assert "replication_num" in result.dropped_properties


class TestRetargetView:
    def test_qualifies_and_repoints_body(self):
        result = retarget(
            VIEW_DDL,
            kind="view",
            object_name="v",
            source_database=SOURCE,
            target_database=TARGET,
        )
        assert "`tgt_db`.`v`" in result.statement
        assert "`tgt_db`.`t`" in result.statement
        assert SOURCE not in result.statement
        # SECURITY clause is semantic and preserved.
        assert "SECURITY NONE" in result.statement


class TestRetargetMaterializedView:
    def test_preserves_refresh_and_partition(self):
        result = retarget(
            MV_DDL,
            kind="materialized_view",
            object_name="mv",
            source_database=SOURCE,
            target_database=TARGET,
        )
        assert "`tgt_db`.`mv`" in result.statement
        assert "REFRESH SCHEDULE EVERY(INTERVAL 1 HOUR)" in result.statement
        assert "DISTRIBUTED BY RANDOM" in result.statement
        assert "tgt_db`.`t" in result.statement
        assert "replication_num" not in result.statement


class TestRetargetFunction:
    def test_qualifies_database_function(self):
        result = retarget(
            FUNCTION_DDL,
            kind="function",
            object_name="f_add",
            source_database=SOURCE,
            target_database=TARGET,
        )
        assert result.statement == (
            "CREATE FUNCTION `tgt_db`.`f_add`(`x` INT, `y` INT) RETURNS `x` + `y`"
        )

    def test_global_function_keeps_no_database(self):
        result = retarget(
            GLOBAL_FUNCTION_DDL,
            kind="function",
            object_name="g_upper",
            source_database=SOURCE,
            target_database=TARGET,
        )
        assert result.statement == GLOBAL_FUNCTION_DDL
        assert TARGET not in result.statement


class TestRetargetRefusals:
    def test_same_database_name_still_qualifies_and_strips(self):
        """Different clusters may share a database name; that is not an error."""
        result = retarget(
            TABLE_DDL,
            kind="table",
            object_name="t",
            source_database="db1",
            target_database="db1",
        )
        assert result.statement.startswith("CREATE TABLE `db1`.`t`")
        assert "replication_num" not in result.statement

    def test_empty_ddl_is_refused(self):
        with pytest.raises(RetargetError):
            retarget(
                "",
                kind="table",
                object_name="t",
                source_database=SOURCE,
                target_database=TARGET,
            )

    def test_kind_mismatch_is_refused(self):
        with pytest.raises(RetargetError):
            retarget(
                VIEW_DDL,
                kind="table",
                object_name="v",
                source_database=SOURCE,
                target_database=TARGET,
            )

    def test_wrong_object_name_is_refused(self):
        with pytest.raises(RetargetError):
            retarget(
                TABLE_DDL,
                kind="table",
                object_name="other",
                source_database=SOURCE,
                target_database=TARGET,
            )


class TestBuildPlan:
    def _candidates(self) -> list[PlanCandidate]:
        return [
            PlanCandidate("v", ObjectKind.VIEW, MigrationVerdict.MIGRATABLE, VIEW_DDL),
            PlanCandidate("t", ObjectKind.TABLE, MigrationVerdict.MIGRATABLE, TABLE_DDL),
            PlanCandidate("f_add", ObjectKind.FUNCTION, MigrationVerdict.LOSSY, FUNCTION_DDL),
            PlanCandidate("mv", ObjectKind.MATERIALIZED_VIEW, MigrationVerdict.MIGRATABLE, MV_DDL),
        ]

    def test_orders_by_dependency(self):
        plan = build_plan(self._candidates(), source_database=SOURCE, target_database=TARGET)
        kinds = [step.kind for step in plan.steps]
        assert kinds == [
            PlanStepKind.DATABASE,
            PlanStepKind.TABLE,
            PlanStepKind.VIEW,
            PlanStepKind.MATERIALIZED_VIEW,
            PlanStepKind.FUNCTION,
        ]

    def test_order_is_sequential(self):
        plan = build_plan(self._candidates(), source_database=SOURCE, target_database=TARGET)
        assert [step.order for step in plan.steps] == list(range(plan.step_count))

    def test_database_step_targets_target_name(self):
        plan = build_plan(self._candidates(), source_database=SOURCE, target_database=TARGET)
        first = plan.steps[0]
        assert first.kind is PlanStepKind.DATABASE
        assert first.statement == "CREATE DATABASE IF NOT EXISTS `tgt_db`"

    def test_target_replication_num_threads_into_table_step(self):
        plan = build_plan(
            self._candidates(),
            source_database=SOURCE,
            target_database=TARGET,
            target_replication_num=1,
        )
        table_step = next(s for s in plan.steps if s.kind is PlanStepKind.TABLE)
        assert '"replication_num" = "1"' in table_step.statement

    def test_create_database_can_be_disabled(self):
        plan = build_plan(
            self._candidates(),
            source_database=SOURCE,
            target_database=TARGET,
            create_database=False,
        )
        assert all(step.kind is not PlanStepKind.DATABASE for step in plan.steps)

    def test_missing_definition_is_blocked_not_dropped(self):
        candidates = self._candidates() + [
            PlanCandidate("task1", ObjectKind.TASK, MigrationVerdict.LOSSY, None),
            PlanCandidate("mask1", ObjectKind.MASKING_POLICY, MigrationVerdict.SKIPPED, None),
        ]
        plan = build_plan(candidates, source_database=SOURCE, target_database=TARGET)
        blocked = {obj.name for obj in plan.blocked}
        assert blocked == {"task1", "mask1"}
        assert all(obj.reason for obj in plan.blocked)

    def test_untargetable_ddl_is_blocked(self):
        candidates = [
            PlanCandidate("t", ObjectKind.TABLE, MigrationVerdict.MIGRATABLE, "SELECT 1"),
        ]
        plan = build_plan(candidates, source_database=SOURCE, target_database=TARGET)
        assert plan.blocked and plan.blocked[0].name == "t"
        assert "retarget" in plan.blocked[0].reason.lower()

    def test_empty_plan_with_only_database(self):
        plan = build_plan([], source_database=SOURCE, target_database=TARGET)
        assert plan.step_count == 1
        assert plan.blocked == ()

    def test_same_database_name_is_allowed(self):
        plan = build_plan([], source_database="x", target_database="x")
        assert plan.step_count == 1
        assert plan.steps[0].statement == "CREATE DATABASE IF NOT EXISTS `x`"


class _FakeRepo:
    """Minimal repo for the plan HTTP contract."""

    async def list_sources(self):
        return [
            {
                "id": "id",
                "name": "local",
                "host": "h",
                "port": 9030,
                "username": "root",
                "secret_ref": "",
                "comment": "",
                "created_at": None,
                "created_by": "alice",
            }
        ]

    async def get_source(self, name):
        if name != "local":
            return None
        return {
            "id": "id",
            "name": "local",
            "host": "h",
            "port": 9030,
            "username": "root",
            "secret_ref": "",
            "comment": "",
            "created_at": None,
            "created_by": "alice",
        }

    async def list_tables(self, conn, database):
        return [{"name": "t", "kind": "table"}]

    async def list_views(self, conn, database):
        return [{"name": "v", "kind": "view"}]

    async def list_materialized_views(self, conn, database):
        return []

    async def list_functions(self, conn, database):
        return []

    async def list_global_functions(self, conn):
        return []

    async def list_tasks(self, conn, database):
        return []

    async def list_pipes(self, conn, database):
        return []

    async def list_masking_policies(self, conn, database):
        return []

    async def list_row_access_policies(self, conn, database):
        return []

    async def get_table_ddl(self, conn, database, table):
        return TABLE_DDL

    async def get_view_ddl(self, conn, database, view):
        return VIEW_DDL

    async def target_replication_num(self):
        return None


@pytest.fixture
def plan_client(monkeypatch):
    install_inline_read_worker(monkeypatch)
    from contextlib import asynccontextmanager

    from app.modules.migration import service as service_module

    monkeypatch.setattr(service_module, "migration_repo", _FakeRepo())

    @asynccontextmanager
    async def _open(source):
        yield object()

    monkeypatch.setattr(service_module, "open_source_connection", _open)

    async def _audit(**kwargs):
        return "audit-id"

    monkeypatch.setattr(service_module, "write_audit_log", _audit)

    app = FastAPI()
    app.include_router(migration_router.router, prefix="/api/v1/migration")
    app.dependency_overrides[migration_router.get_current_user] = lambda: {
        "username": "alice",
        "session_id": "s1",
        "roles": ["ACCOUNTADMIN"],
        "active_role": "ACCOUNTADMIN",
        "encrypted_password": "enc",
    }
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def execute_client(monkeypatch):
    """Client with the gate open and the query pipeline stubbed."""
    from contextlib import asynccontextmanager

    from app.modules.migration import service as service_module

    class _Result:
        error = None

        def __init__(self, rows=None):
            self.rows = rows or []

    #: A caller with every privilege the plan needs, so the preflight passes.
    GRANTS = [
        (
            "'alice'@'%'",
            "default_catalog",
            "GRANT CREATE DATABASE ON CATALOG default_catalog TO USER 'alice'@'%'",
        ),
        (
            "'alice'@'%'",
            "default_catalog",
            "GRANT CREATE TABLE, CREATE VIEW, CREATE MATERIALIZED VIEW, "
            "CREATE FUNCTION, INSERT, SELECT ON ALL DATABASES TO USER 'alice'@'%'",
        ),
    ]
    executed: list[str] = []

    async def _execute(**kwargs):
        sql = kwargs["sql"]
        executed.append(sql)
        if sql.strip().upper().startswith("SHOW GRANTS"):
            return _Result(rows=GRANTS)
        return _Result()

    monkeypatch.setattr(service_module, "migration_repo", _FakeRepo())
    monkeypatch.setattr(service_module.query_service, "execute", _execute)

    @asynccontextmanager
    async def _open(source):
        yield object()

    monkeypatch.setattr(service_module, "open_source_connection", _open)

    async def _audit(**kwargs):
        return "audit-id"

    monkeypatch.setattr(service_module, "write_audit_log", _audit)
    monkeypatch.setattr(service_module.settings, "MIGRATION_EXECUTE_ENABLED", True, raising=False)
    monkeypatch.setattr(
        service_module.settings,
        "MIGRATION_EXECUTE_REQUIRE_CONFIRMATION",
        False,
        raising=False,
    )

    app = FastAPI()
    app.include_router(migration_router.router, prefix="/api/v1/migration")
    app.dependency_overrides[migration_router.get_current_user] = lambda: {
        "username": "alice",
        "session_id": "s1",
        "roles": ["ACCOUNTADMIN"],
        "active_role": "ACCOUNTADMIN",
        "encrypted_password": "enc",
    }
    client = TestClient(app, raise_server_exceptions=False)
    return client, executed


class TestExecutePath:
    def test_execute_applies_plan_and_reports_per_object(self, execute_client):
        _, executed = execute_client
        body = _run_service_execute()
        # database + table + view
        assert body["succeeded"] == 3
        assert body["failed"] == 0
        kinds = [r["kind"] for r in body["results"]]
        assert kinds == ["database", "table", "view"]
        # Every plan statement reached the query pipeline (the preflight's
        # ``SHOW GRANTS`` is a check, not a plan step).
        plan_statements = [s for s in executed if not s.strip().upper().startswith("SHOW GRANTS")]
        assert len(plan_statements) == 3

    def test_object_statements_are_idempotent(self, execute_client):
        _, executed = execute_client
        _run_service_execute()
        table_sql = next(s for s in executed if s.startswith("CREATE TABLE"))
        view_sql = next(s for s in executed if s.startswith("CREATE VIEW"))
        assert "IF NOT EXISTS" in table_sql
        assert "IF NOT EXISTS" in view_sql

    def test_execute_writes_audit_row(self, execute_client, monkeypatch):
        from app.modules.migration import service as service_module

        rows: list[dict] = []

        async def _audit(**kwargs):
            rows.append(kwargs)
            return "audit-id"

        monkeypatch.setattr(service_module, "write_audit_log", _audit)
        _run_service_execute()
        assert any(r.get("action") == "execute" for r in rows)


def _run_service_execute() -> dict:
    from app.modules.migration.service import migration_service

    response = asyncio.run(
        migration_service.execute(
            "local",
            SOURCE,
            target_database=TARGET,
            objects=[],
            create_database=True,
            acknowledge_omissions=True,
            confirmation="",
            actor="alice",
            encrypted_password="enc",
            session_id="s1",
            role="ACCOUNTADMIN",
        )
    )
    return response.model_dump(mode="json")


class TestPlanEndpoint:
    def test_plan_returns_ordered_steps(self, plan_client):
        resp = plan_client.post(
            "/api/v1/migration/plan",
            json={"source": "local", "database": SOURCE, "target_database": TARGET},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["execute_available"] is False
        kinds = [s["kind"] for s in body["steps"]]
        assert kinds == ["database", "table", "view"]

    def test_plan_defaults_target_to_source(self, plan_client):
        resp = plan_client.post(
            "/api/v1/migration/plan",
            json={"source": "local", "database": "same_db"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["target_database"] == "same_db"

    def test_plan_requires_source(self, plan_client):
        resp = plan_client.post("/api/v1/migration/plan", json={"database": SOURCE})
        assert resp.status_code == 422

    def test_plan_unknown_source_is_404(self, plan_client):
        resp = plan_client.post(
            "/api/v1/migration/plan",
            json={"source": "nope", "database": SOURCE},
        )
        assert resp.status_code == 404

    def test_execute_is_gated_not_absent(self, plan_client):
        # The endpoint exists (422 for a malformed body) but the gate refuses
        # before any engine work — and there is no unflagged "apply" alias.
        assert plan_client.post("/api/v1/migration/execute", json={}).status_code == 422
        assert plan_client.post("/api/v1/migration/apply", json={}).status_code == 404


def test_deployment_property_keys_are_lowercase():
    assert all(k == k.lower() for k in DEPLOYMENT_PROPERTY_KEYS)
