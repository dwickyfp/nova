"""L3 integration tests for external catalogs (NOVA-62) on StarRocks 4.1.4.

These run against a **real** engine through the Nova API — not a mock — because
the acceptance criterion is that the engine accepts the catalog DDL, lists the
external table, and returns its rows, and that no credential leaks anywhere in
the process.

The suite brings the engine up via ``docker-compose.test.yml`` by default; to
point at an already-running stack::

    NOVA_ORCH_SR_PORT=29030 uv run pytest tests/integration/test_external_catalogs_l3.py

It uses ``docker-compose.test.yml``'s MinIO and the Iceberg **hadoop** catalog
type (no Hive Metastore service exists in the test stack). A ``hadoop`` Iceberg
catalog is a real external catalog with a real MinIO warehouse, so it exercises
the same CREATE → create table → INSERT → SELECT path a deployment would.

Assertions never contain a real credential; the sentinel values are placeholders
written only into the test stack's MinIO.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid

import asyncmy
import pytest
import pytest_asyncio

from tests.integration._nova_system_ddl import ensure_audit_log
from tests.integration._stack import (
    require_shared_stack,
    shared_stack_host_port,
)

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = _EXPLICIT_PORT or shared_stack_host_port("NOVA_TEST_FE_MYSQL_PORT", 29030)
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")

MINIO_HOST = os.getenv("NOVA_ORCH_S3_HOST", "127.0.0.1")
MINIO_PORT = int(
    os.getenv("NOVA_ORCH_S3_PORT")
    or shared_stack_host_port("NOVA_TEST_MINIO_PORT", 29000)
)
MINIO_ACCESS = "minioadmin"
MINIO_SECRET = "minioadmin"
BUCKET = "test-stage"

#: Placeholders — never a real credential.
ACCESS_SENTINEL = "AKIA_L3_SENTINEL_0001"
SECRET_SENTINEL = "SECRET_L3_SENTINEL_0002"

CATALOG_NAME = "nova_l3_iceberg"
DATABASE_NAME = "db1"
TABLE_NAME = "events"

#: This suite provisions its own admin instead of using the shared
#: ``admin_token`` fixture. ``admin_token`` logs in as ``nova_admin`` with the
#: password conftest assumes, but CI runs ``seed_engine.sh`` first, which resets
#: ``nova_admin`` to the proxy suite's password — so the shared account's
#: credential is not portable, and a hard-coded login there is what made this
#: suite 401 at setup in CI. A suite-local account (same pattern as
#: ``test_tasks_rbac_connection.py``) is deterministic regardless of what the
#: environment seeded.
L3_ADMIN_USER = f"nova_l3_admin_{uuid.uuid4().hex[:8]}"
L3_ADMIN_PASSWORD = "nova_l3_admin_pw"

pytestmark = pytest.mark.engine


def _unique(prefix: str) -> str:
    """A per-test identifier so tests never collide in the engine or MinIO."""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


async def _reachable() -> bool:
    try:
        conn = await asyncio.wait_for(
            asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD),
            timeout=5,
        )
    except Exception:
        return False
    conn.close()
    return True


async def _admin_execute(sql: str):
    conn = await asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD)
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
            with contextlib.suppress(Exception):
                return await cur.fetchall()
    finally:
        conn.close()
    return None


def _minio_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"http://{MINIO_HOST}:{MINIO_PORT}",
        aws_access_key_id=MINIO_ACCESS,
        aws_secret_access_key=MINIO_SECRET,
        region_name="us-east-1",
    )


async def _ensure_catalog_schema() -> None:
    await ensure_audit_log(SR_HOST, SR_PORT, SR_USER, SR_PASSWORD)
    await _admin_execute(
        """
        CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS (
            id                 VARCHAR(64) NOT NULL,
            name               VARCHAR(256) NOT NULL,
            catalog_type       VARCHAR(32) NOT NULL,
            metastore_type     VARCHAR(32),
            metastore_uri      VARCHAR(1024),
            storage_connection VARCHAR(256),
            comment            VARCHAR(1024),
            properties_json    TEXT,
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_by         VARCHAR(128)
        ) PRIMARY KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 1
        PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
        """
    )


@pytest_asyncio.fixture(scope="session")
async def l3_admin_user(request):
    """Create a suite-owned admin account with exactly the privileges the
    feature needs, and drop it afterwards.

    Session-scoped: the account is created once and reused by every test, and
    the password is known here rather than assumed about the environment.
    """
    # Same stack gate as ``engine``: bring the compose stack up if this run is
    # what provides the engine, so a session-scoped fixture that resolves before
    # ``client``/``app`` does not skip against a stack that is about to start.
    require_shared_stack(request)
    if not await _reachable():
        pytest.skip("StarRocks not reachable")
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP USER IF EXISTS '{L3_ADMIN_USER}'")
    await _admin_execute(
        f"CREATE USER '{L3_ADMIN_USER}' IDENTIFIED BY '{L3_ADMIN_PASSWORD}'"
    )
    await _admin_execute(f"GRANT ALL ON *.* TO '{L3_ADMIN_USER}' WITH GRANT OPTION")
    # Creating an external catalog is a SYSTEM-level privilege, distinct from
    # ``ALL ON *.*`` (AGENTS.md §6), and ALTER/DROP are per-catalog grants that
    # cannot be granted ON SYSTEM. Provisioning them on the suite's own account
    # proves the delegate-first RBAC path rather than bypassing it.
    with contextlib.suppress(Exception):
        await _admin_execute(
            f"GRANT CREATE EXTERNAL CATALOG ON SYSTEM TO '{L3_ADMIN_USER}'"
        )
    with contextlib.suppress(Exception):
        await _admin_execute(f"GRANT ALTER, DROP ON CATALOG * TO '{L3_ADMIN_USER}'")

    yield L3_ADMIN_USER

    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP USER IF EXISTS '{L3_ADMIN_USER}'")


@pytest_asyncio.fixture
async def engine(request):
    """Provision the engine once per test: schema and bucket.

    Setup-only (no ``yield``): every name this suite creates is unique per test
    and torn down by ``namespace``, so there is nothing to finalize here.
    """
    require_shared_stack(request)
    if not await _reachable():
        pytest.skip("StarRocks not reachable")
    await _ensure_catalog_schema()
    with contextlib.suppress(Exception):
        _minio_client().create_bucket(Bucket=BUCKET)


@pytest_asyncio.fixture
async def admin_client(engine, client, l3_admin_user):
    """The shared ASGI client, authenticated as this suite's own admin."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": L3_ADMIN_USER, "password": L3_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return client


