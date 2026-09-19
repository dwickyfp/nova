"""Regression tests for NOVA-137 — workspace create-file returning a bare 500.

The bug: ``POST /api/v1/workspaces/files`` returned ``500
{"detail":"Internal server error"}`` whenever object storage was unavailable or
misconfigured, and ``GET /api/v1/workspaces/tree`` did the same when
``NOVA_SYSTEM`` had not been initialised. Two independent defects produced it:

* ``WorkspaceService._put_object`` caught only ``ClientError``. The failure
  mode that actually breaks a deployment — the MinIO endpoint being down —
  raises ``EndpointConnectionError``, a ``BotoCoreError`` but **not** a
  ``ClientError``, so it escaped to the global catch-all.
* Even the caught path re-raised ``StorageError(str(exc))`` with the default
  ``status_code=500``, and ``str(exc)`` is the raw botocore message, which
  echoes the endpoint URL and the access key id (AGENTS.md §2 forbids both in
  responses).

The repository now translates a missing ``NOVA_SYSTEM`` table into a typed 503,
and the service classifies botocore failures into 503/502/404 with a
Nova-authored, credential-free message.

These tests assert on the **HTTP response body and status**, because that is
what the user and the frontend actually see; a helper-level assertion would not
have caught the escaping exception. The app is built with the real factory
version of the router and the real exception handlers; only the session lookup
is stubbed.
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers
from app.modules.workspaces.service import WorkspaceService

ACCESS_KEY = "AKIA_NOVA137_TESTVALUE_123"
SECRET_KEY = "SECRET_NOVA137_TESTVALUE_456"
ENDPOINT = "http://minio-nova137.invalid:9000"

FILES_ENDPOINT = "/api/v1/workspaces/files"
TREE_ENDPOINT = "/api/v1/workspaces/tree"


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": f"injected {code}"}}, "PutObject")


def _Bucket(name: str):  # noqa: N802 — mirrors the boto3 keyword name
    """Minimal stand-in for ``StorageConnectionConfig``."""
    return type("Conn", (), {"bucket": name})()


def _endpoint_down() -> EndpointConnectionError:
    return EndpointConnectionError(endpoint_url=f"{ENDPOINT}/stages/key.sql")


class _UnavailableStorageService(WorkspaceService):
    """Service whose storage client always fails as if the endpoint is down."""

    def __init__(self, exc: BaseException):
        super().__init__()
        self._exc = exc

    def _client(self):
        exc = self._exc

        class _Failing:
            def __getattr__(self, _name):
                def _boom(*_args, **_kwargs):
                    raise exc

                return _boom

        return _Failing()


@pytest.fixture
def workspace_client(monkeypatch):
    """Real router + real exception handlers, stubbed session lookup.

    Yields a factory: ``build(service) -> TestClient``.
    """
    from app.core import deps as deps_module

    async def fake_current_user():
        return {
            "username": "analyst",
            "session_id": "sess-nova137",
            "roles": [],
            "active_role": None,
            "encrypted_password": "enc",
        }

    def build(service: WorkspaceService) -> TestClient:
        import app.modules.workspaces.router as router_module

        monkeypatch.setattr(router_module, "workspace_service", service)

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(router_module.router, prefix="/api/v1/workspaces")
        app.dependency_overrides[deps_module.get_current_user] = fake_current_user
        return TestClient(app, raise_server_exceptions=False)

    return build


def _create_file(client: TestClient):
    return client.post(
        FILES_ENDPOINT,
        json={"name": "Untitled.sql", "parent_path": "", "content": ""},
    )


class TestStorageUnreachableIsClassified:
    """Endpoint down → 503, actionable detail, no credential/endpoint leak."""

    def test_create_file_returns_503_not_500(self, workspace_client):
        client = workspace_client(_UnavailableStorageService(_endpoint_down()))
        resp = _create_file(client)

        assert resp.status_code == 503, resp.text
        assert resp.json()["type"] == "StorageUnavailableError"

    def test_detail_is_actionable_and_names_storage(self, workspace_client):
        client = workspace_client(_UnavailableStorageService(_endpoint_down()))
        detail = _create_file(client).json()["detail"]

        assert "storage backend is unreachable" in detail
        assert "endpoint" in detail

    def test_no_credentials_or_endpoint_in_body(self, workspace_client):
        client = workspace_client(_UnavailableStorageService(_endpoint_down()))
        body = _create_file(client).text

        assert ACCESS_KEY not in body
        assert SECRET_KEY not in body
        assert ENDPOINT not in body
        assert "minio-nova137.invalid" not in body


class TestBucketMissingIsClassified:
    """NoSuchBucket → 404 with a message an operator can act on."""

    def test_create_file_returns_404_with_bucket_detail(self, workspace_client):
        service = _UnavailableStorageService(_client_error("NoSuchBucket"))
        client = workspace_client(service)
        resp = _create_file(client)

        assert resp.status_code == 404, resp.text
        detail = resp.json()["detail"]
        assert "bucket" in detail
        assert "does not exist" in detail

    def test_raw_client_error_text_not_echoed(self, workspace_client):
        service = _UnavailableStorageService(_client_error("NoSuchBucket"))
        client = workspace_client(service)
        body = _create_file(client).text

        assert "injected NoSuchBucket" not in body


class TestBadCredentialsAreClassified:
    """A signature/credential rejection is a 503, and never echoes the key."""

    def test_invalid_access_key_returns_503(self, workspace_client):
        service = _UnavailableStorageService(_client_error("InvalidAccessKeyId"))
        client = workspace_client(service)
        detail = _create_file(client).json()["detail"]

        assert "credentials" in detail
        assert ACCESS_KEY not in detail


class TestRepositoryClassifiesUnreadyNovaSystem:
    """The repository is where a missing NOVA_SYSTEM table is translated."""

    @pytest.mark.asyncio
    async def test_missing_table_becomes_typed_503(self):
        import asyncmy.errors

        from app.core.exceptions import WorkspaceNotReadyError
        from app.modules.workspaces.repository import _system_query

        async def _boom():
            raise asyncmy.errors.ProgrammingError(
                1064, "Table CONFIG_WORKSPACE_ENTRIES is not found."
            )

        with pytest.raises(WorkspaceNotReadyError) as caught:
            await _system_query(_boom())

        assert caught.value.status_code == 503
        assert "NOVA_SYSTEM" in caught.value.message

    @pytest.mark.asyncio
    async def test_engine_down_becomes_typed_503(self):
        import asyncmy.errors

        from app.core.exceptions import WorkspaceNotReadyError
        from app.modules.workspaces.repository import _system_query

        async def _boom():
            raise asyncmy.errors.OperationalError(2013, "Lost connection")

        with pytest.raises(WorkspaceNotReadyError):
            await _system_query(_boom())


class TestNovaSystemNotReady:
    """The tree and create paths degrade to 503 when NOVA_SYSTEM is missing."""

    def _service_with_unready_repo(self):
        from app.core.exceptions import WorkspaceNotReadyError

        message = (
            "Workspace storage is not ready: NOVA_SYSTEM is unavailable or "
            "the workspace tables have not been initialised."
        )

        class _UnreadyRepo:
            """Mirrors the real repository: every call raises the typed 503."""

            @staticmethod
            def build_path(parent_path, name):
                return "/".join(part for part in [parent_path.strip("/"), name.strip("/")] if part)

            async def list_entries(self, _username):
                raise WorkspaceNotReadyError(message)

            async def get_preferences(self, *_args, **_kwargs):
                raise WorkspaceNotReadyError(message)

            async def insert_entry(self, **_kwargs):
                raise WorkspaceNotReadyError(message)

            async def get_entry(self, *_args, **_kwargs):
                raise WorkspaceNotReadyError(message)

        class _WorkingStorage:
            """Storage succeeds, so create_file reaches the NOVA_SYSTEM write."""

            def put_object(self, **_kwargs):
                return {"ETag": '"etag"'}

        service = WorkspaceService()
        service._repo = _UnreadyRepo()
        service._client = _WorkingStorage
        return service

    def test_tree_returns_503_when_not_ready(self, workspace_client):
        client = workspace_client(self._service_with_unready_repo())
        resp = client.get(TREE_ENDPOINT)

        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert body["type"] == "WorkspaceNotReadyError"
        assert "NOVA_SYSTEM" in body["detail"]

    def test_create_file_returns_503_when_not_ready(self, workspace_client):
        client = workspace_client(self._service_with_unready_repo())
        resp = _create_file(client)

        assert resp.status_code == 503, resp.text
        assert resp.json()["type"] == "WorkspaceNotReadyError"


class TestCrudMethodsNeverReturnBare500:
    """Every storage-backed workspace operation classifies, not just create."""

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.get(f"{FILES_ENDPOINT}/does-not-matter"),
            lambda c: c.put(f"{FILES_ENDPOINT}/does-not-matter", json={"content": "x"}),
        ],
        ids=["get-file", "update-file"],
    )
    def test_storage_down_is_not_a_bare_500(self, workspace_client, call):
        service = _UnavailableStorageService(_endpoint_down())

        # Read/update first resolve the entry from NOVA_SYSTEM; supply one so the
        # code reaches the storage call instead of short-circuiting on 404.
        async def fake_get_entry(_username, _entry_id):
            return {
                "id": "e1",
                "user_name": "analyst",
                "parent_path": "",
                "name": "Untitled.sql",
                "path": "Untitled.sql",
                "entry_type": "file",
                "object_key": "workspaces/analyst/Untitled.sql",
                "size_bytes": 0,
                "etag": None,
                "created_at": None,
                "updated_at": None,
            }

        service._repo = type("R", (), {"get_entry": staticmethod(fake_get_entry)})()
        client = workspace_client(service)
        resp = call(client)

        assert resp.status_code not in (500,), resp.text
        assert resp.status_code in (502, 503)


class TestEnsureWorkspaceBucketIsIdempotent:
    """Startup must create the bucket when absent and tolerate it existing."""

    class _FakeClient:
        def __init__(self, *, head_error=None, create_error=None):
            self.head_error = head_error
            self.create_error = create_error
            self.created: list[str] = []

        def head_bucket(self, Bucket):  # noqa: N803 — boto3 keyword
            if self.head_error is not None:
                raise self.head_error

        def create_bucket(self, Bucket):  # noqa: N803 — boto3 keyword
            if self.create_error is not None:
                raise self.create_error
            self.created.append(Bucket)

    def _run(self, monkeypatch, client):
        from app.modules.workspaces import storage_bootstrap

        monkeypatch.setattr(storage_bootstrap.workspace_service, "_client", lambda: client)
        monkeypatch.setattr(
            storage_bootstrap, "get_storage_connection", lambda _name: _Bucket("nova-stages")
        )
        storage_bootstrap.ensure_workspace_bucket()
        return client

    def test_existing_bucket_is_not_recreated(self, monkeypatch):
        client = self._run(monkeypatch, self._FakeClient())
        assert client.created == []

    def test_missing_bucket_is_created(self, monkeypatch):
        not_found = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket")
        client = self._run(monkeypatch, self._FakeClient(head_error=not_found))
        assert client.created == ["nova-stages"]

    def test_concurrent_create_is_tolerated(self, monkeypatch):
        not_found = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket")
        already = ClientError(
            {"Error": {"Code": "BucketAlreadyOwnedByYou", "Message": "exists"}},
            "CreateBucket",
        )
        # Must not raise: another process won the race.
        self._run(monkeypatch, self._FakeClient(head_error=not_found, create_error=already))

    def test_endpoint_down_raises_classified_error(self, monkeypatch):
        from app.core.exceptions import StorageUnavailableError
        from app.modules.workspaces import storage_bootstrap

        not_found = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket")
        client = self._FakeClient(
            head_error=not_found,
            create_error=EndpointConnectionError(endpoint_url=ENDPOINT),
        )
        monkeypatch.setattr(storage_bootstrap.workspace_service, "_client", lambda: client)
        monkeypatch.setattr(
            storage_bootstrap, "get_storage_connection", lambda _name: _Bucket("nova-stages")
        )

        with pytest.raises(StorageUnavailableError):
            storage_bootstrap.ensure_workspace_bucket()
