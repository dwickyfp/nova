"""Stage registration RLS, CAS and import acceptance on the existing PG17 fixture."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio

from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import (
    METADATA_DEPLOYMENT_LOCK,
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import access_control_service
from app.modules.stages import metadata_service as module
from app.modules.stages.access import StageAccessDenied
from app.modules.stages.metadata_service import StageMetadataService
from app.modules.stages.repository import StageRepository
from app.modules.stages.schemas import StageCreate
from app.modules.stages.service import StagePathError, StageRegistrationError
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_pgbouncer import pooler as pooler

pytestmark = pytest.mark.metadata_pg
ALICE = SecurityContext("stage-alice", "ANALYST", security_context_version=7)
BOB = SecurityContext("stage-bob", "COLLEAGUE", security_context_version=11)
ADMIN = SecurityContext("stage-admin", "ACCOUNTADMIN")
READERS = {"web", "query", "proxy", "agent", "task", "ml", "notebook", "automation"}


@dataclass(repr=False)
class StageFixture:
    environment: object
    epoch: str
    decisions: list
    policy: dict

    @asynccontextmanager
    async def transaction(self, actor=ALICE, workload="web", *, read_only=False):
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            async with db.transaction(actor, read_only=read_only) as connection:
                yield StageMetadataService(connection, actor), connection
        finally:
            await db.close()


@pytest_asyncio.fixture
async def stages(environment, monkeypatch):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="c" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    decisions = []
    policy = {}

    async def effective_access(*, principal, active_role, resource):
        decisions.append((principal, active_role, resource))
        key = (principal, active_role, resource)
        if key in policy:
            result = policy[key]
            if isinstance(result, Exception):
                raise result
            return {"object_access": result}
        allowed = active_role == "ACCOUNTADMIN" or (
            principal in {ALICE.principal, BOB.principal}
            and active_role in {"ANALYST", "COLLEAGUE"} and resource == "sales.public"
        )
        return {"object_access": ["SELECT", "INSERT", "DELETE"] if allowed else []}

    def configured(name):
        if name != "synthetic-store":
            raise ValueError("Unknown fixture connection")
        return object()

    monkeypatch.setattr(access_control_service, "effective_access", effective_access)
    monkeypatch.setattr(module, "require_storage_connection", configured)
    return StageFixture(environment, epoch, decisions, policy)


def definition(**changes):
    return StageCreate(**{
        "name": "daily", "database_name": "sales", "schema_name": "public",
        "storage_connection": "synthetic-store", **changes,
    })


async def create(stages, *, actor=ALICE, **changes):
    async with stages.transaction(actor) as (service, _):
        return await service.create_stage(definition(**changes))


async def audit(stages):
    cursor = await stages.environment.admin.execute(
        "SELECT action,user_name,active_role,metadata_key,metadata_revision "
        "FROM nova_meta.audit_log WHERE metadata_table='config_stages' ORDER BY log_id"
    )
    return await cursor.fetchall()


async def raw_scope(connection, *, action="delete", database="sales", schema="public"):
    await connection.execute(
        "SELECT set_config('nova.stage_database',%s,true),set_config('nova.stage_schema',%s,true),"
        "set_config('nova.stage_action',%s,true)", (database, schema, action),
    )


async def assert_scope_empty(connection):
    cursor = await connection.execute(
        "SELECT current_setting('nova.stage_database',true) AS db,"
        "current_setting('nova.stage_schema',true) AS schema,"
        "current_setting('nova.stage_action',true) AS action"
    )
    assert not any((await cursor.fetchone()).values())
    assert await StageRepository(connection).in_scope() == []


async def test_managed_identity_cross_user_schema_access_and_atomic_audit(stages):
    row = await create(stages)
    assert row["base_prefix"] == f"nova/stages/{row['id']}"
    assert row["metadata_revision"] == 1 and row["namespace_kind"] == "MANAGED"
    assert row["created_by"] == ALICE.principal and row["created_at"].tzinfo is UTC
    for actor in (ALICE, BOB):
        async with stages.transaction(actor, read_only=True) as (service, connection):
            assert await service.get_stage(row["id"]) == row
            assert await service.list_stages() == [row]
            await assert_scope_empty(connection)
    assert await audit(stages) == [{
        "action": "CREATE", "user_name": ALICE.principal, "active_role": ALICE.active_role,
        "metadata_key": {"id": row["id"]}, "metadata_revision": 1,
    }]
    assert (BOB.principal, BOB.active_role, "sales.public") in stages.decisions


@pytest.mark.parametrize("workload", sorted(READERS))
async def test_each_stage_consumer_can_read_only_after_scope_authorization(stages, workload):
    row = await create(stages)
    async with stages.transaction(BOB, workload, read_only=True) as (service, connection):
        identities = await service.identities()
        keys = ("id", "name", "database_name", "schema_name")
        assert identities == [{key: row[key] for key in keys}]
        assert await StageRepository(connection).in_scope() == []
        assert await service.get_stage(row["id"], action="write") == row
        await assert_scope_empty(connection)


@pytest.mark.parametrize("role", [
    role for role in (*WORKLOAD_ROLES, "nova_meta_readonly", "nova_meta_backup")
    if role.removeprefix("nova_meta_") not in READERS
])
async def test_non_consumers_have_neither_payload_nor_discovery_access(stages, role):
    async with await psycopg.AsyncConnection.connect(
        stages.environment.role_config(role).dsn, autocommit=True,
    ) as connection:
        await connection.execute(
            "SELECT set_config('nova.principal','stage-admin',false),"
            "set_config('nova.active_role','ACCOUNTADMIN',false),"
            "set_config('nova.security_context_version','1',false),"
            "set_config('nova.principal_type','USER',false),"
            "set_config('nova.service_identity',current_user,false)"
        )
        for statement in (
            "SELECT * FROM nova_meta.discover_stages(NULL,NULL,NULL,NULL)",
            "SELECT * FROM nova_meta.config_stages",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await connection.execute(statement)


@pytest.mark.parametrize("workload", sorted(READERS - {"web"}))
async def test_readers_cannot_register_even_with_admin_actor(stages, workload):
    with pytest.raises(MetadataPermissionDenied):
        async with stages.transaction(ADMIN, workload) as (service, _):
            await service.create_stage(definition())
    assert await audit(stages) == []


async def test_unscoped_connection_cannot_discover_or_read(stages):
    row = await create(stages)
    async with await psycopg.AsyncConnection.connect(
        stages.environment.role_config("nova_meta_web").dsn, autocommit=True,
    ) as connection:
        cursor = await connection.execute("SELECT * FROM nova_meta.config_stages")
        assert await cursor.fetchall() == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await connection.execute("SELECT * FROM nova_meta.discover_stages(NULL,NULL,NULL,NULL)")
        with pytest.raises(ValueError, match="active metadata transaction"):
            await StageRepository(connection).get(row["id"])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await connection.execute("SET ROLE nova_meta_owner")


async def test_scope_is_not_owner_authority_and_denials_do_not_resolve_connection(
    stages, monkeypatch,
):
    row = await create(stages)
    calls = []
    monkeypatch.setattr(module, "require_storage_connection", lambda name: calls.append(name))
    for actor in (replace(ALICE, active_role="ACCOUNTADMIN_ASSIGNED"),
                  replace(ALICE, principal="outsider")):
        async with stages.transaction(actor) as (service, connection):
            assert await service.list_stages() == []
            with pytest.raises(StageAccessDenied):
                await service.get_stage(row["id"])
            with pytest.raises(StageAccessDenied):
                await service.create_stage(definition(name="refused"))
            await assert_scope_empty(connection)
    assert calls == []


@pytest.mark.parametrize("field,value", [
    ("principal", "another-user"), ("active_role", "ACCOUNTADMIN"),
    ("security_context_version", 8), ("principal_type", "SERVICE"),
    ("session_id", "synthetic-other-login"),
])
async def test_supplied_actor_must_match_the_active_uow(stages, field, value):
    async with stages.transaction() as (_, connection):
        service = StageMetadataService(connection, replace(ALICE, **{field: value}))
        with pytest.raises(MetadataPermissionDenied):
            await service.list_stages()
        with pytest.raises(MetadataPermissionDenied):
            await service.create_stage(definition())
    assert stages.decisions == []


async def test_policy_failure_and_revocation_fail_closed_without_scope_leakage(stages):
    row = await create(stages)
    key = (ALICE.principal, ALICE.active_role, "sales.public")
    async with stages.transaction() as (service, connection):
        assert await service.get_stage(row["id"]) == row
        stages.policy[key] = []
        with pytest.raises(StageAccessDenied):
            await service.get_stage(row["id"])
        stages.policy[key] = RuntimeError("Synthetic policy outage")
        with pytest.raises(RuntimeError, match="Synthetic policy outage"):
            await service.list_stages()
        await assert_scope_empty(connection)


async def test_scope_action_checks_cover_read_write_delete_and_creation(stages):
    row = await create(stages)
    stages.policy[(ALICE.principal, ALICE.active_role, "sales.public")] = ["SELECT"]
    async with stages.transaction() as (service, connection):
        assert await service.get_stage(row["id"], action="read") == row
        for action in ("write", "delete"):
            with pytest.raises(StageAccessDenied):
                await service.get_stage(row["id"], action=action)
        with pytest.raises(StageAccessDenied):
            await service.create_stage(definition(name="write-denied"))
        with pytest.raises(StageAccessDenied):
            await service.delete_stage(row["id"], expected_revision=1)
        await raw_scope(connection, action="read")
        identity = str(uuid4())
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with connection.transaction():
                await StageRepository(connection).create(
                    identity, definition(base_prefix=f"nova/stages/{identity}").model_dump(),
                    namespace_kind="MANAGED",
                )


async def test_custom_mapping_requires_exact_active_accountadmin(stages):
    with pytest.raises(StageAccessDenied):
        await create(stages, base_prefix="synthetic/existing")
    row = await create(stages, actor=ADMIN, base_prefix="/synthetic/existing/")
    assert row["base_prefix"] == "synthetic/existing" and row["namespace_kind"] == "MAPPED"
    assert row["created_by"] == ADMIN.principal


@pytest.mark.parametrize("prefix", ["/", "../foreign", "one/../foreign", r"one\foreign"])
async def test_invalid_mapping_is_rejected_before_persistence(stages, prefix):
    with pytest.raises(StagePathError):
        await create(stages, actor=ADMIN, base_prefix=prefix)
    assert await audit(stages) == []


async def test_unknown_connection_is_rejected_without_registration(stages):
    with pytest.raises(StageRegistrationError):
        await create(stages, storage_connection="not-configured")
    assert await audit(stages) == []


async def test_concurrent_name_creation_has_one_winner_and_one_audit(stages, monkeypatch):
    original = StageRepository.create
    ready = 0
    barrier = asyncio.Event()

    async def synchronized(self, *args, **kwargs):
        nonlocal ready
        ready += 1
        if ready == 2:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), timeout=3)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(StageRepository, "create", synchronized)
    result = await asyncio.gather(create(stages), create(stages), return_exceptions=True)
    assert sum(isinstance(value, dict) for value in result) == 1
    assert sum(isinstance(value, MetadataConflict) for value in result) == 1
    assert len(await audit(stages)) == 1


async def test_concurrent_delete_cas_and_name_recreation_never_reuse_namespace(stages, monkeypatch):
    row = await create(stages)
    original = StageRepository.delete
    ready = 0
    barrier = asyncio.Event()

    async def synchronized(self, *args, **kwargs):
        nonlocal ready
        ready += 1
        if ready == 2:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), timeout=3)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(StageRepository, "delete", synchronized)

    async def remove():
        async with stages.transaction() as (service, _):
            return await service.delete_stage(row["id"], expected_revision=1)

    result = await asyncio.gather(remove(), remove(), return_exceptions=True)
    assert sum(isinstance(value, dict) for value in result) == 1
    assert sum(isinstance(value, MetadataConflict) for value in result) == 1
    recreated = await create(stages)
    assert recreated["id"] != row["id"] and recreated["base_prefix"] != row["base_prefix"]
    async with stages.transaction() as (service, _):
        assert await service.get_stage(row["id"]) is None
        with pytest.raises(MetadataConflict):
            await service.delete_stage(recreated["id"], expected_revision=2)
    assert [event["action"] for event in await audit(stages)] == ["CREATE", "DELETE", "CREATE"]


async def test_rollback_and_caught_failure_clear_scope_and_audit(stages):
    with pytest.raises(RuntimeError, match="abort registration"):
        async with stages.transaction() as (service, _):
            await service.create_stage(definition())
            raise RuntimeError("abort registration")
    assert await audit(stages) == []
    async with stages.transaction() as (service, connection):
        await service.create_stage(definition())
        with pytest.raises(psycopg.errors.UniqueViolation):
            await service.create_stage(definition())
        await assert_scope_empty(connection)
        assert len(await service.list_stages()) == 1
    assert len(await audit(stages)) == 1


@pytest.mark.parametrize("statement", [
    "UPDATE nova_meta.config_stages SET base_prefix='foreign'",
    "UPDATE nova_meta.config_stages SET created_by='foreign'",
    "UPDATE nova_meta.config_stages SET database_name='foreign'",
    "DELETE FROM nova_meta.config_stages", "TRUNCATE nova_meta.config_stages",
])
async def test_runtime_cannot_repoint_reassign_or_physically_remove(stages, statement):
    await create(stages)
    with pytest.raises(MetadataPermissionDenied):
        async with stages.transaction(ADMIN) as (_, connection):
            await raw_scope(connection)
            await connection.execute(statement)


@pytest.mark.parametrize("changes", [
    {"namespace_kind": "LEGACY"}, {"base_prefix": "somebody/elses"}, {"id": "legacy-id"},
    {"name": "界" * 50}, {"database_name": " "},
])
async def test_direct_sql_cannot_bypass_namespace_identity_or_source_byte_width(stages, changes):
    identity = str(uuid4())
    data = {**definition().model_dump(), "id": identity, "base_prefix": f"nova/stages/{identity}",
            "namespace_kind": "MANAGED", **changes}
    with pytest.raises((MetadataIntegrityViolation, MetadataPermissionDenied)):
        async with stages.transaction() as (_, connection):
            await raw_scope(connection)
            await StageRepository(connection).create(
                data["id"], data, namespace_kind=data["namespace_kind"],
            )


async def test_maintenance_refuses_uow_and_direct_runtime_writes(stages):
    await stages.environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        await create(stages)
    async with stages.transaction(read_only=True) as (service, _):
        assert await service.list_stages() == []
    async with await psycopg.AsyncConnection.connect(
        stages.environment.role_config("nova_meta_web").dsn, autocommit=True,
    ) as connection:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            await connection.execute("UPDATE nova_meta.config_stages SET is_deleted=true")


@asynccontextmanager
async def importer(stages):
    async with await psycopg.AsyncConnection.connect(
        stages.environment.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as connection, connection.transaction():
        await connection.execute("SET LOCAL ROLE nova_meta_owner")
        yield connection


async def test_legacy_import_preserves_identity_empty_prefix_nullable_owner_and_time(stages):
    await stages.environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
    timestamp = datetime(2020, 2, 29, 12, 34, 56, tzinfo=UTC)
    async with importer(stages) as connection:
        for identity, name, owner, created in (("old/opaque-id", "", None, None),
                                                ("old-id-2", "daily", "old-owner", timestamp)):
            await connection.execute(
                "INSERT INTO nova_meta.config_stages "
                "(id,name,database_name,schema_name,storage_connection,base_prefix,created_by,"
                "created_at,namespace_kind) VALUES (%s,%s,'sales','public','synthetic-store','',"
                "%s,%s,'LEGACY')", (identity, name, owner, created),
            )
        cursor = await stages.environment.admin.execute(
            "SELECT pg_try_advisory_xact_lock_shared(%s) AS available", (METADATA_DEPLOYMENT_LOCK,),
        )
        assert (await cursor.fetchone())["available"] is False
    assert await audit(stages) == []
    await stages.environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET maintenance=false"
    )
    async with stages.transaction(read_only=True) as (service, _):
        rows = await service.list_stages()
    assert rows[0]["id"] == "old/opaque-id" and rows[0]["created_by"] is None
    assert rows[0]["created_at"] is None and rows[0]["base_prefix"] == ""
    assert rows[1]["created_by"] == "old-owner" and rows[1]["created_at"] == timestamp
    assert module.StageService._resolve_prefix(rows[1]) == "sales/public/daily"
    async with importer(stages) as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            await connection.execute("DELETE FROM nova_meta.config_stages")


async def test_legacy_duplicate_names_abort_import_without_coalescing(stages):
    await stages.environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(psycopg.errors.UniqueViolation):
        async with importer(stages) as connection:
            for identity in ("old-first", "old-second"):
                await connection.execute(
                    "INSERT INTO nova_meta.config_stages "
                    "(id,name,database_name,schema_name,storage_connection,base_prefix,"
                    "namespace_kind) VALUES "
                    "(%s,'daily','sales','public','old-connection','','LEGACY')",
                    (identity,),
                )
    cursor = await stages.environment.admin.execute(
        "SELECT count(*) AS count FROM nova_meta.config_stages"
    )
    assert (await cursor.fetchone())["count"] == 0


@pytest_asyncio.fixture
async def metadata(stages):
    async with await psycopg.AsyncConnection.connect(
        stages.environment.config.dsn, autocommit=True,
    ) as admin:
        yield SimpleNamespace(
            dsn=stages.environment.role_config("nova_meta_web").dsn,
            admin=admin, epoch=stages.epoch,
        )


async def test_stage_scope_does_not_leak_through_transaction_pooling(pooler, stages):
    row = await create(stages)
    db = pooler.database(max_size=2)
    await db.open(schema_version=len(load_migrations()), metadata_epoch=stages.epoch)
    pids = set()
    try:
        for actor in (ALICE, BOB, replace(ALICE, active_role="DENIED"), ALICE):
            async with db.transaction(actor, read_only=True) as connection:
                cursor = await connection.execute("SELECT pg_backend_pid() AS pid")
                pids.add((await cursor.fetchone())["pid"])
                await assert_scope_empty(connection)
                service = StageMetadataService(connection, actor)
                if actor.active_role == "DENIED":
                    with pytest.raises(StageAccessDenied):
                        await service.get_stage(row["id"])
                else:
                    assert await service.get_stage(row["id"]) == row
                await assert_scope_empty(connection)
        assert len(pids) == 1
    finally:
        await db.close()


async def test_scoped_payload_cannot_cross_database_or_schema(stages):
    own = await create(stages)
    for changes in ({"database_name": "foreign"}, {"schema_name": "private"}):
        await create(stages, actor=ADMIN, **changes)
    async with stages.transaction() as (service, connection):
        assert len(await service.identities(name="daily")) == 3
        assert await service.list_stages() == [own]
        await raw_scope(connection, action="read")
        assert await StageRepository(connection).in_scope() == [own]


@pytest.mark.parametrize("setting", [
    "nova.principal", "nova.active_role", "nova.security_context_version", "nova.principal_type",
    "nova.service_identity", "nova.stage_database", "nova.stage_schema", "nova.stage_action",
])
async def test_rls_requires_every_actor_and_scope_field(stages, setting):
    await create(stages)
    async with stages.transaction() as (_, connection):
        await raw_scope(connection, action="read")
        assert len(await StageRepository(connection).in_scope()) == 1
        await connection.execute("SELECT set_config(%s,'',true)", (setting,))
        assert await StageRepository(connection).in_scope() == []


@pytest.mark.parametrize("setting,value", [
    ("nova.security_context_version", "8"),
    ("nova.session_id", "synthetic-changed-login"),
])
async def test_actor_change_during_policy_call_cannot_use_an_old_decision(
    stages, monkeypatch, setting, value,
):
    row = await create(stages)
    async with stages.transaction() as (service, connection):
        async def changed(**kwargs):
            await connection.execute("SELECT set_config(%s,%s,true)", (setting, value))
            return {"object_access": ["ALL"]}

        monkeypatch.setattr(access_control_service, "effective_access", changed)
        with pytest.raises(MetadataPermissionDenied):
            await service.get_stage(row["id"])
        await assert_scope_empty(connection)


async def test_cancelled_command_clears_scope_and_leaves_outer_transaction_usable(
    stages, monkeypatch,
):
    entered = asyncio.Event()

    async def stalled(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(StageRepository, "create", stalled)
    async with stages.transaction() as (service, connection):
        task = asyncio.create_task(service.create_stage(definition()))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await assert_scope_empty(connection)
        assert await service.list_stages() == []
    assert await audit(stages) == []


async def test_audit_failure_rolls_back_registration(stages):
    await stages.environment.admin.execute(
        "DROP POLICY audit_trigger_insert ON nova_meta.audit_log"
    )
    with pytest.raises(MetadataPermissionDenied):
        await create(stages)
    cursor = await stages.environment.admin.execute(
        "SELECT count(*) AS count FROM nova_meta.config_stages"
    )
    assert (await cursor.fetchone())["count"] == 0


async def test_runtime_cannot_resurrect_or_revise_a_tombstone(stages):
    row = await create(stages)
    async with stages.transaction() as (service, _):
        await service.delete_stage(row["id"], expected_revision=1)
    for deleted in (False, True):
        with pytest.raises(MetadataIntegrityViolation):
            async with stages.transaction() as (_, connection):
                await raw_scope(connection)
                await connection.execute(
                    "UPDATE nova_meta.config_stages SET is_deleted=%s,metadata_revision=3 "
                    "WHERE id=%s", (deleted, row["id"]),
                )
    async with stages.transaction() as (service, _):
        assert await service.get_stage(row["id"]) is None


@pytest.mark.parametrize("prefix", ["", "/foreign", "foreign/", "a//b", "a/./b", "a/../b", r"a\b"])
async def test_database_rejects_unsafe_mapped_prefix_even_for_admin(stages, prefix):
    with pytest.raises(MetadataIntegrityViolation):
        async with stages.transaction(ADMIN) as (_, connection):
            await raw_scope(connection)
            await StageRepository(connection).create(
                str(uuid4()), definition(base_prefix=prefix).model_dump(), namespace_kind="MAPPED",
            )


async def test_database_refuses_non_admin_custom_mapping(stages):
    with pytest.raises(MetadataPermissionDenied):
        async with stages.transaction() as (_, connection):
            await raw_scope(connection)
            await StageRepository(connection).create(
                str(uuid4()), definition(base_prefix="existing/folder").model_dump(),
                namespace_kind="MAPPED",
            )


async def test_names_are_exact_case_and_sql_filters_are_parameterized(stages):
    first = await create(stages, name="daily' OR '1'='1")
    second = await create(stages, name="DAILY' OR '1'='1")
    async with stages.transaction() as (service, _):
        assert len(await service.list_stages()) == 2
        assert await service.identities(name="missing' OR '1'='1") == []
        assert (await service.identities(name=first["name"]))[0]["id"] == first["id"]
        assert (await service.identities(name=second["name"]))[0]["id"] == second["id"]


async def test_revision_eleven_upgrade_preserves_history_and_epoch(environment, tmp_path):  # noqa: F811
    await environment.bootstrap()
    for migration in load_migrations()[:11]:
        (tmp_path / migration.name).write_text(migration.statement)
    config = environment.role_config("nova_meta_migrator")
    await migrate(config, app_version="a" * 40, directory=tmp_path)
    async with await psycopg.AsyncConnection.connect(config.dsn) as connection:
        await connection.execute("SET LOCAL ROLE nova_meta_owner")
        await connection.execute(
            "INSERT INTO nova_meta.config_agent_capabilities (agent_id,owner_name,manifest) "
            "VALUES ('preserved-agent','preserved-owner','{\"ordered\":[3,1,2]}')"
        )
    epoch = uuid4()
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s", (epoch,),
    )
    result = await migrate(config, app_version="b" * 40)
    assert result.applied_versions == tuple(m.version for m in load_migrations()[11:])
    cursor = await environment.admin.execute(
        "SELECT owner_name,manifest FROM nova_meta.config_agent_capabilities "
        "WHERE agent_id='preserved-agent'"
    )
    assert await cursor.fetchone() == {
        "owner_name": "preserved-owner", "manifest": {"ordered": [3, 1, 2]},
    }
    cursor = await environment.admin.execute(
        "SELECT metadata_epoch,maintenance FROM nova_meta.deployment_state"
    )
    state = await cursor.fetchone()
    assert state["metadata_epoch"] == epoch and state["maintenance"]
    cursor = await environment.admin.execute(
        "SELECT version,checksum FROM nova_meta.schema_migrations ORDER BY version"
    )
    assert [(row["version"], row["checksum"]) for row in await cursor.fetchall()] == [
        (migration.version, migration.checksum) for migration in load_migrations()
    ]
    assert (await migrate(config, app_version="b" * 40)).applied_versions == ()
