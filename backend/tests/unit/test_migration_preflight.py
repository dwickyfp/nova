"""Unit tests for the migration preflight — grant parsing and analysis.

Pure domain plus a small fake for the service path. No engine.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.migration import router as migration_router
from app.modules.migration.preflight import (
    GrantedPrivilege,
    analyze_grants,
    parse_grants,
    required_privileges,
)
from tests.unit.migration_inline_worker import install_inline_read_worker

TABLE_DDL = "CREATE TABLE `t` (`id` int) DUPLICATE KEY(`id`) DISTRIBUTED BY HASH(`id`) BUCKETS 1"
VIEW_DDL = "CREATE VIEW `v` AS SELECT 1"


class TestParseGrants:
    def test_parses_privileges_and_scope(self):
        rows = [
            (
                "'alice'@'%'",
                "default_catalog",
                "GRANT SELECT, INSERT ON ALL TABLES IN ALL DATABASES TO USER 'alice'@'%'",
            ),
        ]
        granted = parse_grants(rows)
        assert {(g.privilege, g.scope) for g in granted} == {
            ("SELECT", "all tables in all databases"),
            ("INSERT", "all tables in all databases"),
        }

    def test_strips_with_grant_option(self):
        rows = [
            (
                "'alice'@'%'",
                "default_catalog",
                "GRANT SELECT ON ALL TABLES IN ALL DATABASES TO USER 'alice'@'%' WITH GRANT OPTION",
            ),
        ]
        granted = parse_grants(rows)
        assert granted[0].privilege == "SELECT"

    def test_ignores_unparseable_rows(self):
        assert parse_grants([("x", "y", "not a grant statement")]) == []

    def test_parses_catalog_grant(self):
        rows = [
            (
                "'a'@'%'",
                "default_catalog",
                "GRANT CREATE DATABASE ON CATALOG default_catalog TO USER 'a'@'%'",
            ),
        ]
        granted = parse_grants(rows)
        assert granted[0].privilege == "CREATE DATABASE"
        assert granted[0].scope == "catalog default_catalog"


class TestAnalyzeGrants:
    def _all_databases(self, privs: str) -> list[GrantedPrivilege]:
        return parse_grants(
            [
                (
                    "'a'@'%'",
                    "default_catalog",
                    f"GRANT {privs} ON ALL DATABASES TO USER 'a'@'%'",
                ),
                (
                    "'a'@'%'",
                    "default_catalog",
                    "GRANT CREATE DATABASE ON CATALOG default_catalog TO USER 'a'@'%'",
                ),
            ]
        )

    def test_all_privileges_present_passes(self):
        result = analyze_grants(
            granted=self._all_databases(
                "CREATE TABLE, CREATE VIEW, CREATE MATERIALIZED VIEW, "
                "CREATE FUNCTION, INSERT, SELECT"
            ),
            target_database="tgt",
            create_database=True,
            has_tables=True,
            has_views=True,
            has_materialized_views=True,
            has_functions=True,
            include_data=True,
        )
        assert result.ok
        assert result.missing == ()

    def test_missing_create_table_is_reported(self):
        result = analyze_grants(
            granted=self._all_databases(
                "CREATE VIEW, CREATE MATERIALIZED VIEW, CREATE FUNCTION, INSERT, SELECT"
            ),
            target_database="tgt",
            create_database=True,
            has_tables=True,
            has_views=True,
            has_materialized_views=True,
            has_functions=True,
            include_data=True,
        )
        assert not result.ok
        assert [c.privilege for c in result.missing] == ["CREATE TABLE"]

    def test_missing_create_database_is_reported(self):
        result = analyze_grants(
            granted=parse_grants(
                [
                    (
                        "'a'@'%'",
                        "default_catalog",
                        "GRANT CREATE TABLE ON ALL DATABASES TO USER 'a'@'%'",
                    )
                ]
            ),
            target_database="tgt",
            create_database=True,
            has_tables=True,
            has_views=False,
            has_materialized_views=False,
            has_functions=False,
            include_data=False,
        )
        assert not result.ok
        assert [c.privilege for c in result.missing] == ["CREATE DATABASE"]

    def test_database_scoped_grant_only_covers_that_database(self):
        granted = parse_grants(
            [
                (
                    "'a'@'%'",
                    "default_catalog",
                    "GRANT CREATE TABLE ON DATABASE other_db TO USER 'a'@'%'",
                )
            ]
        )
        result = analyze_grants(
            granted=granted,
            target_database="tgt",
            create_database=False,
            has_tables=True,
            has_views=False,
            has_materialized_views=False,
            has_functions=False,
            include_data=False,
        )
        assert not result.ok

    def test_database_scoped_grant_matches_named_database(self):
        granted = parse_grants(
            [
                (
                    "'a'@'%'",
                    "default_catalog",
                    "GRANT CREATE TABLE ON DATABASE tgt TO USER 'a'@'%'",
                )
            ]
        )
        result = analyze_grants(
            granted=granted,
            target_database="tgt",
            create_database=False,
            has_tables=True,
            has_views=False,
            has_materialized_views=False,
            has_functions=False,
            include_data=False,
        )
        assert result.ok

    def test_only_relevant_privileges_are_required(self):
        # A tables-only plan must not demand CREATE VIEW / CREATE FUNCTION.
        required = required_privileges(
            create_database=False,
            has_tables=True,
            has_views=False,
            has_materialized_views=False,
            has_functions=False,
            include_data=False,
        )
        assert {p for p, _, _ in required} == {"CREATE TABLE"}

    def test_data_movement_requires_insert_and_select(self):
        required = required_privileges(
            create_database=False,
            has_tables=True,
            has_views=False,
            has_materialized_views=False,
            has_functions=False,
            include_data=True,
        )
        names = {p for p, _, _ in required}
        assert {"CREATE TABLE", "INSERT", "SELECT"} <= names


# ── Service + HTTP ──────────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.affected_rows = 0


class _FakeRepo:
    def __init__(self):
        self.grants_rows: list = []

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
        return 1


def _preflight_client(monkeypatch, grants: list[tuple]):
    install_inline_read_worker(monkeypatch)
    from app.modules.migration import service as service_module

    async def _execute(**kwargs):
        if kwargs["sql"].strip().upper().startswith("SHOW GRANTS"):
            return _FakeResult(rows=grants)
        return _FakeResult()

    monkeypatch.setattr(service_module, "migration_repo", _FakeRepo())
    monkeypatch.setattr(service_module.query_service, "execute", _execute)

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


FULL_GRANTS = [
    (
        "'alice'@'%'",
        "default_catalog",
        "GRANT CREATE DATABASE ON CATALOG default_catalog TO USER 'alice'@'%'",
    ),
    (
        "'alice'@'%'",
        "default_catalog",
        "GRANT CREATE TABLE, CREATE VIEW ON ALL DATABASES TO USER 'alice'@'%'",
    ),
]


class TestPreflightEndpoint:
    def test_ok_when_all_privileges_held(self, monkeypatch):
        client = _preflight_client(monkeypatch, FULL_GRANTS)
        resp = client.post(
            "/api/v1/migration/preflight",
            json={"source": "local", "database": "src", "target_database": "tgt"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["missing"] == []
        assert body["database_step_planned"] is True

    def test_reports_missing_privilege(self, monkeypatch):
        client = _preflight_client(monkeypatch, FULL_GRANTS[:1])  # no CREATE TABLE
        resp = client.post(
            "/api/v1/migration/preflight",
            json={"source": "local", "database": "src", "target_database": "tgt"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is False
        assert "CREATE TABLE" in body["missing"]
        # The reason is surfaced so the operator knows why it is needed.
        check = next(c for c in body["checks"] if c["privilege"] == "CREATE TABLE")
        assert check["reason"]
        assert check["satisfied"] is False

    def test_storage_reported_for_data_movement(self, monkeypatch):
        from app.modules.migration import service as service_module

        # No credentials configured → storage preflight fails.
        monkeypatch.setattr(service_module, "get_credential_params", lambda *a, **k: {})
        client = _preflight_client(monkeypatch, FULL_GRANTS)
        resp = client.post(
            "/api/v1/migration/preflight",
            json={
                "source": "local",
                "database": "src",
                "target_database": "tgt",
                "include_data": True,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["storage_checked"] is True
        assert body["storage_ok"] is False
        assert body["ok"] is False
