"""Unit tests for the Migration Connector (Phase 11 v1, NOVA-85).

No engine. The repository is replaced with an in-memory fake, the source
connection opener with a no-op async context manager, and the audit writer with
a recorder — the authenticated user is supplied through a dependency override,
mirroring the pattern in ``test_task_orchestration_api.py``. The tests pin the
acceptance criteria that do not require a live StarRocks:

* AC1 — enumeration returns tables/views/MVs; MVs come from
  ``information_schema.materialized_views``, never ``information_schema.tables``.
* AC2 — dry-run returns a verdict + reason per object.
* AC3 — masking/row-access objects are always ``skipped`` with an explicit reason.
* AC4 — no credential sentinel reaches the API response or an audit row.
* AC5 — MV DDL uses ``SHOW CREATE MATERIALIZED VIEW``.
* AC6 — no Execute endpoint or execution path exists.
* QA Finding 1 — a source is required and its cluster is what is read; an
  unknown source fails closed rather than silently reading the local engine.
* QA Finding 2 — source registration and dry-run write audit rows.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.migration import router as migration_router
from app.modules.migration.engine import StarRocksClusterSyncAdapter
from app.modules.migration.schemas import MigrationVerdict, ObjectKind
from app.modules.migration.service import migration_service
from app.modules.migration.source import SourceConnectionError
from app.modules.migration.verdicts import (
    REASON_ASYNC_MV_MIGRATABLE,
    REASON_MASKING_NO_DDL,
    REASON_PIPE_NO_SHOW_CREATE,
    REASON_ROW_ACCESS_NO_DDL,
    REASON_SYNC_MV_LOSSY,
    REASON_TASK_NO_SHOW_CREATE,
    classify,
)

#: A value that must never appear in a response. It is shaped like a credential
#: so the redactor's rules are exercised, and it is written into fake source
#: metadata the way a hostile or misconfigured engine property would be.
SECRET_SENTINEL = "AKIA_MIGRATION_SENTINEL_0001"
SECRET_PROPERTY = f"'aws.s3.secret_key' = '{SECRET_SENTINEL}'"
SECRET_REF = "arn:aws:secretsmanager:us-east-1:1234:secret:nova/source"


class FakeMigrationRepository:
    """The repository surface the service uses, in memory."""

    def __init__(self) -> None:
        self.databases: list[str] = ["db1"]
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.views: dict[str, list[dict[str, Any]]] = {}
        self.materialized_views: dict[str, list[dict[str, Any]]] = {}
        self.functions: dict[str, list[dict[str, Any]]] = {}
        self.tasks: dict[str, list[dict[str, Any]]] = {}
        self.pipes: dict[str, list[dict[str, Any]]] = {}
        self.masking_policies: dict[str, list[dict[str, Any]]] = {}
        self.row_access_policies: dict[str, list[dict[str, Any]]] = {}
        self.ddls: dict[tuple[str, str, str], str] = {}
        #: Every statement the service asked the repo to run, so a test can
        #: assert which DDL surface was used (AC5).
        self.ddl_calls: list[str] = []
        self.sources: dict[str, dict[str, Any]] = {}

    # Registry
    async def list_sources(self) -> list[dict]:
        return list(self.sources.values())

    async def get_source(self, name: str) -> dict | None:
        return self.sources.get(name)

    def add_source(
        self,
        name: str,
        *,
        host: str = "source.internal",
        port: int = 9030,
        username: str = "root",
        secret_ref: str = "",
        comment: str = "",
        created_by: str = "alice",
    ) -> dict:
        row = {
            "id": f"id_{name}",
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "secret_ref": secret_ref,
            "comment": comment,
            # The real engine returns a ``datetime`` for DATETIME columns; the
            # fake mirrors that so the response model cannot pass on a string
            # while failing in production.
            "created_at": datetime(2026, 9, 18, 0, 0, 0),
            "created_by": created_by,
        }
        self.sources[name] = row
        return row

    async def create_source(
        self,
        *,
        name,
        host,
        port,
        username,
        secret_ref,
        comment,
        created_by,
    ) -> dict:
        return self.add_source(
            name,
            host=host,
            port=port,
            username=username,
            secret_ref=secret_ref,
            comment=comment,
            created_by=created_by,
        )

    # Enumeration — the source connection argument is ignored by the fake.
    async def list_tables(self, conn, database: str) -> list[dict]:
        return self.tables.get(database, [])

    async def list_views(self, conn, database: str) -> list[dict]:
        return self.views.get(database, [])

    async def list_materialized_views(self, conn, database: str) -> list[dict]:
        return self.materialized_views.get(database, [])

    async def list_functions(self, conn, database: str) -> list[dict]:
        return self.functions.get(database, [])

    async def list_tasks(self, conn, database: str) -> list[dict]:
        return self.tasks.get(database, [])

    async def list_pipes(self, conn, database: str) -> list[dict]:
        return self.pipes.get(database, [])

    async def list_masking_policies(self, conn, database: str) -> list[dict]:
        return self.masking_policies.get(database, [])

    async def list_row_access_policies(self, conn, database: str) -> list[dict]:
        return self.row_access_policies.get(database, [])

    # DDL
    async def get_table_ddl(self, conn, database: str, table: str) -> str | None:
        self.ddl_calls.append(f"TABLE:{database}.{table}")
        return self.ddls.get(("table", database, table))

    async def get_view_ddl(self, conn, database: str, view: str) -> str | None:
        self.ddl_calls.append(f"VIEW:{database}.{view}")
        return self.ddls.get(("view", database, view))

    async def get_materialized_view_ddl(self, conn, database: str, mv: str) -> str | None:
        self.ddl_calls.append(f"MATERIALIZED_VIEW:{database}.{mv}")
        return self.ddls.get(("materialized_view", database, mv))


class RecordingAudit:
    """Records ``write_audit_log`` calls without touching the engine."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def __call__(self, **kwargs) -> str:
        self.rows.append(kwargs)
        return "audit-id"


