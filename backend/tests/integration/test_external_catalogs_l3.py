"""L3 integration tests for external catalogs (NOVA-62) on StarRocks 4.1.4.

These run against a **real** engine through the Nova API — not a mock — because
the acceptance criterion is that the engine lists the external table and
returns its rows, and that no credential leaks anywhere in the process.

Nova creates catalogs only from a connection now; the Iceberg catalog here is
made in raw SQL (``tests/integration/_lakehouse.py``), as a DBA would, and the
``/external-catalogs`` API lists, describes, alters, and drops it.

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

from tests.conftest import root_password, s3_credentials
from tests.integration._lakehouse import iceberg_catalog_sql
from tests.integration._stack import (
    require_shared_stack,
    shared_stack_host_port,
)

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = _EXPLICIT_PORT or shared_stack_host_port("NOVA_TEST_FE_MYSQL_PORT", 29030)
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD") or root_password()

MINIO_HOST = os.getenv("NOVA_ORCH_S3_HOST", "127.0.0.1")
MINIO_PORT = int(
    os.getenv("NOVA_ORCH_S3_PORT") or shared_stack_host_port("NOVA_TEST_MINIO_PORT", 29000)
)
MINIO_ACCESS = s3_credentials()[0]
MINIO_SECRET = s3_credentials()[1]
BUCKET = "test-stage"

#: Placeholders — never a real credential.
SECRET_SENTINEL = "SECRET_L3_SENTINEL_0002"

CATALOG_NAME = "nova_l3_iceberg"
DATABASE_NAME = "db1"
TABLE_NAME = "events"

#: Keep catalog changes under a suite-local admin so they do not depend on
#: the shared ``nova_admin`` account or another test's session state.
L3_ADMIN_USER = f"nova_l3_admin_{uuid.uuid4().hex[:8]}"
L3_ADMIN_PASSWORD = "nova_l3_admin_pw"
L3_ADMIN_ROLE = f"nova_l3_catalog_{uuid.uuid4().hex[:8]}"

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


async def _admin_execute_in_catalog(catalog: str, *statements: str) -> None:
    """Run statements as root after ``SET CATALOG`` on one session.

    StarRocks grants database and table privileges of an external catalog in
    that catalog's context, so the switch and the grants share a connection.
    """
    conn = await asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD)
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"SET CATALOG `{catalog}`")
            for statement in statements:
                await cur.execute(statement)
    finally:
        conn.close()


def _minio_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"http://{MINIO_HOST}:{MINIO_PORT}",
        aws_access_key_id=MINIO_ACCESS,
        aws_secret_access_key=MINIO_SECRET,
        region_name="us-east-1",
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
    # Idempotent: ``test_catalog_l3`` registers this fixture too, so a run of
    # both modules sets the account up twice under the same names.
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP USER IF EXISTS '{L3_ADMIN_USER}'")
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP ROLE IF EXISTS '{L3_ADMIN_ROLE}'")
    await _admin_execute(f"CREATE USER '{L3_ADMIN_USER}' IDENTIFIED BY '{L3_ADMIN_PASSWORD}'")
    await _admin_execute(f"GRANT ALL ON *.* TO '{L3_ADMIN_USER}' WITH GRANT OPTION")
    from app.integrations.ranger.client import ranger_client
    from app.integrations.ranger.schemas import RangerRole, RangerRoleMember

    await ranger_client.put_user_attributes(L3_ADMIN_USER, {})
    await ranger_client.put_role(
        RangerRole(name=L3_ADMIN_ROLE, users=[RangerRoleMember(name=L3_ADMIN_USER)])
    )
    await _admin_execute(f"CREATE ROLE '{L3_ADMIN_ROLE}'")
    await _admin_execute(f"GRANT '{L3_ADMIN_ROLE}' TO '{L3_ADMIN_USER}'")
    await _admin_execute(f"SET DEFAULT ROLE '{L3_ADMIN_ROLE}' TO '{L3_ADMIN_USER}'")
    # Creating an external catalog is a SYSTEM-level privilege, distinct from
    # ``ALL ON *.*`` (AGENTS.md §6), and USAGE/ALTER/DROP are per-catalog grants
    # that cannot be granted ON SYSTEM. USAGE is what lets the Explorer, which
    # lists metadata on the caller's own session, show the catalog's databases.
    # Provisioning them on the suite's own account proves the delegate-first
    # RBAC path rather than bypassing it.
    with contextlib.suppress(Exception):
        await _admin_execute(f"GRANT CREATE EXTERNAL CATALOG ON SYSTEM TO '{L3_ADMIN_USER}'")
    with contextlib.suppress(Exception):
        await _admin_execute(f"GRANT ALTER, DROP ON CATALOG * TO '{L3_ADMIN_USER}'")
    with contextlib.suppress(Exception):
        await _admin_execute(f"GRANT USAGE ON ALL CATALOGS TO '{L3_ADMIN_USER}'")

    yield L3_ADMIN_USER

    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP USER IF EXISTS '{L3_ADMIN_USER}'")
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP ROLE IF EXISTS '{L3_ADMIN_ROLE}'")
    # The other registration may already have removed the Ranger role.
    with contextlib.suppress(Exception):
        await ranger_client.delete_role(L3_ADMIN_ROLE)


@pytest_asyncio.fixture
async def engine(request):
    """Provision the engine once per test: schema and bucket.

    Setup-only (no ``yield``): every name this suite creates is unique per test
    and torn down by ``namespace``, so there is nothing to finalize here.
    """
    require_shared_stack(request)
    if not await _reachable():
        pytest.skip("StarRocks not reachable")
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
    # Remove the warehouse objects so the namespace cannot shadow a later run.
    with contextlib.suppress(Exception):
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix=f"{warehouse}/"):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                client.delete_objects(Bucket=BUCKET, Delete={"Objects": keys})


async def _create_catalog(namespace: dict) -> None:
    """The suite's Iceberg catalog, made in raw SQL as root.

    Raw SQL bypasses Nova's catalog service, so nothing invalidates the briefly
    cached visibility answers; bump the epoch as a managed create would.
    """
    from app.modules.catalog import visibility

    await _admin_execute(iceberg_catalog_sql(
        namespace["catalog"], f"s3://{BUCKET}/{namespace['warehouse']}",
        comment="NOVA-62 L3 iceberg catalog",
    ))
    await visibility.bump()


class TestCatalogApi:
    async def test_list_get_alter_drop(self, admin_client, namespace):
        catalog = namespace["catalog"]
        await _create_catalog(namespace)

        resp = await admin_client.get("/api/v1/external-catalogs")
        assert resp.status_code == 200
        assert catalog in [c["name"] for c in resp.json()["catalogs"]]

        # GET: create_statement is present and redacted
        resp = await admin_client.get(f"/api/v1/external-catalogs/{catalog}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["create_statement"]
        assert MINIO_SECRET not in detail["create_statement"]

        # ALTER: a benign, non-secret property change
        warehouse = f"s3://{BUCKET}/{namespace['warehouse']}"
        resp = await admin_client.patch(
            f"/api/v1/external-catalogs/{catalog}",
            json={"properties": {"iceberg.catalog.warehouse": warehouse}},
        )
        assert resp.status_code == 200, resp.text

        resp = await admin_client.delete(f"/api/v1/external-catalogs/{catalog}")
        assert resp.status_code == 200, resp.text

    async def test_lakehouse_catalogs_are_not_created_through_nova(self, admin_client, namespace):
        body = {"name": namespace["catalog"], "type": "iceberg", "metastore_uri": "thrift://h:1"}
        assert (await admin_client.post("/api/v1/external-catalogs", json=body)).status_code == 405
        assert (await admin_client.post("/api/v1/catalog/catalogs", json=body)).status_code == 405
        rows = await _admin_execute("SHOW CATALOGS")
        assert namespace["catalog"] not in str(rows)

    async def test_dotted_secret_alter_is_rejected(self, admin_client, namespace):
        catalog = namespace["catalog"]
        await _create_catalog(namespace)

        resp = await admin_client.patch(
            f"/api/v1/external-catalogs/{catalog}",
            json={"properties": {"azure.account.key": SECRET_SENTINEL}},
        )
        assert resp.status_code == 422, resp.text

        detail = (await admin_client.get(f"/api/v1/external-catalogs/{catalog}")).json()
        assert SECRET_SENTINEL not in str(detail)
        assert "azure.account.key" not in detail.get("properties", {})


class TestExternalTableQuery:
    async def test_create_insert_select_through_engine(self, admin_client, namespace):
        await _create_catalog(namespace)

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
        await _admin_execute(f"INSERT INTO {catalog}.{database}.{table} VALUES (1,'a'),(2,'b')")
        rows = await _admin_execute(
            f"SELECT id, name FROM {catalog}.{database}.{table} ORDER BY id"
        )
        assert [tuple(r) for r in (rows or [])] == [(1, "a"), (2, "b")], rows

    async def test_external_table_appears_in_catalog_tree(self, admin_client, namespace):
        await _create_catalog(namespace)
        catalog, database, table = (
            namespace["catalog"],
            namespace["database"],
            namespace["table"],
        )
        await _admin_execute(
            f"CREATE TABLE {catalog}.{database}.{table} (id INT, name STRING) "
            "PROPERTIES ('write.format.default'='parquet')"
        )
        tables_url = f"/api/v1/external-catalogs/{catalog}/databases/{database}/tables"

        # Both listings run on the caller's own session, so the engine decides
        # what appears. With USAGE on the catalog but no privilege in the
        # database, the catalog shows and the database does not.
        resp = await admin_client.get("/api/v1/explorer/catalogs")
        assert resp.status_code == 200
        catalogs = resp.json()["catalogs"]
        ext = next((c for c in catalogs if c["name"] == catalog), None)
        assert ext is not None, catalogs
        assert database not in ext["databases"]
        resp = await admin_client.get(tables_url)
        assert resp.status_code == 404, resp.text

        await _admin_execute_in_catalog(
            catalog,
            f"GRANT ALL ON DATABASE `{database}` TO '{L3_ADMIN_USER}'",
            f"GRANT ALL ON ALL TABLES IN DATABASE `{database}` TO '{L3_ADMIN_USER}'",
        )
        # A grant made outside Nova reaches cached visibility within its short
        # TTL; Nova's own access changes retire the cache at once, as here.
        from app.modules.catalog import visibility

        await visibility.bump()

        resp = await admin_client.get(tables_url)
        assert resp.status_code == 200, resp.text
        assert table in [t["name"] for t in resp.json()]
        resp = await admin_client.get("/api/v1/explorer/catalogs")
        assert resp.status_code == 200
        ext = next((c for c in resp.json()["catalogs"] if c["name"] == catalog), None)
        assert ext is not None
        assert database in ext["databases"]
        resp = await admin_client.get(f"/api/v1/explorer/databases/{database}?catalog={catalog}")
        assert resp.status_code == 200, resp.text
        assert table in [t["name"] for t in resp.json()["tables"]]


class TestNoCredentialLeak:
    async def test_show_create_returns_no_credential(self, admin_client, namespace):
        """The engine returns ``session_token``/``metastore.password`` in full;
        the API response must not."""
        await _create_catalog(namespace)
        from app.modules.catalog import visibility

        await visibility.bump()

        detail = (
            await admin_client.get(f"/api/v1/external-catalogs/{namespace['catalog']}")
        ).json()
        statement = detail.get("create_statement") or ""

        # The real MinIO credentials from the test stack must not appear.
        assert MINIO_ACCESS not in statement
        assert MINIO_SECRET not in statement
        assert s3_credentials()[1] not in str(detail)
