"""The Iceberg REST and Kafka connection sources (added 2026-11-16).

Each is one ``CatalogSource`` subclass. Iceberg builds the engine's Iceberg
REST catalog with the storage key pair; Kafka backs no catalog and gives
Routine Load its broker list and SASL settings. Endpoints never carry a
credential, and credential-bearing properties are refused.
"""

from __future__ import annotations

import pytest

from app.common.sql_guard import redact_sql_credentials
from app.modules.catalog.connectors import connector_for
from app.modules.catalog.errors import CatalogError
from app.modules.catalog.schemas import ExternalCatalogAlter
from app.modules.catalog.sources import Credentials, describe_catalog_source, source_for

SECRET = "S3cr3t\\'key"
KEYS = Credentials("AKIAEXAMPLE", SECRET)


# ── Iceberg REST ──────────────────────────────────────────────────────────


def test_iceberg_endpoint_keeps_uri_and_storage_options():
    source = source_for("iceberg")
    endpoint = source.parse_endpoint(
        "http://iceberg-rest:8181?warehouse=s3://lake/warehouse"
        "&s3_endpoint=http://minio:9000&s3_region=us-east-1"
    )
    assert (endpoint.host, endpoint.port, endpoint.database) == ("iceberg-rest", 8181, None)
    assert endpoint.options == {
        "warehouse": "s3://lake/warehouse", "s3_endpoint": "http://minio:9000",
        "s3_region": "us-east-1",
    }
    https = source.parse_endpoint("https://catalog.example.com/api/catalog")
    assert https.port == 443 and https.options == {"scheme": "https", "path": "/api/catalog"}
    assert source.catalog_uri(https) == "https://catalog.example.com:443/api/catalog"
    assert source.parse_endpoint("iceberg-rest").port == 8181


@pytest.mark.parametrize(
    "value",
    [
        "http://user:pw@rest:8181",
        "http://rest:8181?token=abc",
        "http://rest:8181?s3_secret_key=x",
        "ftp://rest:8181",
        "http://rest:99999",
        "http://rest:8181?s3_endpoint=http://u:p@minio:9000",
        "http://rest:8181?s3_region=US EAST",
        "http://rest:8181?s3_path_style=maybe",
        "http://rest:8181?warehouse=s3://a@b",
        "http://rest:8181#frag",
        "",
    ],
)
def test_iceberg_endpoint_refuses_credentials_and_bad_values(value):
    with pytest.raises(CatalogError):
        source_for("iceberg").parse_endpoint(value)


def test_iceberg_catalog_sql_carries_the_storage_keys_only_for_the_engine():
    source = source_for("iceberg")
    endpoint = source.parse_endpoint(
        "http://iceberg-rest:8181?s3_endpoint=http://minio:9000&warehouse=s3://lake/w"
    )
    sql = source.build_catalog_sql(
        "lake", endpoint, KEYS, comment="Lake",
        properties={"enable_iceberg_metadata_cache": "false"},
    )
    assert sql.startswith("CREATE EXTERNAL CATALOG lake COMMENT 'Lake' PROPERTIES (")
    for expected in (
        "'type' = 'iceberg'", "'iceberg.catalog.type' = 'rest'",
        "'iceberg.catalog.uri' = 'http://iceberg-rest:8181'",
        "'iceberg.catalog.warehouse' = 's3://lake/w'",
        "'aws.s3.endpoint' = 'http://minio:9000'",
        "'aws.s3.enable_path_style_access' = 'true'", "'aws.s3.region' = 'us-east-1'",
        "'aws.s3.access_key' = 'AKIAEXAMPLE'", "'enable_iceberg_metadata_cache' = 'false'",
    ):
        assert expected in sql
    # The secret is escaped for the engine and redacted everywhere else.
    assert "'aws.s3.secret_key' = 'S3cr3t\\\\''key'" in sql
    assert "S3cr3t" not in redact_sql_credentials(sql)


@pytest.mark.parametrize(
    "key", ["iceberg.catalog.uri", "aws.s3.secret_key", "iceberg.catalog.oauth2.credential",
            "catalog.access.control", "jdbc_meta_cache_enable"],
)
def test_iceberg_catalog_properties_from_the_caller_are_limited(key):
    source = source_for("iceberg")
    with pytest.raises(CatalogError, match="cannot be set on an Iceberg catalog"):
        source.check_properties({key: "x"})
    # The request model already refuses credential names; the connector refuses the rest.
    with pytest.raises((CatalogError, ValueError)):
        connector_for("iceberg", connection_backed=True).build_alter_sql(
            "lake", ExternalCatalogAlter(properties={key: "x"})
        )


