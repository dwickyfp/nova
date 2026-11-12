"""The prepared Workspace caller port against real PG and a fault-injecting object store."""

from __future__ import annotations

import asyncio
import hashlib
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio

from app.core.exceptions import StorageError
from app.core.metadata_db import MetadataDatabase
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.workspaces.metadata_files import WorkspaceFileRepository
from app.modules.workspaces.metadata_service import (
    WorkspaceMetadataService,
    bound_transaction,
)
from app.modules.workspaces.metadata_sharing import WorkspaceSharingRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
ALICE = "workspace-alice"


class MemoryObjects:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail_put = self.fail_delete = False
        self.puts = 0

    def key(self, namespace, *parts):
        return "/".join(["workspaces", namespace, *parts])

    async def put(self, key, body):
        self.puts += 1
        if self.fail_put:
            raise StorageError("synthetic object store outage", status_code=503)
        self.objects[key] = bytes(body)
        return hashlib.md5(body).hexdigest()  # noqa: S324 - mirrors an S3 ETag

    async def get(self, key):
        if key not in self.objects:
            raise StorageError("Workspace entry not found", status_code=404)
        return self.objects[key]

    async def delete(self, key):
        if self.fail_delete:
            raise StorageError("synthetic delete outage", status_code=503)
        self.objects.pop(key, None)


class Harness:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch
        self.objects = MemoryObjects()
        self._databases: list[MetadataDatabase] = []

    async def service(self, user=None):
        db = MetadataDatabase(self.environment.role_config("nova_meta_web"))
        await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
        self._databases.append(db)
        return WorkspaceMetadataService(bound_transaction(db, user or session()), self.objects)

    async def close(self):
        for db in self._databases:
            await db.close()

    async def referenced(self):
        cursor = await self.environment.admin.execute(
            "SELECT object_key FROM nova_meta.config_workspace_entries "
            "WHERE is_deleted IS NOT TRUE AND object_key IS NOT NULL UNION "
            "SELECT object_key FROM nova_meta.config_workspace_file_versions WHERE NOT is_deleted"
        )
        return {row["object_key"] for row in await cursor.fetchall()}

    async def assert_no_orphans(self):
        assert set(self.objects.objects) == await self.referenced()