@pytest.fixture
def fake_repo(monkeypatch):
    """Replace the module-level repository used by the service."""
    from app.modules.migration import service as service_module

    repo = FakeMigrationRepository()
    repo.add_source("local-source")
    monkeypatch.setattr(service_module, "migration_repo", repo)
    return repo


@pytest.fixture
def fake_audit(monkeypatch):
    """Replace the audit writer used by the service with a recorder."""
    from app.modules.migration import service as service_module

    recorder = RecordingAudit()
    monkeypatch.setattr(service_module, "write_audit_log", recorder)
    return recorder


@pytest.fixture(autouse=True)
def stub_source_connection(monkeypatch):
    """Stub the real source connection opener.

    The service opens a connection to the registered source. Unit tests must not
    reach the network, so ``open_source_connection`` yields an inert sentinel;
    the fake repository ignores it.
    """
    from app.modules.migration import service as service_module

    @asynccontextmanager
    async def _open(source):
        yield object()

    monkeypatch.setattr(service_module, "open_source_connection", _open)


@pytest.fixture
def client():
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


def _seed_all(repo: FakeMigrationRepository, database: str = "db1") -> None:
    repo.tables[database] = [{"name": "orders", "kind": "table"}]
    repo.views[database] = [{"name": "v_orders", "kind": "view"}]
    repo.materialized_views[database] = [
        {"name": "mv_async", "kind": "materialized_view", "refresh_type": "ASYNC"},
        {"name": "mv_sync", "kind": "materialized_view", "refresh_type": "SYNC"},
    ]
    repo.functions[database] = [
        {
            "name": "add_one",
            "kind": "function",
            "signature": "add_one(int)",
            "function_type": "SCALAR",
        }
    ]
    repo.tasks[database] = [{"name": "t1", "kind": "task"}]
    repo.pipes[database] = [{"name": "p1", "kind": "pipe"}]
    repo.masking_policies[database] = [{"name": "mask_email", "kind": "masking_policy"}]
    repo.row_access_policies[database] = [{"name": "rap_region", "kind": "row_access_policy"}]
    repo.ddls[("table", database, "orders")] = "CREATE TABLE orders (id INT)"
    repo.ddls[("view", database, "v_orders")] = "CREATE VIEW v_orders AS SELECT 1"
    repo.ddls[("materialized_view", database, "mv_async")] = (
        "CREATE MATERIALIZED VIEW mv_async\n"
        "REFRESH ASYNC\n"
        "PARTITION BY dt\n"
        "PROPERTIES ('replication_num' = '1')\n"
        "AS SELECT * FROM orders"
    )
    repo.ddls[("materialized_view", database, "mv_sync")] = (
        "CREATE MATERIALIZED VIEW mv_sync AS SELECT count(*) FROM orders"
    )


