"""Transaction-pooling acceptance using a local pooler and the existing PG fixture."""

from __future__ import annotations

import asyncio
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.metadata_db import (
    METADATA_DEPLOYMENT_LOCK,
    MetadataDatabase,
    MetadataPoolConfig,
    MetadataTimeout,
)
from app.modules.access_control.security_context import SecurityContext
from tests.integration.build_metadata_pooler import VERSION
from tests.integration.test_metadata_pg_foundation import metadata as metadata

pytestmark = pytest.mark.metadata_pg


@dataclass(repr=False)
class PoolerFixture:
    dsn: str = field(repr=False)
    epoch: str
    log: Path
    password: str = field(repr=False)

    def database(self, **budgets):
        return MetadataDatabase(MetadataPoolConfig(self.dsn, **budgets))


@pytest_asyncio.fixture
async def pooler(metadata, tmp_path):
    binary = os.environ.get("NOVA_TEST_PGBOUNCER_BINARY")
    if not binary:
        pytest.skip("PgBouncer acceptance requires the pinned NOVA_TEST_PGBOUNCER_BINARY")
    version = await asyncio.create_subprocess_exec(
        binary, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(version.communicate(), timeout=5)
    assert version.returncode == 0
    assert output.startswith(f"PgBouncer {VERSION}\n".encode())
    parameters = conninfo_to_dict(metadata.dsn)
    assert parameters["host"] in {"localhost", "127.0.0.1"}
    assert parameters["dbname"] == "nova_control" and parameters["sslmode"] == "verify-full"
    backend_port = int(parameters["port"])
    assert 1 <= backend_port <= 65535
    certificate = Path(parameters["sslrootcert"])
    private_key = certificate.with_name("server.key")
    assert certificate.is_file() and private_key.is_file()
    cursor = await metadata.admin.execute(
        "SELECT rolpassword FROM pg_authid WHERE rolname=%s", (parameters["user"],),
    )
    verifier = (await cursor.fetchone())[0]
    assert verifier.startswith("SCRAM-SHA-256$"), "Fixture login must use SCRAM"
    auth_file = tmp_path / "users.txt"
    auth_file.write_text(f'"{parameters["user"]}" "{verifier}"\n')
    auth_file.chmod(0o600)
    with socket.socket() as allocation:
        allocation.bind(("127.0.0.1", 0))
        port = allocation.getsockname()[1]
    configuration = tmp_path / "pgbouncer.ini"
    configuration.write_text(f"""[databases]
nova_control = host={parameters['host']} port={backend_port} dbname=nova_control
[pgbouncer]
listen_addr = 127.0.0.1
listen_port = {port}
unix_socket_dir =
auth_type = scram-sha-256
auth_file = {auth_file}
pool_mode = transaction
default_pool_size = 1
max_db_connections = 1
max_client_conn = 12
max_prepared_statements = 0
ignore_startup_parameters = options,extra_float_digits,search_path
server_reset_query =
server_reset_query_always = 0
query_timeout = 5
query_wait_timeout = 2
server_connect_timeout = 3
server_login_retry = 1
server_tls_sslmode = verify-full
server_tls_ca_file = {certificate}
client_tls_sslmode = require
client_tls_key_file = {private_key}
client_tls_cert_file = {certificate}
log_connections = 0
log_disconnections = 0
""")
    configuration.chmod(0o600)
    dsn = make_conninfo(metadata.dsn, host="localhost", hostaddr="127.0.0.1", port=port)
    log = tmp_path / "pgbouncer.log"
    with log.open("wb") as output_file:
        process = await asyncio.create_subprocess_exec(
            binary, str(configuration), stdout=output_file, stderr=asyncio.subprocess.STDOUT,
            env={"PATH": os.defpath, "LANG": "C"},
        )
        try:
            async with asyncio.timeout(10):
                while True:
                    assert process.returncode is None, "PgBouncer exited; inspect private log"
                    try:
                        async with await psycopg.AsyncConnection.connect(dsn, connect_timeout=1):
                            break
                    except psycopg.OperationalError:
                        await asyncio.sleep(0.05)
            yield PoolerFixture(dsn, metadata.epoch, log, parameters["password"])
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            contents = log.read_text()
            clean = all(secret not in contents for secret in (
                parameters["password"], verifier, "synthetic-wrong-pooler-password",
            ))
            auth_file.unlink()
            assert clean, "PgBouncer logged a credential canary"


async def test_transaction_pooling_reuses_one_backend_without_actor_leakage(pooler):
    first, second = pooler.database(max_size=2), pooler.database(max_size=2)
    try:
        await first.open(schema_version=1, metadata_epoch=pooler.epoch)
        await second.open(schema_version=1, metadata_epoch=pooler.epoch)
        alice = SecurityContext("alice", "analyst", security_context_version=7)
        bob = SecurityContext("bob", "finance", security_context_version=42)
        async with first.transaction(alice) as conn:
            await conn.execute(
                "INSERT INTO nova_meta.test_owned_rows VALUES (1,'alice','analyst',1,0)"
            )

        async def inspect(index):
            actor = alice if index % 2 == 0 else bob
            database = first if index % 3 else second
            async with database.transaction(actor, read_only=True) as conn:
                cursor = await conn.execute(
                    "SELECT pg_backend_pid() AS pid, "
                    "current_setting('nova.principal') AS principal, "
                    "current_setting('nova.active_role') AS role, "
                    "current_setting('nova.security_context_version') AS version, "
                    "(SELECT count(*) FROM nova_meta.test_owned_rows) AS visible"
                )
                row = await cursor.fetchone()
                assert row["principal"] == actor.principal
                assert row["role"] == actor.active_role
                assert row["version"] == str(actor.security_context_version)
                assert row["visible"] == (1 if actor == alice else 0)
                return row["pid"]

        assert len(set(await asyncio.gather(*(inspect(i) for i in range(12))))) == 1
        async with await psycopg.AsyncConnection.connect(pooler.dsn, autocommit=True) as unscoped:
            cursor = await unscoped.execute(
                "SELECT current_setting('nova.principal',true), "
                "(SELECT count(*) FROM nova_meta.test_owned_rows)"
            )
            principal, visible = await cursor.fetchone()
            assert not principal and visible == 0
    finally:
        await first.close()
        await second.close()


async def test_transaction_deadlines_survive_ignored_startup_options(pooler, metadata):
    database = pooler.database(statement_timeout_ms=30, lock_timeout_ms=20)
    await database.open(schema_version=1, metadata_epoch=pooler.epoch)
    try:
        with pytest.raises(MetadataTimeout):
            async with database.transaction(SecurityContext("alice", "analyst")) as conn:
                await conn.execute("SELECT pg_sleep(1)")
        async with metadata.admin.transaction():
            await metadata.admin.execute(
                "SELECT pg_advisory_xact_lock(%s)", (METADATA_DEPLOYMENT_LOCK,),
            )
            with pytest.raises(MetadataTimeout):
                async with database.transaction(SecurityContext("alice", "analyst")):
                    pytest.fail("A transaction passed an exclusive deployment lock")
        async with database.transaction(SecurityContext("bob", "finance")) as conn:
            cursor = await conn.execute("SELECT current_setting('nova.principal') AS principal")
            assert (await cursor.fetchone())["principal"] == "bob"
    finally:
        await database.close()


async def test_startup_validation_has_a_deadline_through_the_pooler(pooler, metadata):
    database = pooler.database(acquire_timeout=0.3, statement_timeout_ms=100)
    try:
        await database._pool.open(wait=True, timeout=5)
        async with metadata.admin.transaction():
            await metadata.admin.execute(
                "LOCK nova_meta.schema_migrations IN ACCESS EXCLUSIVE MODE"
            )
            async with asyncio.timeout(2):
                with pytest.raises(MetadataTimeout):
                    await database.open(schema_version=1, metadata_epoch=pooler.epoch)
        assert not database._ready
        recovered = pooler.database()
        try:
            await recovered.open(schema_version=1, metadata_epoch=pooler.epoch)
        finally:
            await recovered.close()
    finally:
        await database.close()


async def test_cancel_rolls_back_before_a_different_actor_reuses_the_backend(pooler, metadata):
    database = pooler.database()
    await database.open(schema_version=1, metadata_epoch=pooler.epoch)
    started = asyncio.Event()

    async def pending_write():
        async with database.transaction(SecurityContext("alice", "analyst")) as conn:
            await conn.execute(
                "INSERT INTO nova_meta.test_owned_rows VALUES (1,'alice','analyst',1,0)"
            )
            started.set()
            await conn.execute("SELECT pg_sleep(20)")

    pending = asyncio.create_task(pending_write())
    try:
        async with asyncio.timeout(3):
            await started.wait()
            await asyncio.sleep(0.03)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        cursor = await metadata.admin.execute("SELECT count(*) FROM nova_meta.test_owned_rows")
        assert await cursor.fetchone() == (0,)
        async with database.transaction(SecurityContext("bob", "finance")) as conn:
            cursor = await conn.execute(
                "SELECT current_setting('nova.principal') AS principal, "
                "current_setting('nova.active_role') AS role"
            )
            assert await cursor.fetchone() == {"principal": "bob", "role": "finance"}
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await database.close()


@pytest.mark.parametrize("transport", ["plaintext", "wrong_hostname", "wrong_password"])
async def test_pooler_refuses_unverified_or_unauthenticated_clients(pooler, transport):
    changes = {
        "plaintext": {"sslmode": "disable"},
        "wrong_hostname": {"host": "nova-wrong-host.invalid"},
        "wrong_password": {"password": "synthetic-wrong-pooler-password"},
    }
    with pytest.raises(psycopg.OperationalError):
        async with await psycopg.AsyncConnection.connect(
            make_conninfo(pooler.dsn, **changes[transport]), connect_timeout=2,
        ):
            pytest.fail("An unverified/unauthenticated connection reached the database")
