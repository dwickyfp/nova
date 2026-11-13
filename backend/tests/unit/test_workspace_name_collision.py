"""A new or renamed file never takes a path another entry already uses.

A file's object key is built from its path. When "New File" picked a name that
an unopened file already had, both entries pointed at one object and the new,
empty file overwrote the old file's content. The service now refuses the path.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.core.exceptions import StorageError
from app.core.metadata_db import MetadataConflict
from app.modules.workspaces.metadata_service import WorkspaceMetadataService, WorkspaceObjectStore
from app.modules.workspaces.service import WorkspaceService

pytestmark = pytest.mark.asyncio


class FakeRepo:
    def __init__(self, entries: list[dict[str, Any]]):
        self.entries = entries

    async def get_entry(self, username, entry_id):
        return next((row for row in self.entries if row["id"] == entry_id), None)

    async def insert_entry(self, namespace, **fields):
        assert any(row["name"] == fields["name"] and row["parent_path"] == fields["parent_path"]
                   for row in self.entries)
        raise MetadataConflict()

    async def move(self, namespace, entry_id, *, expected_revision, new_name, new_parent_path):
        assert any(row["id"] != entry_id and row["name"] == new_name
                   and row["parent_path"] == new_parent_path for row in self.entries)
        raise MetadataConflict()


@pytest.fixture
def service(monkeypatch) -> tuple[WorkspaceService, list[str]]:
    workspace = WorkspaceService()
    repo = FakeRepo([
        {"id": "old", "path": "Untitled.sql", "parent_path": "", "name": "Untitled.sql",
         "metadata_revision": 1, "entry_type": "file", "object_key": "workspaces/ana/Untitled.sql"},
        {"id": "other", "path": "Report.sql", "parent_path": "", "name": "Report.sql",
         "metadata_revision": 1, "entry_type": "file", "object_key": "workspaces/ana/Report.sql"},
    ])
    @asynccontextmanager
    async def transaction(*, read_only=False):
        yield repo
    files = WorkspaceMetadataService(transaction, WorkspaceObjectStore(workspace))
    monkeypatch.setattr(WorkspaceService, "_files", property(lambda self: files))
    writes: list[str] = []
    deleted: list[str] = []
    monkeypatch.setattr(workspace, "_put_object", lambda key, body: writes.append(key) or {})
    monkeypatch.setattr(workspace, "_delete_object", deleted.append)
    return workspace, writes, deleted


async def test_a_new_file_cannot_take_an_existing_name(service):
    workspace, writes, deleted = service
    with pytest.raises(StorageError) as error:
        await workspace.create_file("ana", "", "Untitled.sql", "")
    assert error.value.status_code == 409
    assert len(writes) == 1 and writes == deleted
    assert ".objects/" in writes[0]
    assert "Untitled.sql" not in writes[0]  # the existing object was not changed


async def test_a_folder_cannot_take_an_existing_name(service):
    workspace, _, _ = service
    with pytest.raises(StorageError) as error:
        await workspace.create_folder("ana", "", "Report.sql")
    assert error.value.status_code == 409


async def test_a_rename_cannot_land_on_another_file(service):
    workspace, writes, deleted = service
    with pytest.raises(StorageError) as error:
        await workspace.rename_entry("ana", "other", "Untitled.sql")
    assert error.value.status_code == 409
    assert writes == []
