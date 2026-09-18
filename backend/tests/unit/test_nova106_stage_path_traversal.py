"""Regression tests for NOVA-106 — stage file operations must not escape the prefix.

Finding #4 (High) of the NOVA-103 security audit: ``filename:path`` from the URL
was concatenated into the S3 key (``f"{prefix}/{filename}"``) with no
normalization, so ``../`` escaped the stage prefix and let a user read or delete
objects belonging to another stage in the same bucket.

These tests drive the service with a recording S3 client so the assertion is on
the **key that would have been used** — the pre-fix tree builds an escaped key
(and the fake client returns or deletes an out-of-prefix object), so the tests
fail for the reported reason rather than merely on an exception.
"""

import pytest

from app.modules.stages.service import StagePathError, StageService

STAGE = {
    "id": "stage-1",
    "name": "stage1",
    "database_name": "db",
    "schema_name": "schema",
    "storage_connection": "default",
    "base_prefix": "db/schema/stage1",
}

PREFIX = "db/schema/stage1"


class RecordingS3:
    """Minimal boto3 S3 client stand-in that records every key it touches."""

    def __init__(self) -> None:
        self.put_keys: list[str] = []
        self.get_keys: list[str] = []
        self.delete_keys: list[str] = []
        self.list_prefixes: list[str] = []

    def put_object(self, *, Bucket, Key, Body):  # noqa: N803 - boto3 API
        self.put_keys.append(Key)
        return {"ETag": "etag"}

    def get_object(self, *, Bucket, Key):  # noqa: N803 - boto3 API
        self.get_keys.append(Key)

        class _Body:
            def read(self):
                return b"data"

        return {"Body": _Body()}

    def delete_object(self, *, Bucket, Key):  # noqa: N803 - boto3 API
        self.delete_keys.append(Key)

    def get_paginator(self, _name):
        outer = self

        class _Paginator:
            def paginate(self, *, Bucket, Prefix, Delimiter):  # noqa: N803
                outer.list_prefixes.append(Prefix)
                return []

        return _Paginator()


@pytest.fixture
def service(monkeypatch):
    """StageService whose stage lookup and S3 client are stubbed."""
    s3 = RecordingS3()

    async def get_stage(_self, stage_id):
        return STAGE if stage_id == "stage-1" else None

    monkeypatch.setattr(StageService, "get_stage", get_stage)
    monkeypatch.setattr(
        StageService, "_s3_client_for_stage", staticmethod(lambda stage: (s3, "bucket"))
    )
    return StageService(), s3


ESCAPING_PATHS = [
    "../other_stage/secret.csv",
    "subdir/../../other_stage/secret.csv",
    "/etc/passwd",
    "..\\other_stage\\secret.csv",
    "../../outside.parquet",
    "folder/..",
]


class TestUploadCannotEscape:
    async def test_traversal_is_rejected_before_a_key_is_built(self, service):
        svc, s3 = service
        with pytest.raises(StagePathError):
            await svc.upload_file("stage-1", "../other_stage/secret.csv", b"x")

        # No partial key reached the client.
        assert s3.put_keys == []

    @pytest.mark.parametrize("path", ESCAPING_PATHS)
    async def test_every_escaping_form_is_rejected(self, service, path):
        svc, s3 = service
        with pytest.raises(StagePathError):
            await svc.upload_file("stage-1", path, b"x")
        assert s3.put_keys == []

    async def test_legitimate_subpath_still_works(self, service):
        svc, s3 = service
        await svc.upload_file("stage-1", "folder/data.csv", b"x")
        assert s3.put_keys == [f"{PREFIX}/folder/data.csv"]


class TestDownloadCannotEscape:
    async def test_traversal_is_rejected_before_a_key_is_built(self, service):
        svc, s3 = service
        with pytest.raises(StagePathError):
            await svc.download_file("stage-1", "../other_stage/secret.csv")
        assert s3.get_keys == []

    @pytest.mark.parametrize("path", ESCAPING_PATHS)
    async def test_every_escaping_form_is_rejected(self, service, path):
        svc, s3 = service
        with pytest.raises(StagePathError):
            await svc.download_file("stage-1", path)
        assert s3.get_keys == []

    async def test_legitimate_subpath_still_works(self, service):
        svc, s3 = service
        content = await svc.download_file("stage-1", "folder/data.csv")
        assert content == b"data"
        assert s3.get_keys == [f"{PREFIX}/folder/data.csv"]