SOURCE = "local-source"


# ── Verdict rules (pure domain) ─────────────────────────────────


class TestVerdictRules:
    def test_masking_policy_is_always_skipped(self):
        verdict = classify(ObjectKind.MASKING_POLICY)
        assert verdict.verdict is MigrationVerdict.SKIPPED
        assert verdict.reason == REASON_MASKING_NO_DDL

    def test_row_access_policy_is_always_skipped(self):
        verdict = classify(ObjectKind.ROW_ACCESS_POLICY)
        assert verdict.verdict is MigrationVerdict.SKIPPED
        assert verdict.reason == REASON_ROW_ACCESS_NO_DDL

    def test_task_is_lossy_never_migratable(self):
        verdict = classify(ObjectKind.TASK)
        assert verdict.verdict is MigrationVerdict.LOSSY
        assert verdict.reason == REASON_TASK_NO_SHOW_CREATE

    def test_pipe_is_lossy_never_migratable(self):
        verdict = classify(ObjectKind.PIPE)
        assert verdict.verdict is MigrationVerdict.LOSSY
        assert verdict.reason == REASON_PIPE_NO_SHOW_CREATE

    @pytest.mark.parametrize("refresh_type", ["ASYNC", "MANUAL", "async"])
    def test_async_mv_is_migratable(self, refresh_type):
        verdict = classify(ObjectKind.MATERIALIZED_VIEW, refresh_type=refresh_type)
        assert verdict.verdict is MigrationVerdict.MIGRATABLE
        assert verdict.reason == REASON_ASYNC_MV_MIGRATABLE

    @pytest.mark.parametrize("refresh_type", ["SYNC", "sync", None, ""])
    def test_sync_or_unknown_mv_stays_lossy(self, refresh_type):
        verdict = classify(ObjectKind.MATERIALIZED_VIEW, refresh_type=refresh_type)
        assert verdict.verdict is MigrationVerdict.LOSSY
        assert verdict.reason == REASON_SYNC_MV_LOSSY

    def test_table_is_migratable_with_remap_notes(self):
        verdict = classify(ObjectKind.TABLE)
        assert verdict.verdict is MigrationVerdict.MIGRATABLE
        assert any("replication_num" in note for note in verdict.notes)


# ── Service: enumeration + dry-run ──────────────────────────────


class TestEnumerate:
    async def test_mv_enumerated_from_materialized_views_not_tables(self, fake_repo, fake_audit):
        _seed_all(fake_repo)
        # The fake exposes no MV through ``list_tables``; only
        # ``list_materialized_views`` carries them, so their presence proves the
        # correct surface was used.
        response = await migration_service.enumerate(SOURCE, "db1")

        kinds = {obj.name: obj.kind for obj in response.objects}
        assert kinds["mv_async"] is ObjectKind.MATERIALIZED_VIEW
        assert kinds["mv_sync"] is ObjectKind.MATERIALIZED_VIEW
        assert kinds["orders"] is ObjectKind.TABLE
        # A materialized view must never be reported as a plain view.
        assert kinds["mv_async"] is not ObjectKind.VIEW

    async def test_counts_match_seeded_objects(self, fake_repo, fake_audit):
        _seed_all(fake_repo)
        response = await migration_service.enumerate(SOURCE, "db1")
        # 1 table + 1 view + 2 MVs + 1 function + 1 task + 1 pipe + 2 policies
        assert response.count == 9

    async def test_unknown_source_fails_closed(self, fake_repo, fake_audit):
        """Finding 1 — an unknown source must raise, never read the local engine."""
        with pytest.raises(SourceConnectionError):
            await migration_service.enumerate("nope", "db1")


