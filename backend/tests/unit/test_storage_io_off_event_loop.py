from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from botocore.exceptions import EndpointConnectionError

from app.core.exceptions import StorageUnavailableError
from app.modules.query.service import QueryService
from app.modules.workspaces.service import WorkspaceService


@pytest.mark.asyncio
async def test_csv_header_read_runs_off_event_loop_and_closes_body(monkeypatch):
    import boto3

    loop_thread = threading.get_ident()
    seen = {}

    class Body:
        def read(self, size):
            seen["read_thread"] = threading.get_ident()
            seen["read_size"] = size
            return b"id,name\n1,Ada\n"

        def close(self):
            seen["closed"] = True

    class Client:
        def get_object(self, **kwargs):
            seen["get_thread"] = threading.get_ident()
            return {"Body": Body()}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: Client())
    ref = SimpleNamespace(
        stage_name="source", file_name="data.csv", path_parts=[], start=0
    )
    config = SimpleNamespace(
        base_prefix="prefix",
        storage_connection=None,
        endpoint="http://storage.invalid",
        access_key="access",
        secret_key="secret",
        region="us-east-1",
        bucket="bucket",
    )

    params, columns = await QueryService()._detect_csv_params(
        SimpleNamespace(stage_refs=[ref]), {"source": config}
    )

    assert params["csv.skip_header"] == "1"
    assert columns == ["id", "name"]
    assert seen["get_thread"] == seen["read_thread"] != loop_thread
    assert seen["read_size"] == 8192
    assert seen["closed"] is True


@pytest.mark.asyncio
async def test_workspace_read_runs_off_event_loop():
    loop_thread = threading.get_ident()
    seen = {}
    service = WorkspaceService()

    class Repository:
        async def get_entry(self, username, entry_id):
            return {"entry_type": "file", "object_key": "workspace/file.sql"}

    def read_object(key):
        seen["thread"] = threading.get_ident()
        return b"SELECT 1"

    service._repo = Repository()
    service._read_object = read_object

    _, content = await service.get_file("analyst", "entry-id")

    assert content == "SELECT 1"
    assert seen["thread"] != loop_thread


@pytest.mark.asyncio
async def test_workspace_delete_keeps_metadata_when_storage_fails():
    service = WorkspaceService()
    deleted_entries = []

    class Repository:
        async def get_entry(self, username, entry_id):
            return {
                "id": entry_id,
                "entry_type": "file",
                "object_key": "workspace/file.sql",
                "path": "file.sql",
            }

        async def list_entries(self, username):
            return [await self.get_entry(username, "entry-id")]

        async def soft_delete_entry(self, username, entry_id):
            deleted_entries.append(entry_id)

    class Client:
        def delete_object(self, **kwargs):
            raise EndpointConnectionError(endpoint_url="http://private.invalid")

    service._repo = Repository()
    service._client = lambda: Client()
    service._bucket = lambda: "bucket"

    with pytest.raises(StorageUnavailableError):
        await service.delete_entry("analyst", "entry-id")

    assert deleted_entries == []


@pytest.mark.asyncio
async def test_workspace_delete_purges_more_than_one_version_page():
    service = WorkspaceService()
    object_keys = [f"versions/{number}.sql" for number in range(1001)]
    deleted = []
    metadata_deleted = []

    class Repository:
        async def list_versions(self, username, entry_id, limit=100, offset=0):
            return [
                {"object_key": key} for key in object_keys[offset : offset + limit]
            ]

        async def delete_versions_for_entry(self, username, entry_id):
            metadata_deleted.append(entry_id)

    service._repo = Repository()
    service._delete_object = deleted.append

    await service._purge_versions("analyst", "entry-id")

    assert deleted == object_keys
    assert metadata_deleted == ["entry-id"]
