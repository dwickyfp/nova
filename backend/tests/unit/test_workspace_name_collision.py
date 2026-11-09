"""A new or renamed file never takes a path another entry already uses.

A file's object key is built from its path. When "New File" picked a name that
an unopened file already had, both entries pointed at one object and the new,
empty file overwrote the old file's content. The service now refuses the path.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.exceptions import StorageError
from app.modules.workspaces.service import WorkspaceService

pytestmark = pytest.mark.asyncio


class FakeRepo:
    def __init__(self, entries: list[dict[str, Any]]):
        self.entries = entries

    @staticmethod
    def build_path(parent: str, name: str) -> str:
        parent = parent.strip("/")
        return f"{parent}/{name}" if parent else name

    async def list_entries(self, username: str) -> list[dict[str, Any]]:
        return self.entries

    async def get_entry(self, username: str, entry_id: str) -> dict[str, Any] | None:
        return next((entry for entry in self.entries if entry["id"] == entry_id), None)


@pytest.fixture
def service(monkeypatch) -> tuple[WorkspaceService, list[str]]:
    workspace = WorkspaceService()
    workspace._repo = FakeRepo([  # type: ignore[assignment]
        {"id": "old", "path": "Untitled.sql", "parent_path": "", "name": "Untitled.sql",
         "entry_type": "file", "object_key": "workspaces/ana/Untitled.sql"},
        {"id": "other", "path": "Report.sql", "parent_path": "", "name": "Report.sql",
         "entry_type": "file", "object_key": "workspaces/ana/Report.sql"},
    ])
    writes: list[str] = []
    monkeypatch.setattr(workspace, "_put_object", lambda key, body: writes.append(key) or {})
    monkeypatch.setattr(workspace, "_move_object", lambda src, dst: writes.append(dst))
    return workspace, writes


async def test_a_new_file_cannot_take_an_existing_name(service):
    workspace, writes = service
    with pytest.raises(StorageError) as error:
        await workspace.create_file("ana", "", "Untitled.sql", "")
    assert error.value.status_code == 409
    assert writes == []  # the old file's object was never touched


async def test_a_folder_cannot_take_an_existing_name(service):
    workspace, _ = service
    with pytest.raises(StorageError) as error:
        await workspace.create_folder("ana", "", "Report.sql")
    assert error.value.status_code == 409


async def test_a_rename_cannot_land_on_another_file(service):
    workspace, writes = service
    with pytest.raises(StorageError) as error:
        await workspace.rename_entry("ana", "other", "Untitled.sql")
    assert error.value.status_code == 409
    assert writes == []