class TestDeleteCannotEscape:
    async def test_traversal_is_rejected_before_a_key_is_built(self, service):
        svc, s3 = service
        with pytest.raises(StagePathError):
            await svc.delete_file("stage-1", "../other_stage/secret.csv")
        assert s3.delete_keys == []

    @pytest.mark.parametrize("path", ESCAPING_PATHS)
    async def test_every_escaping_form_is_rejected(self, service, path):
        svc, s3 = service
        with pytest.raises(StagePathError):
            await svc.delete_file("stage-1", path)
        assert s3.delete_keys == []

    async def test_legitimate_subpath_still_works(self, service):
        svc, s3 = service
        await svc.delete_file("stage-1", "folder/data.csv")
        assert s3.delete_keys == [f"{PREFIX}/folder/data.csv"]


class TestListPrefixCannotEscape:
    async def test_traversal_prefix_is_rejected(self, service):
        svc, s3 = service
        with pytest.raises(StagePathError):
            await svc.list_files("stage-1", prefix="../other_stage")
        assert s3.list_prefixes == []

    async def test_legitimate_prefix_still_works(self, service):
        svc, s3 = service
        await svc.list_files("stage-1", prefix="folder")
        assert s3.list_prefixes == [f"{PREFIX}/folder/"]

    async def test_empty_prefix_uses_stage_root(self, service):
        svc, s3 = service
        await svc.list_files("stage-1", prefix="")
        assert s3.list_prefixes == [f"{PREFIX}/"]


class TestKeyHelperBoundary:
    """The helper is the single boundary every operation routes through."""

    def test_absolute_path_rejected(self):
        with pytest.raises(StagePathError):
            StageService._build_key(STAGE, "/etc/passwd")

    def test_null_byte_rejected(self):
        with pytest.raises(StagePathError):
            StageService._build_key(STAGE, "data\x00.csv")

    def test_normalized_key_is_always_prefixed(self):
        key = StageService._build_key(STAGE, "a//b/./c.csv")
        assert key == f"{PREFIX}/a/b/c.csv"
        assert key.startswith(f"{PREFIX}/")

    def test_missing_stage_name_is_a_plain_value_error(self):
        """Stage-not-found stays a 404 — it must not be confused with a bad path."""
        assert not issubclass(StagePathError, FileNotFoundError)
        assert issubclass(StagePathError, ValueError)


class TestRouterMapsEscapeToBadRequest:
    """AC 2: an escape is a 400 from the API, a missing stage is still a 404."""

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.core import deps as deps_module
        from app.modules.stages.router import router as stages_router

        async def caller():
            return {
                "username": "analyst",
                "session_id": "s",
                "roles": [],
                "active_role": None,
                "encrypted_password": "enc",
            }

        app = FastAPI()
        app.include_router(stages_router, prefix="/api/v1/stages")
        app.dependency_overrides[deps_module.get_current_user] = caller
        return TestClient(app, raise_server_exceptions=False)

    async def _service_get_stage(self, stage_id):
        return STAGE if stage_id == "stage-1" else None

    def test_download_traversal_is_400(self, client, monkeypatch):
        from app.modules.stages.service import StageService

        monkeypatch.setattr(StageService, "get_stage", self._service_get_stage)
        # URL-encoded so the traversal reaches the route as a path parameter;
        # httpx would normalize a literal `../` away before it is sent.
        resp = client.get("/api/v1/stages/stage-1/files/%2e%2e/other/secret.csv")
        assert resp.status_code == 400, resp.text
        assert "Invalid" in resp.text

    def test_delete_traversal_is_400(self, client, monkeypatch):
        from app.modules.stages.service import StageService

        monkeypatch.setattr(StageService, "get_stage", self._service_get_stage)
        resp = client.delete("/api/v1/stages/stage-1/files/%2e%2e/other/secret.csv")
        assert resp.status_code == 400, resp.text

    def test_upload_traversal_is_400(self, client, monkeypatch):
        from app.modules.stages.service import StageService

        monkeypatch.setattr(StageService, "get_stage", self._service_get_stage)
        resp = client.post(
            "/api/v1/stages/stage-1/files",
            files={"file": ("../escape.csv", b"x", "text/csv")},
        )
        assert resp.status_code == 400, resp.text

    def test_list_traversal_prefix_is_400(self, client, monkeypatch):
        from app.modules.stages.service import StageService

        monkeypatch.setattr(StageService, "get_stage", self._service_get_stage)
        resp = client.get("/api/v1/stages/stage-1/files", params={"prefix": "../other"})
        assert resp.status_code == 400, resp.text

    def test_missing_stage_is_still_404(self, client, monkeypatch):
        from app.modules.stages.service import StageService

        monkeypatch.setattr(StageService, "get_stage", self._service_get_stage)
        resp = client.get("/api/v1/stages/nope/files/data.csv")
        assert resp.status_code == 404, resp.text
