"""Unit tests for the migration preflight — Ranger access analysis.

Pure domain plus a small fake Ranger for the service path. No engine, and no
native ``SHOW GRANTS``: strict Ranger mode decides what the active role may do.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.modules.access_control.domain import AccessRequest
from app.modules.access_control.service import AccessControlService
from app.modules.migration import router as migration_router
from app.modules.migration.preflight import (
    access_requests,
    analyze_access,
    flatten_requests,
    required_privileges,
)
from tests.unit.migration_inline_worker import install_inline_read_worker

TABLE_DDL = "CREATE TABLE `t` (`id` int) DUPLICATE KEY(`id`) DISTRIBUTED BY HASH(`id`) BUCKETS 1"
VIEW_DDL = "CREATE VIEW `v` AS SELECT 1"


def _policy(resources: dict, accesses: list[str], *, roles=("migrator",), **extra) -> dict:
    return {
        "service": settings.RANGER_SERVICE_NAME,
        "isEnabled": True,
        "policyType": 0,
        "resources": {
            key: {"values": list(values) if isinstance(values, (list, tuple)) else [values]}
            for key, values in resources.items()
        },
        "policyItems": [
            {
                "roles": list(roles),
                "accesses": [{"type": access, "isAllowed": True} for access in accesses],
            }
        ],
        **extra,
    }


CATALOG_CREATE = _policy({"catalog": "default_catalog"}, ["create database"])
DATABASE_CREATE = _policy(
    {"catalog": "default_catalog", "database": "*"},
    ["create table", "create view", "create materialized view", "create function"],
)
TABLE_DATA = _policy(
    {"catalog": "default_catalog", "database": "tgt", "table": "*", "column": "*"},
    ["insert", "select"],
)


class _FakeRanger:
    def __init__(self, policies: list[dict]):
        self.policies = policies

    async def list_policies(self):
        return self.policies


def _service(policies: list[dict]) -> AccessControlService:
    return AccessControlService(ranger=_FakeRanger(policies))


async def _analyze(policies: list[dict], *, role: str = "migrator", **shape) -> object:
    plan = {
        "target_database": "tgt",
        "tables": ["t"],
        "create_database": True,
        "has_tables": True,
        "has_views": False,
        "has_materialized_views": False,
        "has_functions": False,
        "include_data": False,
        **shape,
    }
    required = access_requests(**plan)
    allowed = await _service(policies).role_allows(
        principal="alice", active_role=role, requests=flatten_requests(required)
    )
    return analyze_access(required, allowed)


class TestRangerAccess:
    @pytest.mark.asyncio
    async def test_all_privileges_present_passes(self):
        result = await _analyze(
            [CATALOG_CREATE, DATABASE_CREATE, TABLE_DATA],
            has_views=True,
            has_materialized_views=True,
            has_functions=True,
            include_data=True,
        )
        assert result.ok
        assert result.missing == ()

    @pytest.mark.asyncio
    async def test_missing_create_table_is_reported(self):
        database = _policy(
            {"catalog": "default_catalog", "database": "*"}, ["create view"]
        )
        result = await _analyze([CATALOG_CREATE, database], has_views=True)
        assert not result.ok
        assert [c.privilege for c in result.missing] == ["CREATE TABLE"]

    @pytest.mark.asyncio
    async def test_missing_create_database_is_reported(self):
        result = await _analyze([DATABASE_CREATE])
        assert [c.privilege for c in result.missing] == ["CREATE DATABASE"]

    @pytest.mark.asyncio
    async def test_database_policy_only_covers_its_database(self):
        other = _policy({"catalog": "default_catalog", "database": "other_db"}, ["create table"])
        result = await _analyze([other], create_database=False)
        assert not result.ok

    @pytest.mark.asyncio
    async def test_database_policy_matches_named_database_case_insensitively(self):
        named = _policy({"catalog": "default_catalog", "database": "TGT"}, ["create table"])
        result = await _analyze([named], create_database=False)
        assert result.ok

    @pytest.mark.asyncio
    async def test_policy_for_another_role_does_not_count(self):
        result = await _analyze(
            [CATALOG_CREATE, DATABASE_CREATE], role="analyst"
        )
        assert {c.privilege for c in result.missing} == {"CREATE DATABASE", "CREATE TABLE"}

    @pytest.mark.asyncio
    async def test_deny_item_overrides_allow(self):
        denied = _policy(
            {"catalog": "default_catalog", "database": "tgt"},
            [],
            denyPolicyItems=[
                {"roles": ["migrator"], "accesses": [{"type": "create table", "isAllowed": True}]}
            ],
        )
        result = await _analyze([CATALOG_CREATE, DATABASE_CREATE, denied])
        assert [c.privilege for c in result.missing] == ["CREATE TABLE"]

    @pytest.mark.asyncio
    async def test_conditional_allow_never_counts(self):
        conditional = _policy(
            {"catalog": "default_catalog", "database": "*"},
            ["create table"],
            conditions=[{"type": "ip-range", "values": ["10.0.0.0/8"]}],
        )
        result = await _analyze([CATALOG_CREATE, conditional])
        assert [c.privilege for c in result.missing] == ["CREATE TABLE"]

    @pytest.mark.asyncio
    async def test_excluded_database_is_not_covered(self):
        excluded = _policy({"catalog": "default_catalog", "database": "tgt"}, ["create table"])
        excluded["resources"]["database"]["isExcludes"] = True
        result = await _analyze([excluded], create_database=False)
        assert not result.ok

    @pytest.mark.asyncio
    async def test_table_access_must_cover_every_target_table(self):
        one_table = _policy(
            {"catalog": "default_catalog", "database": "tgt", "table": "t1"},
            ["insert", "select"],
        )
        result = await _analyze(
            [CATALOG_CREATE, DATABASE_CREATE, one_table],
            tables=["t1", "t2"],
            include_data=True,
        )
        assert {c.privilege for c in result.missing} == {"INSERT", "SELECT"}

    @pytest.mark.asyncio
    async def test_column_scoped_policy_does_not_grant_table_access(self):
        column = _policy(
            {"catalog": "default_catalog", "database": "tgt", "table": "*", "column": "id"},
            ["insert", "select"],
        )
        result = await _analyze(
            [CATALOG_CREATE, DATABASE_CREATE, column], include_data=True
        )
        assert {c.privilege for c in result.missing} == {"INSERT", "SELECT"}

    @pytest.mark.asyncio
    async def test_missing_active_role_fails_closed(self):
        result = await _analyze([CATALOG_CREATE, DATABASE_CREATE], role="")
        assert not result.ok

    def test_answers_must_align_with_requests(self):
        required = access_requests(
            target_database="tgt",
            tables=[],
            create_database=True,
            has_tables=False,
            has_views=False,
            has_materialized_views=False,
            has_functions=False,
            include_data=False,
        )
        with pytest.raises(ValueError):
            analyze_access(required, [])

    def test_requests_carry_ranger_resource_levels(self):
        required = access_requests(
            target_database="tgt",
            tables=["t"],
            create_database=True,
            has_tables=True,
            has_views=False,
            has_materialized_views=False,
            has_functions=False,
            include_data=True,
        )
        assert flatten_requests(required) == [
            AccessRequest(access="create database", catalog="default_catalog"),
            AccessRequest(access="create table", catalog="default_catalog", database="tgt"),
            AccessRequest(
                access="insert", catalog="default_catalog", database="tgt", table="t"
            ),
            AccessRequest(
                access="select", catalog="default_catalog", database="tgt", table="t"
            ),
        ]

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


def _preflight_client(monkeypatch, policies: list[dict]):
    install_inline_read_worker(monkeypatch)
    from app.modules.migration import service as service_module

    async def _execute(**kwargs):
        if kwargs["sql"].strip().upper().startswith("SHOW GRANTS"):
            raise AssertionError("preflight must not read native grants")
        return _FakeResult()

    monkeypatch.setattr(service_module, "migration_repo", _FakeRepo())
    monkeypatch.setattr(service_module.query_service, "execute", _execute)
    monkeypatch.setattr(service_module, "access_control_service", _service(policies))

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


FULL_POLICIES = [
    _policy({"catalog": "default_catalog"}, ["create database"], roles=["ACCOUNTADMIN"]),
    _policy(
        {"catalog": "default_catalog", "database": "*"},
        ["create table", "create view"],
        roles=["ACCOUNTADMIN"],
    ),
]


class TestPreflightEndpoint:
    def test_ok_when_all_privileges_held(self, monkeypatch):
        client = _preflight_client(monkeypatch, FULL_POLICIES)
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
        client = _preflight_client(monkeypatch, FULL_POLICIES[:1])  # no CREATE TABLE
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
        client = _preflight_client(monkeypatch, FULL_POLICIES)
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
