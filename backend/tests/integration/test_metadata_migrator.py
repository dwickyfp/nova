"""Migration fault tests on the existing explicitly disposable PG17 fixture.

Never provision a server or remove pre-existing schemas/roles. The application
epoch is set only in synthetic test cases; migrate() cannot activate a cutover.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from app.core.metadata_bootstrap import ALL_ROLES
from app.core.metadata_db import (
    METADATA_DEPLOYMENT_LOCK,
    MetadataDatabase,
    MetadataOperationFailed,
    MetadataOutcomeUnknown,
    MetadataPoolConfig,
    MetadataSchemaMismatch,
    MetadataTimeout,
)
from app.core.metadata_logging import provision_role_logging
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import SchemaManifestError, load_migrations
from app.modules.access_control.security_context import SecurityContext

pytestmark = pytest.mark.metadata_pg
APP_VERSION = "a" * 40
SCHEMA_VERSION = len(load_migrations())
APPLIED_VERSIONS = tuple(range(1, SCHEMA_VERSION + 1))


@dataclass(repr=False)
class MigrationEnvironment:
    admin: psycopg.AsyncConnection
    migrator: MetadataPoolConfig = field(repr=False)
    application: MetadataPoolConfig = field(repr=False)
    directory: Path


@pytest_asyncio.fixture
async def migration_environment(tmp_path):
    dsn = os.environ.get("NOVA_TEST_METADATA_ADMIN_DSN")
    if not dsn:
        pytest.skip("Dedicated PG17 TLS fixture requires NOVA_TEST_METADATA_ADMIN_DSN")
    if os.environ.get("NOVA_TEST_METADATA_DISPOSABLE") != "acknowledged":
        pytest.fail("PG migration fixture requires disposable database acknowledgement")
    if conninfo_to_dict(dsn).get("dbname") != "nova_control":
        pytest.fail("PG migration fixture must select nova_control")
    marker = str(uuid4())
    app_role = "nova_fixture_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(30)
    created = False
    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, row_factory=dict_row,
    ) as admin:
        if admin.info.server_version // 10000 != 17:
            pytest.fail("Migration acceptance requires PostgreSQL 17")
        cursor = await admin.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='nova_meta') "
            "OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)) AS occupied",
            (list(ALL_ROLES),),
        )
        assert not (await cursor.fetchone())["occupied"], "Refusing existing metadata schema/roles"
        try:
            async with admin.transaction():
                for role in ALL_ROLES:
                    await admin.execute(
                        sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)),
                    )
                await admin.execute(
                    sql.SQL("ALTER ROLE nova_meta_migrator LOGIN PASSWORD {}").format(
                        sql.Literal(password),
                    ),
                )
                await admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(app_role), sql.Literal(password),
                ))
                await provision_role_logging(admin, "nova_meta_migrator")
                await provision_role_logging(admin, app_role)
                await admin.execute(sql.SQL("COMMENT ON ROLE nova_meta_owner IS {}").format(
                    sql.Literal(marker),
                ))
                await admin.execute(
                    "GRANT nova_meta_owner TO nova_meta_migrator WITH INHERIT FALSE, SET TRUE; "
                    "GRANT CREATE ON DATABASE nova_control TO nova_meta_owner"
                )
                await admin.execute(sql.SQL("GRANT nova_meta_app TO {}").format(
                    sql.Identifier(app_role),
                ))
            created = True
            for migration in load_migrations():
                (tmp_path / migration.name).write_text(migration.statement)
            yield MigrationEnvironment(
                admin,
                MetadataPoolConfig(make_conninfo(
                    dsn, user="nova_meta_migrator", password=password,
                )),
                MetadataPoolConfig(make_conninfo(dsn, user=app_role, password=password)),
                tmp_path,
            )
        finally:
            if created:
                cursor = await admin.execute(
                    "SELECT shobj_description(oid, 'pg_authid') AS marker "
                    "FROM pg_roles WHERE rolname='nova_meta_owner'"
                )
                assert (await cursor.fetchone())["marker"] == marker, "Fixture ownership changed"
                cursor = await admin.execute(
                    "SELECT nspowner = 'nova_meta_owner'::regrole AS owned "
                    "FROM pg_namespace WHERE nspname='nova_meta'"
                )
                schema = await cursor.fetchone()
                if schema:
                    assert schema["owned"], "Refusing cleanup of a foreign metadata schema"
                    await admin.execute("DROP SCHEMA nova_meta CASCADE")
                await admin.execute("REVOKE CREATE ON DATABASE nova_control FROM nova_meta_owner")
                # ALTER DEFAULT PRIVILEGES persists outside a schema after its removal.
                await admin.execute("DROP OWNED BY nova_meta_owner")
                for role in (app_role, *reversed(ALL_ROLES)):
                    await admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def install(env):
    return await migrate(env.migrator, app_version=APP_VERSION, directory=env.directory)


async def state(env):
    cursor = await env.admin.execute("SELECT * FROM nova_meta.deployment_state")
    return await cursor.fetchone()


async def ledger(env):
    cursor = await env.admin.execute("SELECT * FROM nova_meta.schema_migrations ORDER BY version")
    return await cursor.fetchall()


async def test_fresh_schema_has_restricted_grants_and_no_active_epoch(migration_environment):
    env = migration_environment
    result = await install(env)
    assert result.schema_version == SCHEMA_VERSION
    assert result.applied_versions == APPLIED_VERSIONS
    current = await state(env)
    assert current["maintenance"] and current["metadata_epoch"] is None
    records = await ledger(env)
    assert records[0]["checksum"] == load_migrations()[0].checksum
    assert records[0]["app_version"] == APP_VERSION
    assert records[0]["applied_at"].utcoffset().total_seconds() == 0
    cursor = await env.admin.execute(
        "SELECT bool_and(relowner = 'nova_meta_owner'::regrole) AS owned "
        "FROM pg_class WHERE relnamespace = 'nova_meta'::regnamespace"
    )
    assert (await cursor.fetchone())["owned"]
    async with await psycopg.AsyncConnection.connect(env.application.dsn, autocommit=True) as conn:
        cursor = await conn.execute("SELECT count(*) FROM nova_meta.schema_migrations")
        assert await cursor.fetchone() == (SCHEMA_VERSION,)
        for statement in (
            "UPDATE nova_meta.deployment_state SET maintenance=false",
            "DELETE FROM nova_meta.schema_migrations",
            "CREATE TABLE nova_meta.forbidden(id integer)",
            "SET ROLE nova_meta_owner",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(statement)
    app = MetadataDatabase(env.application)
    with pytest.raises(MetadataSchemaMismatch):
        await app.open(schema_version=SCHEMA_VERSION, metadata_epoch=str(uuid4()))
    cursor = await env.admin.execute(
        "SELECT has_schema_privilege('nova_meta_backup', 'nova_meta', 'USAGE') AS can_read"
    )
    assert not (await cursor.fetchone())["can_read"]


async def test_reapply_is_unchanged_and_does_not_rewrite_ledger(migration_environment):
    env = migration_environment
    await install(env)
    before = await ledger(env), await state(env)
    result = await migrate(env.migrator, app_version="b" * 40, directory=env.directory)
    assert result.applied_versions == ()
    assert (await ledger(env), await state(env)) == before


async def test_upgrade_from_deployment_only_schema_preserves_first_revision(migration_environment):
    env = migration_environment
    pending = [migration for migration in load_migrations() if migration.version > 1]
    for migration in pending:
        (env.directory / migration.name).unlink()
    result = await install(env)
    assert result.schema_version == 1 and result.applied_versions == (1,)
    first = (await ledger(env))[0]
    for migration in pending:
        (env.directory / migration.name).write_text(migration.statement)
    result = await install(env)
    assert result.applied_versions == tuple(m.version for m in pending)
    assert (await ledger(env))[0] == first
    cursor = await env.admin.execute(
        "SELECT relrowsecurity AND relforcerowsecurity AS protected FROM pg_class "
        "WHERE oid='nova_meta.config_user_preferences'::regclass"
    )
    assert (await cursor.fetchone())["protected"]
    assert (await state(env))["metadata_epoch"] is None


async def test_changed_applied_bytes_refuse_migrator_and_application(migration_environment):
    env = migration_environment
    await install(env)
    original = await ledger(env)
    path = next(env.directory.glob("*.sql"))
    path.write_text(path.read_text() + "\n-- changed revision\n")
    with pytest.raises(SchemaManifestError):
        await install(env)
    assert await ledger(env) == original
    epoch = str(uuid4())
    await env.admin.execute("UPDATE nova_meta.deployment_state SET metadata_epoch=%s", (epoch,))
    await env.admin.execute("UPDATE nova_meta.schema_migrations SET checksum=%s", ("0" * 64,))
    app = MetadataDatabase(env.application)
    with pytest.raises(MetadataSchemaMismatch):
        await app.open(schema_version=SCHEMA_VERSION, metadata_epoch=epoch)


async def test_two_migrators_publish_one_schema_revision(migration_environment):
    env = migration_environment
    results = await asyncio.gather(install(env), install(env))
    assert sorted(result.applied_versions for result in results) == [(), APPLIED_VERSIONS]
    assert len(await ledger(env)) == SCHEMA_VERSION


@pytest.mark.parametrize("failure", [
    "SELECT missing_column FROM nova_meta.deployment_state;",
    "COMMIT;", "EXECUTE 'COMMIT';",
])
async def test_partial_ddl_and_transaction_escape_roll_back(migration_environment, failure):
    env = migration_environment
    await install(env)
    before = await ledger(env), await state(env)
    (env.directory / f"{SCHEMA_VERSION + 1:04d}_atomic.sql").write_text(
        "CREATE TABLE nova_meta.atomic_probe(id integer PRIMARY KEY);\n" + failure,
    )
    with pytest.raises(MetadataOperationFailed):
        await install(env)
    assert (await ledger(env), await state(env)) == before
    cursor = await env.admin.execute("SELECT to_regclass('nova_meta.atomic_probe') AS probe")
    assert (await cursor.fetchone())["probe"] is None


async def test_lost_migration_connection_rolls_back_and_resumes(migration_environment):
    env = migration_environment
    await install(env)
    path = env.directory / f"{SCHEMA_VERSION + 1:04d}_interrupted.sql"
    statement = "CREATE TABLE nova_meta.interrupted_probe(id integer PRIMARY KEY);"
    path.write_text(statement + "\nPERFORM pg_sleep(30);")
    pending = asyncio.create_task(install(env))
    try:
        async with asyncio.timeout(5):
            while True:
                cursor = await env.admin.execute(
                    "SELECT pid FROM pg_stat_activity WHERE usename='nova_meta_migrator' "
                    "AND application_name='nova-metadata-migrator' AND wait_event='PgSleep'"
                )
                active = await cursor.fetchone()
                if active:
                    break
                await asyncio.sleep(0.02)
        await env.admin.execute("SELECT pg_terminate_backend(%s)", (active["pid"],))
        with pytest.raises(MetadataOutcomeUnknown):
            await pending
        assert len(await ledger(env)) == SCHEMA_VERSION
        cursor = await env.admin.execute(
            "SELECT to_regclass('nova_meta.interrupted_probe') AS probe",
        )
        assert (await cursor.fetchone())["probe"] is None
        path.write_text(statement)
        assert (await install(env)).applied_versions == (SCHEMA_VERSION + 1,)
        assert (await state(env))["schema_version"] == SCHEMA_VERSION + 1
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_schema_upgrade_requires_maintenance_and_preserves_epoch(migration_environment):
    env = migration_environment
    await install(env)
    epoch = str(uuid4())
    await env.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s, maintenance=false", (epoch,),
    )
    (env.directory / f"{SCHEMA_VERSION + 1:04d}_upgrade.sql").write_text(
        "CREATE TABLE nova_meta.upgrade_probe(id integer PRIMARY KEY);",
    )
    with pytest.raises(MetadataSchemaMismatch):
        await install(env)
    assert len(await ledger(env)) == SCHEMA_VERSION
    await env.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
    await install(env)
    current = await state(env)
    assert str(current["metadata_epoch"]) == epoch and current["maintenance"]
    async with await psycopg.AsyncConnection.connect(env.application.dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT * FROM nova_meta.upgrade_probe")


async def test_migration_waits_for_readers_and_lock_wait_is_bounded(migration_environment):
    env = migration_environment
    await install(env)
    epoch = str(uuid4())
    await env.admin.execute("UPDATE nova_meta.deployment_state SET metadata_epoch=%s", (epoch,))
    app = MetadataDatabase(env.application)
    await app.open(schema_version=SCHEMA_VERSION, metadata_epoch=epoch)
    try:
        async with app.transaction(SecurityContext(principal="fixture", active_role="ANALYST"),
                                   read_only=True):
            async with env.admin.transaction():
                cursor = await env.admin.execute(
                    "SELECT pg_try_advisory_xact_lock(%s) AS locked", (METADATA_DEPLOYMENT_LOCK,),
                )
                assert not (await cursor.fetchone())["locked"]
            with pytest.raises(MetadataTimeout):
                await migrate(
                    MetadataPoolConfig(env.migrator.dsn, lock_timeout_ms=50),
                    app_version=APP_VERSION, directory=env.directory,
                )
        assert (await install(env)).applied_versions == ()
    finally:
        await app.close()


@pytest.mark.parametrize("privileged", ["nova_meta_app", "nova_meta_readonly"])
async def test_privileged_runtime_role_refuses_schema_install(migration_environment, privileged):
    env = migration_environment
    await env.admin.execute(sql.SQL("ALTER ROLE {} BYPASSRLS").format(sql.Identifier(privileged)))
    with pytest.raises(MetadataSchemaMismatch):
        await install(env)
    cursor = await env.admin.execute("SELECT to_regnamespace('nova_meta') AS schema")
    assert (await cursor.fetchone())["schema"] is None


async def test_application_identity_cannot_run_migrator(migration_environment):
    env = migration_environment
    with pytest.raises(MetadataSchemaMismatch):
        await migrate(env.application, app_version=APP_VERSION, directory=env.directory)


async def test_failed_fresh_install_leaves_no_partial_schema(migration_environment):
    env = migration_environment
    path = env.directory / f"{SCHEMA_VERSION + 1:04d}_failed.sql"
    path.write_text("CREATE TABLE nova_meta.probe(id integer); COMMIT;")
    with pytest.raises(MetadataOperationFailed):
        await install(env)
    cursor = await env.admin.execute("SELECT to_regnamespace('nova_meta') AS schema")
    assert (await cursor.fetchone())["schema"] is None
    path.unlink()
    assert (await install(env)).applied_versions == APPLIED_VERSIONS


async def test_missing_ledger_never_overwrites_existing_objects(migration_environment):
    env = migration_environment
    await env.admin.execute("CREATE SCHEMA nova_meta AUTHORIZATION nova_meta_owner")
    await env.admin.execute("CREATE TABLE nova_meta.preexisting(id integer PRIMARY KEY)")
    await env.admin.execute("INSERT INTO nova_meta.preexisting VALUES (42)")
    with pytest.raises(SchemaManifestError):
        await install(env)
    cursor = await env.admin.execute("SELECT id FROM nova_meta.preexisting")
    assert await cursor.fetchall() == [{"id": 42}]


async def test_one_shot_entrypoint_installs_without_publishing_epoch(migration_environment):
    env = migration_environment
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.core.metadata_migrations", "--app-version", APP_VERSION,
        env={**os.environ, "NOVA_METADATA_MIGRATOR_DSN": env.migrator.dsn},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode == 0
    assert json.loads(stdout) == {
        "status": "SCHEMA_INSTALLED", "schema_version": SCHEMA_VERSION,
        "applied_versions": list(APPLIED_VERSIONS), "metadata_epoch_published": False,
    }
    password = conninfo_to_dict(env.migrator.dsn)["password"]
    assert password.encode() not in stdout + stderr
    assert (await state(env))["metadata_epoch"] is None
