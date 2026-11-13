from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
async def test_csv_header_glob_without_match_returns_untuned(monkeypatch):
    """An empty glob must not raise StopIteration out of the worker thread.

    StopIteration cannot cross into the awaiting future, so the statement used
    to hang instead of letting the engine report the empty match.
    """
    import boto3

    fetched = []

    class Paginator:
        def paginate(self, **kwargs):
            return [{"Contents": [{"Key": "prefix/other.csv"}]}, {}]

    class Client:
        def get_paginator(self, name):
            return Paginator()

        def get_object(self, **kwargs):
            fetched.append(kwargs["Key"])
            raise AssertionError("no object matches the glob")

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: Client())
    ref = SimpleNamespace(
        stage_name="source", file_name="nothing_*.csv", path_parts=[], start=0
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

    params, columns = await asyncio.wait_for(
        QueryService()._detect_csv_params(
            SimpleNamespace(stage_refs=[ref]), {"source": config}
        ),
        timeout=5,
    )

    assert params == {}
    assert columns is None
    assert fetched == []


@pytest.fixture
def workspace(monkeypatch):
    from app.modules.workspaces.metadata_service import (
        WorkspaceMetadataService,
        WorkspaceObjectStore,
    )

    service = WorkspaceService()
    entry = {"id": "entry-id", "entry_type": "file", "object_key": "workspace/file.sql",
             "path": "file.sql", "metadata_revision": 3}
    files = SimpleNamespace(get_entry=AsyncMock(return_value=entry), delete=AsyncMock())
    transactions = []
    @asynccontextmanager
    async def transaction(*, read_only=False):
        transactions.append("read" if read_only else "write")
        yield files
        transactions.append("committed")
    owner = WorkspaceMetadataService(transaction, WorkspaceObjectStore(service))
    monkeypatch.setattr(WorkspaceService, "_files", property(lambda self: owner))
    return service, files, transactions


async def test_workspace_read_runs_off_event_loop(workspace):
    service, _, _ = workspace
    loop_thread = threading.get_ident()
    seen = {}
    def read_object(key):
        seen["thread"] = threading.get_ident()
        return b"SELECT 1"
    service._read_object = read_object
    _, content = await service.get_file("analyst", "entry-id")
    assert content == "SELECT 1" and seen["thread"] != loop_thread


async def test_workspace_delete_commits_tombstones_before_storage_cleanup(workspace, caplog):
    service, files, transactions = workspace
    files.delete.return_value = ["workspace/file.sql"]
    def delete(key):
        assert transactions[-2:] == ["write", "committed"]
        raise StorageUnavailableError("Object store unavailable")
    service._delete_object = delete
    await service.delete_entry("analyst", "entry-id")
    files.delete.assert_awaited_once_with("analyst", "entry-id", expected_revision=3)
    assert "1 unreferenced objects" in caplog.text


async def test_workspace_delete_purges_more_than_one_version_page(workspace):
    service, files, transactions = workspace
    object_keys = [f"versions/{number}.sql" for number in range(1001)]
    files.delete.return_value = object_keys
    deleted = []
    thread = threading.get_ident()
    def delete(key):
        assert threading.get_ident() != thread
        assert transactions[-1] == "committed"
        deleted.append(key)
    service._delete_object = delete
    await service.delete_entry("analyst", "entry-id")
    assert deleted == object_keys
    files.delete.assert_awaited_once_with("analyst", "entry-id", expected_revision=3)
