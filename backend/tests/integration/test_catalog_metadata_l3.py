"""L3: Nova Catalog business metadata, search, and lineage on a real engine.

Proves the phase 3 exit criteria against StarRocks 4.1.4:

* a tag on a column of a native table can be searched, and the column, its tag,
  and its lineage come back through the API;
* when the table is dropped at the source, the next sync marks it dropped and
  the column keeps its tag, shown with a dropped status (to a security admin);
* the same holds for a column of an Iceberg table in a catalog created in raw
  SQL, and a caller without a security-admin role or Ranger ``ALTER`` is
  refused a tag change, with the refusal audited;
* lineage comes from the audit row of an ``INSERT ... SELECT`` run through Nova.

Fixtures and the raw Iceberg catalog come from the phase 1 and external-catalog
suites, so every name is unique per test and torn down afterwards.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

from app.modules.catalog import visibility
from app.modules.catalog.securables import column_id, relation_id
from tests.integration import test_catalog_l3 as catalog_l3
from tests.integration import test_external_catalogs_l3 as external_l3
from tests.integration.test_external_catalogs_l3 import _admin_execute

pytestmark = pytest.mark.engine

l3_admin_user = external_l3.l3_admin_user
engine = external_l3.engine
admin_client = external_l3.admin_client
namespace = external_l3.namespace
catalog_refresh = catalog_l3.catalog_refresh

NATIVE = "default_catalog"


#: Login answers that carry a usable session token.
_SIGNED_IN = {"AUTHENTICATED", "SETUP_REQUIRED", "PASSWORD_CHANGE_REQUIRED"}


async def _login(client, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _nova_admin_headers(client) -> dict[str, str]:
    body = await _login(
        client, "nova_admin", os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    )
    # A fresh stack answers SETUP_REQUIRED with a usable session token, as the
    # shared ``admin_token`` fixture relies on; no token is a failure, not a skip.
    assert body.get("status") in _SIGNED_IN and body.get("access_token"), body
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    if body.get("active_role") != "ACCOUNTADMIN":
        switched = await client.post(
            "/api/v1/auth/switch-role", json={"role": "ACCOUNTADMIN"}, headers=headers
        )
        assert switched.status_code == 200, switched.text
    return headers


async def _search(client, headers, **params):
    resp = await client.get("/api/v1/catalog/search", params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return {hit["full_name"]: hit for hit in resp.json()["results"]}


class TestNativeTableMetadata:
    async def test_tagged_column_is_searchable_has_lineage_and_survives_a_drop(
        self, engine, client, metadata_inspect, catalog_refresh
    ):
        database = f"p3_l3_{uuid.uuid4().hex[:10]}"
        source, target = "customers", "customers_by_region"
        email = column_id(NATIVE, database, source, "customer_email")
        headers = await _nova_admin_headers(client)
        await _admin_execute(f"CREATE DATABASE {database}")
        try:
            for table, columns in (
                (source, "id BIGINT, customer_email VARCHAR(128), region VARCHAR(16)"),
                (target, "region VARCHAR(16), n BIGINT"),
            ):
                await _admin_execute(
                    f"CREATE TABLE {database}.{table} ({columns}) DUPLICATE KEY("
                    f"{columns.split()[0]}) DISTRIBUTED BY HASH({columns.split()[0]}) "
                    "BUCKETS 1 PROPERTIES('replication_num'='1')"
                )
            run = await client.post(
                "/api/v1/query/execute",
                json={
                    "sql": f"INSERT INTO {database}.{target} SELECT region, count(*) "
                    f"FROM {database}.{source} GROUP BY region"
                },
                headers=headers,
            )
            assert run.status_code == 200 and not run.json()[0].get("error"), run.text
            runs = await catalog_refresh()
            assert next(run for run in runs if run["catalog_name"] == NATIVE)["status"] == "ok"
            assert (await catalog_refresh(lineage=True))["status"] == "ok"
            await visibility.bump()

            resolved = await client.get(
                "/api/v1/catalog/objects/resolve",
                params={
                    "catalog": NATIVE,
                    "database": database,
                    "object": source,
                    "column": "customer_email",
                },
                headers=headers,
            )
            assert resolved.status_code == 200, resolved.text
            assert resolved.json()["id"] == email
            tagged = await client.put(
                f"/api/v1/catalog/objects/{email}/tags",
                json={"tags": [{"key": "pii", "value": "email"}]},
                headers=headers,
            )
            assert tagged.status_code == 200, tagged.text
            annotated = await client.patch(
                f"/api/v1/catalog/objects/{email}/annotations",
                json={"comment": "Contact address", "classification": "CONFIDENTIAL"},
                headers=headers,
            )
            assert annotated.status_code == 200, annotated.text

            name = f"{NATIVE}.{database}.{source}.customer_email"
            found = await _search(client, headers, tag="pii=email")
            assert found[name]["status"] == "live"
            by_comment = await _search(client, headers, q="contact address")
            assert list(by_comment) == [name] and by_comment[name]["matched"] == ["comment"]
            lineage = await client.get(
                f"/api/v1/catalog/objects/{relation_id(NATIVE, database, target)}/lineage",
                headers=headers,
            )
            assert [n["full_name"] for n in lineage.json()["upstream"]] == [
                f"{NATIVE}.{database}.{source}"
            ]

            # Dropped at the source: the column keeps its tag and shows dropped.
            await _admin_execute(f"DROP TABLE {database}.{source} FORCE")
            assert (await catalog_refresh(NATIVE))["status"] == "ok"
            assert name not in await _search(client, headers, tag="pii")
            dropped = (await _search(client, headers, tag="pii", include_dropped="true"))[name]
            assert dropped["status"] == "dropped"
            assert [(t["key"], t["value"]) for t in dropped["tags"]] == [("pii", "email")]
            assert dropped["classification"] == "CONFIDENTIAL"

            audit = await metadata_inspect(
                "SELECT action, status, sql_text FROM nova_meta.audit_log "
                f"WHERE event_type = 'CATALOG' AND object_name = '{name}' "
                "ORDER BY event_time"
            )
            assert sorted(row[:2] for row in audit) == [
                ("ANNOTATE", "SUCCESS"),
                ("SET_TAG", "SUCCESS"),
            ]
            # Neither the tag value nor the comment text reaches the audit row.
            assert all("Contact address" not in str(row[2]) for row in audit)
            assert all(str(row[2]) == "SET TAG pii" for row in audit if row[0] == "SET_TAG")
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP DATABASE IF EXISTS {database} FORCE")


class TestIcebergColumnMetadata:
    async def test_iceberg_column_tag_is_governed_and_kept_after_a_drop(
        self, admin_client, namespace, metadata_inspect, catalog_refresh
    ):
        catalog, database, table = namespace["catalog"], namespace["database"], namespace["table"]
        await catalog_l3._create_raw_catalog(namespace)
        await _admin_execute(
            f"CREATE TABLE {catalog}.{database}.{table} (id INT, ip STRING) "
            "PROPERTIES ('write.format.default'='parquet')"
        )
        ip = column_id(catalog, database, table, "ip")
        try:
            await catalog_refresh()
            await visibility.bump()
            headers = await _nova_admin_headers(admin_client)
            # The suite's own admin role is neither a security admin nor granted
            # ALTER in Ranger: the change is refused and the refusal audited.
            refused = await admin_client.put(
                f"/api/v1/catalog/objects/{ip}/tags", json={"tags": [{"key": "pii"}]}
            )
            assert refused.status_code in (403, 404), refused.text
            audit = await metadata_inspect(
                "SELECT status FROM nova_meta.audit_log WHERE event_type = 'CATALOG' "
                "AND action = 'SET_TAG' ORDER BY event_time DESC LIMIT 1"
            )
            assert audit and audit[0][0] == "DENIED"

            tagged = await admin_client.put(
                f"/api/v1/catalog/objects/{ip}/tags",
                json={"tags": [{"key": "pii", "value": "ip"}]},
                headers=headers,
            )
            assert tagged.status_code == 200, tagged.text
            name = f"{catalog}.{database}.{table}.ip"
            assert (await _search(admin_client, headers, tag="pii=ip"))[name]["status"] == "live"

            await _admin_execute(f"DROP TABLE {catalog}.{database}.{table} FORCE")
            assert (await catalog_refresh(catalog))["status"] == "ok"
            dropped = await _search(admin_client, headers, tag="pii=ip", include_dropped="true")
            assert dropped[name]["status"] == "dropped"
            assert dropped[name]["tags"][0]["key"] == "pii"
            catalog_l3._assert_no_credential(dropped)
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP CATALOG {catalog}")
