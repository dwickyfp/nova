"""Workspace sharing boundaries and concurrent replacements on the real PG fixture."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
from app.modules.workspaces.metadata_sharing import WorkspaceSharingRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401

pytestmark = pytest.mark.metadata_pg
READERS = {"web", "query", "agent", "notebook"}


def session(name="workspace-alice", *, active="ANALYST", roles=None, **changes):
    return {"username": name, "active_role": active, "assigned_roles": roles or [active],
            "session_id": "login-" + name, "security_context_version": 5, **changes}


@dataclass(repr=False)
class WorkspaceFixture:
    environment: object
    epoch: str

    @asynccontextmanager
    async def transaction(self, user=None, *, workload="web", read_only=False, bound=True):
        user = user or session()
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            actor = SecurityContext.from_session(user)
            async with db.transaction(actor, read_only=read_only) as conn:
                if bound:
                    async with WorkspaceSharingRepository.bind(conn, user) as repo:
                        yield repo, conn
                else:
                    yield None, conn
        finally:
            await db.close()


@pytest_asyncio.fixture
async def workspaces(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="d" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return WorkspaceFixture(environment, epoch)


async def create(workspaces):
    async with workspaces.transaction() as (repo, _):
        return await repo.create("Project notebooks")


async def audit(workspaces):
    cursor = await workspaces.environment.admin.execute(
        "SELECT metadata_table,metadata_key,metadata_revision,action,user_name,active_role "
        "FROM nova_meta.audit_log WHERE metadata_table LIKE 'config_workspace%' ORDER BY log_id"
    )
    return await cursor.fetchall()


async def test_assigned_inactive_roles_share_files_without_changing_data_role(workspaces):
    row = await create(workspaces)
    async with workspaces.transaction() as (repo, _):
        shared = await repo.replace_shares(row["id"], {"TEAM": "view", "EDITORS": "edit"},
                                           expected_revision=1)
        assert shared["metadata_revision"] == 2
        assert (await repo.get(row["id"]))["permission"] == "owner"
    for roles, permission in [(["OTHER", "TEAM"], "view"),
                              (["OTHER", "TEAM", "EDITORS"], "edit")]:
        user = session("workspace-bob", active="OTHER", roles=roles)
        async with workspaces.transaction(user, read_only=True) as (repo, conn):
            assert (await repo.get(row["id"]))["permission"] == permission
            assert len(await repo.visible()) == 1
            assert len(await repo.shares(row["id"])) == 2
            cursor = await conn.execute("SELECT current_setting('nova.active_role') AS role")
            assert (await cursor.fetchone())["role"] == "OTHER"
    for user in (session("unshared"), session("administrator", active="ACCOUNTADMIN")):
        async with workspaces.transaction(user) as (repo, _):
            assert await repo.get(row["id"]) is None
            assert await repo.visible() == await repo.shares(row["id"]) == []
    events = await audit(workspaces)
    assert len(events) == 4
    assert {e["user_name"] for e in events} == {"workspace-alice"}
    assert {e["active_role"] for e in events} == {"ANALYST"}


@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(workspaces, workload):
    row = await create(workspaces)
    if workload not in READERS:
        with pytest.raises(MetadataPermissionDenied):
            async with workspaces.transaction(workload=workload) as (repo, _):
                await repo.get(row["id"])
        return
    async with workspaces.transaction(workload=workload, read_only=True) as (repo, _):
        assert (await repo.get(row["id"]))["owner"] == "workspace-alice"
    if workload != "web":
        with pytest.raises(MetadataPermissionDenied):
            async with workspaces.transaction(workload=workload) as (repo, _):
                await repo.rename(row["id"], "Blocked", expected_revision=1)


async def test_shared_editor_cannot_manage_or_raw_update(workspaces):
    row = await create(workspaces)
    async with workspaces.transaction() as (repo, _):
        await repo.replace_shares(row["id"], {"TEAM": "edit"}, expected_revision=1)
    user = session("workspace-bob", active="TEAM")
    async with workspaces.transaction(user) as (repo, conn):
        for call in (
            lambda: repo.rename(row["id"], "Stolen", expected_revision=2),
            lambda: repo.replace_shares(row["id"], {}, expected_revision=2),
            lambda: repo.delete(row["id"], expected_revision=2),
        ):
            with pytest.raises(MetadataPermissionDenied):
                await call()
        cursor = await conn.execute(
            "UPDATE nova_meta.config_workspaces SET name='Stolen',"
            "metadata_revision=metadata_revision+1 WHERE id=%s RETURNING id", (row["id"],),
        )
        assert await cursor.fetchall() == []
    assert len(await audit(workspaces)) == 3


async def test_concurrent_share_replacements_have_one_winner_and_no_partial_set(workspaces):
    row = await create(workspaces)
    ready = asyncio.Barrier(2)

    async def replace(role):
        async with workspaces.transaction() as (repo, _):
            await ready.wait()
            return await repo.replace_shares(row["id"], {role: "view"}, expected_revision=1)

    results = await asyncio.gather(replace("TEAM_A"), replace("TEAM_B"), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    async with workspaces.transaction() as (repo, _):
        shares = await repo.shares(row["id"])
        assert len(shares) == 1 and shares[0]["role"] in {"TEAM_A", "TEAM_B"}
        assert (await repo.get(row["id"]))["metadata_revision"] == 2
    assert len(await audit(workspaces)) == 3


async def test_revocation_tombstone_and_regrant_are_revisioned(workspaces):
    row = await create(workspaces)
    async with workspaces.transaction() as (repo, _):
        await repo.replace_shares(row["id"], {"TEAM": "edit"}, expected_revision=1)
        await repo.replace_shares(row["id"], {}, expected_revision=2)
    async with workspaces.transaction(session("workspace-bob", active="TEAM")) as (repo, _):
        assert await repo.visible() == []
    async with workspaces.transaction() as (repo, _):
        await repo.replace_shares(row["id"], {"TEAM": "view"}, expected_revision=3)
        assert (await repo.shares(row["id"]))[0]["metadata_revision"] == 3
        await repo.delete(row["id"], expected_revision=4)
        assert await repo.get(row["id"]) is None
        assert await repo.shares(row["id"]) == []
    cursor = await workspaces.environment.admin.execute(
        "SELECT is_deleted,metadata_revision FROM nova_meta.config_workspaces WHERE id=%s",
        (row["id"],),
    )
    assert await cursor.fetchone() == {"is_deleted": True, "metadata_revision": 5}


async def test_failed_replacement_and_audit_roll_back_together(workspaces, monkeypatch):
    row = await create(workspaces)
    async with workspaces.transaction() as (repo, conn):
        original = conn.execute

        async def fail_share(query, params=None, **kwargs):
            if isinstance(query, str) and query.startswith(
                "INSERT INTO nova_meta.config_workspace_shares"
            ):
                raise RuntimeError("synthetic persistence failure")
            return await original(query, params, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(conn, "execute", fail_share)
            with pytest.raises(RuntimeError, match="synthetic"):
                await repo.replace_shares(row["id"], {"TEAM": "view"}, expected_revision=1)
        assert (await repo.get(row["id"]))["metadata_revision"] == 1
        assert await repo.shares(row["id"]) == []
    assert len(await audit(workspaces)) == 1


@pytest.mark.parametrize("changes", [
    {"username": "another"}, {"session_id": "another-login"},
    {"security_context_version": 6}, {"active_role": "OTHER", "assigned_roles": ["OTHER"]},
])
async def test_scope_rejects_different_transaction_actor(workspaces, changes):
    async with workspaces.transaction(bound=False) as (_, conn):
        with pytest.raises(MetadataPermissionDenied):
            async with WorkspaceSharingRepository.bind(conn, session(**changes)):
                pytest.fail("mismatched session admitted")


@pytest.mark.parametrize("failure", [False, True])
async def test_scope_cleanup_and_no_use_after_scope(workspaces, failure):
    async with workspaces.transaction(bound=False) as (_, conn):
        captured = None
        try:
            async with WorkspaceSharingRepository.bind(conn, session()) as repo:
                captured = repo
                if failure:
                    raise RuntimeError("scope abort")
        except RuntimeError:
            assert failure
        cursor = await conn.execute("SELECT current_setting('nova.workspace_roles',true) AS roles")
        assert not (await cursor.fetchone())["roles"]
        with pytest.raises(ValueError, match="scope"):
            await captured.visible()


@pytest.mark.parametrize("setting,value", [
    ("nova.principal", "another"), ("nova.active_role", "OTHER"),
    ("nova.session_id", "another-login"), ("nova.security_context_version", "6"),
    ("nova.workspace_roles", '["ANALYST","UNASSIGNED"]'),
])
async def test_scope_rejects_changed_context_before_read_or_write(workspaces, setting, value):
    async with workspaces.transaction() as (repo, conn), conn.transaction():
        await conn.execute("SELECT set_config(%s,%s,true)", (setting, value))
        with pytest.raises(MetadataPermissionDenied):
            await repo.visible()
        with pytest.raises(MetadataPermissionDenied):
            await repo.create("Denied")


async def test_nested_scope_rejected_without_clearing_outer_scope(workspaces):
    async with workspaces.transaction() as (repo, conn):
        with pytest.raises(MetadataPermissionDenied):
            async with WorkspaceSharingRepository.bind(conn, session()):
                pytest.fail("nested scope admitted")
        row = await repo.create("Still authorized")
        assert await repo.get(row["id"])


@pytest.mark.parametrize("roles", ["ANALYST", [], ["ANALYST", ""], ["ANALYST", " X"],
                                 ["ANALYST", "a" * 129], ["ANALYST", 4]])
async def test_invalid_role_claims_rejected_before_scope(workspaces, roles):
    async with workspaces.transaction(bound=False) as (_, conn):
        user = {**session(), "assigned_roles": roles}
        with pytest.raises(ValueError):
            async with WorkspaceSharingRepository.bind(conn, user):
                pytest.fail("invalid role claim admitted")


@pytest.mark.parametrize("shares", [{"": "view"}, {"TEAM": "owner"}, {"TEAM": []}])
async def test_invalid_share_request_does_not_advance_revision(workspaces, shares):
    row = await create(workspaces)
    async with workspaces.transaction() as (repo, _):
        with pytest.raises(ValueError):
            await repo.replace_shares(row["id"], shares, expected_revision=1)
        assert (await repo.get(row["id"]))["metadata_revision"] == 1
    assert len(await audit(workspaces)) == 1


async def test_raw_revision_and_hard_delete_guards(workspaces):
    row = await create(workspaces)
    with pytest.raises(MetadataIntegrityViolation):
        async with workspaces.transaction() as (_, conn):
            await conn.execute(
                "UPDATE nova_meta.config_workspaces SET name='No revision' WHERE id=%s",
                (row["id"],),
            )
    for table in ("config_workspaces", "config_workspace_shares"):
        with pytest.raises(MetadataPermissionDenied):
            async with workspaces.transaction() as (_, conn):
                await conn.execute(psycopg.sql.SQL("DELETE FROM nova_meta.{}").format(
                    psycopg.sql.Identifier(table),
                ))
    assert len(await audit(workspaces)) == 1


async def test_cancelled_scope_rolls_back_mutation_and_clears_roles(workspaces):
    async with workspaces.transaction(bound=False) as (_, conn):
        with pytest.raises(asyncio.CancelledError):
            async with WorkspaceSharingRepository.bind(conn, session()) as repo:
                await repo.create("Cancelled")
                raise asyncio.CancelledError
        async with WorkspaceSharingRepository.bind(conn, session()) as repo:
            assert await repo.visible() == []
    assert await audit(workspaces) == []


async def test_maintenance_blocks_runtime_mutation(workspaces):
    row = await create(workspaces)
    await workspaces.environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET maintenance=true"
    )
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with workspaces.transaction() as (repo, _):
            await repo.rename(row["id"], "Blocked", expected_revision=1)
    async with workspaces.transaction(read_only=True) as (repo, _):
        assert await repo.get(row["id"])


async def test_import_preserves_opaque_identity_null_dates_and_requires_maintenance(workspaces):
    env = workspaces.environment
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as conn:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                await conn.execute(
                    "INSERT INTO nova_meta.config_workspaces (id,name,owner) "
                    "VALUES ('legacy','N','U')"
                )
        await env.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE nova_meta_owner")
            await conn.execute(
                "INSERT INTO nova_meta.config_workspaces (id,name,owner,created_at,updated_at) "
                "VALUES ('legacy/id','Old name','workspace-alice',NULL,NULL)"
            )
            await conn.execute(
                "INSERT INTO nova_meta.config_workspace_shares "
                "(workspace_id,role_name,permission,granted_by,granted_at) "
                "VALUES ('legacy/id','TEAM','view',NULL,NULL)"
            )
        await env.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=false")
    async with workspaces.transaction() as (repo, _):
        row = await repo.get("legacy/id")
        assert row["created_at"] is row["updated_at"] is None
        assert (await repo.shares("legacy/id"))[0]["granted_at"] is None
    assert await audit(workspaces) == []
