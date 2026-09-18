"""Unit tests for the Phase 11 v1 Migration Connector (NOVA-85).

The engine is absent here: the repository is an in-memory fake and the source
store is an in-memory fake, so the suite pins the *contract* — enumeration
surfaces, verdict vocabulary, credential invisibility, and the deliberate
absence of any execute path — without a live StarRocks.

The credential-invisibility tests use a sentinel that is planted in every input
a source password could plausibly travel through (encrypted password, decrypted
password, DDL text) and assert it never appears in a response, a reason string,
or a serialised audit-shaped payload.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import decrypt_password, encrypt_password
from app.modules.migration import router as migration_router
from app.modules.migration import service as service_module

#: Planted wherever a source password could travel. Must never come back out.
CRED_SENTINEL = "SENTINEL_SOURCE_PASSWORD_DO_NOT_LEAK"
ACCESS_SENTINEL = "SENTINEL_ACCESS_KEY_DO_NOT_LEAK"
SECRET_SENTINEL = "SENTINEL_SECRET_KEY_DO_NOT_LEAK"


class FakeMigrationSourceStore:
    """In-memory stand-in for the Redis-backed source store."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    async def put(
        self,
        connection_id: str,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str | None = None,
    ) -> None:
        self._data[connection_id] = {
            "host": host,
            "port": port,
            "username": username,
            "encrypted_password": encrypt_password(password),
            "database": database,
        }

    async def get(self, connection_id: str) -> dict[str, Any]:
        from app.modules.migration.source_store import MigrationSourceNotFound

        if connection_id not in self._data:
            raise MigrationSourceNotFound(connection_id)
        return self._data[connection_id]


class FakeMigrationRepository:
    """In-memory source metadata. Records which enumeration surface is used."""

    def __init__(self) -> None:
        self.tables: list[str] = []
        self.views: list[str] = []
        # (name, refresh_mode) — SYNC/ASYNC distinguishes the MV hazard.
        self.mvs: list[tuple[str, str]] = []
        self.tasks: list[str] = []
        self.pipes: list[str] = []
        self.functions: list[str] = []
        self.masking: list[str] = []
        self.row_access: list[str] = []
        self.databases: list[str] = ["analytics"]
        self.mv_surface_calls: list[str] = []
        self.version = "4.1.4"

    async def server_version(self, **_: Any) -> str:
        return self.version

    async def list_databases(self, **_: Any) -> list[str]:
        return list(self.databases)

    async def list_tables(self, database: str, **_: Any) -> list[str]:
        return list(self.tables)

    async def list_views(self, database: str, **_: Any) -> list[str]:
        return list(self.views)

    async def list_materialized_views(self, database: str, **_: Any) -> list[dict]:
        self.mv_surface_calls.append("information_schema.materialized_views")
        return [{"name": n, "refresh_mode": m} for n, m in self.mvs]

    async def list_tasks(self, database: str, **_: Any) -> list[str]:
        return list(self.tasks)

    async def list_pipes(self, database: str, **_: Any) -> list[str]:
        return list(self.pipes)

    async def list_functions(self, database: str, **_: Any) -> list[str]:
        return list(self.functions)

    async def list_policy_names(self, table: str, database: str, **_: Any) -> list[str]:
        if table.endswith("masking_policies"):
            return list(self.masking)
        return list(self.row_access)


def make_client(
    repo: FakeMigrationRepository,
    store: FakeMigrationSourceStore,
    user: dict[str, Any],
) -> TestClient:
    app = FastAPI()
    app.include_router(migration_router.router, prefix="/api/v1/migration")
    app.dependency_overrides[migration_router.get_current_user] = lambda: user
    service_module.migration_service._repo = repo  # type: ignore[assignment]
    service_module.migration_service._sources = store  # type: ignore[assignment]
    return TestClient(app, raise_server_exceptions=False)


