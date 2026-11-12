"""Workspace file entries, version reservations and receipts on the real PG fixture."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio

from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.workspaces.metadata_files import WorkspaceFileRepository
from app.modules.workspaces.metadata_sharing import WorkspaceSharingRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
ALICE = "workspace-alice"
SHA = "a" * 64


class Files:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, user=None, *, workload="web", read_only=False,
                          repository=WorkspaceFileRepository):
        user = user or session()
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            async with (
                db.transaction(SecurityContext.from_session(user), read_only=read_only) as conn,
                repository.bind(conn, user) as repo,
            ):
                yield repo, conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None


@pytest_asyncio.fixture
async def files(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="d" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Files(environment, epoch)


def key(path):
    return f"workspaces/{ALICE}/{path}"


async def create_file(files, parent="", name="report.sql", namespace=ALICE, user=None):
    async with files.transaction(user) as (repo, _):
        return await repo.insert_entry(
            namespace, parent_path=parent, name=name, entry_type="file",
            object_key=key(f"{parent}/{name}".strip("/")), size_bytes=3, etag="e1",
        )


async def shared_workspace(files, permission):
    async with files.transaction(repository=WorkspaceSharingRepository) as (repo, _):
        space = await repo.create("Team files")
        await repo.replace_shares(space["id"], {"TEAM": permission}, expected_revision=1)
    return "~ws/" + space["id"]


async def audit(files):
    return await files.admin(
        "SELECT metadata_table,object_name,action,metadata_key,user_name,active_role "
        "FROM nova_meta.audit_log WHERE metadata_table IN "
        "('config_workspace_entries','config_workspace_file_versions') ORDER BY log_id"
    )


async def test_personal_entries_are_private_and_audited_without_names(files):
    folder_name, file_name = "Secret plans", "salaries.sql"
    async with files.transaction() as (repo, _):
        folder = await repo.insert_entry(ALICE, parent_path="", name=folder_name,
                                         entry_type="folder", object_key=None)
        entry = await repo.insert_entry(
            ALICE, parent_path=folder_name, name=file_name, entry_type="file",
            object_key=key("x"), size_bytes=4, etag="e",
        )
        assert entry["path"] == f"{folder_name}/{file_name}"
        assert [row["id"] for row in await repo.list_entries(ALICE)] == [folder["id"], entry["id"]]
        assert await repo.namespace_of(entry["id"]) == ALICE
    for user in (session("workspace-bob"), session("administrator", active="ACCOUNTADMIN")):
        async with files.transaction(user) as (repo, _):
            assert await repo.list_entries(ALICE) == []
            assert await repo.namespace_of(entry["id"]) is None
            with pytest.raises(MetadataPermissionDenied):
                async with files.transaction(user) as (other, _):
                    await other.insert_entry(ALICE, parent_path="", name="planted.sql",
                                             entry_type="file", object_key=key("p"))
    events = await audit(files)
    assert [e["action"] for e in events] == ["CREATE", "CREATE"]
    assert {e["user_name"] for e in events} == {ALICE}
    text = repr(events)
    assert folder_name not in text and file_name not in text and "workspaces/" not in text


@pytest.mark.parametrize("permission", ["view", "edit"])
async def test_shared_namespace_follows_assigned_role_share(files, permission):
    namespace = await shared_workspace(files, permission)
    entry = await create_file(files, namespace=namespace)
    bob = session("workspace-bob", active="OTHER", roles=["OTHER", "TEAM"])
    async with files.transaction(bob) as (repo, conn):
        assert [row["id"] for row in await repo.list_entries(namespace)] == [entry["id"]]
        cursor = await conn.execute("SELECT current_setting('nova.active_role') AS role")
        assert (await cursor.fetchone())["role"] == "OTHER"
    if permission == "view":
        for call in (
            lambda r: r.update_file(namespace, entry["id"], expected_revision=1,
                                    object_key=key("y"), size_bytes=1, etag="e2"),
            lambda r: r.reserve_version(namespace, entry["id"], object_key_for=str),
            lambda r: r.delete(namespace, entry["id"], expected_revision=1),
            lambda r: r.insert_entry(namespace, parent_path="", name="new.sql",
                                     entry_type="file", object_key=key("n")),
        ):
            with pytest.raises(MetadataPermissionDenied):
                async with files.transaction(bob) as (repo, _):
                    await call(repo)
    else:
        async with files.transaction(bob) as (repo, _):
            updated = await repo.update_file(namespace, entry["id"], expected_revision=1,
                                             object_key=key("y"), size_bytes=1, etag="e2")
            assert updated["metadata_revision"] == 2
            reserved = await repo.reserve_version(namespace, entry["id"],
                                                  object_key_for=lambda n: key(f"v{n}"))
            assert reserved["version"] == 1 and reserved["state"] == "reserved"
    async with files.transaction(session("workspace-carol", active="TEAM2")) as (repo, _):
        assert await repo.list_entries(namespace) == []


async def test_namespace_aliases_are_refused(files):
    async with files.transaction() as (repo, _):
        for namespace in ("~ws/../" + ALICE, ALICE + "/x", "~ws/missing"):
            assert await repo.list_entries(namespace) == []
    with pytest.raises((MetadataPermissionDenied, ValueError)):
        async with files.transaction(session("~ws/abc")) as (repo, _):
            await repo.insert_entry("~ws/abc", parent_path="", name="a.sql",
                                    entry_type="file", object_key=key("a"))


@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(files, workload):
    entry = await create_file(files)
    if workload not in {"web", "query", "agent", "notebook"}:
        with pytest.raises(MetadataPermissionDenied):
            async with files.transaction(workload=workload) as (repo, _):
                await repo.get_entry(ALICE, entry["id"])
        return
    async with files.transaction(workload=workload, read_only=True) as (repo, _):
        assert (await repo.get_entry(ALICE, entry["id"]))["name"] == "report.sql"
    if workload != "web":
        with pytest.raises(MetadataPermissionDenied):
            async with files.transaction(workload=workload) as (repo, _):
                await repo.update_file(ALICE, entry["id"], expected_revision=1,
                                       object_key=key("z"), size_bytes=0, etag="e")


async def test_concurrent_saves_have_one_winner(files):
    entry = await create_file(files)
    ready = asyncio.Barrier(2)

    async def save(etag):
        async with files.transaction() as (repo, _):
            await ready.wait()
            return await repo.update_file(ALICE, entry["id"], expected_revision=1,
                                          object_key=entry["object_key"], size_bytes=1, etag=etag)

    results = await asyncio.gather(save("A"), save("B"), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    async with files.transaction() as (repo, _):
        row = await repo.get_entry(ALICE, entry["id"])
        assert row["metadata_revision"] == 2 and row["etag"] in {"A", "B"}


async def test_concurrent_reservations_receive_distinct_consecutive_numbers(files):
    entry = await create_file(files)

    async def reserve():
        async with files.transaction() as (repo, _):
            return await repo.reserve_version(ALICE, entry["id"],
                                              object_key_for=lambda n: key(f".versions/{n}"))

    rows = await asyncio.gather(*(reserve() for _ in range(6)))
    assert sorted(r["version"] for r in rows) == [1, 2, 3, 4, 5, 6]
    assert len({r["object_key"] for r in rows}) == 6
    assert {r["provenance"] for r in rows} == {"runtime"}


async def test_version_receipt_lifecycle_is_immutable(files):
    entry = await create_file(files)
    async with files.transaction() as (repo, _):
        first = await repo.reserve_version(ALICE, entry["id"], object_key_for=lambda n: key(n))
        second = await repo.reserve_version(ALICE, entry["id"], object_key_for=lambda n: key(n))
        assert await repo.list_versions(ALICE, entry["id"]) == []
        for receipt in ({"size_bytes": -1, "etag": "e", "content_sha256": SHA},
                        {"size_bytes": 1, "etag": "", "content_sha256": SHA},
                        {"size_bytes": 1, "etag": "e", "content_sha256": "A" * 64}):
            with pytest.raises(ValueError):
                await repo.publish_version(ALICE, first["id"], **receipt)
        published = await repo.publish_version(ALICE, first["id"], size_bytes=9, etag="e9",
                                               content_sha256=SHA)
        assert published["state"] == "published" and published["metadata_revision"] == 2
        await repo.abandon_version(ALICE, second["id"])
        assert [v["id"] for v in await repo.list_versions(ALICE, entry["id"])] == [first["id"]]
        assert (await repo.get_version(ALICE, entry["id"], 1))["etag"] == "e9"
        assert await repo.get_version(ALICE, entry["id"], 2) is None
        assert await repo.reserved_versions(ALICE) == []
    for call in (lambda r: r.publish_version(ALICE, first["id"], size_bytes=1, etag="x",
                                             content_sha256=SHA),
                 lambda r: r.abandon_version(ALICE, second["id"])):
        with pytest.raises(MetadataConflict):
            async with files.transaction() as (repo, _):
                await call(repo)
    with pytest.raises(MetadataIntegrityViolation):
        async with files.transaction() as (_, conn):
            await conn.execute(
                "UPDATE nova_meta.config_workspace_file_versions SET etag='forged',"
                "metadata_revision=metadata_revision+1 WHERE id=%s", (first["id"],),
            )
    with pytest.raises(MetadataIntegrityViolation):
        async with files.transaction() as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_workspace_file_versions "
                "(id,entry_id,user_name,version,object_key) VALUES (%s,%s,%s,7,'k')",
                (str(uuid4()), entry["id"], ALICE),
            )


async def test_folder_move_is_atomic_and_wildcards_do_not_overmatch(files):
    async with files.transaction() as (repo, _):
        folder = await repo.insert_entry(ALICE, parent_path="", name="a_%", entry_type="folder",
                                         object_key=None)
        await repo.insert_entry(ALICE, parent_path="a_%", name="sub", entry_type="folder",
                                object_key=None)
        inner = await repo.insert_entry(ALICE, parent_path="a_%/sub", name="q.sql",
                                        entry_type="file", object_key=key("a_%/sub/q.sql"))
        bystander = await repo.insert_entry(ALICE, parent_path="", name="abX",
                                            entry_type="folder", object_key=None)
        lookalike = await repo.insert_entry(ALICE, parent_path="abX", name="q.sql",
                                            entry_type="file", object_key=key("abX/q.sql"))
        with pytest.raises(ValueError, match="itself"):
            await repo.move(ALICE, folder["id"], expected_revision=1, new_parent_path="a_%/sub",
                            new_name="inside", object_key_for=key)
        moved, moves = await repo.move(ALICE, folder["id"], expected_revision=1,
                                       new_parent_path="archive", new_name="b",
                                       object_key_for=key)
        assert moved["path"] == "archive/b"
        assert moves == [(key("a_%/sub/q.sql"), key("archive/b/sub/q.sql"))]
        paths = {row["id"]: row["path"] for row in await repo.list_entries(ALICE)}
        assert paths[inner["id"]] == "archive/b/sub/q.sql"
        assert paths[bystander["id"]] == "abX" and paths[lookalike["id"]] == "abX/q.sql"
    with pytest.raises(MetadataConflict):
        async with files.transaction() as (repo, _):
            await repo.move(ALICE, bystander["id"], expected_revision=1,
                            new_parent_path="archive", new_name="b", object_key_for=key)


async def test_failed_subtree_move_rolls_back_every_row(files, monkeypatch):
    async with files.transaction() as (repo, _):
        folder = await repo.insert_entry(ALICE, parent_path="", name="src", entry_type="folder",
                                         object_key=None)
        child = await repo.insert_entry(ALICE, parent_path="src", name="c.sql",
                                        entry_type="file", object_key=key("src/c.sql"))
    async with files.transaction() as (repo, _):
        calls = []

        def failing_key(path):
            calls.append(path)
            raise RuntimeError("synthetic object key failure")

        with pytest.raises(RuntimeError, match="synthetic"):
            await repo.move(ALICE, folder["id"], expected_revision=1, new_parent_path="",
                            new_name="dst", object_key_for=failing_key)
        assert calls
        paths = {row["id"]: row["path"] for row in await repo.list_entries(ALICE)}
        assert paths == {folder["id"]: "src", child["id"]: "src/c.sql"}


async def test_delete_retires_subtree_and_versions_and_returns_object_keys(files):
    async with files.transaction() as (repo, _):
        folder = await repo.insert_entry(ALICE, parent_path="", name="d", entry_type="folder",
                                         object_key=None)
        entry = await repo.insert_entry(ALICE, parent_path="d", name="f.sql", entry_type="file",
                                        object_key=key("d/f.sql"))
        version = await repo.reserve_version(ALICE, entry["id"],
                                             object_key_for=lambda n: key(f"v/{n}"))
        await repo.publish_version(ALICE, version["id"], size_bytes=1, etag="e",
                                   content_sha256=SHA)
        keys = await repo.delete(ALICE, folder["id"], expected_revision=1)
        assert keys == sorted([key("d/f.sql"), key("v/1")])
        assert await repo.list_entries(ALICE) == []
        assert await repo.list_versions(ALICE, entry["id"]) == []
        again = await repo.insert_entry(ALICE, parent_path="", name="d", entry_type="folder",
                                        object_key=None)
        assert again["id"] != folder["id"]
    rows = await files.admin(
        "SELECT is_deleted,deleted_at IS NOT NULL AS stamped "
        "FROM nova_meta.config_workspace_entries WHERE id=ANY(%s)", ([folder["id"], entry["id"]],),
    )
    assert rows == [{"is_deleted": True, "stamped": True}] * 2
    for table in ("config_workspace_entries", "config_workspace_file_versions"):
        with pytest.raises(MetadataPermissionDenied):
            async with files.transaction() as (_, conn):
                await conn.execute(psycopg.sql.SQL("DELETE FROM nova_meta.{}").format(
                    psycopg.sql.Identifier(table),
                ))


@pytest.mark.parametrize("name", ["", " ", ".", "..", "a/b", "a\x00", "x" * 257])
async def test_invalid_names_are_refused_by_repository_and_database(files, name):
    async with files.transaction() as (repo, _):
        with pytest.raises(ValueError):
            await repo.insert_entry(ALICE, parent_path="", name=name, entry_type="folder",
                                    object_key=None)
    if "\x00" in name or len(name) > 256:
        return
    with pytest.raises(MetadataIntegrityViolation):
        async with files.transaction() as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_workspace_entries "
                "(id,user_name,parent_path,name,entry_type) VALUES (%s,%s,'',%s,'folder')",
                (str(uuid4()), ALICE, name),
            )


@pytest.mark.parametrize("columns", [
    {"entry_type": "link"}, {"entry_type": "folder", "object_key": "k"},
    {"entry_type": "file", "object_key": None}, {"parent_path": "/lead"},
    {"parent_path": "a//b"}, {"size_bytes": -1}, {"id": "not-a-uuid"},
])
async def test_database_refuses_inconsistent_entries(files, columns):
    row = {"id": str(uuid4()), "user_name": ALICE, "parent_path": "", "name": "n",
           "entry_type": "file", "object_key": "k", "size_bytes": 0, **columns}
    with pytest.raises(MetadataIntegrityViolation):
        async with files.transaction() as (_, conn):
            await conn.execute(
                psycopg.sql.SQL("INSERT INTO nova_meta.config_workspace_entries ({}) VALUES ({})")
                .format(psycopg.sql.SQL(",").join(map(psycopg.sql.Identifier, row)),
                        psycopg.sql.SQL(",").join(psycopg.sql.Placeholder() * len(row))),
                tuple(row.values()),
            )


async def test_duplicate_live_path_conflicts(files):
    await create_file(files)
    with pytest.raises(MetadataConflict):
        await create_file(files)


async def test_maintenance_blocks_runtime_mutation(files):
    entry = await create_file(files)
    await files.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with files.transaction() as (repo, _):
            await repo.reserve_version(ALICE, entry["id"], object_key_for=key)
    async with files.transaction(read_only=True) as (repo, _):
        assert await repo.get_entry(ALICE, entry["id"])


async def test_import_preserves_duplicate_versions_and_numbers_new_ones_above(files):
    env = files.environment
    entry_id, ids = "legacy-entry", ["legacy-v1", "legacy-v1-dup", "legacy-v2"]
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as conn:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                await conn.execute(
                    "INSERT INTO nova_meta.config_workspace_entries "
                    "(id,user_name,parent_path,name,entry_type) VALUES ('x','u','','n','folder')"
                )
        await files.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE nova_meta_owner")
            await conn.execute(
                "INSERT INTO nova_meta.config_workspace_entries (id,user_name,parent_path,name,"
                "entry_type,object_key,size_bytes,created_at,updated_at,is_deleted) "
                "VALUES (%s,%s,'','old.sql','file','k',NULL,NULL,NULL,NULL)", (entry_id, ALICE),
            )
            for identity, number in zip(ids, (1, 1, 2), strict=True):
                await conn.execute(
                    "INSERT INTO nova_meta.config_workspace_file_versions "
                    "(id,entry_id,user_name,version,object_key,size_bytes,etag,created_at) "
                    "VALUES (%s,%s,%s,%s,'old-key',NULL,NULL,NULL)",
                    (identity, entry_id, ALICE, number),
                )
        await files.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    async with files.transaction() as (repo, _):
        entry = await repo.get_entry(ALICE, entry_id)
        assert entry["created_at"] is None and entry["size_bytes"] == 0
        listed = await repo.list_versions(ALICE, entry_id)
        assert sorted(v["id"] for v in listed) == sorted(ids)
        assert {v["provenance"] for v in listed} == {"imported"}
        with pytest.raises(MetadataConflict):
            await repo.get_version(ALICE, entry_id, 1)
        assert (await repo.get_version(ALICE, entry_id, 2))["id"] == "legacy-v2"
        reserved = await repo.reserve_version(ALICE, entry_id, object_key_for=lambda n: f"k{n}")
        assert reserved["version"] == 3
    assert [e["metadata_table"] for e in await audit(files)] == ["config_workspace_file_versions"]


async def test_scope_rejects_changed_context(files):
    entry = await create_file(files)
    async with files.transaction() as (repo, conn), conn.transaction():
        await conn.execute("SELECT set_config('nova.workspace_roles','[\"ANALYST\",\"X\"]',true)")
        with pytest.raises(MetadataPermissionDenied):
            await repo.list_entries(ALICE)
        with pytest.raises(MetadataPermissionDenied):
            await repo.reserve_version(ALICE, entry["id"], object_key_for=key)
