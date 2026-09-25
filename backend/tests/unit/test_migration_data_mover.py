"""Unit tests for migration data movement (11-C).

Pure domain: the SQL builders and path/column logic, plus the service
orchestration with the source connection and query pipeline faked. No engine.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.migration import router as migration_router
from app.modules.migration.data_mover import (
    CopyColumn,
    DataMovementError,
    TableCopyPlan,
    build_table_copy,
    is_numeric_type,
    quote,
    stage_path_for,
)

COLUMNS = (
    CopyColumn("id", "int"),
    CopyColumn("name", "varchar"),
    CopyColumn("amount", "double"),
)


class TestQuote:
    def test_quotes_a_bare_identifier(self):
        assert quote("orders") == "`orders`"

    @pytest.mark.parametrize("bad", ["", "has space", "a-b", "x;y", "`tick`"])
    def test_rejects_unsafe_identifier(self, bad):
        with pytest.raises(DataMovementError):
            quote(bad)


class TestNumericType:
    @pytest.mark.parametrize(
        "dtype", ["int", "BIGINT", "double", "DECIMAL(10,2)", "float", "largeint"]
    )
    def test_numeric(self, dtype):
        assert is_numeric_type(dtype)

    @pytest.mark.parametrize("dtype", ["varchar", "date", "json", "array<int>", ""])
    def test_non_numeric(self, dtype):
        assert not is_numeric_type(dtype)


class TestBuildTableCopy:
    def _plan(self) -> TableCopyPlan:
        return build_table_copy(
            source_database="src",
            target_database="tgt",
            table="orders",
            columns=COLUMNS,
            stage_path="s3://bucket/stage/run1/src/orders",
            files_credential_sql="'aws.s3.access_key'='AK', 'aws.s3.secret_key'='SK'",
        )

    def test_export_reads_source_and_writes_files(self):
        sql = self._plan().export_sql
        assert sql.startswith("INSERT INTO FILES(")
        assert "SELECT `id`, `name`, `amount` FROM `src`.`orders`" in sql
        assert "'path'='s3://bucket/stage/run1/src/orders/'" in sql
        assert "'format'='parquet'" in sql
        assert "'aws.s3.access_key'='AK'" in sql

    def test_import_reads_files_into_target(self):
        sql = self._plan().import_sql
        assert sql.startswith("INSERT INTO `tgt`.`orders`")
        assert "FROM FILES(" in sql
        # A bare directory does not expand in FILES(); the read needs a glob.
        assert "'path'='s3://bucket/stage/run1/src/orders/*.parquet'" in sql

    def test_verify_uses_count_on_both_sides(self):
        plan = self._plan()
        assert plan.count_sql_source == "SELECT COUNT(*) FROM `src`.`orders`"
        assert plan.count_sql_target == "SELECT COUNT(*) FROM `tgt`.`orders`"

    def test_digest_sums_numeric_columns_only(self):
        plan = self._plan()
        assert plan.digest_sql_source is not None
        assert "`id`" in plan.digest_sql_source
        assert "`amount`" in plan.digest_sql_source
        # A varchar column must not be cast into the digest.
        assert "`name`" not in plan.digest_sql_source

    def test_no_numeric_column_means_no_digest(self):
        plan = build_table_copy(
            source_database="src",
            target_database="tgt",
            table="t",
            columns=(CopyColumn("name", "varchar"),),
            stage_path="s3://b/p",
        )
        assert plan.digest_sql_source is None
        assert plan.digest_sql_target is None

    def test_empty_columns_is_refused(self):
        with pytest.raises(DataMovementError):
            build_table_copy(
                source_database="src",
                target_database="tgt",
                table="t",
                columns=(),
                stage_path="s3://b/p",
            )

    def test_unsafe_column_name_is_refused(self):
        with pytest.raises(DataMovementError):
            build_table_copy(
                source_database="src",
                target_database="tgt",
                table="t",
                columns=(CopyColumn("bad name", "int"),),
                stage_path="s3://b/p",
            )

    def test_credentials_omitted_when_blank(self):
        plan = build_table_copy(
            source_database="src",
            target_database="tgt",
            table="t",
            columns=COLUMNS,
            stage_path="s3://b/p",
        )
        assert "access_key" not in plan.export_sql


class TestStagePath:
    def test_builds_scoped_path(self):
        path = stage_path_for("s3://bucket/migration-staging", "run1", "db", "t")
        assert path == "s3://bucket/migration-staging/run1/db/t"

    @pytest.mark.parametrize("bad", ["a/b", "a b", "", "a;b"])
    def test_rejects_unsafe_segment(self, bad):
        with pytest.raises(DataMovementError):
            stage_path_for("s3://b/x", bad, "db", "t")


# ── Service orchestration with a faked source + query pipeline ───


class _FakeResult:
    def __init__(self, rows=None, affected=0, error=None):
        self.rows = rows or []
        self.affected_rows = affected
        self.error = error


class _FakeSourceConn:
    """Records statements and answers the metadata/aggregate reads."""

    def __init__(self):
        self.executed: list[str] = []
        self.columns = [("id", "int"), ("v", "int")]
        self.count = 3
        self.digest = 60.0

    def cursor(self):
        conn = self

        class _Cur:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *a):
                return False

            async def execute(self_inner, sql, params=None):
                conn.executed.append(sql)

        return _Cur()


class _FakeRepo:
    def __init__(self, source_conn):
        self._source = source_conn

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
            "created_by": "a",
        }

    async def list_sources(self):
        return [await self.get_source("local")]

    async def list_tables(self, conn, database):
        return [{"name": "t", "kind": "table"}]

    async def list_views(self, conn, database):
        return []

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
        return (
            "CREATE TABLE `t` (`id` int, `v` int) "
            "DUPLICATE KEY(`id`) DISTRIBUTED BY HASH(`id`) BUCKETS 1"
        )

    async def target_replication_num(self):
        return 1

    async def list_columns(self, conn, database, table):
        return self._source.columns

    async def count_rows(self, conn, database, table):
        return self._source.count

    async def scalar(self, conn, statement):
        return self._source.digest


@pytest.fixture
def data_client(monkeypatch):
    from app.modules.migration import service as service_module

    source_conn = _FakeSourceConn()
    monkeypatch.setattr(service_module, "migration_repo", _FakeRepo(source_conn))

    @asynccontextmanager
    async def _open(source):
        yield source_conn

    monkeypatch.setattr(service_module, "open_source_connection", _open)

    # Target: schema creates succeed; data import reports affected rows; verify
    # scalars return the source count/digest.
    target_calls: list[str] = []

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

    async def _query_execute(**kwargs):
        sql = kwargs["sql"]
        target_calls.append(sql)
        if sql.strip().upper().startswith("SHOW GRANTS"):
            return _FakeResult(rows=GRANTS)
        if sql.startswith("SELECT COUNT(*)"):
            return _FakeResult(rows=[[3]])
        if sql.startswith("SELECT COALESCE"):
            return _FakeResult(rows=[[60.0]])
        if sql.startswith("INSERT INTO") and "FILES(" in sql:
            return _FakeResult(affected=3)
        return _FakeResult()

    from app.modules.migration import service as sm

    monkeypatch.setattr(sm.query_service, "execute", _query_execute)

    async def _audit(**kwargs):
        return "audit-id"

    monkeypatch.setattr(service_module, "write_audit_log", _audit)

    # Storage credentials so data movement is allowed.
    from app.core.config import StorageConnectionConfig

    monkeypatch.setattr(
        service_module,
        "get_storage_connection",
        lambda name=None: StorageConnectionConfig(
            name="production",
            type="s3",
            endpoint="http://minio:9000",
            bucket="nova-stages",
            access_key="AK",
            secret_key="SK",
        ),
    )
    monkeypatch.setattr(
        service_module,
        "get_credential_params",
        lambda stype, conn: {"aws.s3.access_key": "AK", "aws.s3.secret_key": "SK"},
    )
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
    return TestClient(app, raise_server_exceptions=False), source_conn, target_calls


class TestDataMovementExecute:
    def test_include_data_exports_imports_and_verifies(self, data_client):
        _, source_conn, target_calls = data_client
        body = _run_service_execute(include_data=True)
        assert body["data"], "expected a per-table copy result"
        copy = body["data"][0]
        assert copy["table"] == "t"
        assert copy["verified"] is True
        assert copy["rows_imported"] == 3
        assert copy["digest_match"] is True
        assert body["rows_moved"] == 3

        # Export ran on the source connection.
        assert any(s.startswith("INSERT INTO FILES(") for s in source_conn.executed)
        # Import ran on the target.
        assert any(s.startswith("INSERT INTO `tgt`.`t`") and "FILES(" in s for s in target_calls)

    def test_schema_only_by_default(self, data_client):
        _, source_conn, _ = data_client
        body = _run_service_execute()
        assert body["data"] == []
        assert body["rows_moved"] == 0
        # No export happened.
        assert not any("INSERT INTO FILES(" in s for s in source_conn.executed)

    def test_count_mismatch_is_reported_not_hidden(self, data_client, monkeypatch):
        from app.modules.migration import service as sm

        GRANTS = [
            (
                "'alice'@'%'",
                "default_catalog",
                "GRANT CREATE DATABASE ON CATALOG default_catalog TO USER 'alice'@'%'",
            ),
            (
                "'alice'@'%'",
                "default_catalog",
                "GRANT CREATE TABLE, INSERT, SELECT ON ALL DATABASES TO USER 'alice'@'%'",
            ),
        ]

        async def _bad(**kwargs):
            sql = kwargs["sql"]
            if sql.strip().upper().startswith("SHOW GRANTS"):
                return _FakeResult(rows=GRANTS)
            if sql.startswith("SELECT COUNT(*)"):
                return _FakeResult(rows=[[2]])  # mismatch vs source 3
            if sql.startswith("SELECT COALESCE"):
                return _FakeResult(rows=[[60.0]])
            if "FILES(" in sql:
                return _FakeResult(affected=2)
            return _FakeResult()

        monkeypatch.setattr(sm.query_service, "execute", _bad)
        body = _run_service_execute(include_data=True)
        copy = body["data"][0]
        assert copy["verified"] is False
        assert any("mismatch" in e for e in copy["errors"])
        assert body["rows_moved"] == 0


def _run_service_execute(*, include_data: bool = False) -> dict:
    from app.modules.migration.service import migration_service

    response = asyncio.run(
        migration_service.execute(
            "local",
            "src",
            target_database="tgt",
            objects=[],
            create_database=True,
            acknowledge_omissions=True,
            confirmation="",
            actor="alice",
            encrypted_password="enc",
            session_id="s1",
            role="ACCOUNTADMIN",
            include_data=include_data,
        )
    )
    return response.model_dump(mode="json")
