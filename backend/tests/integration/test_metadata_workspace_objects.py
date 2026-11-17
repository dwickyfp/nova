"""NDV-STORE-009 on the real PG17 fixture (0060): the Workspace object reconciler,
as its SERVICE principal on the maintenance login, learns which object keys live
metadata references, and nothing else; every other identity learns nothing."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.core.metadata_db import MetadataDatabase, MetadataPermissionDenied
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.workspaces.object_reconciler import (
    RECONCILER,
    StoredObject,
    reconcile,
    referenced_in,
)
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspace_files import SHA, files  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
ALICE = "workspace-alice"


def key(name):
    return f"workspaces/{ALICE}/.objects/{name}"


@asynccontextmanager
async def connection(files, actor, workload, *, read_only=True):  # noqa: F811
    db = MetadataDatabase(files.environment.role_config("nova_meta_" + workload))
    try:
        await db.open(schema_version=len(load_migrations()), metadata_epoch=files.epoch)
        async with db.transaction(actor, read_only=read_only) as conn:
            yield conn
    finally:
        await db.close()


async def history(files):  # noqa: F811
    """A live file with a published version, an abandoned reservation, a deleted
    file, and an orphan key nothing ever referenced."""
    async with files.transaction() as (repo, _):
        live = await repo.insert_entry(ALICE, parent_path="", name="live.sql",
                                       entry_type="file", object_key=key("live/1"),
                                       size_bytes=3, etag="e", content_sha256=SHA)
        gone = await repo.insert_entry(ALICE, parent_path="", name="gone.sql",
                                       entry_type="file", object_key=key("gone/1"),
                                       size_bytes=3, etag="e", content_sha256=SHA)
    async with files.transaction() as (repo, _):
        published = await repo.reserve_version(ALICE, live["id"],
                                               object_key_for=lambda n: key(f"live/v{n}"))
        await repo.publish_version(ALICE, published["id"], size_bytes=3, etag="e",
                                   content_sha256=SHA)
    async with files.transaction() as (repo, _):
        abandoned = await repo.reserve_version(ALICE, live["id"],
                                               object_key_for=lambda n: key(f"live/v{n}"))
        await repo.abandon_version(ALICE, abandoned["id"])
    async with files.transaction() as (repo, _):
        await repo.delete(ALICE, gone["id"], expected_revision=gone["metadata_revision"])
    return {"live": key("live/1"), "published": published["object_key"],
            "abandoned": abandoned["object_key"], "deleted": key("gone/1"),
            "orphan": key("never/1")}


async def test_the_reconciler_learns_only_which_keys_are_referenced(files):  # noqa: F811
    keys = await history(files)
    async with connection(files, RECONCILER, "maintenance") as conn:
        assert await referenced_in(conn, list(keys.values())) == {
            keys["live"], keys["published"]}
    # Keys only: names, paths, owners and digests stay unreadable.
    for column in ("name", "parent_path", "user_name", "content_sha256"):
        with pytest.raises(MetadataPermissionDenied):
            async with connection(files, RECONCILER, "maintenance") as conn:
                await conn.execute(
                    f"SELECT {column} FROM nova_meta.config_workspace_entries LIMIT 1")


async def test_any_other_identity_on_the_maintenance_login_learns_nothing(files):  # noqa: F811
    keys = await history(files)
    for actor in (SecurityContext("catalog-refresher", "SECURITYADMIN", principal_type="SERVICE"),
                  SecurityContext.from_session(session("workspace-alice"))):
        async with connection(files, actor, "maintenance") as conn:
            assert await referenced_in(conn, list(keys.values())) == set()


async def test_the_reconciler_never_writes_metadata(files):  # noqa: F811
    keys = await history(files)
    for change in ("UPDATE nova_meta.config_workspace_entries SET object_key=object_key",
                   "DELETE FROM nova_meta.config_workspace_file_versions"):
        with pytest.raises(MetadataPermissionDenied):
            async with connection(files, RECONCILER, "maintenance", read_only=False) as conn:
                await conn.execute(change)
    async with connection(files, RECONCILER, "maintenance") as conn:
        assert keys["live"] in await referenced_in(conn, [keys["live"]])


async def test_a_pass_removes_only_unreferenced_objects(files):  # noqa: F811
    keys = await history(files)
    now = datetime.now(UTC)

    class Store:
        def __init__(self):
            self.objects = {k: now - timedelta(days=2) for k in keys.values()}
            self.objects[key("pending/1")] = now - timedelta(minutes=5)  # may still commit

        def list_objects(self):
            return [StoredObject(k, when) for k, when in self.objects.items()]

        async def delete(self, k):
            del self.objects[k]

    store = Store()

    async def lookup(batch):
        async with connection(files, RECONCILER, "maintenance") as conn:
            return await referenced_in(conn, batch)

    result = await reconcile(store, lookup, now=now)
    assert sorted(result.removed_keys) == sorted(
        [keys["abandoned"], keys["deleted"], keys["orphan"]])
    assert set(store.objects) == {keys["live"], keys["published"], key("pending/1")}
