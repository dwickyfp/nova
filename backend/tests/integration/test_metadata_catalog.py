"""Catalog metadata on the real PG fixture: CAS, sync fencing, secrets, stewardship."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.common import crypto
from app.core.config import settings
from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataOperationFailed,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.catalog.metadata_repository import CatalogMetadataRepository, link_row
from app.modules.catalog.repository import row_id
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg

SECRET_USER = "svc_reader_7f3a"
SECRET_PASSWORD = "Pa55-never-shown-91c2"
CATALOG_READERS = {"web", "query", "proxy", "agent", "maintenance", "notebook", "task"}
INVENTORY_WRITERS = {"web", "query", "proxy", "maintenance"}


class Catalogs:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, workload="web", *, name="catalog-admin", active="SECURITYADMIN",
                          read_only=False):
        user = session(name, active=active)
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            actor = SecurityContext.from_session(user)
            async with db.transaction(actor, read_only=read_only) as conn:
                yield CatalogMetadataRepository(conn, actor), conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None


@pytest_asyncio.fixture
async def catalogs(environment, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(crypto, "_fernet", None)
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="e" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Catalogs(environment, epoch)


def catalog(**changes):
    return {"name": "lake", "kind": "external", "catalog_type": "iceberg", "managed": True,
            "source": "nova", "metastore_uri": "thrift://hms:9083",
            "properties": {"region": "us-east-1"}, **changes}


async def create_catalog(catalogs, **changes):
    async with catalogs.transaction() as (repo, _):
        return await repo.create_catalog(catalog(**changes))


async def claim(catalogs, name="lake", workload="maintenance"):
    async with catalogs.transaction(workload) as (repo, _):
        return await repo.claim_sync(name)


def relation(name, database="sales", object_type="table"):
    return {"database_name": database, "object_name": name, "object_type": object_type,
            "fingerprint": "f" * 64}


# ── Catalog definitions ─────────────────────────────────────────────────────


async def test_catalog_definitions_change_only_at_the_revision_read(catalogs):
    row = await create_catalog(catalogs)
    assert row["metadata_revision"] == 1 and row["created_by"] == "catalog-admin"
    assert row["properties"] == {"region": "us-east-1"} and row["provenance"] == "runtime"
    ready = asyncio.Barrier(2)

    async def edit(comment):
        async with catalogs.transaction() as (repo, _):
            await ready.wait()
            return await repo.update_catalog("lake", {"comment": comment}, expected_revision=1)

    results = await asyncio.gather(edit("a"), edit("b"), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    with pytest.raises(MetadataIntegrityViolation):
        async with catalogs.transaction() as (_, conn):
            await conn.execute(
                "UPDATE nova_meta.catalog_catalogs SET comment='x' WHERE name='lake'")
    with pytest.raises(MetadataConflict):
        await create_catalog(catalogs)
    async with catalogs.transaction() as (repo, _):
        dropped = await repo.drop_catalog("lake", expected_revision=2)
        assert dropped["dropped_at"] is not None and dropped["metadata_revision"] == 3
        revived = await repo.update_catalog("lake", {}, expected_revision=3, revive=True)
        assert revived["dropped_at"] is None
    actions = await catalogs.admin(
        "SELECT action FROM nova_meta.audit_log WHERE metadata_table='catalog_catalogs' "
        "ORDER BY recorded_at")
    assert [a["action"] for a in actions] == ["CREATE", "UPDATE", "UPDATE", "UPDATE"]


async def test_catalog_properties_never_hold_credentials(catalogs):
    row = await create_catalog(catalogs, properties={"region": "x", "aws.s3.secret_key": "s"})
    assert row["properties"] == {"region": "x"}
    for key in ("hive.metastore.password", "azure.account.key", "AWS.S3.ACCESS_KEY", "credential"):
        with pytest.raises(MetadataIntegrityViolation):
            async with catalogs.transaction() as (_, conn):
                await conn.execute(
                    "UPDATE nova_meta.catalog_catalogs SET properties_json=%s,"
                    "metadata_revision=metadata_revision+1 WHERE name='lake'",
                    ('{"' + key + '":"v"}',))
    with pytest.raises(MetadataIntegrityViolation):
        await create_catalog(catalogs, name="other", kind="mystery")


# ── Sync fencing and inventory ──────────────────────────────────────────────


async def test_an_older_harvest_cannot_drop_what_a_newer_one_found(catalogs):
    await create_catalog(catalogs)
    old = await claim(catalogs)
    new = await claim(catalogs, workload="web")
    assert (old, new) == (1, 2)
    async with catalogs.transaction("maintenance") as (repo, _):
        await repo.upsert_objects("lake", [relation("orders")], generation=new)
    with pytest.raises(MetadataConflict):
        async with catalogs.transaction("maintenance") as (repo, _):
            await repo.mark_objects_dropped(
                "lake", [row_id("lake", "sales", "orders", "relation")], generation=old)
    with pytest.raises(MetadataConflict):
        async with catalogs.transaction("maintenance") as (repo, _):
            await repo.upsert_objects("lake", [relation("stale")], generation=old)
    async with catalogs.transaction("maintenance") as (repo, _):
        assert await repo.record_sync_status("lake", generation=old, status="error",
                                             error="late") is False
    async with catalogs.transaction("maintenance") as (repo, _):
        assert await repo.record_sync_status("lake", generation=new, status="ok") is True
        live = await repo.list_objects("lake")
        stored = await repo.get_catalog("lake")
    assert [r["object_name"] for r in live] == ["orders"]
    assert stored["status"] == "ok" and stored["last_sync_at"] is not None
    assert stored["metadata_revision"] == 1  # bookkeeping is not a definition change


async def test_a_claim_waits_for_an_in_flight_harvest(catalogs):
    await create_catalog(catalogs)
    first = await claim(catalogs)
    writing, release = asyncio.Event(), asyncio.Event()
    order = []

    async def harvest():
        async with catalogs.transaction("maintenance") as (repo, _):
            await repo.upsert_objects("lake", [relation("orders")], generation=first)
            writing.set()
            await release.wait()
            order.append("harvest committed")

    async def take_over():
        await writing.wait()
        task = asyncio.create_task(claim(catalogs, workload="web"))
        await asyncio.sleep(0.3)
        assert not task.done()  # blocked on the harvest's FOR SHARE lock
        release.set()
        generation = await task
        order.append("claimed")
        return generation

    _, second = await asyncio.gather(harvest(), take_over())
    assert order == ["harvest committed", "claimed"] and second == first + 1


async def test_inventory_rows_keep_derived_ids_and_lifecycle_times(catalogs):
    await create_catalog(catalogs)
    generation = await claim(catalogs)
    columns = [{"ordinal": 1, "column_name": "id", "data_type": "bigint", "is_nullable": False},
               {"ordinal": 2, "column_name": "total", "data_type": "decimal(10,2)"}]
    async with catalogs.transaction("maintenance") as (repo, _):
        await repo.upsert_objects("lake", [{"database_name": "sales", "object_type": "database"},
                                           relation("orders")], generation=generation)
        await repo.insert_columns("lake", [{**c, "database_name": "sales", "object_name": "orders"}
                                           for c in columns], generation=generation)
        await repo.replace_columns("lake", "sales", "orders", columns[:1], generation=generation)
        stored = await repo.list_columns("lake", "sales", "orders")
    assert [c["column_name"] for c in stored] == ["id"]
    assert stored[0]["id"] == row_id("lake", "sales", "orders", 1)
    orders = row_id("lake", "sales", "orders", "relation")
    async with catalogs.transaction("maintenance") as (repo, _):
        assert await repo.mark_objects_dropped("lake", [orders], generation=generation) == 1
        dropped = {r["id"]: r for r in await repo.list_objects("lake", include_dropped=True)}
        await repo.upsert_objects("lake", [relation("orders")], generation=generation)
        back = {r["id"]: r for r in await repo.list_objects("lake")}
    assert dropped[orders]["dropped_at"] is not None
    assert back[orders]["dropped_at"] is None
    assert back[orders]["first_seen_at"] > dropped[orders]["first_seen_at"]
    with pytest.raises(MetadataIntegrityViolation):
        async with catalogs.transaction("maintenance") as (repo, conn):
            await repo._fence("lake", generation)
            await conn.execute(
                "INSERT INTO nova_meta.catalog_objects (id,catalog_name,database_name,object_name,"
                "object_type) VALUES ('spoofed','lake','sales','x','table')")
    with pytest.raises(MetadataPermissionDenied):
        async with catalogs.transaction("maintenance") as (_, conn):
            await conn.execute("DELETE FROM nova_meta.catalog_objects")


async def test_retiring_a_dropped_catalog_is_fenced_too(catalogs):
    await create_catalog(catalogs)
    generation = await claim(catalogs)
    async with catalogs.transaction("maintenance") as (repo, _):
        await repo.upsert_objects("lake", [relation("orders")], generation=generation)
        await repo.insert_columns("lake", [{"database_name": "sales", "object_name": "orders",
                                            "ordinal": 1, "column_name": "id"}],
                                  generation=generation)
    with pytest.raises(MetadataConflict):
        async with catalogs.transaction("maintenance") as (repo, _):
            await repo.retire_catalog_objects("lake", generation=None)
    newer = await claim(catalogs)
    async with catalogs.transaction("maintenance") as (repo, _):
        assert await repo.retire_catalog_objects("lake", generation=newer) == 1
        assert await repo.catalogs_with_live_objects() == set()
        assert await repo.list_columns("lake", "sales", "orders") == []


async def test_sync_runs_are_created_once_and_finished_once(catalogs):
    await create_catalog(catalogs)
    generation = await claim(catalogs)

    async def start():
        async with catalogs.transaction("maintenance") as (repo, _):
            return await repo.create_sync_run_once({"id": "run-1", "catalog_name": "lake",
                                                    "trigger": "schedule",
                                                    "sync_generation": generation})

    results = await asyncio.gather(*(start() for _ in range(4)))
    assert sum(created for _, created in results) == 1
    async with catalogs.transaction("maintenance") as (repo, _):
        assert await repo.finish_sync_run("run-1", "ok", counts={"relations": 3}) is True
        assert await repo.finish_sync_run("run-1", "error", error="late") is False
        run = (await repo.list_sync_runs("lake"))[0]
    assert run["status"] == "ok" and run["counts"] == {"relations": 3}
    assert run["finished_at"] is not None and run["error"] is None
    with pytest.raises(MetadataConflict):
        async with catalogs.transaction("maintenance") as (repo, _):
            await repo.create_sync_run_once({"id": "run-1", "catalog_name": "other",
                                             "trigger": "manual", "sync_generation": 1})
    with pytest.raises(MetadataIntegrityViolation):
        async with catalogs.transaction("maintenance") as (_, conn):
            await conn.execute("UPDATE nova_meta.catalog_sync_runs SET status='error' "
                               "WHERE id='run-1'")
    with pytest.raises(MetadataConflict):  # a run names the current generation
        async with catalogs.transaction("maintenance") as (repo, _):
            await repo.create_sync_run_once({"id": "run-2", "catalog_name": "lake",
                                             "trigger": "manual", "sync_generation": 99})
    async with catalogs.transaction("maintenance") as (repo, _):
        await repo.create_sync_run_once({"id": "lin-1", "catalog_name": "lineage:audit",
                                         "trigger": "lineage"})
        await repo.finish_sync_run("lin-1", "ok", counts={"watermark": "2026-11-01T00:00:00+00:00"})
        assert await repo.lineage_watermark() == datetime(2026, 11, 1, tzinfo=UTC)


async def test_securables_keep_their_identity_and_tombstones(catalogs):
    await create_catalog(catalogs)
    generation = await claim(catalogs)
    root = row_id("lake", "", "", "catalog")
    database = row_id("lake", "sales", "", "database")
    table = row_id("lake", "sales", "orders", "relation")
    column = row_id("lake", "sales", "orders", "column", "id")
    rows = [
        {"id": root, "securable_type": "catalog"},
        {"id": database, "securable_type": "database", "database_name": "sales", "parent_id": root},
        {"id": table, "securable_type": "table", "database_name": "sales",
         "object_name": "orders", "parent_id": database},
        {"id": column, "securable_type": "column", "database_name": "sales",
         "object_name": "orders", "column_name": "ID", "parent_id": table, "ordinal": 1},
    ]
    async with catalogs.transaction("maintenance") as (repo, _):
        assert await repo.write_securables("lake", rows, generation=generation) == 4
        # A column's spelling may change under its case-insensitive id.
        await repo.write_securables("lake", [{**rows[3], "column_name": "id"}],
                                    generation=generation)
        await repo.write_securables("lake", [{**rows[2], "securable_type": "view"}],
                                    generation=generation)
        assert await repo.mark_securables_dropped("lake", [column], generation=generation) == 1
        children = await repo.securable_children(table)
        assert [c["column_name"] for c in children] == ["id"]
        assert children[0]["dropped_at"] is not None
        assert [r["id"] for r in await repo.search_securable_names("ORD", 10)] == [column, table]
    for bad in ({**rows[2], "id": row_id("lake", "sales", "other", "relation")},
                {**rows[3], "id": "c2", "parent_id": database},
                {**rows[1], "id": row_id("lake", "x", "", "database"), "database_name": "x",
                 "parent_id": None}):
        with pytest.raises(MetadataIntegrityViolation):
            async with catalogs.transaction("maintenance") as (repo, _):
                await repo.write_securables("lake", [bad], generation=generation)
    with pytest.raises(MetadataIntegrityViolation):
        async with catalogs.transaction("maintenance") as (repo, _):
            await repo.write_securables("lake", [{**rows[2], "securable_type": "column"}],
                                        generation=generation)


# ── Connections and credentials ─────────────────────────────────────────────


def connection(**changes):
    return {"name": "pg_sales", "connector_type": "postgres", "host": "db.internal",
            "port": 5432, "database_name": "sales", "options": {"sslmode": "require"},
            "secret_ref": "nova://pg_sales", **changes}


async def test_credentials_are_ciphertext_and_never_disclosed(catalogs):
    async with catalogs.transaction() as (repo, _):
        created = await repo.create_connection(connection())
        assert created["owner"] == "catalog-admin" and created["options"] == {"sslmode": "require"}
        assert await repo.put_credentials("pg_sales", SECRET_USER, SECRET_PASSWORD) == 1
        assert await repo.put_credentials("pg_sales", SECRET_USER, SECRET_PASSWORD) == 2
        assert await repo.put_credentials("pg_sales", "writer", SECRET_PASSWORD, write=True) == 1
        assert await repo.has_credentials("pg_sales", write=True) is True
        assert await repo.read_credentials("pg_sales") == (SECRET_USER, SECRET_PASSWORD)
    stored = await catalogs.admin("SELECT username_enc,password_enc FROM "
                                  "nova_meta.catalog_connection_credentials")
    assert all(v.startswith("enc:gAAAAA") and SECRET_PASSWORD not in v for v in stored[0].values())
    audit = await catalogs.admin("SELECT * FROM nova_meta.audit_log")
    assert audit and all(SECRET_PASSWORD not in str(r) and SECRET_USER not in str(r)
                         and "enc:" not in str(r) for r in audit)
    # Management can see that a write credential exists, never its ciphertext.
    for workload in ("web", "query", "proxy"):
        with pytest.raises(MetadataPermissionDenied):
            async with catalogs.transaction(workload, read_only=True) as (repo, _):
                await repo.read_credentials("pg_sales", write=True)
    for workload in ("notebook", "task"):
        async with catalogs.transaction(workload, read_only=True) as (repo, _):
            assert await repo.read_credentials("pg_sales", write=True) == ("writer",
                                                                         SECRET_PASSWORD)
            assert (await repo.get_connection("pg_sales"))["host"] == "db.internal"
        with pytest.raises(MetadataPermissionDenied):
            async with catalogs.transaction(workload, read_only=True) as (repo, _):
                await repo.read_credentials("pg_sales")
    async with catalogs.transaction("maintenance", read_only=True) as (repo, _):
        assert await repo.read_credentials("pg_sales") == (SECRET_USER, SECRET_PASSWORD)
    with pytest.raises(MetadataPermissionDenied):
        async with catalogs.transaction("agent", read_only=True) as (repo, _):
            await repo.get_connection("pg_sales")


async def test_plaintext_or_orphan_credentials_are_refused_without_echo(catalogs):
    async with catalogs.transaction() as (repo, _):
        await repo.create_connection(connection())
    for name, value in (("pg_sales", SECRET_PASSWORD), ("pg_sales", "enc:" + SECRET_PASSWORD),
                        ("missing", "enc:gAAAAABfake")):
        with pytest.raises(MetadataIntegrityViolation) as caught:
            async with catalogs.transaction() as (_, conn):
                await conn.execute(
                    "INSERT INTO nova_meta.catalog_connection_credentials "
                    "(connection_name,username_enc,password_enc) VALUES (%s,%s,%s)",
                    (name, value, value))
        assert SECRET_PASSWORD not in f"{caught.value!r} {caught.value}"
        assert caught.value.__cause__ is None


async def test_connection_changes_and_deletion(catalogs):
    async with catalogs.transaction() as (repo, _):
        await repo.create_connection(connection())
        await repo.put_credentials("pg_sales", SECRET_USER, SECRET_PASSWORD)
        await repo.put_credentials("pg_sales", "w", SECRET_PASSWORD, write=True)
    ready = asyncio.Barrier(2)

    async def edit(host):
        async with catalogs.transaction() as (repo, _):
            await ready.wait()
            return await repo.update_connection("pg_sales", {"host": host}, expected_revision=1)

    results = await asyncio.gather(edit("a.internal"), edit("b.internal"), return_exceptions=True)
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    for bad in ({"secret_ref": "user:password"}, {"secret_ref": "nova://other"},
                {"port": 70000}, {"options": {"password": "x"}}):
        with pytest.raises(MetadataIntegrityViolation):
            async with catalogs.transaction() as (repo, _):
                await repo.update_connection("pg_sales", bad, expected_revision=2)
    async with catalogs.transaction("query") as (repo, _):
        assert await repo.delete_connection("pg_sales") is True
    counts = await catalogs.admin(
        "SELECT (SELECT count(*) FROM nova_meta.catalog_connections) AS c,"
        "(SELECT count(*) FROM nova_meta.catalog_connection_credentials) AS r,"
        "(SELECT count(*) FROM nova_meta.catalog_connection_write_credentials) AS w")
    assert counts[0] == {"c": 0, "r": 0, "w": 0}


# ── Business metadata ───────────────────────────────────────────────────────


async def test_annotations_follow_revision_and_stewardship(catalogs):
    table = row_id("lake", "sales", "orders", "relation")

    async def create(comment):
        async with catalogs.transaction(active="ANALYST") as (repo, _):
            return await repo.put_annotation(table, "table", {"comment": comment},
                                             expected_revision=0)

    results = await asyncio.gather(create("a"), create("b"), return_exceptions=True)
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    with pytest.raises(MetadataPermissionDenied):
        async with catalogs.transaction(active="ANALYST") as (repo, _):
            await repo.put_annotation(table, "table", {"owner_name": "bob", "owner_type": "user"},
                                      expected_revision=1)
    with pytest.raises(MetadataPermissionDenied):
        async with catalogs.transaction(active="ANALYST") as (repo, _):
            await repo.put_annotation(row_id("lake", "", "", "catalog"), "catalog",
                                      {"comment": "c"}, expected_revision=0)
    async with catalogs.transaction(name="steward") as (repo, _):
        row = await repo.put_annotation(table, "table", {"owner_name": "sales_team",
                                                         "owner_type": "role"},
                                        expected_revision=1)
    assert row["metadata_revision"] == 2 and row["updated_by"] == "steward"
    for bad in ({"classification": "PUBLIC"}, {"owner_type": None}, {"comment": "a\x07b"}):
        with pytest.raises(MetadataIntegrityViolation):
            async with catalogs.transaction() as (repo, _):
                await repo.put_annotation(table, "table", bad, expected_revision=2)
    with pytest.raises(MetadataConflict):
        async with catalogs.transaction() as (repo, _):
            await repo.put_annotation(table, "table", {"comment": "stale"}, expected_revision=1)
    keys = await catalogs.admin("SELECT metadata_key FROM nova_meta.audit_log "
                                "WHERE metadata_table='catalog_annotations' ORDER BY recorded_at")
    assert [k["metadata_key"]["fields"] for k in keys] == [["comment"], ["owner"]]


async def test_tags_keep_creator_and_steward_rules(catalogs):
    table = row_id("lake", "sales", "orders", "relation")
    async with catalogs.transaction(name="alice", active="ANALYST") as (repo, _):
        first = await repo.put_tag(table, "table", "domain", "sales")
    async with catalogs.transaction(name="bob", active="ANALYST") as (repo, _):
        second = await repo.put_tag(table, "table", "domain", "finance", expected_revision=1)
        with pytest.raises(MetadataConflict):
            await repo.put_tag(table, "table", "domain", "x", expected_revision=1)
    assert (first["metadata_revision"], second["metadata_revision"]) == (1, 2)
    assert second["created_by"] == "alice" and second["updated_by"] == "bob"
    for key in ("1bad", "has space"):
        with pytest.raises(MetadataIntegrityViolation):
            async with catalogs.transaction() as (repo, _):
                await repo.put_tag(table, "table", key, "v")
    database = row_id("lake", "sales", "", "database")
    with pytest.raises(MetadataPermissionDenied):
        async with catalogs.transaction(active="ANALYST") as (repo, _):
            await repo.put_tag(database, "database", "tier", "gold")
    async with catalogs.transaction() as (repo, _):
        await repo.put_tag(database, "database", "tier", "gold")
    with pytest.raises(MetadataPermissionDenied):
        async with catalogs.transaction(active="ANALYST") as (repo, _):
            await repo.delete_tag(database, "tier")
    async with catalogs.transaction(active="ANALYST") as (repo, _):
        assert await repo.delete_tag(table, "domain") is True
        assert await repo.delete_tag(table, "domain") is False
    async with catalogs.transaction("agent", read_only=True) as (repo, _):
        assert [t["tag_key"] for t in await repo.search_tags(term="GOLD")] == ["tier"]
    audit = await catalogs.admin("SELECT * FROM nova_meta.audit_log "
                                 "WHERE metadata_table='config_object_tags'")
    assert all("finance" not in str(r) and "gold" not in str(r) for r in audit)


async def test_lineage_maintenance_projection_and_legacy_watermark(catalogs, monkeypatch):
    from dataclasses import replace
    from zoneinfo import ZoneInfo

    from app.common.metadata_audit import AuditEvent, AuditIdentity, AuditRepository
    from app.core.metadata_actor import acting_as
    from app.core.metadata_runtime import install_metadata_db, uninstall_metadata_db
    from app.modules.catalog.lineage import LineageRepository, harvest_lineage
    from app.modules.catalog.metadata_repository import CATALOG_REFRESHER

    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "Asia/Jakarta")
    now = datetime.now(UTC)
    for event_type, status, statement in (
        ("query", "SUCCESS", "INSERT INTO archive SELECT * FROM orders"),
        ("query", "FAILED", "INSERT INTO failed SELECT * FROM orders"),
        ("query", "SUCCESS", "SELECT * FROM orders"),
        ("login", "SUCCESS", "INSERT INTO other SELECT * FROM orders"),
    ):
        async with catalogs.transaction(name="query-author", active="ANALYST") as (_, conn):
            await AuditRepository(conn).append(
                AuditIdentity(uuid4(), now),
                AuditEvent(event_type, "EXECUTE", "TABLE", "orders", status,
                           sql_text=statement, database_name="sales"),
            )
    legacy = (now - timedelta(minutes=1)).astimezone(ZoneInfo("Asia/Jakarta")).replace(tzinfo=None)
    async with catalogs.transaction("maintenance") as (repo, _):
        await repo.create_sync_run_once({"id": "old-lineage", "catalog_name": "lineage:audit",
                                         "trigger": "lineage"})
        await repo.finish_sync_run("old-lineage", "ok", counts={"watermark": legacy.isoformat()})
    database = MetadataDatabase(catalogs.environment.role_config("nova_meta_maintenance"))
    await database.open(schema_version=len(load_migrations()), metadata_epoch=catalogs.epoch)
    install_metadata_db(database)
    try:
        with acting_as(CATALOG_REFRESHER):
            rows = await LineageRepository().statements(now - timedelta(minutes=2))
            assert len(rows) == 1 and rows[0][3] == "INSERT INTO archive SELECT * FROM orders"
            for _ in range(2):
                run = await harvest_lineage()
                assert run["status"] == "ok" and run["counts"]["edges"] == 1
                assert datetime.fromisoformat(run["counts"]["watermark"]).utcoffset() is not None
        async with database.transaction(CATALOG_REFRESHER, read_only=True) as conn:
            cursor = await conn.execute("SELECT sql_text FROM nova_meta.audit_log")
            assert await cursor.fetchall() == []
            row = await (await conn.execute("SELECT current_user AS login")).fetchone()
            assert row["login"] == "nova_meta_maintenance"
        for limit in (0, 5001, None):
            with pytest.raises(MetadataOperationFailed):
                async with database.transaction(CATALOG_REFRESHER, read_only=True) as conn:
                    await conn.execute("SELECT * FROM nova_meta.catalog_lineage_statements(%s,%s)",
                                       (now - timedelta(minutes=2), limit))
        for actor in (
            replace(CATALOG_REFRESHER, principal_type="USER"),
            replace(CATALOG_REFRESHER, principal="other-maintenance"),
            replace(CATALOG_REFRESHER, active_role="ANALYST"),
        ):
            with pytest.raises(MetadataPermissionDenied):
                async with database.transaction(actor, read_only=True) as conn:
                    await conn.execute("SELECT * FROM nova_meta.catalog_lineage_statements(%s,1)",
                                       (now - timedelta(minutes=2),))
        with pytest.raises(MetadataPermissionDenied):
            async with catalogs.transaction("web", read_only=True) as (_, conn):
                await conn.execute("SELECT * FROM nova_meta.catalog_lineage_statements(%s,1)",
                                   (now - timedelta(minutes=2),))
    finally:
        uninstall_metadata_db(database)
        await database.close()
    async with catalogs.transaction("maintenance", read_only=True) as (repo, _):
        source_id = row_id("default_catalog", "sales", "orders", "relation")
        edges = await repo.lineage_edges_of(source_id)
        assert len(edges) == 1 and edges[0]["first_seen_at"] == now
    grants = await catalogs.admin(
        "SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s) AND "
        "has_function_privilege(rolname,'nova_meta.catalog_lineage_statements(timestamptz,integer)',"
        "'EXECUTE')", (list(WORKLOAD_ROLES),),
    )
    assert [row["rolname"] for row in grants] == ["nova_meta_maintenance"]
    web = MetadataDatabase(catalogs.environment.role_config("nova_meta_web"))
    await web.open(schema_version=len(load_migrations()), metadata_epoch=catalogs.epoch)
    install_metadata_db(web)
    try:
        for name, count in (("query-author", 1), ("different-author", 0)):
            with acting_as(SecurityContext(name, "ANALYST")):
                rows = await LineageRepository().statements(now - timedelta(minutes=2))
                assert len(rows) == count
    finally:
        uninstall_metadata_db(web)
        await web.close()


async def test_lineage_spans_only_widen(catalogs):
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    source, target = row_id("c", "d", "a", "relation"), row_id("c", "d", "b", "relation")
    edge = {"source_id": source, "target_id": target, "source_name": "c.d.a",
            "target_name": "c.d.b", "statement_kind": "insert"}
    async with catalogs.transaction("maintenance") as (repo, _):
        await repo.upsert_lineage([{**edge, "first_seen_at": t0, "last_seen_at": t0,
                                    "last_query_id": "q2"}])
        # An older audit row arrives late, from an overlapping pass.
        await repo.upsert_lineage([{**edge, "first_seen_at": t0 - timedelta(days=1),
                                    "last_seen_at": t0 - timedelta(days=1),
                                    "last_query_id": "q1"}])
        stored = (await repo.lineage_edges_of(source))[0]
    assert stored["first_seen_at"] == t0 - timedelta(days=1)
    assert stored["last_seen_at"] == t0 and stored["last_query_id"] == "q2"
    assert stored["id"] == row_id(source, target, "lineage")
    with pytest.raises(ValueError):
        async with catalogs.transaction("maintenance") as (repo, _):
            await repo.upsert_lineage([{**edge, "first_seen_at": datetime(2026, 1, 1),
                                        "last_seen_at": datetime(2026, 1, 1)}])
    with pytest.raises(MetadataIntegrityViolation):
        async with catalogs.transaction("maintenance") as (repo, _):
            await repo.upsert_lineage([{**edge, "statement_kind": "select",
                                        "first_seen_at": t0, "last_seen_at": t0}])


async def test_owner_links_are_replaced_as_one_set(catalogs):
    links = [link_row("semantic_view", "sv1", "lake.sales.v", source)
             for source in ("sales.orders", "lake.sales.items")]
    async with catalogs.transaction("agent") as (repo, _):
        stored = await repo.replace_owner_links("semantic_view", "sv1", links)
        assert len(stored) == 2
    async with catalogs.transaction("maintenance") as (repo, _):
        stored = await repo.replace_owner_links("semantic_view", "sv1", links[1:])
        assert [link["relation_name"] for link in stored] == ["lake.sales.items"]
    forged = {**links[0], "securable_id": row_id("x", "y", "z", "relation")}
    with pytest.raises(MetadataIntegrityViolation):
        async with catalogs.transaction() as (repo, _):
            await repo.write_links([forged])
    with pytest.raises(MetadataIntegrityViolation):
        async with catalogs.transaction() as (repo, _):
            await repo.write_links([{**links[0], "id": "made-up"}])


# ── Workloads, maintenance and import ───────────────────────────────────────


@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(catalogs, workload):
    await create_catalog(catalogs)
    if workload not in CATALOG_READERS:
        with pytest.raises(MetadataPermissionDenied):
            async with catalogs.transaction(workload, read_only=True) as (repo, _):
                await repo.get_catalog("lake")
        return
    async with catalogs.transaction(workload, read_only=True) as (repo, _):
        assert (await repo.get_catalog("lake"))["catalog_type"] == "iceberg"
    if workload in INVENTORY_WRITERS:
        async with catalogs.transaction(workload) as (repo, _):
            generation = await repo.claim_sync("lake")
            await repo.upsert_objects("lake", [relation("t_" + workload)], generation=generation)
    else:
        with pytest.raises(MetadataPermissionDenied):
            async with catalogs.transaction(workload) as (repo, _):
                await repo.claim_sync("lake")
    if workload != "web":
        with pytest.raises(MetadataPermissionDenied):
            async with catalogs.transaction(workload) as (repo, _):
                await repo.put_tag(row_id("lake", "sales", "t", "relation"), "table", "k", "v")
    if workload in {"notebook", "task"}:
        with pytest.raises(MetadataPermissionDenied):
            async with catalogs.transaction(workload, read_only=True) as (repo, _):
                await repo.list_objects("lake")
    with pytest.raises(MetadataPermissionDenied):
        async with catalogs.transaction(workload, read_only=True) as (_, conn):
            await conn.execute("SELECT * FROM nova_meta.config_external_catalogs")


async def test_maintenance_blocks_writes_but_not_reads(catalogs):
    await create_catalog(catalogs)
    await catalogs.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with catalogs.transaction("maintenance") as (repo, _):
            await repo.claim_sync("lake")
    async with catalogs.transaction(read_only=True) as (repo, _):
        assert await repo.get_catalog("lake")
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with catalogs.transaction(read_only=True) as (_, conn):
            await conn.execute("UPDATE nova_meta.catalog_catalogs SET sync_generation=1")


async def test_import_runs_only_under_maintenance_and_keeps_legacy_rows(catalogs):
    env = catalogs.environment
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as conn:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                await conn.execute(
                    "INSERT INTO nova_meta.config_external_catalogs (id,name,catalog_type) "
                    "VALUES ('x','x','hive')")
        await catalogs.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE nova_meta_owner")
            await conn.execute(
                "INSERT INTO nova_meta.catalog_catalogs (name,kind,catalog_type,managed,source,"
                "properties_json,metadata_revision) VALUES ('legacy','external','hive',true,"
                "'nova','{\"region\":\"eu\"}',4)")
            # Legacy ids are kept as they were, even when not derivable.
            await conn.execute(
                "INSERT INTO nova_meta.catalog_objects (id,catalog_name,database_name,"
                "object_name,object_type) VALUES ('legacy-object','legacy','db','t','table'),"
                "('orphan-object','gone','db','t','table')")
            await conn.execute(
                "INSERT INTO nova_meta.config_external_catalogs (id,name,catalog_type,"
                "properties_json) VALUES ('ext-1','old','hive',"
                "'{\"hive.metastore.password\":\"x\"}')")
            await conn.execute(
                "INSERT INTO nova_meta.catalog_connection_credentials (connection_name,"
                "username_enc,password_enc) VALUES ('legacy_conn','enc:legacy','enc:legacy')")
        await catalogs.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    async with catalogs.transaction() as (repo, _):
        legacy = await repo.get_catalog("legacy")
        assert legacy["provenance"] == "imported" and legacy["properties"] == {"region": "eu"}
        updated = await repo.update_catalog("legacy", {"comment": "kept"}, expected_revision=4)
        assert updated["metadata_revision"] == 5
        objects = await repo.list_objects("legacy")
        assert [o["id"] for o in objects] == ["legacy-object"]
    async with catalogs.transaction("maintenance") as (repo, _):
        # An orphan whose catalog never had a row can still be retired.
        assert await repo.retire_catalog_objects("gone", generation=None) == 1