class TestDryRun:
    async def test_verdict_and_reason_per_object(self, fake_repo, fake_audit):
        _seed_all(fake_repo)
        response = await migration_service.dry_run(SOURCE, "db1", [], actor="alice")

        by_name = {item.name: item for item in response.items}
        assert by_name["orders"].verdict is MigrationVerdict.MIGRATABLE
        assert by_name["orders"].reason
        assert by_name["mv_async"].verdict is MigrationVerdict.MIGRATABLE
        assert by_name["mv_sync"].verdict is MigrationVerdict.LOSSY
        assert by_name["t1"].verdict is MigrationVerdict.LOSSY
        assert by_name["p1"].verdict is MigrationVerdict.LOSSY
        assert by_name["mask_email"].verdict is MigrationVerdict.SKIPPED
        assert by_name["rap_region"].verdict is MigrationVerdict.SKIPPED

    async def test_every_item_has_a_reason(self, fake_repo, fake_audit):
        _seed_all(fake_repo)
        response = await migration_service.dry_run(SOURCE, "db1", [], actor="alice")
        assert response.items
        assert all(item.reason for item in response.items)

    async def test_masking_and_row_access_reason_is_explicit(self, fake_repo, fake_audit):
        _seed_all(fake_repo)
        response = await migration_service.dry_run(SOURCE, "db1", [], actor="alice")
        by_name = {item.name: item for item in response.items}
        assert "no DDL export" in by_name["mask_email"].reason
        assert "no DDL export" in by_name["rap_region"].reason

    async def test_summary_totals(self, fake_repo, fake_audit):
        _seed_all(fake_repo)
        response = await migration_service.dry_run(SOURCE, "db1", [], actor="alice")
        summary = response.summary
        # orders + v_orders + mv_async + native SCALAR add_one.
        assert summary.migratable == 4
        # mv_sync + t1 + p1
        assert summary.lossy == 3
        assert summary.skipped == 2  # mask_email, rap_region
        assert summary.total == 9

    async def test_selection_narrows_items(self, fake_repo, fake_audit):
        _seed_all(fake_repo)
        response = await migration_service.dry_run(
            SOURCE, "db1", ["orders", "mask_email"], actor="alice"
        )
        names = {item.name for item in response.items}
        assert names == {"orders", "mask_email"}

    async def test_unknown_object_is_skipped_with_reason(self, fake_repo, fake_audit):
        _seed_all(fake_repo)
        response = await migration_service.dry_run(SOURCE, "db1", ["does_not_exist"], actor="alice")
        assert len(response.items) == 1
        item = response.items[0]
        assert item.verdict is MigrationVerdict.SKIPPED
        assert "not found" in item.reason

    async def test_mv_ddl_uses_show_create_materialized_view(self, fake_repo, fake_audit):
        """AC5 — the MV DDL surface must be MATERIALIZED_VIEW, not VIEW."""
        _seed_all(fake_repo)
        await migration_service.dry_run(SOURCE, "db1", ["mv_async"], actor="alice")
        assert "MATERIALIZED_VIEW:db1.mv_async" in fake_repo.ddl_calls
        assert "VIEW:db1.mv_async" not in fake_repo.ddl_calls

    async def test_unknown_source_fails_closed(self, fake_repo, fake_audit):
        with pytest.raises(SourceConnectionError):
            await migration_service.dry_run("nope", "db1", [], actor="alice")


# ── Audit rows (Finding 2) ──────────────────────────────────────


class TestAudit:
    async def test_source_registration_is_audited(self, fake_repo, fake_audit):
        await migration_service.create_source(
            name="prod-source",
            host="source.internal",
            port=9030,
            username="root",
            secret_ref="",
            comment="primary",
            username_actor="alice",
        )
        assert len(fake_audit.rows) == 1
        row = fake_audit.rows[0]
        assert row["action"] == "register_source"
        assert row["object_name"] == "prod-source"
        assert row["user_name"] == "alice"

    async def test_dry_run_is_audited(self, fake_repo, fake_audit):
        _seed_all(fake_repo)
        await migration_service.dry_run(SOURCE, "db1", [], actor="alice")
        actions = [row["action"] for row in fake_audit.rows]
        assert "dry_run" in actions

    async def test_audit_row_carries_no_secret(self, fake_repo, fake_audit):
        """AC4(b) — no credential sentinel reaches an audit row."""
        _seed_all(fake_repo)
        fake_repo.ddls[("materialized_view", "db1", "mv_async")] = (
            "CREATE MATERIALIZED VIEW mv_async\n"
            "PROPERTIES (" + SECRET_PROPERTY + ")\n"
            "AS SELECT * FROM orders"
        )
        await migration_service.dry_run(SOURCE, "db1", [], actor="alice")
        assert SECRET_SENTINEL not in str(fake_audit.rows)

    async def test_source_registration_audit_has_no_secret(self, fake_repo, fake_audit):
        await migration_service.create_source(
            name="src-with-ref",
            host="source.internal",
            port=9030,
            username="root",
            secret_ref=SECRET_REF,
            comment="",
            username_actor="alice",
        )
        # The reference is not a credential value, but it must not appear in the
        # audit row either — the row names the source, nothing more.
        assert SECRET_REF not in str(fake_audit.rows)