def test_iceberg_catalogs_alter_only_cache_settings_and_drop():
    connector = connector_for("iceberg", connection_backed=True)
    body = ExternalCatalogAlter(properties={"iceberg_table_cache_refresh_interval_sec": "30"})
    assert connector.build_alter_sql("lake", body) == (
        "ALTER CATALOG lake SET ('iceberg_table_cache_refresh_interval_sec' = '30')"
    )
    with pytest.raises(CatalogError):
        connector.build_alter_sql("lake", ExternalCatalogAlter(metastore_uri="thrift://x:9083"))
    assert connector.build_drop_sql("lake") == "DROP CATALOG lake"
    # An Iceberg catalog made in raw SQL before Iceberg was a source keeps its rules.
    legacy = connector_for("iceberg").build_alter_sql(
        "old", ExternalCatalogAlter(metastore_uri="thrift://hms:9083")
    )
    assert legacy == "ALTER CATALOG old SET ('hive.metastore.uris' = 'thrift://hms:9083')"
    info = describe_catalog_source("iceberg")
    assert info.label == "Iceberg" and "stages" not in info.object_groups


async def test_iceberg_object_kinds_come_from_the_rest_service(monkeypatch):
    import httpx

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path == "/v1/config":
            return httpx.Response(200, json={"overrides": {"prefix": "main"}})
        if path.endswith("/tables"):
            if request.url.params.get("pageToken"):
                return httpx.Response(200, json={"identifiers": [{"name": "t2"}]})
            return httpx.Response(200, json={"identifiers": [{"name": "t1"}],
                                             "next-page-token": "n"})
        if path.endswith("/views"):
            return httpx.Response(200, json={"identifiers": [{"name": "v1"}]})
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    source = source_for("iceberg")
    kinds = await source.read_objects(
        source.parse_endpoint("http://rest:8181"), KEYS, ["sales db"]
    )
    assert {key: kind.object_type for key, kind in kinds.items()} == {
        ("sales db", "t1"): "table", ("sales db", "t2"): "table", ("sales db", "v1"): "view",
    }
    assert any("/v1/main/namespaces/sales%20db/tables" in url for url in calls)
    assert all(SECRET not in url for url in calls)


# ── Kafka ─────────────────────────────────────────────────────────────────


def test_kafka_endpoint_lists_brokers_and_security_settings():
    source = source_for("kafka")
    plain = source.parse_endpoint("kafka-1:9092,kafka-2:9093,kafka-1:9092")
    assert plain.host == "kafka-1:9092,kafka-2:9093" and plain.port == 9092
    assert plain.options == {"security_protocol": "PLAINTEXT"}
    assert not source.requires_credentials(plain)
    sasl = source.parse_endpoint("b:9092?security_protocol=sasl_ssl&sasl_mechanism=scram-sha-512")
    assert sasl.options == {"security_protocol": "SASL_SSL", "sasl_mechanism": "SCRAM-SHA-512"}
    assert source.requires_credentials(sasl)
    assert source.parse_endpoint("b?security_protocol=SASL_PLAINTEXT").options[
        "sasl_mechanism"] == "PLAIN"
    assert not source.provides_catalog


@pytest.mark.parametrize(
    "value",
    ["user:pw@b:9092", "SASL_SSL://b:9092", "b:9092?sasl.password=x", "b:notaport",
     "b:9092?security_protocol=KERBEROS", "b:9092?sasl_mechanism=PLAIN",
     "b:9092?security_protocol=SASL_SSL&sasl_mechanism=GSSAPI", ",".join(["b:1"] * 2) + ",",
     ",".join(f"b{i}:9092" for i in range(40)), ""],
)
def test_kafka_endpoint_refuses_credentials_and_bad_values(value):
    with pytest.raises(CatalogError):
        source_for("kafka").parse_endpoint(value)


def test_kafka_routine_load_properties_carry_sasl_only_when_needed():
    source = source_for("kafka")
    plain = source.parse_endpoint("kafka:9092")
    assert source.routine_load_properties(plain, None) == {"kafka_broker_list": "kafka:9092"}
    ssl = source.parse_endpoint("kafka:9093?security_protocol=SSL")
    assert source.routine_load_properties(ssl, None) == {
        "kafka_broker_list": "kafka:9093", "property.security.protocol": "SSL",
    }
    sasl = source.parse_endpoint("kafka:9094?security_protocol=SASL_SSL&sasl_mechanism=PLAIN")
    with pytest.raises(CatalogError, match="SASL credentials"):
        source.routine_load_properties(sasl, None)
    props = source.routine_load_properties(sasl, Credentials("loader", SECRET))
    assert props["property.sasl.username"] == "loader"
    assert props["property.sasl.password"] == SECRET
    assert props["property.sasl.mechanism"] == "PLAIN"


def test_kafka_connections_back_no_catalog():
    source = source_for("kafka")
    with pytest.raises(CatalogError, match="does not back a catalog"):
        source.build_catalog_sql("k", source.parse_endpoint("kafka:9092"), KEYS)
    with pytest.raises(CatalogError):
        connector_for("kafka")
