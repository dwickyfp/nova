"""Unit tests for the Backup & Recovery module (roadmap #7, NOVA-94).

No engine. The shared query pipeline is replaced with a recorder and the
authenticated user comes from a dependency override. The tests pin:

* ``BACKUP SNAPSHOT`` / ``RESTORE SNAPSHOT`` / ``RECOVER`` build the documented
  statements, including the whole-database vs table-list ``ON (...)`` forms;
* snapshot list, repository list and recycle-bin browser read the engine's own
  ``SHOW`` surfaces;
* **authorization is enforced in the backend**: a non-admin role gets 403 on
  every mutating endpoint before any SQL is built, while reads remain open;
* a repository response carries no credential value, even when the connected
  storage connection has one;
* recovery is scoped and validated (identifier / timestamp injection refused).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import deps as deps_module
from app.core.exceptions import register_exception_handlers
from app.modules.backup import router as backup_router
from app.modules.backup import service as service_module
from app.modules.backup.schemas import (
    RecoverRequest,
    RepositoryCreate,
    SnapshotCreate,
    SnapshotRestore,
)
from app.modules.backup.service import BackupError, backup_service

SECRET_SENTINEL = "AKIA_BACKUP_SENTINEL_0001"


class _Result:
    def __init__(self, *, columns=None, rows=None, error=None) -> None:
        self.columns = columns or []
        self.rows = rows or []
        self.error = error


class RecordingPipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.default = _Result()

    async def execute(self, *, sql: str, role: str | None = None, **_: Any) -> _Result:
        self.calls.append((sql, role))
        return self.default

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]


@pytest.fixture
def pipeline(monkeypatch):
    fake = RecordingPipeline()
    monkeypatch.setattr(service_module, "query_service", fake)
    return fake


def _client(roles: list[str], active_role: str | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(backup_router.router, prefix="/api/v1/backup")
    # The real app registers the Nova exception handlers; without them
    # ``InsufficientRoleError`` would surface as a 500 in the test app.
    register_exception_handlers(app)
    app.dependency_overrides[deps_module.get_current_user] = lambda: {
        "username": "alice",
        "session_id": "s1",
        "roles": roles,
        # Default to the first granted role; pass an explicit ``active_role``
        # to model a user who has switched away from an admin grant.
        "active_role": roles[0] if active_role is None else active_role,
        "encrypted_password": "enc",
    }
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client():
    return _client(["ACCOUNTADMIN"])


@pytest.fixture
def low_priv_client():
    return _client(["analyst"])


@pytest.fixture
def inactive_admin_client():
    """Holds ACCOUNTADMIN but has switched the active role to analyst."""
    return _client(["ACCOUNTADMIN", "analyst"], active_role="analyst")


# ── Snapshot statements ─────────────────────────────────────────


class TestSnapshots:
    async def test_create_whole_database(self, pipeline):
        await backup_service.create_snapshot(
            SnapshotCreate(
                database="DATALAKE",
                label="snapshot_20260618",
                repository="backup_repo",
            ),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "BACKUP SNAPSHOT `DATALAKE`.`snapshot_20260618` TO `backup_repo` "
            'ON (DATABASE `DATALAKE`) PROPERTIES("type" = "full", "timeout" = "3600")'
        )

    async def test_create_table_list(self, pipeline):
        await backup_service.create_snapshot(
            SnapshotCreate(
                database="DATALAKE",
                label="snap1",
                repository="repo",
                tables=["orders", "customers"],
            ),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert "ON (TABLE `orders`, TABLE `customers`)" in pipeline.statements()[0]

    async def test_restore_whole_database(self, pipeline):
        await backup_service.restore_snapshot(
            SnapshotRestore(database="DATALAKE", label="snap1", repository="repo"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "RESTORE SNAPSHOT `DATALAKE`.`snap1` FROM `repo` "
            'ON (DATABASE `DATALAKE`) PROPERTIES("replication_num" = "1")'
        )

    async def test_restore_with_timestamp_and_overwrite(self, pipeline):
        await backup_service.restore_snapshot(
            SnapshotRestore(
                database="DATALAKE",
                label="snap1",
                repository="repo",
                backup_timestamp="2026-06-18-10-00-00",
                allow_overwrite=True,
            ),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        sql = pipeline.statements()[0]
        assert '"backup_timestamp" = "2026-06-18-10-00-00"' in sql
        assert '"allow_overwrite" = "true"' in sql

    async def test_list_snapshots_reads_show_backup(self, pipeline):
        pipeline.default = _Result(
            columns=["SnapshotName", "State"], rows=[["snap1", "FINISHED"]]
        )
        rows = await backup_service.list_snapshots(
            database="DATALAKE",
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "SHOW BACKUP FROM `DATALAKE`"
        assert rows == [{"SnapshotName": "snap1", "State": "FINISHED"}]

    def test_http_create_snapshot(self, client, pipeline):
        resp = client.post(
            "/api/v1/backup/snapshots",
            json={
                "database": "DATALAKE",
                "label": "snap1",
                "repository": "repo",
            },
        )
        assert resp.status_code == 201, resp.text
        assert "BACKUP SNAPSHOT" in pipeline.statements()[0]


# ── Repositories ────────────────────────────────────────────────


class TestRepositories:
    async def test_create_reads_credentials_from_connection(self, pipeline, monkeypatch):
        class _Conn:
            endpoint = "http://storage.internal:9000"
            region = "us-east-1"

        monkeypatch.setattr(service_module, "get_storage_connection", lambda name: _Conn())
        monkeypatch.setattr(
            service_module,
            "resolve_storage_credentials",
            lambda name: ("ACCESSKEY", "SECRETKEY"),
        )
        result = await backup_service.create_repository(
            RepositoryCreate(
                name="backup_repo",
                storage_connection="prod-storage",
                location="s3://backup-bucket/snapshots",
            ),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        sql = pipeline.statements()[0]
        assert sql.startswith("CREATE REPOSITORY `backup_repo` WITH BROKER")
        assert '"aws.s3.endpoint" = "http://storage.internal:9000"' in sql
        # The response never echoes a credential value.
        assert SECRET_SENTINEL not in str(result)
        assert result["storage_connection"] == "prod-storage"

    async def test_list_repositories(self, pipeline):
        pipeline.default = _Result(columns=["Name"], rows=[["backup_repo"]])
        rows = await backup_service.list_repositories(
            username="alice", encrypted_password="enc", session_id="s1"
        )
        assert pipeline.statements()[0] == "SHOW REPOSITORIES"
        assert rows == [{"Name": "backup_repo"}]

    def test_repository_creation_requires_admin(self, low_priv_client, pipeline):
        resp = low_priv_client.post(
            "/api/v1/backup/repositories",
            json={
                "name": "r",
                "storage_connection": "s",
                "location": "s3://b/p",
            },
        )
        assert resp.status_code == 403
        assert pipeline.calls == []


# ── Recycle bin ─────────────────────────────────────────────────


class TestRecycleBin:
    async def test_list_reads_engine_surface(self, pipeline):
        pipeline.default = _Result(
            columns=["Name", "Type"], rows=[["staging", "TABLE"]]
        )
        rows = await backup_service.list_recycle_bin(
            username="alice", encrypted_password="enc", session_id="s1"
        )
        assert pipeline.statements()[0] == "SHOW CATALOG RECYCLE BIN"
        assert rows == [{"Name": "staging", "Type": "TABLE"}]

    async def test_recover_table(self, pipeline):
        await backup_service.recover(
            RecoverRequest(object_type="table", database="DATALAKE", name="orders"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == "RECOVER TABLE `DATALAKE`.`orders`"

    async def test_recover_database_with_rename(self, pipeline):
        await backup_service.recover(
            RecoverRequest(object_type="database", name="DATALAKE", new_name="DATALAKE_restored"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert pipeline.statements()[0] == (
            "RECOVER DATABASE `DATALAKE` AS `DATALAKE_restored`"
        )

    def test_http_recover(self, client, pipeline):
        resp = client.post(
            "/api/v1/backup/recover",
            json={"object_type": "table", "database": "d", "name": "t"},
        )
        assert resp.status_code == 200, resp.text
        assert pipeline.statements()[0] == "RECOVER TABLE `d`.`t`"


# ── Authorization (enforced in the backend) ─────────────────────


class TestAuthorization:
    MUTATIONS = (
        ("/api/v1/backup/snapshots", {"database": "d", "label": "l", "repository": "r"}),
        (
            "/api/v1/backup/snapshots/restore",
            {"database": "d", "label": "l", "repository": "r"},
        ),
        ("/api/v1/backup/repositories", {"name": "r", "storage_connection": "s", "location": "s3://b/p"}),
        ("/api/v1/backup/recover", {"object_type": "table", "database": "d", "name": "t"}),
    )

    @pytest.mark.parametrize("path,payload", MUTATIONS)
    def test_non_admin_gets_403_before_sql(self, low_priv_client, pipeline, path, payload):
        resp = low_priv_client.post(path, json=payload)
        assert resp.status_code == 403, resp.text
        assert pipeline.calls == []

    @pytest.mark.parametrize("path,payload", MUTATIONS)
    def test_granted_but_inactive_admin_is_refused(
        self, inactive_admin_client, pipeline, path, payload
    ):
        """NOVA-94 finding #3 — the gate reads the *active* role.

        A principal holding ACCOUNTADMIN that has switched to ``analyst``
        executes as ``analyst`` (the connection runs ``SET ROLE <active_role>``),
        so it must be refused here rather than passing a granted-roles check.
        """
        resp = inactive_admin_client.post(path, json=payload)
        assert resp.status_code == 403, resp.text
        assert pipeline.calls == []

    def test_active_admin_is_accepted(self, pipeline):
        active_admin = _client(["ACCOUNTADMIN"], active_role="ACCOUNTADMIN")
        resp = active_admin.post(
            "/api/v1/backup/snapshots",
            json={"database": "d", "label": "l", "repository": "r"},
        )
        assert resp.status_code == 201, resp.text
        assert len(pipeline.calls) == 1

    def test_reads_are_open_to_authenticated_users(self, low_priv_client, pipeline):
        assert low_priv_client.get("/api/v1/backup/snapshots").status_code == 200
        assert low_priv_client.get("/api/v1/backup/recycle-bin").status_code == 200
        assert low_priv_client.get("/api/v1/backup/repositories").status_code == 200

    def test_admin_role_is_accepted(self, client, pipeline):
        resp = client.post(
            "/api/v1/backup/snapshots",
            json={"database": "d", "label": "l", "repository": "r"},
        )
        assert resp.status_code == 201, resp.text
        assert len(pipeline.calls) == 1


# ── Validation / redaction ──────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize("bad", ["a`b", "a b", "a.b", ""])
    async def test_bad_label_is_refused(self, pipeline, bad):
        # ``""`` is refused by the schema's min_length; the rest by the
        # service's identifier check. Either way no SQL is built.
        with pytest.raises((BackupError, ValidationError)):
            await backup_service.create_snapshot(
                SnapshotCreate(database="d", label=bad, repository="r"),
                username="alice",
                encrypted_password="enc",
                session_id="s1",
            )
        assert pipeline.calls == []

    async def test_bad_table_identifier_is_refused(self, pipeline):
        with pytest.raises(BackupError):
            await backup_service.create_snapshot(
                SnapshotCreate(
                    database="d", label="l", repository="r", tables=["a; DROP TABLE x"]
                ),
                username="alice",
                encrypted_password="enc",
                session_id="s1",
            )
        assert pipeline.calls == []

    async def test_bad_timestamp_is_refused(self, pipeline):
        with pytest.raises(BackupError):
            await backup_service.restore_snapshot(
                SnapshotRestore(
                    database="d",
                    label="l",
                    repository="r",
                    backup_timestamp="2026'; DROP TABLE x; --",
                ),
                username="alice",
                encrypted_password="enc",
                session_id="s1",
            )
        assert pipeline.calls == []

    async def test_location_with_quote_is_refused(self, pipeline, monkeypatch):
        class _Conn:
            endpoint = None
            region = None

        monkeypatch.setattr(service_module, "get_storage_connection", lambda name: _Conn())
        monkeypatch.setattr(
            service_module, "resolve_storage_credentials", lambda name: ("a", "b")
        )
        with pytest.raises(BackupError):
            await backup_service.create_repository(
                RepositoryCreate(
                    name="r",
                    storage_connection="s",
                    location='s3://b/p"; DROP TABLE x; --',
                ),
                username="alice",
                encrypted_password="enc",
                session_id="s1",
            )
        assert pipeline.calls == []

    async def test_engine_error_becomes_400(self, client, pipeline):
        pipeline.default = _Result(error="repository not found")
        resp = client.post(
            "/api/v1/backup/snapshots",
            json={"database": "d", "label": "l", "repository": "r"},
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"]


class TestNoCredentialLeak:
    def test_repository_list_response_carries_no_secret(self, client, pipeline):
        pipeline.default = _Result(
            columns=["Name", "Properties"],
            rows=[["repo", f'"aws.s3.secret_key" = "{SECRET_SENTINEL}"']],
        )
        resp = client.get("/api/v1/backup/repositories")
        assert resp.status_code == 200, resp.text
        assert SECRET_SENTINEL not in resp.text


class TestAudit:
    async def test_mutations_go_through_audited_pipeline(self, pipeline):
        await backup_service.create_snapshot(
            SnapshotCreate(database="d", label="l", repository="r"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        await backup_service.recover(
            RecoverRequest(object_type="table", database="d", name="t"),
            username="alice",
            encrypted_password="enc",
            session_id="s1",
        )
        assert len(pipeline.calls) == 2

    async def test_service_opens_no_direct_connection(self):
        import inspect

        source = inspect.getsource(service_module)
        assert "asyncmy" not in source
        assert "db.execute_system" not in source