@pytest_asyncio.fixture
async def namespace(engine):
    """A per-test catalog + warehouse namespace, torn down afterwards.

    Every test gets fresh names because the Iceberg hadoop warehouse is durable
    in MinIO: reusing a catalog name would make a later ``CREATE TABLE`` collide
    with the previous test's metadata.
    """
    name = _unique(CATALOG_NAME)
    database = _unique(DATABASE_NAME)
    table = _unique(TABLE_NAME)
    warehouse = f"iceberg_wh/{name}"
    # The hadoop catalog treats ``warehouse`` as the root and each namespace as a
    # ``<warehouse>/<db>`` prefix. MinIO has no directories, so the zero-byte
    # marker is what makes the namespace visible to the engine.
    client = _minio_client()
    client.put_object(Bucket=BUCKET, Key=f"{warehouse}/{database}/.keep", Body=b"")

    yield {"catalog": name, "database": database, "table": table, "warehouse": warehouse}

    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP CATALOG IF EXISTS {name}")
    with contextlib.suppress(Exception):
        await _admin_execute(
            f"DELETE FROM NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS WHERE name = '{name}'"
        )
    # Remove the warehouse objects so the namespace cannot shadow a later run.
    with contextlib.suppress(Exception):
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix=f"{warehouse}/"):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                client.delete_objects(Bucket=BUCKET, Delete={"Objects": keys})