@pytest_asyncio.fixture
async def harness(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="d" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    value = Harness(environment, epoch)
    try:
        yield value
    finally:
        await value.close()


async def test_save_snapshots_previous_object_without_copying_or_overwriting(harness):
    svc = await harness.service()
    entry = await svc.create_file(ALICE, "", "q.sql", "select 1")
    first_key = entry["object_key"]
    assert "q.sql" not in first_key
    saved = await svc.update_file(ALICE, entry["id"], "select 2")
    assert saved["object_key"] != first_key and saved["metadata_revision"] == 2
    assert harness.objects.objects[first_key] == b"select 1"
    versions = await svc.list_file_versions(ALICE, entry["id"])
    assert [(v["version"], v["object_key"]) for v in versions] == [(1, first_key)]
    assert versions[0]["content_sha256"] == hashlib.sha256(b"select 1").hexdigest()
    assert await svc.get_file_version(ALICE, entry["id"], 1) == "select 1"
    puts = harness.objects.puts
    unchanged = await svc.update_file(ALICE, entry["id"], "select 2")
    assert unchanged["metadata_revision"] == 2 and harness.objects.puts == puts
    assert (await svc.get_file(ALICE, entry["id"]))[1] == "select 2"
    await harness.assert_no_orphans()


async def test_concurrent_saves_from_one_revision_have_one_winner_and_no_orphan(harness):
    svc = await harness.service()
    entry = await svc.create_file(ALICE, "", "q.sql", "base")
    other = await harness.service()
    results = await asyncio.gather(
        svc.update_file(ALICE, entry["id"], "alpha"),
        other.update_file(ALICE, entry["id"], "beta"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    losers = [r for r in results if isinstance(r, StorageError)]
    assert len(losers) == 1 and losers[0].status_code == 409
    content = (await svc.get_file(ALICE, entry["id"]))[1]
    assert content in {"alpha", "beta"}
    assert [v["version"] for v in await svc.list_file_versions(ALICE, entry["id"])] == [1]
    await harness.assert_no_orphans()


async def test_stale_expected_revision_is_refused_before_any_write(harness):
    svc = await harness.service()
    entry = await svc.create_file(ALICE, "", "q.sql", "base")
    await svc.update_file(ALICE, entry["id"], "next")
    puts = harness.objects.puts
    with pytest.raises(StorageError) as error:
        await svc.update_file(ALICE, entry["id"], "stale", expected_revision=1)
    assert error.value.status_code == 409 and harness.objects.puts == puts


async def test_object_outage_changes_no_metadata(harness):
    svc = await harness.service()
    entry = await svc.create_file(ALICE, "", "q.sql", "base")
    harness.objects.fail_put = True
    for call in (lambda: svc.update_file(ALICE, entry["id"], "next"),
                 lambda: svc.create_file(ALICE, "", "new.sql", "x")):
        with pytest.raises(StorageError) as error:
            await call()
        assert error.value.status_code == 503
    assert [e["id"] for e in await svc.list_entries(ALICE)] == [entry["id"]]
    assert (await svc.get_file(ALICE, entry["id"]))[1] == "base"
    await harness.assert_no_orphans()


async def test_metadata_failures_discard_the_written_object(harness, monkeypatch):
    svc = await harness.service()
    entry = await svc.create_file(ALICE, "", "q.sql", "base")
    with pytest.raises(StorageError) as error:
        await svc.create_file(ALICE, "", "q.sql", "duplicate")
    assert error.value.status_code == 409

    async def broken(*args, **kwargs):
        raise RuntimeError("synthetic metadata failure")

    monkeypatch.setattr(WorkspaceFileRepository, "update_file", broken)
    with pytest.raises(RuntimeError, match="synthetic"):
        await svc.update_file(ALICE, entry["id"], "lost")
    monkeypatch.undo()
    assert (await svc.get_file(ALICE, entry["id"]))[1] == "base"
    await harness.assert_no_orphans()


async def test_failed_version_publication_rolls_back_the_save(harness, monkeypatch):
    svc = await harness.service()
    entry = await svc.create_file(ALICE, "", "q.sql", "base")

    async def broken(*args, **kwargs):
        raise RuntimeError("synthetic receipt failure")

    monkeypatch.setattr(WorkspaceFileRepository, "publish_version", broken)
    with pytest.raises(RuntimeError, match="synthetic"):
        await svc.update_file(ALICE, entry["id"], "next")
    monkeypatch.undo()
    saved = await svc.get_file(ALICE, entry["id"])
    assert saved[1] == "base" and saved[0]["metadata_revision"] == 1
    assert await svc.list_file_versions(ALICE, entry["id"]) == []
    await harness.assert_no_orphans()


async def test_folder_rename_moves_no_objects_and_delete_drains_after_commit(harness):
    svc = await harness.service()
    folder = await svc.create_folder(ALICE, "", "reports")
    inner = await svc.create_file(ALICE, "reports", "a.sql", "one")
    await svc.update_file(ALICE, inner["id"], "two")
    keep = await svc.create_file(ALICE, "", "keep.sql", "keep")
    before = dict(harness.objects.objects)
    renamed = await svc.rename_entry(ALICE, folder["id"], "archive", "old")
    assert renamed["path"] == "old/archive"
    assert harness.objects.objects == before
    paths = {e["id"]: e["path"] for e in await svc.list_entries(ALICE)}
    assert paths[inner["id"]] == "old/archive/a.sql"
    await svc.delete_entry(ALICE, folder["id"])
    assert set(harness.objects.objects) == {keep["object_key"]}
    await harness.assert_no_orphans()


async def test_delete_object_outage_still_retires_metadata(harness):
    svc = await harness.service()
    entry = await svc.create_file(ALICE, "", "q.sql", "base")
    harness.objects.fail_delete = True
    await svc.delete_entry(ALICE, entry["id"])
    assert await svc.list_entries(ALICE) == []
    assert set(harness.objects.objects) == {entry["object_key"]}
    assert await harness.referenced() == set()


async def test_replace_upload_keeps_identity_without_history(harness):
    svc = await harness.service()
    first = await svc.create_file_bytes(ALICE, "data", "t.csv", b"a,b\n")
    second = await svc.create_file_bytes(ALICE, "data", "t.csv", b"a,b\n1,2\n", replace=True)
    assert second["id"] == first["id"] and second["object_key"] != first["object_key"]
    assert await svc.list_file_versions(ALICE, first["id"]) == []
    assert (await svc.read_entry_bytes(ALICE, first["id"]))[1] == b"a,b\n1,2\n"
    await harness.assert_no_orphans()
    with pytest.raises(StorageError) as error:
        await svc.create_file_bytes(ALICE, "data", "t.csv", b"x")
    assert error.value.status_code == 409


async def test_shared_view_and_edit_permissions(harness):
    owner = await harness.service()
    db = MetadataDatabase(harness.environment.role_config("nova_meta_web"))
    await db.open(schema_version=len(load_migrations()), metadata_epoch=harness.epoch)
    harness._databases.append(db)
    namespaces = {}
    for permission in ("view", "edit"):
        from app.modules.access_control.security_context import SecurityContext

        user = session()
        async with (
            db.transaction(SecurityContext.from_session(user)) as conn,
            WorkspaceSharingRepository.bind(conn, user) as spaces,
        ):
            space = await spaces.create(f"Team {permission}")
            await spaces.replace_shares(space["id"], {"TEAM": permission}, expected_revision=1)
        namespaces[permission] = "~ws/" + space["id"]
    bob = await harness.service(session("workspace-bob", active="TEAM"))
    for permission, namespace in namespaces.items():
        entry = await owner.create_file(namespace, "", "shared.sql", "base")
        assert (await bob.get_file(namespace, entry["id"]))[1] == "base"
        if permission == "view":
            puts = harness.objects.puts
            with pytest.raises(StorageError) as error:
                await bob.update_file(namespace, entry["id"], "edited")
            assert error.value.status_code == 403
            assert harness.objects.puts == puts + 1  # written, then discarded
        else:
            saved = await bob.update_file(namespace, entry["id"], "edited")
            assert saved["metadata_revision"] == 2
    outsider = await harness.service(session("workspace-carol", active="OTHER"))
    with pytest.raises(StorageError) as error:
        await outsider.get_file(namespaces["edit"], entry["id"])
    assert error.value.status_code == 404
    await harness.assert_no_orphans()


async def test_legacy_object_is_kept_as_history_and_duplicates_refuse_to_open(harness):
    env = harness.environment
    legacy_key = f"workspaces/{ALICE}/legacy.sql"
    harness.objects.objects[legacy_key] = b"legacy"
    harness.objects.objects["old/v1"] = b"v1"
    await env.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE nova_meta_owner")
        await conn.execute(
            "INSERT INTO nova_meta.config_workspace_entries "
            "(id,user_name,parent_path,name,entry_type,object_key,etag) "
            "VALUES ('legacy','workspace-alice','','legacy.sql','file',%s,NULL)", (legacy_key,),
        )
        for identity in ("dup-a", "dup-b"):
            await conn.execute(
                "INSERT INTO nova_meta.config_workspace_file_versions "
                "(id,entry_id,user_name,version,object_key) "
                "VALUES (%s,'legacy','workspace-alice',1,'old/v1')", (identity,),
            )
    await env.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=false")
    svc = await harness.service()
    with pytest.raises(StorageError) as error:
        await svc.get_file_version(ALICE, "legacy", 1)
    assert error.value.status_code == 409
    saved = await svc.update_file(ALICE, "legacy", "new")
    assert harness.objects.objects[legacy_key] == b"legacy"
    record, content = await svc.get_file_version_record(ALICE, "legacy", 2)
    assert content == "legacy" and record["object_key"] == legacy_key
    digest = hashlib.sha256(b"legacy").hexdigest()
    assert record["etag"] == f"sha256:{digest}" and record["content_sha256"] == digest
    assert saved["object_key"] != legacy_key
    await harness.assert_no_orphans()
