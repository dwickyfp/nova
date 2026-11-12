"""L3: Nova Catalog sync against a real engine (StarRocks 4.1.4).

Proves the Phase 1 exit criteria on a real Iceberg (hadoop, MinIO) catalog:

* a catalog created in raw SQL, outside Nova, is recorded by a sync with
  ``source='engine'``, and its tables and columns land in the store;
* a table that disappears is marked dropped, and so is a dropped catalog;
* no credential reaches a ``CATALOG_*`` row, a sync run, or an API response;
* the API lists catalogs from the caller's session and refuses a metadata
  refresh to a role that is not a security admin.

Fixtures, names, and teardown come from ``test_external_catalogs_l3`` so both
suites share one engine account and the same per-test namespace cleanup.
"""

from __future__ import annotations

import pytest

from app.modules.catalog import visibility
from app.modules.catalog.sync import reconcile_catalogs, sync_catalog
from tests.conftest import s3_credentials
from tests.integration import test_external_catalogs_l3 as external_l3
from tests.integration._lakehouse import iceberg_catalog_sql
from tests.integration.test_external_catalogs_l3 import (
    BUCKET,
    L3_ADMIN_USER,
    _admin_execute,
    _admin_execute_in_catalog,
)

pytestmark = pytest.mark.engine

# The external-catalog suite's fixtures, registered here under the same names.
l3_admin_user = external_l3.l3_admin_user
engine = external_l3.engine
admin_client = external_l3.admin_client
namespace = external_l3.namespace

MINIO_ACCESS, MINIO_SECRET = s3_credentials()


async def _create_raw_catalog(names: dict) -> None:
    """``CREATE EXTERNAL CATALOG`` as root, the way a DBA would outside Nova."""
    await _admin_execute(
        iceberg_catalog_sql(names["catalog"], f"s3://{BUCKET}/{names['warehouse']}")
    )


async def _store_rows(catalog: str, metadata_inspect) -> dict[str, list]:
    rows = {}
    for table, column in (
        ("CATALOG_CATALOGS", "name"),
        ("CATALOG_OBJECTS", "catalog_name"),
        ("CATALOG_COLUMNS", "catalog_name"),
        ("CATALOG_SYNC_RUNS", "catalog_name"),
    ):
        rows[table] = list(
            await metadata_inspect(
                f"SELECT * FROM nova_meta.{table.lower()} WHERE {column}=%s", (catalog,)
            )
            or []
        )
    return rows


def _assert_no_credential(value: object) -> None:
    flat = str(value)
    assert MINIO_ACCESS not in flat
    assert MINIO_SECRET not in flat


class TestRawSqlCatalogSync:
    async def test_raw_catalog_tables_columns_and_drops_are_recorded(
        self, admin_client, namespace, metadata_inspect
    ):
        catalog, database, table = namespace["catalog"], namespace["database"], namespace["table"]
        await _create_raw_catalog(namespace)
        await _admin_execute(
            f"CREATE TABLE {catalog}.{database}.{table} (id INT COMMENT 'key', name STRING) "
            "PROPERTIES ('write.format.default'='parquet')"
        )

        runs = await reconcile_catalogs(trigger="manual")

        run = next(r for r in runs if r["catalog_name"] == catalog)
        assert run["status"] == "ok", run
        rows = await _store_rows(catalog, metadata_inspect)
        (row,) = rows["CATALOG_CATALOGS"]
        assert "engine" in row and "iceberg" in row  # source, catalog_type
        objects = {(r[2], r[3], r[4]) for r in rows["CATALOG_OBJECTS"]}
        assert (database, table, "table") in objects
        assert (database, "", "database") in objects
        columns = sorted((r[4], r[5], r[8]) for r in rows["CATALOG_COLUMNS"])
        assert [c[1] for c in columns] == ["id", "name"]
        assert columns[0][2] == "key"
        _assert_no_credential(rows)

        # A table dropped outside Nova is marked, not forgotten.
        await _admin_execute(f"DROP TABLE {catalog}.{database}.{table} FORCE")
        run = await sync_catalog(catalog, trigger="manual")
        assert run["counts"]["dropped"] == 1
        dropped = await metadata_inspect(
            "SELECT dropped_at FROM nova_meta.catalog_objects "
            f"WHERE catalog_name = '{catalog}' AND object_name = '{table}'"
        )
        assert dropped and dropped[0][0] is not None

        # So is the catalog itself once the engine no longer has it.
        await _admin_execute(f"DROP CATALOG {catalog}")
        await reconcile_catalogs(trigger="manual")
        gone = await metadata_inspect(
            f"SELECT dropped_at FROM nova_meta.catalog_catalogs WHERE name = '{catalog}'"
        )
        assert gone and gone[0][0] is not None


class TestCatalogApi:
    async def test_caller_lists_catalogs_columns_and_cannot_refresh(
        self, admin_client, namespace, metadata_inspect
    ):
        catalog, database, table = namespace["catalog"], namespace["database"], namespace["table"]
        await _create_raw_catalog(namespace)
        await _admin_execute(
            f"CREATE TABLE {catalog}.{database}.{table} (id INT, name STRING) "
            "PROPERTIES ('write.format.default'='parquet')"
        )
        await _admin_execute_in_catalog(
            catalog,
            f"GRANT ALL ON DATABASE `{database}` TO '{L3_ADMIN_USER}'",
            f"GRANT ALL ON ALL TABLES IN DATABASE `{database}` TO '{L3_ADMIN_USER}'",
        )
        await sync_catalog(catalog, trigger="manual")
        await reconcile_catalogs(trigger="manual")
        await visibility.bump()

        resp = await admin_client.get("/api/v1/catalog/catalogs")
        assert resp.status_code == 200, resp.text
        listed = {c["name"]: c for c in resp.json()["catalogs"]}
        assert listed[catalog]["source"] == "engine"
        assert listed[catalog]["managed"] is False
        assert database in listed[catalog]["databases"]
        _assert_no_credential(resp.text)

        resp = await admin_client.get(
            f"/api/v1/catalog/catalogs/{catalog}/databases/{database}/objects/{table}/columns"
        )
        assert resp.status_code == 200, resp.text
        assert [c["name"] for c in resp.json()] == ["id", "name"]

        resp = await admin_client.get(
            f"/api/v1/explorer/databases/{database}/tables/{table}?catalog={catalog}"
        )
        assert resp.status_code == 200, resp.text
        assert [c["name"] for c in resp.json()["columns"]] == ["id", "name"]

        detail = await admin_client.get(f"/api/v1/catalog/catalogs/{catalog}")
        assert detail.status_code == 200, detail.text
        _assert_no_credential(detail.text)

        # The suite's role is not a security admin: refresh is refused and audited.
        resp = await admin_client.post(f"/api/v1/catalog/catalogs/{catalog}/refresh")
        assert resp.status_code == 403, resp.text
        audit = await metadata_inspect(
            "SELECT status FROM nova_meta.audit_log "
            f"WHERE action = 'REFRESH_METADATA' AND object_name = '{catalog}' "
            "ORDER BY event_time DESC LIMIT 1"
        )
        assert audit and audit[0][0] == "DENIED"
