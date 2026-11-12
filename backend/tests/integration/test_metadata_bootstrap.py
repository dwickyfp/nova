"""Provisioning acceptance on the existing, explicitly disposable PG17 fixture."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from pydantic import SecretStr

from app.core import metadata_bootstrap as module
from app.core.metadata_bootstrap import (
    ALL_ROLES,
    LOGIN_ROLES,
    WORKLOAD_ROLES,
    BootstrapCredentials,
    BootstrapVerificationFailed,
)
from app.core.metadata_db import (
    MetadataOperationFailed,
    MetadataOutcomeUnknown,
    MetadataPoolConfig,
    MetadataSchemaMismatch,
)
from app.core.metadata_logging import logging_is_private
from app.core.metadata_migrations import migrate

pytestmark = pytest.mark.metadata_pg


@dataclass(repr=False)
class Environment:
    admin: psycopg.AsyncConnection
    config: MetadataPoolConfig
    credentials: BootstrapCredentials
    deployment_id: UUID

    async def bootstrap(self):
        return await module.bootstrap(
            self.config, self.credentials, deployment_id=self.deployment_id,
        )

    def role_config(self, role):
        return MetadataPoolConfig(make_conninfo(
            self.config.dsn, user=role,
            password=self.credentials.passwords[role].get_secret_value(),
        ))

    @asynccontextmanager
    async def importer(self):
        """Load a legacy fixture through the migrator's guarded maintenance path."""
        cursor = await self.admin.execute("SELECT maintenance FROM nova_meta.deployment_state")
        previous = (await cursor.fetchone())["maintenance"]
        await self.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
        try:
            async with await psycopg.AsyncConnection.connect(
                self.role_config("nova_meta_migrator").dsn,
                autocommit=True, row_factory=dict_row,
            ) as connection, connection.transaction():
                await connection.execute("SET LOCAL ROLE nova_meta_owner")
                yield connection
        finally:
            await self.admin.execute(
                "UPDATE nova_meta.deployment_state SET maintenance=%s", (previous,),
            )


@asynccontextmanager
async def disposable_environment():
    dsn = os.environ.get("NOVA_TEST_METADATA_ADMIN_DSN")
    if not dsn:
        pytest.skip("Existing dedicated PG17 TLS fixture requires NOVA_TEST_METADATA_ADMIN_DSN")
    if os.environ.get("NOVA_TEST_METADATA_DISPOSABLE") != "acknowledged":
        pytest.fail("Disposable database acknowledgement is required")
    assert conninfo_to_dict(dsn).get("dbname") == "nova_control"
    deployment_id = uuid4()
    marker = f"nova-control-bootstrap:v1:{deployment_id}"
    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, row_factory=dict_row,
    ) as admin:
        assert admin.info.server_version // 10000 == 17
        cursor = await admin.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)) "
            "OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='nova_meta') AS occupied",
            (list(ALL_ROLES),),
        )
        assert not (await cursor.fetchone())["occupied"], "Refusing pre-existing roles/schema"
        cursor = await admin.execute(
            "SELECT shobj_description(oid,'pg_database') AS marker FROM pg_database "
            "WHERE datname=current_database()"
        )
        original_comment = (await cursor.fetchone())["marker"]
        assert original_comment is None, "Fixture must have no pre-existing deployment marker"
        cursor = await admin.execute(
            "SELECT a.privilege_type FROM pg_database d CROSS JOIN LATERAL "
            "aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a "
            "WHERE d.datname=current_database() AND a.grantee=0"
        )
        database_public = [row["privilege_type"] for row in await cursor.fetchall()]
        cursor = await admin.execute(
            "SELECT a.privilege_type FROM pg_namespace n CROSS JOIN LATERAL "
            "aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a "
            "WHERE n.nspname='public' AND a.grantee=0"
        )
        schema_public = [row["privilege_type"] for row in await cursor.fetchall()]
        credentials = BootstrapCredentials({
            role: SecretStr(secrets.token_urlsafe(32)) for role in LOGIN_ROLES
        })
        try:
            yield Environment(admin, MetadataPoolConfig(dsn), credentials, deployment_id)
        finally:
            cursor = await admin.execute(
                "SELECT rolname, shobj_description(oid,'pg_authid') AS marker FROM pg_roles "
                "WHERE rolname=ANY(%s)", (list(ALL_ROLES),),
            )
            roles = await cursor.fetchall()
            assert all(row["marker"] == marker for row in roles), "Fixture role ownership changed"
            cursor = await admin.execute(
                "SELECT r.rolname='nova_meta_owner' AS owned FROM pg_namespace n "
                "JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname='nova_meta'"
            ) if roles else None
            schema = await cursor.fetchone() if cursor else None
            if schema:
                assert schema["owned"], "Fixture schema ownership changed"
                await admin.execute("DROP SCHEMA nova_meta CASCADE")
            for row in roles:
                await admin.execute(sql.SQL("DROP OWNED BY {}").format(
                    sql.Identifier(row["rolname"]),
                ))
            for row in roles:
                await admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(row["rolname"])))
            await admin.execute("REVOKE ALL ON DATABASE nova_control FROM PUBLIC")
            await admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            for privilege in database_public:
                assert privilege in {"CONNECT", "CREATE", "TEMPORARY"}
                await admin.execute(sql.SQL("GRANT {} ON DATABASE nova_control TO PUBLIC").format(
                    sql.SQL(privilege),
                ))
            for privilege in schema_public:
                assert privilege in {"CREATE", "USAGE"}
                await admin.execute(sql.SQL("GRANT {} ON SCHEMA public TO PUBLIC").format(
                    sql.SQL(privilege),
                ))
            await admin.execute(sql.SQL("COMMENT ON DATABASE nova_control IS {}").format(
                sql.Literal(original_comment),
            ))