def _user() -> dict[str, Any]:
    return {
        "username": "alice",
        "roles": [],
        "session_id": "s",
        "encrypted_password": encrypt_password("alice-password"),
    }


@pytest.fixture
def repo() -> FakeMigrationRepository:
    return FakeMigrationRepository()


@pytest.fixture
def store() -> FakeMigrationSourceStore:
    return FakeMigrationSourceStore()


@pytest.fixture
def client(repo, store) -> TestClient:
    return make_client(repo, store, _user())


def _connect(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/migration/connections",
        json={
            "host": "source.internal",
            "port": 9030,
            "username": "sync_user",
            "password": CRED_SENTINEL,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connected"] is True
    return body["connection_id"]


# ── Connect ─────────────────────────────────────────────────────


class TestConnect:
    def test_connect_returns_opaque_id_not_credential(self, client):
        connection_id = _connect(client)
        assert connection_id
        assert CRED_SENTINEL not in connection_id

    def test_connect_response_never_contains_source_password(self, client):
        resp = client.post(
            "/api/v1/migration/connections",
            json={
                "host": "source.internal",
                "port": 9030,
                "username": "sync_user",
                "password": CRED_SENTINEL,
            },
        )
        assert resp.status_code == 200
        assert CRED_SENTINEL not in resp.text

    def test_connect_stores_password_encrypted_not_plaintext(self, store, client):
        connection_id = _connect(client)
        # The store is in-memory and the client above already returned, so the
        # payload is inspectable without driving the event loop from a sync test
        # (which fights the suite-wide loop state).
        stored = store._data[connection_id]
        assert CRED_SENTINEL not in stored["encrypted_password"]
        assert decrypt_password(stored["encrypted_password"]) == CRED_SENTINEL


# ── Enumerate ───────────────────────────────────────────────────


class TestEnumerate:
    def test_enumerate_returns_objects(self, repo, client):
        repo.tables = ["orders"]
        repo.views = ["v_orders"]
        repo.mvs = [("mv_orders", "ASYNC")]
        connection_id = _connect(client)
        resp = client.post(
            f"/api/v1/migration/connections/{connection_id}/enumerate",
            params={"database": "analytics"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        kinds = {o["kind"] for o in body["objects"]}
        assert {"table", "view", "materialized_view"} <= kinds

    def test_materialized_views_come_from_the_mv_surface(self, repo, client):
        repo.mvs = [("mv_orders", "ASYNC")]
        connection_id = _connect(client)
        resp = client.post(
            f"/api/v1/migration/connections/{connection_id}/enumerate",
            params={"database": "analytics"},
        )
        assert resp.status_code == 200
        # The endpoint names the surface it used so a regression to
        # information_schema.tables is visible in the payload itself.
        assert resp.json()["materialized_view_source"] == (
            "information_schema.materialized_views"
        )
        assert repo.mv_surface_calls == ["information_schema.materialized_views"]

    def test_unknown_connection_is_404(self, client):
        resp = client.post(
            "/api/v1/migration/connections/does-not-exist/enumerate",
            params={"database": "analytics"},
        )
        assert resp.status_code == 404


# ── Dry-run verdicts ────────────────────────────────────────────


class TestDryRunVerdicts:
    def test_tables_and_views_are_migratable(self, repo, client):
        repo.tables = ["orders"]
        repo.views = ["v_orders"]
        connection_id = _connect(client)
        body = client.post(
            f"/api/v1/migration/connections/{connection_id}/dry-run",
            json={"database": "analytics"},
        ).json()
        verdicts = {(o["kind"], o["name"]): o["verdict"] for o in body["objects"]}
        assert verdicts[("table", "orders")] == "migratable"
        assert verdicts[("view", "v_orders")] == "migratable"

    def test_async_mv_is_migratable(self, repo, client):
        repo.mvs = [("mv_async", "ASYNC")]
        connection_id = _connect(client)
        body = client.post(
            f"/api/v1/migration/connections/{connection_id}/dry-run",
            json={"database": "analytics"},
        ).json()
        mv = next(o for o in body["objects"] if o["kind"] == "materialized_view")
        assert mv["verdict"] == "migratable"
        assert mv["reason"] is None

    def test_sync_mv_is_lossy_with_reason(self, repo, client):
        repo.mvs = [("mv_sync", "SYNC")]
        connection_id = _connect(client)
        body = client.post(
            f"/api/v1/migration/connections/{connection_id}/dry-run",
            json={"database": "analytics"},
        ).json()
        mv = next(o for o in body["objects"] if o["kind"] == "materialized_view")
        assert mv["verdict"] == "lossy"
        assert mv["reason"]

    def test_task_pipe_function_are_lossy(self, repo, client):
        repo.tasks = ["t1"]
        repo.pipes = ["p1"]
        repo.functions = ["f1"]
        connection_id = _connect(client)
        body = client.post(
            f"/api/v1/migration/connections/{connection_id}/dry-run",
            json={"database": "analytics"},
        ).json()
        by_kind = {o["kind"]: o for o in body["objects"]}
        for kind in ("task", "pipe", "function"):
            assert by_kind[kind]["verdict"] == "lossy"
            assert by_kind[kind]["reason"]

    def test_masking_policy_is_skipped_with_reason(self, repo, client):
        repo.masking = ["mask_email"]
        connection_id = _connect(client)
        body = client.post(
            f"/api/v1/migration/connections/{connection_id}/dry-run",
            json={"database": "analytics"},
        ).json()
        row = next(
            o for o in body["objects"] if o["kind"] == "masking_policy"
        )
        assert row["verdict"] == "skipped"
        assert row["reason"]

    def test_row_access_policy_is_skipped_with_reason(self, repo, client):
        repo.row_access = ["hide_region"]
        connection_id = _connect(client)
        body = client.post(
            f"/api/v1/migration/connections/{connection_id}/dry-run",
            json={"database": "analytics"},
        ).json()
        row = next(
            o for o in body["objects"] if o["kind"] == "row_access_policy"
        )
        assert row["verdict"] == "skipped"
        assert row["reason"]

    def test_summary_counts_and_flags(self, repo, client):
        repo.tables = ["t"]
        repo.tasks = ["task1"]
        repo.masking = ["mask1"]
        connection_id = _connect(client)
        body = client.post(
            f"/api/v1/migration/connections/{connection_id}/dry-run",
            json={"database": "analytics"},
        ).json()
        assert body["has_lossy"] is True
        assert body["has_skipped"] is True
        assert body["summary"]["lossy"] == 1
        assert body["summary"]["skipped"] == 1
        # database + table are the two migratable rows.
        assert body["summary"]["migratable"] == 2
        assert body["summary"]["total"] == body["summary"]["migratable"] + 1 + 1


# ── Credential invisibility ─────────────────────────────────────


class TestCredentialInvisibility:
    def test_password_not_in_any_dry_run_response(self, repo, client):
        repo.tables = ["orders"]
        repo.tasks = ["t1"]
        connection_id = _connect(client)
        resp = client.post(
            f"/api/v1/migration/connections/{connection_id}/dry-run",
            json={"database": "analytics"},
        )
        assert CRED_SENTINEL not in resp.text

    def test_password_not_in_reason_strings(self, repo, client):
        repo.tasks = ["t"]
        repo.masking = ["m"]
        connection_id = _connect(client)
        body = client.post(
            f"/api/v1/migration/connections/{connection_id}/dry-run",
            json={"database": "analytics"},
        ).json()
        reasons = " ".join(o.get("reason") or "" for o in body["objects"])
        assert CRED_SENTINEL not in reasons


# ── Engine adapter (Ruling A: adopt-and-invoke, never bundle) ───


class TestEngineAdapter:
    def test_unconfigured_engine_is_reported_not_raised(self, monkeypatch):
        from app.modules.migration.engine import ClusterSyncEngine, EngineConfig

        engine = ClusterSyncEngine(EngineConfig(path=""))
        status = engine.status()
        assert status.configured is False
        assert status.available is False
        assert status.execute_supported is False

    def test_missing_binary_names_the_path(self, monkeypatch):
        from app.modules.migration.engine import ClusterSyncEngine, EngineConfig

        engine = ClusterSyncEngine(
            EngineConfig(path="/nonexistent/starrocks-cluster-sync")
        )
        status = engine.status()
        assert status.configured is True
        assert status.available is False
        assert "/nonexistent/starrocks-cluster-sync" in status.message

    def test_require_available_raises_typed_error(self):
        from app.modules.migration.engine import (
            ClusterSyncEngine,
            EngineConfig,
            EngineUnavailableError,
        )

        engine = ClusterSyncEngine(EngineConfig(path="/nonexistent/binary"))
        with pytest.raises(EngineUnavailableError, match="/nonexistent/binary"):
            engine.require_available()

    def test_engine_endpoint_reports_status(self, client):
        resp = client.get("/api/v1/migration/engine")
        assert resp.status_code == 200
        body = resp.json()
        assert body["execute_supported"] is False


# ── Sensitive-property filter (Ruling B hardening) ──────────────


class TestSensitivePropertyFilter:
    def test_redact_consumed_ddl_redacts_credential_values(self):
        from app.modules.migration.service import redact_consumed_ddl

        ddl = (
            "CREATE MATERIALIZED VIEW mv PROPERTIES "
            f"('aws.s3.access_key'='{ACCESS_SENTINEL}', "
            f"'aws.s3.secret_key'='{SECRET_SENTINEL}') AS SELECT 1"
        )
        redacted = redact_consumed_ddl(ddl)
        assert ACCESS_SENTINEL not in redacted
        assert SECRET_SENTINEL not in redacted
        assert "***" in redacted

    def test_drop_sensitive_properties_removes_credential_keys(self):
        from app.modules.migration.service import drop_sensitive_properties

        filtered = drop_sensitive_properties(
            {
                "aws.s3.access_key": ACCESS_SENTINEL,
                "aws.s3.secret_key": SECRET_SENTINEL,
                "replication_num": "3",
            }
        )
        assert "aws.s3.access_key" not in filtered
        assert "aws.s3.secret_key" not in filtered
        assert filtered["replication_num"] == "3"


# ── Repository guards ───────────────────────────────────────────


class TestRepositoryGuards:
    def test_escape_doubles_quotes_and_backslashes(self):
        from app.modules.migration.repository import _escape

        assert _escape("a'b") == "a''b"
        assert _escape("a\\b") == "a\\\\b"

    def test_policy_view_is_whitelisted(self):
        import asyncio

        from app.modules.migration.repository import MigrationRepository

        repo = MigrationRepository()
        with pytest.raises(ValueError, match="Unsupported policy view"):
            asyncio.run(
                repo.list_policy_names(
                    "information_schema.not_a_real_view",
                    "db",
                    host="h",
                    port=1,
                    username="u",
                    encrypted_password="e",
                )
            )


# ── Execute absence (hard boundary) ─────────────────────────────


class TestNoExecutePath:
    def test_no_execute_route_exists(self):
        paths = {route.path for route in migration_router.router.routes}
        assert not any("execute" in p for p in paths), paths
        assert not any("cutover" in p for p in paths), paths

    def test_engine_class_has_no_execute_method(self):
        from app.modules.migration.engine import ClusterSyncEngine

        assert not hasattr(ClusterSyncEngine, "execute")
        assert not hasattr(ClusterSyncEngine, "run")

    def test_expected_routes_are_present(self):
        paths = {route.path for route in migration_router.router.routes}
        assert "/engine" in paths
        assert "/connections" in paths
        assert "/connections/{connection_id}/enumerate" in paths
        assert "/connections/{connection_id}/dry-run" in paths