def _create_body(namespace: dict) -> dict:
    warehouse = f"s3://{BUCKET}/{namespace['warehouse']}"
    return {
        "name": namespace["catalog"],
        "type": "iceberg",
        "metastore_type": "hms",
        "metastore_uri": warehouse,
        "storage_connection": "production",
        "comment": "NOVA-62 L3 iceberg catalog",
        # The hadoop catalog type reads its warehouse from a non-secret
        # property; credentials come from the storage connection.
        "properties": {
            "iceberg.catalog.type": "hadoop",
            "iceberg.catalog.warehouse": warehouse,
        },
    }


class TestCatalogCrudThroughApi:
    async def test_create_list_get_alter_drop(self, admin_client, namespace):
        catalog = namespace["catalog"]
        body = _create_body(namespace)

        # CREATE
        resp = await admin_client.post("/api/v1/external-catalogs", json=body)
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["name"] == catalog
        assert created["type"] == "iceberg"

        # LIST
        resp = await admin_client.get("/api/v1/external-catalogs")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()["catalogs"]]
        assert catalog in names

        # GET — create_statement is present and redacted
        resp = await admin_client.get(f"/api/v1/external-catalogs/{catalog}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["create_statement"]
        assert ACCESS_SENTINEL not in detail["create_statement"]
        assert SECRET_SENTINEL not in detail["create_statement"]

        # ALTER — a benign, non-secret property change
        resp = await admin_client.patch(
            f"/api/v1/external-catalogs/{catalog}",
            json={"properties": {"iceberg.catalog.warehouse": body["metastore_uri"]}},
        )
        assert resp.status_code == 200, resp.text

        # DROP
        resp = await admin_client.delete(f"/api/v1/external-catalogs/{catalog}")
        assert resp.status_code == 200, resp.text

    async def test_duplicate_create_is_rejected(self, admin_client, namespace):
        body = _create_body(namespace)
        first = await admin_client.post("/api/v1/external-catalogs", json=body)
        assert first.status_code == 201, first.text
        second = await admin_client.post("/api/v1/external-catalogs", json=body)
        assert second.status_code == 400

    async def test_secret_property_in_request_is_rejected(self, admin_client, namespace):
        body = _create_body(namespace)
        body["properties"]["aws.s3.secret_key"] = SECRET_SENTINEL
        resp = await admin_client.post("/api/v1/external-catalogs", json=body)
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "key",
        [
            # The dotted spellings a final-segment match accepted. None may be
            # persisted, echoed in ``properties``, or reach the engine.
            "aws.s3.secret.key",
            "azure.account.key",
            "gcp.gcs.private.key",
            "gcp.gcs.service.account.key",
            "aws.s3.session.token",
            "hive.metastore.account.key",
        ],
    )
    async def test_dotted_secret_property_is_rejected(
        self, admin_client, namespace, key
    ):
        catalog = namespace["catalog"]
        body = _create_body(namespace)
        body["properties"][key] = SECRET_SENTINEL

        resp = await admin_client.post("/api/v1/external-catalogs", json=body)
        assert resp.status_code == 422, resp.text

        # The credential was refused, not persisted and not echoed. The GET
        # endpoint always synthesizes a response, so assert on the value: the
        # sentinel must not appear anywhere in the payload.
        resp = await admin_client.get(f"/api/v1/external-catalogs/{catalog}")
        assert SECRET_SENTINEL not in resp.text
        assert key not in resp.text
        rows = await _admin_execute(
            "SELECT properties_json FROM NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS "
            f"WHERE name = '{catalog}'"
        )
        assert not rows, rows
        assert SECRET_SENTINEL not in str(rows)

    async def test_dotted_secret_alter_is_rejected(self, admin_client, namespace):
        catalog = namespace["catalog"]
        resp = await admin_client.post(
            "/api/v1/external-catalogs", json=_create_body(namespace)
        )
        assert resp.status_code == 201, resp.text

        resp = await admin_client.patch(
            f"/api/v1/external-catalogs/{catalog}",
            json={"properties": {"azure.account.key": SECRET_SENTINEL}},
        )
        assert resp.status_code == 422, resp.text

        detail = (
            await admin_client.get(f"/api/v1/external-catalogs/{catalog}")
        ).json()
        assert SECRET_SENTINEL not in str(detail)
        assert "azure.account.key" not in detail.get("properties", {})