@pytest_asyncio.fixture
async def environment():
    async with disposable_environment() as fixture:
        yield fixture


async def test_fresh_roles_reapply_without_password_rotation_and_install_schema(environment):
    env = environment
    first = await env.bootstrap()
    assert first.created and first.login_count == 15
    before = await env.admin.execute(
        "SELECT rolname,rolpassword FROM pg_authid WHERE rolname=ANY(%s) ORDER BY rolname",
        (list(LOGIN_ROLES),),
    )
    before_rows = await before.fetchall()
    all_scram = all(row["rolpassword"].startswith("SCRAM-SHA-256$") for row in before_rows)
    assert all_scram, "A login uses an unexpected password verifier"
    second = await env.bootstrap()
    assert not second.created
    after = await env.admin.execute(
        "SELECT rolname,rolpassword FROM pg_authid WHERE rolname=ANY(%s) ORDER BY rolname",
        (list(LOGIN_ROLES),),
    )
    unchanged = before_rows == await after.fetchall()
    assert unchanged, "Reapplication changed a stored verifier"
    await migrate(env.role_config("nova_meta_migrator"), app_version="b" * 40)
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_readonly").dsn, autocommit=True,
    ) as reader:
        cursor = await reader.execute(
            "SELECT maintenance,metadata_epoch FROM nova_meta.deployment_state"
        )
        assert await cursor.fetchone() == (True, None)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await reader.execute("UPDATE nova_meta.deployment_state SET maintenance=false")
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_backup").dsn, autocommit=True,
    ) as backup:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await backup.execute("SELECT * FROM nova_meta.deployment_state")


