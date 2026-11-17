"""NDV-OPS-004 on the real PG17 fixture: a scheduled export killed between writing
its workbook and committing the file saves exactly one verified file on retry, a
retry after the commit saves nothing new, and the reconciler removes the dead
attempt's object while keeping the saved one.

The run uses the task login exactly as the scheduled export does: the role's
account in a task session, adding a file to a workspace the role may edit. The
object store is in memory; the metadata, grants and RLS are PostgreSQL's.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.exceptions import StorageError
from app.core.metadata_db import MetadataDatabase
from app.modules.viz import scheduled_export
from app.modules.viz.scheduled_export import FOLDER, Occurrence, find_saved, workbook
from app.modules.workspaces import service as workspace_module
from app.modules.workspaces.metadata_files import WorkspaceFileRepository
from app.modules.workspaces.metadata_service import WorkspaceMetadataService
from app.modules.workspaces.object_reconciler import (
    RECONCILER,
    StoredObject,
    reconcile,
    referenced_in,
)
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_viz import viz  # noqa: F401
from tests.integration.test_metadata_viz_export import runner

pytestmark = pytest.mark.metadata_pg

DUE = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)


class Killed(BaseException):
    """The worker process dying: no handler below the task runner survives it."""


class Objects:
    """Object storage in memory, with the write time the reconciler sees."""

    def __init__(self):
        self.data: dict[str, bytes] = {}
        self.written: dict[str, datetime] = {}

    def key(self, namespace, *parts):
        return "/".join(["workspaces", namespace, *parts])

    async def put(self, key, body):
        self.data[key] = body
        self.written[key] = datetime.now(UTC)
        return hashlib.md5(body).hexdigest()  # noqa: S324 - an ETag, not a digest check

    async def get(self, key):
        if key not in self.data:
            raise StorageError("missing", status_code=404)
        return self.data[key]

    async def delete(self, key):
        self.data.pop(key, None)
        self.written.pop(key, None)

    def list_objects(self):
        return [StoredObject(key, when) for key, when in self.written.items()]


@pytest.fixture
def export(viz, monkeypatch):  # noqa: F811
    objects = Objects()

    @asynccontextmanager
    async def transaction(*, read_only=False):
        async with viz.transaction(runner(), workload="task",
                                   repository_type=WorkspaceFileRepository) as (files, _):
            yield files

    files = WorkspaceMetadataService(transaction, objects)
    # find_saved reads the folder through the Workspace owner, as the run does.
    monkeypatch.setattr(workspace_module, "workspace_service", files)
    return files, objects, "~ws/" + viz.workspace_id


async def lookup(viz, keys):  # noqa: F811
    database = MetadataDatabase(viz.environment.role_config("nova_meta_maintenance"))
    await database.open(schema_version=viz.schema_version, metadata_epoch=viz.epoch)
    try:
        async with database.transaction(RECONCILER, read_only=True) as connection:
            return await referenced_in(connection, keys)
    finally:
        await database.close()


async def saved_files(files, namespace):
    return [entry for entry in await files.list_entries(namespace)
            if entry["entry_type"] == "file" and entry["parent_path"] == FOLDER]


async def test_a_kill_between_render_and_commit_saves_once_and_leaves_nothing(
    export, viz, monkeypatch,  # noqa: F811
):
    files, objects, namespace = export
    occurrence = Occurrence(str(uuid4()), DUE)
    name = scheduled_export.file_name("Weekly sales", "Overview", DUE)
    data = workbook([("Overview", ["Region", "Revenue"], [["North", 10.5]])],
                    run=occurrence.key)
    await files.create_folder(namespace, "", FOLDER)

    # Attempt 1: the workbook reaches storage, then the worker dies before the
    # metadata commits. Nothing below the runner cleans up after a kill.
    async def killed(_entry):
        raise Killed

    async def no_cleanup(_keys):
        return 0

    monkeypatch.setattr(files, "_discard", no_cleanup)
    with pytest.raises(Killed):
        await files.create_file_bytes(namespace, FOLDER, name, data, on_created=killed)
    monkeypatch.undo()
    monkeypatch.setattr(workspace_module, "workspace_service", files)
    assert await saved_files(files, namespace) == []
    assert len(objects.data) == 1  # the dead attempt's object
    assert await find_saved(namespace, occurrence, "UTC") is None

    # Attempt 2 (the task is taken again): nothing of this run is saved, so it saves.
    entry = await files.create_file_bytes(namespace, FOLDER, name, data)
    found = await find_saved(namespace, occurrence, "UTC")
    assert found is not None and found[1] == "saved" and found[0]["id"] == entry["id"]

    # Attempt 3: the worker died after the commit, before reporting. The retry
    # finds the verified file and writes nothing.
    writes = len(objects.data)
    found = await find_saved(namespace, occurrence, "UTC")
    assert found is not None and found[1] == "saved"
    assert len(objects.data) == writes
    assert [f["name"] for f in await saved_files(files, namespace)] == [name]

    # The reconciler, past the retention, removes only the dead attempt's object.
    live = entry["object_key"]
    orphan = next(key for key in objects.data if key != live)
    result = await reconcile(objects, lambda keys: lookup(viz, keys),
                             now=datetime.now(UTC) + timedelta(days=2))
    assert result.removed_keys == [orphan]
    assert set(objects.data) == {live}
    _, kept = await files.read_entry_bytes(namespace, entry["id"])
    assert kept == data  # digest-verified on read
