"""PG17 foundation checks on an explicitly disposable, TLS-enabled database.

No container fixture or schema reset is invoked. Refuse a database that already
has a nova_meta schema; clean up only the schema/role this fixture created.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import NamedTuple
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from app.core.metadata_db import (
    METADATA_DEPLOYMENT_LOCK,
    MetadataConflict,
    MetadataDatabase,
    MetadataOperationFailed,
    MetadataOutcomeUnknown,
    MetadataPoolConfig,
    MetadataReadOnlyMaintenance,
    MetadataSchemaMismatch,
    MetadataUnavailable,
)
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext

pytestmark = pytest.mark.metadata_pg


class MetadataFixture(NamedTuple):
    db: MetadataDatabase
    admin: psycopg.AsyncConnection
    dsn: str
    epoch: str

    def __repr__(self) -> str:
        return f"MetadataFixture(epoch={self.epoch!r}, credentials=REDACTED)"


@pytest_asyncio.fixture
async def metadata():
    dsn = os.environ.get("NOVA_TEST_METADATA_ADMIN_DSN")
    if not dsn:
        pytest.skip("Dedicated PG17 TLS fixture requires NOVA_TEST_METADATA_ADMIN_DSN")
    if os.environ.get("NOVA_TEST_METADATA_DISPOSABLE") != "acknowledged":
        pytest.fail("PG fixture requires an explicit disposable database acknowledgement")
    if conninfo_to_dict(dsn).get("dbname") != "nova_control":
        pytest.fail("PG fixture must select nova_control")
    role = "nova_meta_test_" + uuid4().hex[:12]
    epoch = str(uuid4())
    password = secrets.token_urlsafe(30)
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    created = False
    app = None
    try:
        if admin.info.server_version // 10000 != 17:
            pytest.fail("Metadata acceptance requires PostgreSQL 17")
        cursor = await admin.execute(
            "SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = 'nova_meta'"
        )
        if await cursor.fetchone():
            pytest.fail("nova_meta already exists; refusing to change existing metadata")
        async with admin.transaction():
            await admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role),
                    sql.Literal(password),
                )
            )
            await admin.execute("CREATE SCHEMA nova_meta")
            await admin.execute(
                sql.SQL("COMMENT ON SCHEMA nova_meta IS {}").format(sql.Literal(epoch))
            )
            await admin.execute("""
                CREATE TABLE nova_meta.deployment_state (
                    singleton boolean PRIMARY KEY CHECK (singleton),
                    schema_version bigint NOT NULL,
                    metadata_epoch uuid NOT NULL,
                    maintenance boolean NOT NULL DEFAULT false
                );
                CREATE TABLE nova_meta.schema_migrations (
                    version bigint PRIMARY KEY, name text NOT NULL, checksum text NOT NULL
                );
                CREATE TABLE nova_meta.test_owned_rows (
                    id integer PRIMARY KEY,
                    owner_name text NOT NULL,
                    active_role text NOT NULL,
                    revision bigint NOT NULL DEFAULT 1,
                    value integer NOT NULL DEFAULT 0
                );
                ALTER TABLE nova_meta.test_owned_rows ENABLE ROW LEVEL SECURITY;
                ALTER TABLE nova_meta.test_owned_rows FORCE ROW LEVEL SECURITY;
                CREATE POLICY actor_scope ON nova_meta.test_owned_rows
                    USING (owner_name = current_setting('nova.principal', true)
                           AND active_role = current_setting('nova.active_role', true))
                    WITH CHECK (owner_name = current_setting('nova.principal', true)
                                AND active_role = current_setting('nova.active_role', true));
            """)
            await admin.execute(
                "INSERT INTO nova_meta.deployment_state VALUES (true, 1, %s, false)", (epoch,)
            )
            for migration in load_migrations():
                await admin.execute(
                    "INSERT INTO nova_meta.schema_migrations VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
            await admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA nova_meta TO {}").format(sql.Identifier(role))
            )
            await admin.execute(
                sql.SQL(
                    "GRANT SELECT ON nova_meta.deployment_state, "
                    "nova_meta.schema_migrations TO {}; "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON nova_meta.test_owned_rows TO {}"
                ).format(sql.Identifier(role), sql.Identifier(role))
            )
        created = True
        app_dsn = make_conninfo(dsn, user=role, password=password)
        app = MetadataDatabase(MetadataPoolConfig(app_dsn, max_size=2, acquire_timeout=2))
        await app.open(schema_version=1, metadata_epoch=epoch)
        yield MetadataFixture(app, admin, app_dsn, epoch)
    finally:
        if app:
            await app.close()
        if created:
            cursor = await admin.execute(
                "SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace "
                "WHERE nspname = 'nova_meta'"
            )
            marker = await cursor.fetchone()
            if marker != (epoch,):
                pytest.fail("Fixture ownership changed; refusing cleanup")
            await admin.execute("DROP SCHEMA nova_meta CASCADE")
            await admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        await admin.close()


def actor(name="alice", role="analyst"):
    return SecurityContext(principal=name, active_role=role)


async def insert(db, row_id=1):
    async with db.transaction(actor()) as conn:
        await conn.execute(
            "INSERT INTO nova_meta.test_owned_rows(id, owner_name, active_role) "
            "VALUES (%s, %s, %s)",
            (row_id, "alice", "analyst"),
        )


async def test_actor_context_is_transaction_local_and_rls_is_fail_closed(metadata):
    db, _, dsn, _ = metadata
    await insert(db)
    for identity, count in [
        (actor(), 1),
        (actor("bob"), 0),
        (actor(role="finance"), 0),
        (actor(), 1),
    ]:
        async with db.transaction(identity) as conn:
            cursor = await conn.execute("SELECT count(*) AS count FROM nova_meta.test_owned_rows")
            assert (await cursor.fetchone())["count"] == count
            cursor = await conn.execute("SHOW timezone")
            assert (await cursor.fetchone())["TimeZone"] == "UTC"
    async with db._pool.connection() as reused:
        cursor = await reused.execute(
            "SELECT current_setting('nova.principal', true) AS principal, "
            "(SELECT count(*) FROM nova_meta.test_owned_rows) AS visible"
        )
        clean = await cursor.fetchone()
        assert not clean["principal"] and clean["visible"] == 0
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as raw:
        cursor = await raw.execute("SELECT count(*) FROM nova_meta.test_owned_rows")
        assert await cursor.fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await raw.execute(
                "INSERT INTO nova_meta.test_owned_rows VALUES (2, 'alice', 'analyst', 1, 0)"
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await raw.execute("CREATE TABLE nova_meta.forbidden(id integer)")


async def test_concurrent_revision_update_has_one_winner(metadata):
    db = metadata[0]
    await insert(db)
    ready = 0
    barrier = asyncio.Event()

    async def change(value):
        nonlocal ready
        async with db.transaction(actor()) as conn:
            ready += 1
            if ready == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), timeout=5)
            cursor = await conn.execute(
                "UPDATE nova_meta.test_owned_rows SET value=%s, revision=revision+1 "
                "WHERE id=1 AND revision=1 RETURNING revision",
                (value,),
            )
            return await cursor.fetchone()

    outcomes = await asyncio.gather(change(10), change(20))
    assert sum(result is not None for result in outcomes) == 1
    async with db.transaction(actor()) as conn:
        cursor = await conn.execute("SELECT revision, value FROM nova_meta.test_owned_rows")
        row = await cursor.fetchone()
        assert row["revision"] == 2 and row["value"] in {10, 20}


async def test_unique_conflict_and_application_exception_rollback(metadata):
    db = metadata[0]
    await insert(db)
    with pytest.raises(MetadataConflict):
        await insert(db)
    with pytest.raises(RuntimeError, match="abort fixture"):
        async with db.transaction(actor()) as conn:
            await conn.execute("UPDATE nova_meta.test_owned_rows SET value=99 WHERE id=1")
            raise RuntimeError("abort fixture")
    async with db.transaction(actor()) as conn:
        cursor = await conn.execute("SELECT value FROM nova_meta.test_owned_rows WHERE id=1")
        assert (await cursor.fetchone())["value"] == 0


async def test_wrong_epoch_refuses_startup(metadata):
    _, _, dsn, _ = metadata
    wrong = MetadataDatabase(MetadataPoolConfig(dsn))
    with pytest.raises(MetadataSchemaMismatch):
        await wrong.open(schema_version=1, metadata_epoch=str(uuid4()))
    with pytest.raises(MetadataUnavailable):
        async with wrong.transaction(actor()):
            pytest.fail("Mismatched epoch served a transaction")


async def test_schema_create_privilege_refuses_application_startup(metadata):
    _, admin, dsn, epoch = metadata
    role = conninfo_to_dict(dsn)["user"]
    await admin.execute(
        sql.SQL("GRANT CREATE ON SCHEMA nova_meta TO {}").format(sql.Identifier(role))
    )
    privileged = MetadataDatabase(MetadataPoolConfig(dsn))
    with pytest.raises(MetadataSchemaMismatch):
        await privileged.open(schema_version=1, metadata_epoch=epoch)


@pytest.mark.parametrize("inherited_role", ["pg_read_all_data", "pg_write_all_data"])
async def test_broad_inherited_database_privileges_refuse_startup(metadata, inherited_role):
    _, admin, dsn, epoch = metadata
    role = conninfo_to_dict(dsn)["user"]
    await admin.execute(sql.SQL("GRANT {} TO {}").format(
        sql.Identifier(inherited_role), sql.Identifier(role),
    ))
    app = MetadataDatabase(MetadataPoolConfig(dsn))
    with pytest.raises(MetadataSchemaMismatch):
        await app.open(schema_version=1, metadata_epoch=epoch)


async def test_database_create_grant_refuses_startup(metadata):
    _, admin, dsn, epoch = metadata
    role = conninfo_to_dict(dsn)["user"]
    await admin.execute(sql.SQL("GRANT CREATE ON DATABASE nova_control TO {}").format(
        sql.Identifier(role),
    ))
    try:
        app = MetadataDatabase(MetadataPoolConfig(dsn))
        with pytest.raises(MetadataSchemaMismatch):
            await app.open(schema_version=1, metadata_epoch=epoch)
    finally:
        await admin.execute(sql.SQL("REVOKE CREATE ON DATABASE nova_control FROM {}").format(
            sql.Identifier(role),
        ))


async def test_statement_timeout_rolls_back_and_pool_recovers(metadata):
    _, _, dsn, epoch = metadata
    bounded = MetadataDatabase(MetadataPoolConfig(dsn, statement_timeout_ms=25))
    await bounded.open(schema_version=1, metadata_epoch=epoch)
    try:
        with pytest.raises(MetadataUnavailable):
            async with bounded.transaction(actor()) as conn:
                await conn.execute("SELECT pg_sleep(1)")
        async with bounded.transaction(actor()) as conn:
            cursor = await conn.execute("SELECT 1 AS ok")
            assert (await cursor.fetchone())["ok"] == 1
    finally:
        await bounded.close()


async def test_lost_connection_is_uncertain_and_never_retried(metadata):
    db, admin, _, _ = metadata
    invocations = 0
    with pytest.raises(MetadataOutcomeUnknown):
        async with db.transaction(actor()) as conn:
            invocations += 1
            await conn.execute(
                "INSERT INTO nova_meta.test_owned_rows VALUES (1, 'alice', 'analyst', 1, 0)"
            )
            await admin.execute("SELECT pg_terminate_backend(%s)", (conn.info.backend_pid,))
            await conn.execute("SELECT 1")
    assert invocations == 1
    cursor = await admin.execute("SELECT count(*) FROM nova_meta.test_owned_rows")
    assert await cursor.fetchone() == (0,)


async def test_database_diagnostics_do_not_expose_secret_bind_values(metadata):
    db = metadata[0]
    canary = "synthetic-secret-canary"
    with pytest.raises(MetadataOperationFailed) as caught:
        async with db.transaction(actor()) as conn:
            await conn.execute("SELECT %s::integer", (canary,))
    assert canary not in str(caught.value)
    assert caught.value.__suppress_context__


async def test_epoch_change_rejects_a_running_process(metadata):
    db, admin, _, _ = metadata
    await admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch = %s", (str(uuid4()),)
    )
    with pytest.raises(MetadataSchemaMismatch):
        async with db.transaction(actor()):
            pytest.fail("Stale process was allowed to write")


async def test_read_only_transaction_cannot_mutate_and_resets_context(metadata):
    db = metadata[0]
    await insert(db)
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with db.transaction(actor(), read_only=True, request_id="qa-read") as conn:
            cursor = await conn.execute("SELECT current_setting('nova.request_id') AS request_id")
            assert (await cursor.fetchone())["request_id"] == "qa-read"
            await conn.execute("DELETE FROM nova_meta.test_owned_rows")
    async with db.transaction(actor()) as conn:
        cursor = await conn.execute(
            "SELECT current_setting('nova.request_id') AS request_id, "
            "current_setting('synchronous_commit') AS synchronous_commit"
        )
        assert await cursor.fetchone() == {"request_id": "", "synchronous_commit": "on"}
        cursor = await conn.execute("SELECT count(*) AS count FROM nova_meta.test_owned_rows")
        assert (await cursor.fetchone())["count"] == 1


async def test_maintenance_waits_for_writers_and_admits_only_reads(metadata):
    db, admin, _, _ = metadata
    entered = asyncio.Event()
    release = asyncio.Event()

    async def existing_writer():
        async with db.transaction(actor()) as conn:
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
            await conn.execute(
                "INSERT INTO nova_meta.test_owned_rows VALUES (1, 'alice', 'analyst', 1, 0)"
            )

    pending = asyncio.create_task(existing_writer())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with admin.transaction():
            cursor = await admin.execute(
                "SELECT pg_try_advisory_xact_lock(%s)", (METADATA_DEPLOYMENT_LOCK,)
            )
            assert await cursor.fetchone() == (False,)
        release.set()
        await pending
        async with admin.transaction():
            await admin.execute("SELECT pg_advisory_xact_lock(%s)", (METADATA_DEPLOYMENT_LOCK,))
            await admin.execute("UPDATE nova_meta.deployment_state SET maintenance = true")
        with pytest.raises(MetadataReadOnlyMaintenance):
            await insert(db, 2)
        async with db.transaction(actor(), read_only=True) as conn:
            cursor = await conn.execute("SELECT count(*) AS count FROM nova_meta.test_owned_rows")
            assert (await cursor.fetchone())["count"] == 1
    finally:
        release.set()
        await pending


async def test_typed_values_keep_precision_timezone_unicode_and_null_distinctions(metadata):
    db, admin, _, _ = metadata
    await admin.execute(
        "ALTER TABLE nova_meta.test_owned_rows "
        "ADD COLUMN document jsonb, ADD COLUMN amount numeric(38, 12), "
        "ADD COLUMN happened_at timestamptz, ADD COLUMN marker boolean, "
        "ADD COLUMN json_null jsonb, ADD COLUMN sql_null jsonb"
    )
    document = {"label": "日本語 · café · 🦉", "ordered": [3, None, 1], "empty": {}}
    amount = Decimal("12345678901234567890123456.123456789012")
    happened_at = datetime(2026, 11, 11, 17, 5, 3, 123456, tzinfo=timezone(timedelta(hours=7)))
    async with db.transaction(actor()) as conn:
        await conn.execute(
            "INSERT INTO nova_meta.test_owned_rows "
            "(id, owner_name, active_role, document, amount, happened_at, marker, json_null) "
            "VALUES (1, 'alice', 'analyst', %s, %s, %s, %s, %s)",
            (Jsonb(document), amount, happened_at, False, Jsonb(None)),
        )
    async with db.transaction(actor(), read_only=True) as conn:
        cursor = await conn.execute(
            "SELECT document, amount, happened_at, marker, json_null IS NULL AS json_missing, "
            "jsonb_typeof(json_null) AS json_type, sql_null IS NULL AS sql_missing "
            "FROM nova_meta.test_owned_rows"
        )
        row = await cursor.fetchone()
        assert row == {
            "document": document, "amount": amount, "happened_at": happened_at.astimezone(UTC),
            "marker": False, "json_missing": False, "json_type": "null", "sql_missing": True,
        }