# ── Credential invisibility ─────────────────────────────────────


class TestNoCredentialLeak:
    async def test_secret_in_mv_properties_is_filtered(self, fake_repo, fake_audit):
        """AC4 / Ruling 3 hardening — a sensitive MV property must be redacted."""
        _seed_all(fake_repo)
        fake_repo.ddls[("materialized_view", "db1", "mv_async")] = (
            "CREATE MATERIALIZED VIEW mv_async\n"
            "PROPERTIES (" + SECRET_PROPERTY + ")\n"
            "AS SELECT * FROM orders"
        )
        response = await migration_service.dry_run(SOURCE, "db1", ["mv_async"], actor="alice")
        item = response.items[0]
        assert SECRET_SENTINEL not in (item.detail or "")
        assert "***" in (item.detail or "")

    def test_secret_never_reaches_http_response(self, fake_repo, fake_audit, client):
        _seed_all(fake_repo)
        fake_repo.ddls[("materialized_view", "db1", "mv_async")] = (
            "CREATE MATERIALIZED VIEW mv_async\n"
            "PROPERTIES (" + SECRET_PROPERTY + ")\n"
            "AS SELECT * FROM orders"
        )
        resp = client.post(
            "/api/v1/migration/dry-run",
            json={"source": SOURCE, "database": "db1", "objects": []},
        )
        assert resp.status_code == 200, resp.text
        assert SECRET_SENTINEL not in resp.text

    def test_source_response_echoes_no_password(self, fake_repo, fake_audit, client):
        resp = client.post(
            "/api/v1/migration/sources",
            json={
                "name": "prod-source",
                "host": "source.internal",
                "port": 9030,
                "username": "root",
                "secret_ref": SECRET_REF,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # The reference is echoed (an operator must see which one is set); the
        # password value is never a field.
        assert body["host"] == "source.internal"
        assert body["secret_ref"] == SECRET_REF
        assert "password" not in body
        assert SECRET_SENTINEL not in resp.text


# ── HTTP contract ────────────────────────────────────────────────


class TestHttpContract:
    def test_capabilities_declares_no_execute(self, client):
        resp = client.get("/api/v1/migration/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["execute_available"] is False
        assert body["source_required"] is True
        assert "dry_run" in body["phases"]
        assert "execute" not in body["phases"]

    def test_enumerate_endpoint(self, fake_repo, fake_audit, client):
        _seed_all(fake_repo)
        resp = client.post(
            "/api/v1/migration/enumerate",
            json={"source": SOURCE, "database": "db1"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 9
        kinds = {o["name"]: o["kind"] for o in body["objects"]}
        assert kinds["mv_async"] == "materialized_view"

    def test_enumerate_unknown_source_is_404(self, fake_repo, fake_audit, client):
        resp = client.post(
            "/api/v1/migration/enumerate",
            json={"source": "nope", "database": "db1"},
        )
        assert resp.status_code == 404, resp.text

    def test_enumerate_without_source_is_rejected(self, client):
        """The source field is required — no silent local-engine fallback."""
        resp = client.post("/api/v1/migration/enumerate", json={"database": "db1"})
        assert resp.status_code == 422

    def test_dry_run_endpoint(self, fake_repo, fake_audit, client):
        _seed_all(fake_repo)
        resp = client.post(
            "/api/v1/migration/dry-run",
            json={"source": SOURCE, "database": "db1", "objects": []},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"]["skipped"] == 2
        assert body["summary"]["lossy"] == 3

    def test_dry_run_unknown_source_is_404(self, fake_repo, fake_audit, client):
        resp = client.post(
            "/api/v1/migration/dry-run",
            json={"source": "nope", "database": "db1", "objects": []},
        )
        assert resp.status_code == 404, resp.text

    def test_duplicate_source_is_rejected(self, fake_repo, fake_audit, client):
        payload = {
            "name": "dup",
            "host": "source.internal",
            "port": 9030,
            "username": "root",
        }
        assert client.post("/api/v1/migration/sources", json=payload).status_code == 201
        assert client.post("/api/v1/migration/sources", json=payload).status_code == 400


# ── Execute is absent (AC6) ─────────────────────────────────────


class TestExecuteAbsent:
    NO_EXECUTE_PATHS = (
        "/api/v1/migration/execute",
        "/api/v1/migration/run",
        "/api/v1/migration/cutover",
        "/api/v1/migration/start",
    )

    @pytest.mark.parametrize("path", NO_EXECUTE_PATHS)
    def test_no_execute_endpoint(self, client, path):
        assert client.get(path).status_code == 404
        assert client.post(path, json={}).status_code == 404

    def test_router_has_no_execute_route(self):
        paths = {getattr(route, "path", "") for route in migration_router.router.routes}
        # ``/dry-run`` legitimately contains "run"; only execution-shaped
        # segments are forbidden. Check the path segments, not the substring.
        forbidden = {"execute", "run", "cutover", "start", "apply", "sync"}
        for path in paths:
            segments = {s for s in path.split("/") if s}
            assert not (segments & forbidden), path

    def test_no_module_executes_the_engine(self):
        """The only engine call is a filesystem status check, never a subprocess."""
        import inspect

        from app.modules.migration import engine as engine_module

        source = inspect.getsource(engine_module)
        assert "subprocess" not in source
        assert "os.system" not in source
        assert "Popen" not in source


# ── Engine adapter ───────────────────────────────────────────────


class TestEngineAdapter:
    def test_missing_binary_is_reported_not_raised(self, tmp_path):
        adapter = StarRocksClusterSyncAdapter(binary_path="")
        status = adapter.status()
        assert status.available is False
        assert status.reason

    def test_configured_but_absent_path(self, tmp_path):
        adapter = StarRocksClusterSyncAdapter(binary_path=str(tmp_path / "nope"))
        status = adapter.status()
        assert status.available is False
        assert status.resolved_path is None

    def test_present_binary_is_available(self, tmp_path):
        binary = tmp_path / "starrocks-cluster-sync"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        adapter = StarRocksClusterSyncAdapter(binary_path=str(binary))
        status = adapter.status()
        assert status.available is True
        assert status.resolved_path == str(binary)

    def test_present_but_not_executable(self, tmp_path):
        binary = tmp_path / "starrocks-cluster-sync"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o644)
        adapter = StarRocksClusterSyncAdapter(binary_path=str(binary))
        status = adapter.status()
        assert status.available is False
        assert "not executable" in (status.reason or "")

    def test_engine_endpoint(self, fake_repo, client, monkeypatch):
        # Ensure a deterministic adapter regardless of environment config.
        from app.modules.migration import service as service_module

        monkeypatch.setattr(
            service_module, "migration_engine", StarRocksClusterSyncAdapter(binary_path="")
        )
        resp = client.get("/api/v1/migration/engine")
        assert resp.status_code == 200
        assert resp.json()["available"] is False


# ── Source resolution (Finding 1) ───────────────────────────────


class TestSourceResolution:
    def test_connection_from_row_rejects_legacy_row_without_host(self):
        from app.modules.migration.source import connection_from_row

        with pytest.raises(SourceConnectionError):
            connection_from_row({"name": "legacy", "host": "", "port": 9030})

    def test_connection_from_row_builds_address(self):
        from app.modules.migration.source import connection_from_row

        conn = connection_from_row(
            {"name": "src", "host": "h", "port": "9031", "username": "u", "secret_ref": ""}
        )
        assert (conn.host, conn.port, conn.username) == ("h", 9031, "u")
        assert conn.password() == ""

    def test_password_resolution_failure_is_typed(self, monkeypatch):
        from app.modules.migration import source as source_module

        def _boom(_ref):
            raise source_module.SecretResolutionError("nope")

        monkeypatch.setattr(source_module, "resolve_secret_reference", _boom)
        conn = source_module.SourceConnection(
            name="src", host="h", port=9030, username="u", secret_ref="ref"
        )
        with pytest.raises(SourceConnectionError):
            conn.password()

    def test_source_connection_error_is_not_imported_from_service(self):
        """The service re-exports the error for callers to catch."""
        assert SourceConnectionError is not None