class TestExternalTableQuery:
    async def test_create_insert_select_through_engine(self, admin_client, namespace):
        resp = await admin_client.post(
            "/api/v1/external-catalogs", json=_create_body(namespace)
        )
        assert resp.status_code == 201, resp.text

        catalog, database, table = (
            namespace["catalog"],
            namespace["database"],
            namespace["table"],
        )
        # The hadoop catalog exposes the pre-created namespace.
        await _admin_execute(
            f"CREATE TABLE {catalog}.{database}.{table} (id INT, name STRING) "
            "PROPERTIES ('write.format.default'='parquet')"
        )
        await _admin_execute(
            f"INSERT INTO {catalog}.{database}.{table} VALUES (1,'a'),(2,'b')"
        )
        rows = await _admin_execute(
            f"SELECT id, name FROM {catalog}.{database}.{table} ORDER BY id"
        )
        assert [tuple(r) for r in (rows or [])] == [(1, "a"), (2, "b")], rows

    async def test_external_table_appears_in_catalog_tree(self, admin_client, namespace):
        resp = await admin_client.post(
            "/api/v1/external-catalogs", json=_create_body(namespace)
        )
        assert resp.status_code == 201, resp.text

        catalog, database, table = (
            namespace["catalog"],
            namespace["database"],
            namespace["table"],
        )
        await _admin_execute(
            f"CREATE TABLE {catalog}.{database}.{table} (id INT, name STRING) "
            "PROPERTIES ('write.format.default'='parquet')"
        )

        resp = await admin_client.get(
            f"/api/v1/external-catalogs/{catalog}/databases/{database}/tables"
        )
        assert resp.status_code == 200, resp.text
        assert table in [t["name"] for t in resp.json()]

        # And through the explorer catalog tree endpoint.
        resp = await admin_client.get("/api/v1/explorer/catalogs")
        assert resp.status_code == 200
        catalogs = resp.json()["catalogs"]
        ext = next((c for c in catalogs if c["name"] == catalog), None)
        assert ext is not None, catalogs
        assert database in ext["databases"]


class TestNoCredentialLeak:
    async def test_show_create_returns_no_credential(self, admin_client, namespace):
        """The engine returns ``session_token``/``metastore.password`` in full;
        the API response must not."""
        body = _create_body(namespace)
        resp = await admin_client.post("/api/v1/external-catalogs", json=body)
        assert resp.status_code == 201, resp.text

        detail = (
            await admin_client.get(f"/api/v1/external-catalogs/{namespace['catalog']}")
        ).json()
        statement = detail.get("create_statement") or ""

        # The real MinIO credentials from the test stack must not appear. They
        # are the actual resolved values for the ``production`` connection.
        assert MINIO_ACCESS not in statement
        assert MINIO_SECRET not in statement
        assert "minioadmin" not in str(detail)

    async def test_audit_row_statement_is_redacted(self, admin_client, namespace):
        catalog = namespace["catalog"]
        resp = await admin_client.post(
            "/api/v1/external-catalogs", json=_create_body(namespace)
        )
        assert resp.status_code == 201, resp.text

        rows = await _admin_execute(
            "SELECT rewritten_sql FROM NOVA_SYSTEM.AUDIT_LOG "
            f"WHERE event_type = 'query' AND rewritten_sql LIKE '%{catalog}%' "
            "ORDER BY event_time DESC LIMIT 5"
        )
        assert rows, "no audit row was written for the catalog DDL"
        for (rewritten,) in rows:
            assert MINIO_ACCESS not in (rewritten or "")
            assert MINIO_SECRET not in (rewritten or "")

    async def test_metadata_table_has_no_credential(self, admin_client, namespace):
        catalog = namespace["catalog"]
        resp = await admin_client.post(
            "/api/v1/external-catalogs", json=_create_body(namespace)
        )
        assert resp.status_code == 201, resp.text

        rows = await _admin_execute(
            "SELECT properties_json, metastore_uri, storage_connection "
            "FROM NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS "
            f"WHERE name = '{catalog}'"
        )
        assert rows
        flat = str(rows)
        assert MINIO_ACCESS not in flat
        assert MINIO_SECRET not in flat