@pytest.mark.parametrize("role", WORKLOAD_ROLES)
async def test_workload_reads_deployment_state_but_cannot_change_authority(environment, role):
    env = environment
    await env.bootstrap()
    await migrate(env.role_config("nova_meta_migrator"), app_version="b" * 40)
    async with await psycopg.AsyncConnection.connect(
        env.role_config(role).dsn, autocommit=True,
    ) as app:
        cursor = await app.execute(
            "SELECT maintenance,metadata_epoch FROM nova_meta.deployment_state"
        )
        assert await cursor.fetchone() == (True, None)
        for statement in (
            "CREATE SCHEMA forbidden", "CREATE TEMP TABLE forbidden (id int)",
            "UPDATE nova_meta.deployment_state SET maintenance=false",
            "SET ROLE nova_meta_owner", "SET ROLE nova_meta_app",
            "CREATE ROLE forbidden", "CREATE TABLE nova_meta.forbidden (id int)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await app.execute(statement)


@pytest.mark.parametrize("role", LOGIN_ROLES)
async def test_metadata_logins_cannot_enable_statement_or_error_value_logging(environment, role):
    await environment.bootstrap()
    async with await psycopg.AsyncConnection.connect(
        environment.role_config(role).dsn, autocommit=True, row_factory=dict_row,
    ) as app:
        assert await logging_is_private(app)
        for statement in (
            "SET log_min_messages = 'error'", "SET log_statement = 'all'",
            "SET log_min_duration_statement = 0", "SET log_error_verbosity = 'verbose'",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await app.execute(statement)
        assert await logging_is_private(app)


async def test_concurrent_bootstrap_has_one_creator(environment):
    results = await asyncio.gather(environment.bootstrap(), environment.bootstrap())
    assert sorted(result.created for result in results) == [False, True]


async def test_other_deployment_cannot_adopt_roles(environment):
    await environment.bootstrap()
    with pytest.raises(MetadataSchemaMismatch):
        await module.bootstrap(
            environment.config, environment.credentials, deployment_id=uuid4(),
        )


async def test_partial_existing_role_is_not_adopted(environment):
    env = environment
    marker = f"nova-control-bootstrap:v1:{env.deployment_id}"
    await env.admin.execute("CREATE ROLE nova_meta_app NOLOGIN")
    await env.admin.execute(sql.SQL("COMMENT ON ROLE nova_meta_app IS {}").format(
        sql.Literal(marker),
    ))
    with pytest.raises(MetadataSchemaMismatch):
        await env.bootstrap()
    cursor = await env.admin.execute(
        "SELECT rolname,rolcanlogin FROM pg_roles WHERE rolname=ANY(%s)", (list(ALL_ROLES),),
    )
    assert await cursor.fetchall() == [{"rolname": "nova_meta_app", "rolcanlogin": False}]


async def test_existing_schema_is_not_adopted(environment):
    await environment.admin.execute("CREATE SCHEMA nova_fixture_existing")
    try:
        with pytest.raises(MetadataSchemaMismatch):
            await environment.bootstrap()
        cursor = await environment.admin.execute(
            "SELECT COUNT(*) AS count FROM pg_roles WHERE rolname=ANY(%s)", (list(ALL_ROLES),),
        )
        assert (await cursor.fetchone())["count"] == 0
    finally:
        await environment.admin.execute("DROP SCHEMA nova_fixture_existing")


async def test_ordinary_cli_verifies_without_secret_output(environment):
    env = environment
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.core.metadata_bootstrap",
        env={
            **os.environ,
            "NOVA_METADATA_BOOTSTRAP_DSN": env.config.dsn,
            "NOVA_METADATA_BOOTSTRAP_DEPLOYMENT_ID": str(env.deployment_id),
            "NOVA_METADATA_BOOTSTRAP_ROLE_PASSWORDS": json.dumps({
                role: password.get_secret_value()
                for role, password in env.credentials.passwords.items()
            }),
        },
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()
    output = (stdout + stderr).decode()
    clean = all(password.get_secret_value() not in output
                for password in env.credentials.passwords.values())
    assert clean and env.config.dsn not in output
    assert process.returncode == 0, "Bootstrap CLI failed; output withheld for credential safety"
    assert json.loads(stdout) == {
        "status": "IDENTITIES_VERIFIED", "created": True,
        "login_count": 15, "metadata_epoch_published": False,
    }


async def test_wrong_replay_password_is_refused_without_rotation(environment):
    await environment.bootstrap()
    passwords = dict(environment.credentials.passwords)
    passwords["nova_meta_web"] = SecretStr(secrets.token_urlsafe(32))
    with pytest.raises(BootstrapVerificationFailed):
        await module.bootstrap(
            environment.config, BootstrapCredentials(passwords),
            deployment_id=environment.deployment_id,
        )
    assert not (await environment.bootstrap()).created


@pytest.mark.parametrize("drift", [
    "ALTER ROLE nova_meta_web SUPERUSER",
    "GRANT nova_meta_owner TO nova_meta_agent",
    "GRANT TEMP ON DATABASE nova_control TO PUBLIC",
    "GRANT CONNECT ON DATABASE nova_control TO pg_monitor",
    "GRANT USAGE ON SCHEMA public TO PUBLIC",
    "ALTER ROLE nova_meta_web SET statement_timeout=0",
    "ALTER ROLE nova_meta_web IN DATABASE nova_control SET search_path=public",
    "ALTER ROLE nova_meta_web RESET log_min_messages",
    "ALTER ROLE nova_meta_web SET log_min_messages=error",
    "ALTER ROLE nova_meta_web SET log_statement='all'",
    "GRANT SET ON PARAMETER log_min_messages TO nova_meta_web",
    "GRANT SET ON PARAMETER session_replication_role TO nova_meta_web",
    "GRANT ALTER SYSTEM ON PARAMETER TimeZone TO nova_meta_web",
    "ALTER ROLE nova_meta_web SET session_replication_role=replica",
])
async def test_privilege_drift_is_refused_without_silently_repairing(environment, drift):
    await environment.bootstrap()
    try:
        await environment.admin.execute(drift)
        with pytest.raises(MetadataSchemaMismatch):
            await environment.bootstrap()
    finally:
        if "pg_monitor" in drift:
            await environment.admin.execute(
                "REVOKE CONNECT ON DATABASE nova_control FROM pg_monitor"
            )


async def test_failed_setup_rolls_back_every_role_and_marker(environment, monkeypatch):
    original = module._verify_roles

    async def fail(*_args):
        raise MetadataOperationFailed()

    monkeypatch.setattr(module, "_verify_roles", fail)
    with pytest.raises(MetadataOperationFailed):
        await environment.bootstrap()
    cursor = await environment.admin.execute(
        "SELECT COUNT(*) AS count FROM pg_roles WHERE rolname=ANY(%s)", (list(ALL_ROLES),),
    )
    assert (await cursor.fetchone())["count"] == 0
    monkeypatch.setattr(module, "_verify_roles", original)
    assert (await environment.bootstrap()).created


async def test_terminated_provisioning_rolls_back_and_can_be_verified(environment, monkeypatch):
    original = module._verify_roles
    arrived = asyncio.Future()
    proceed = asyncio.Event()

    async def pause(conn, marker):
        if not arrived.done():
            arrived.set_result(conn.info.backend_pid)
            await proceed.wait()
        await original(conn, marker)

    monkeypatch.setattr(module, "_verify_roles", pause)
    task = asyncio.create_task(environment.bootstrap())
    try:
        async with asyncio.timeout(10):
            pid = await arrived
            await environment.admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
            proceed.set()
            with pytest.raises(MetadataOutcomeUnknown):
                await task
        assert (await environment.bootstrap()).created
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_legacy_fixture_preserves_unpublished_epoch_maintenance(environment):
    from app.core.metadata_migrations import migrate

    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="e" * 40)
    async with environment.importer() as connection:
        cursor = await connection.execute("SELECT current_user")
        assert (await cursor.fetchone())["current_user"] == "nova_meta_owner"
    cursor = await environment.admin.execute(
        "SELECT metadata_epoch,maintenance FROM nova_meta.deployment_state"
    )
    state = await cursor.fetchone()
    assert state["metadata_epoch"] is None and state["maintenance"] is True
