"""Nova Catalog sources: StarRocks, PostgreSQL, Snowflake.

Every source is one ``CatalogSource`` subclass. These tests check what each one
decides on its own: how its endpoint is written, the engine catalog it builds,
and how it reads its relations' kinds from the source's metadata (the source
clients are faked). The registry and the alter/drop connectors are covered too.
"""

from __future__ import annotations

import sys
import types

import pytest

from app.common.sql_guard import redact_sql_credentials
from app.modules.catalog.connectors import connector_for
from app.modules.catalog.errors import CatalogError
from app.modules.catalog.schemas import ExternalCatalogAlter
from app.modules.catalog.sources import (
    NATIVE_GROUPS,
    OBJECT_GROUPS,
    SOURCES,
    CatalogSource,
    Credentials,
    Endpoint,
    ObjectKind,
    check_jdbc_properties,
    describe_catalog_source,
    parse_endpoint,
    source_for,
    validate_connector_type,
)
from app.modules.catalog.sources import snowflake as snowflake_source

PASSWORD = "Pg-s3cr3t\\'pw"
CREDENTIALS = Credentials("reader", PASSWORD)


# ── Registry ──────────────────────────────────────────────────────────────


def test_the_three_supported_sources_are_registered():
    assert sorted(SOURCES) == ["postgres", "snowflake", "starrocks"]
    for connector_type, source in SOURCES.items():
        assert isinstance(source, CatalogSource)
        assert source.connector_type == connector_type
        assert source.label and source.driver.jar and source.driver.driver_class
        assert source.object_groups == OBJECT_GROUPS


@pytest.mark.parametrize(
    ("value", "expected"),
    [(" PostgreSQL ", "postgres"), ("postgres", "postgres"), ("Snowflake", "snowflake"),
     ("starrocks", "starrocks")],
)
def test_connection_types_are_the_registered_sources(value, expected):
    assert validate_connector_type(value) == expected


@pytest.mark.parametrize("value", ["mysql", "iceberg", "hive", "jdbc", ""])
def test_other_connection_types_are_refused(value):
    with pytest.raises(CatalogError, match="expected one of postgres, snowflake, starrocks"):
        validate_connector_type(value)


def test_a_new_source_is_one_subclass():
    class DuckSource(CatalogSource):
        connector_type = "duck"
        label = "Duck"
        driver = SOURCES["postgres"].driver
        jdbc_scheme = "duck"
        default_port = 1234

        async def read_objects(self, endpoint, credentials, databases):
            return {}

    endpoint = DuckSource().parse_endpoint("lake:99/main?mode=ro")
    assert endpoint == Endpoint("lake", 99, "main", {"mode": "ro"})
    assert DuckSource().jdbc_uri(endpoint) == "jdbc:duck://lake:99/main?mode=ro"
    sql = DuckSource().build_catalog_sql("d", endpoint, CREDENTIALS)
    assert "'jdbc_uri' = 'jdbc:duck://lake:99/main?mode=ro'" in sql


@pytest.mark.parametrize(
    ("catalog_type", "source_type", "label", "groups"),
    [
        ("internal", "internal", "Nova", NATIVE_GROUPS),
        ("Internal", "internal", "Nova", NATIVE_GROUPS),
        ("postgres", "postgres", "PostgreSQL", OBJECT_GROUPS),
        ("snowflake", "snowflake", "Snowflake", OBJECT_GROUPS),
        ("starrocks", "starrocks", "StarRocks", OBJECT_GROUPS),
        ("iceberg", "iceberg", "Iceberg", OBJECT_GROUPS),
        ("Jdbc", "jdbc", "JDBC", OBJECT_GROUPS),
    ],
)
def test_catalogs_are_presented_by_their_source(catalog_type, source_type, label, groups):
    info = describe_catalog_source(catalog_type)
    assert (info.source_type, info.label, info.object_groups) == (source_type, label, groups)


def test_source_catalogs_show_only_data_groups():
    assert OBJECT_GROUPS == ("tables", "views", "materialized_views")
    for core in ("stages", "pipes", "tasks", "functions", "entities"):
        assert core in NATIVE_GROUPS and core not in OBJECT_GROUPS


# ── Endpoints ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("connector_type", "endpoint", "expected"),
    [
        ("postgres", "pg.internal:5433/shop", Endpoint("pg.internal", 5433, "shop")),
        ("postgresql", "pg/shop", Endpoint("pg", 5432, "shop")),
        ("postgres", "pg:5432/shop?sslmode=require",
         Endpoint("pg", 5432, "shop", {"sslmode": "require"})),
        ("starrocks", "sr-fe", Endpoint("sr-fe", 9030, None)),
        ("starrocks", "sr-fe:19030", Endpoint("sr-fe", 19030, None)),
        ("snowflake", "ORGNAME-ACCT01/NOVA_DB?warehouse=WH&role=READER",
         Endpoint("ORGNAME-ACCT01", 443, "NOVA_DB", {"warehouse": "WH", "role": "READER"})),
        ("snowflake", "orgname-acct01.snowflakecomputing.com/DB?warehouse=WH",
         Endpoint("orgname-acct01", 443, "DB", {"warehouse": "WH"})),
    ],
)
def test_endpoints_parse_into_metadata(connector_type, endpoint, expected):
    assert parse_endpoint(connector_type, endpoint) == expected


@pytest.mark.parametrize(
    ("connector_type", "endpoint"),
    [
        ("postgres", "admin:pw@pg:5432/shop"),  # user and password in the endpoint
        ("postgres", "postgresql://pg:5432/shop"),  # a URL scheme
        ("postgres", "pg:5432/shop?password=x"),  # a credential option
        ("postgres", "pg:5432/shop?user=admin"),
        ("postgres", "pg:5432/shop?options=-c"),  # not a transport option
        ("postgres", "pg"),  # Postgres needs its database
        ("postgres", "pg:0/shop"),
        ("postgres", "pg:70000/shop"),
        ("postgres", "pg:abc/shop"),
        ("postgres", "pg 5432/shop"),
        ("postgres", "-pg/shop"),
        ("postgres", ""),
        ("snowflake", "acct/DB"),  # no warehouse
        ("snowflake", "acct?warehouse=WH"),  # no database
        ("snowflake", "acct:443/DB?warehouse=WH"),  # no port in an account endpoint
        ("snowflake", "acct/DB?warehouse=WH&authenticator=SNOWFLAKE_JWT"),
        ("snowflake", "acct/DB?warehouse=WH&private_key_file=/k.p8"),
        ("snowflake", "acct/DB?warehouse=WH&token=x"),
        ("starrocks", "root@sr-fe:9030"),
    ],
)
def test_endpoints_that_could_carry_credentials_or_are_malformed_are_refused(
    connector_type, endpoint
):
    with pytest.raises(CatalogError):
        parse_endpoint(connector_type, endpoint)


# ── Engine catalogs ───────────────────────────────────────────────────────


def _sql(connector_type="postgres", endpoint="pg:5432/shop", properties=None):
    return source_for(connector_type).build_catalog_sql(
        "sales",
        parse_endpoint(connector_type, endpoint),
        CREDENTIALS,
        comment="Sales",
        properties=properties,
    )


def test_postgres_catalog_uses_the_bundled_driver_and_the_connection_endpoint():
    sql = _sql()
    assert sql.startswith("CREATE EXTERNAL CATALOG sales COMMENT 'Sales' PROPERTIES (")
    assert "'type' = 'jdbc'" in sql
    assert "'jdbc_uri' = 'jdbc:postgresql://pg:5432/shop'" in sql
    assert "'driver_url' = 'file:///opt/starrocks/drivers/postgresql-42.7.12.jar'" in sql
    assert "'driver_class' = 'org.postgresql.Driver'" in sql
    assert "http" not in sql  # never a download at create time


def test_starrocks_catalog_reads_the_other_cluster_through_the_mariadb_driver():
    sql = _sql("starrocks", "sr-fe:19030")
    assert "'jdbc_uri' = 'jdbc:mariadb://sr-fe:19030'" in sql
    assert "'driver_class' = 'org.mariadb.jdbc.Driver'" in sql
    assert "mariadb-java-client-3.3.2.jar" in sql


def test_snowflake_catalog_pins_json_results_and_the_nova_timezone(monkeypatch):
    monkeypatch.setattr(snowflake_source, "configured_timezone", lambda: "Asia/Jakarta")
    sql = _sql("snowflake", "ORGNAME-ACCT01/NOVA_DB?warehouse=WH&role=READER")
    assert (
        "'jdbc_uri' = 'jdbc:snowflake://ORGNAME-ACCT01.snowflakecomputing.com/?db=NOVA_DB"
        "&role=READER&warehouse=WH&JDBC_QUERY_RESULT_FORMAT=JSON&TIMEZONE=Asia%2FJakarta'"
    ) in sql
    assert "'driver_class' = 'net.snowflake.client.jdbc.SnowflakeDriver'" in sql
    assert "'driver_url' = 'file:///opt/starrocks/drivers/snowflake-jdbc-3.28.0.jar'" in sql
    # The PAT travels as the hidden JDBC password, never in the URI.
    assert "authenticator" not in sql and "token" not in sql.lower()
    assert "'user' = 'reader'" in sql


def test_password_is_escaped_for_the_engine_and_redacted_everywhere_else():
    sql = _sql()
    # Backslash and quote doubled, so the engine reads the password unchanged.
    assert "'password' = 'Pg-s3cr3t\\\\''pw'" in sql
    redacted = redact_sql_credentials(sql)
    assert "s3cr3t" not in redacted
    assert "'password' = '***'" in redacted


def test_credentials_never_appear_in_their_repr():
    assert PASSWORD not in repr(CREDENTIALS)


def test_show_create_catalog_output_for_jdbc_is_redacted():
    """Regression: the JDBC ``password`` property never survives redaction,
    whatever the engine prints for it."""
    shown = (
        "CREATE EXTERNAL CATALOG `pg_sales`\nPROPERTIES (\"driver_class\"  =  "
        "\"org.postgresql.Driver\",\n\"password\"  =  \"Pg-s3cr3t\",\n\"type\"  =  \"jdbc\",\n"
        "\"user\"  =  \"reader\",\n\"jdbc_uri\"  =  \"jdbc:postgresql://pg:5432/shop\"\n)"
    )
    assert "s3cr3t" not in redact_sql_credentials(shown)


@pytest.mark.parametrize(
    "key",
    ["driver_url", "driver_class", "jdbc_uri", "type", "user", "password", "checksum",
     "jdbc.password", "anything"],
)
def test_properties_that_choose_driver_endpoint_or_identity_are_refused(key):
    with pytest.raises(CatalogError):
        check_jdbc_properties({key: "x"})


def test_engine_tuning_properties_are_accepted():
    props = {"jdbc_meta_default_cache_enable": "false", "jdbc_connection_pool_size": "4"}
    assert check_jdbc_properties(props) == props
    assert "'jdbc_connection_pool_size' = '4'" in _sql(properties=props)


# ── Alter and drop of existing catalogs ───────────────────────────────────


@pytest.mark.parametrize("catalog_type", ["postgres", "snowflake", "starrocks", "mysql", "Jdbc"])
def test_foreign_catalogs_alter_only_pool_and_cache_properties(catalog_type):
    connector = connector_for(catalog_type)
    body = ExternalCatalogAlter(properties={"jdbc_connection_pool_size": "4"})
    assert connector.build_alter_sql("c", body) == (
        "ALTER CATALOG c SET ('jdbc_connection_pool_size' = '4')"
    )
    assert connector.build_drop_sql("c") == "DROP CATALOG c"
    with pytest.raises(CatalogError):
        connector.build_alter_sql("c", ExternalCatalogAlter(properties={"jdbc_uri": "x"}))


@pytest.mark.parametrize("catalog_type", ["iceberg", "hive"])
def test_existing_lakehouse_catalogs_can_still_be_altered_and_dropped(catalog_type):
    connector = connector_for(catalog_type)
    body = ExternalCatalogAlter(metastore_uri="thrift://hms:9083")
    assert connector.build_alter_sql("lake", body) == (
        "ALTER CATALOG lake SET ('hive.metastore.uris' = 'thrift://hms:9083')"
    )
    assert connector.build_drop_sql("lake") == "DROP CATALOG lake"
    assert not hasattr(connector, "build_create_sql")


def test_the_internal_catalog_is_never_changed():
    connector = connector_for("internal")
    with pytest.raises(CatalogError):
        connector.build_drop_sql("default_catalog")
    with pytest.raises(CatalogError):
        connector.build_alter_sql("default_catalog", ExternalCatalogAlter(comment="x"))


def test_unknown_catalog_types_are_refused():
    with pytest.raises(CatalogError):
        connector_for("paimon")


def test_catalog_names_are_identifiers():
    with pytest.raises(CatalogError):
        connector_for("postgres").build_drop_sql("x; DROP DATABASE y")


# ── Source metadata ───────────────────────────────────────────────────────


async def test_postgres_reads_kinds_from_pg_class_read_only(monkeypatch):
    import psycopg

    seen: dict = {}

    class Cursor:
        async def fetchall(self):
            return [
                ("public", "orders", "r"),
                ("public", "events", "p"),
                ("public", "remote", "f"),
                ("public", "jakarta_orders", "v"),
                ("public", "city_totals", "m"),
                ("public", "orders_id_seq", "S"),
            ]

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def set_read_only(self, value):
            seen["read_only"] = value

        async def execute(self, sql, params):
            seen["sql"], seen["params"] = sql, params
            return Cursor()

    async def connect(**kwargs):
        seen["connect"] = kwargs
        return Connection()

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    endpoint = parse_endpoint("postgres", "pg:5433/shop?sslmode=require")
    kinds = await source_for("postgres").read_objects(endpoint, CREDENTIALS, ["public"])

    assert kinds == {
        ("public", "orders"): ObjectKind("table", "Table"),
        ("public", "events"): ObjectKind("table", "Partitioned table"),
        ("public", "remote"): ObjectKind("table", "Foreign table"),
        ("public", "jakarta_orders"): ObjectKind("view", "View"),
        ("public", "city_totals"): ObjectKind("materialized_view", "Materialized view"),
    }
    assert seen["read_only"] is True
    assert seen["params"][1] == ["public"]
    connect = seen["connect"]
    assert (connect["host"], connect["port"], connect["dbname"]) == ("pg", 5433, "shop")
    assert (connect["user"], connect["password"], connect["sslmode"]) == (
        "reader", PASSWORD, "require"
    )


class _SnowflakeProgrammingError(Exception):
    pass


def _fake_snowflake(monkeypatch, results: dict[str, list[dict]], seen: dict) -> None:
    class Cursor:
        def __init__(self):
            self.rows: list[dict] = []

        def execute(self, sql):
            seen.setdefault("sql", []).append(sql)
            for command, rows in results.items():
                if sql.startswith(command + " IN DATABASE"):
                    self.rows = rows
                    return
            raise _SnowflakeProgrammingError(sql)

        def fetchall(self):
            return self.rows

    class Connection:
        def cursor(self, kind):
            seen["cursor"] = kind
            return Cursor()

        def close(self):
            seen["closed"] = True

    def connect(**kwargs):
        seen["connect"] = kwargs
        return Connection()

    connector = types.ModuleType("snowflake.connector")
    connector.connect = connect  # type: ignore[attr-defined]
    connector.DictCursor = "DictCursor"  # type: ignore[attr-defined]
    errors = types.ModuleType("snowflake.connector.errors")
    errors.ProgrammingError = _SnowflakeProgrammingError  # type: ignore[attr-defined]
    package = types.ModuleType("snowflake")
    package.connector = connector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "snowflake", package)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)
    monkeypatch.setitem(sys.modules, "snowflake.connector.errors", errors)


async def test_snowflake_reads_every_table_kind_with_show_commands(monkeypatch):
    seen: dict = {}
    _fake_snowflake(monkeypatch, {
        "SHOW TABLES": [
            {"schema_name": "SALES", "name": "ORDERS", "kind": "TABLE"},
            {"schema_name": "SALES", "name": "STAGING", "kind": "TRANSIENT"},
            {"schema_name": "SALES", "name": "DT", "kind": "TABLE", "is_dynamic": "Y"},
            {"schema_name": "SALES", "name": "ICE", "kind": "TABLE", "is_iceberg": "Y"},
            {"schema_name": "SALES", "name": "HYB", "kind": "TABLE", "is_hybrid": "Y"},
            {"schema_name": "OTHER", "name": "HIDDEN", "kind": "TABLE"},
        ],
        "SHOW VIEWS": [
            {"schema_name": "SALES", "name": "V", "is_materialized": "false"},
            {"schema_name": "SALES", "name": "SV", "is_secure": "true"},
            {"schema_name": "SALES", "name": "MV", "is_materialized": "true"},
        ],
        "SHOW EXTERNAL TABLES": [{"schema_name": "SALES", "name": "EXT"}],
        # SHOW MATERIALIZED/DYNAMIC/ICEBERG/EVENT TABLES fail: an edition
        # without the feature. The rest of the read still counts.
    }, seen)
    endpoint = parse_endpoint("snowflake", "acct/NOVA_DB?warehouse=WH&role=READER")

    kinds = await source_for("snowflake").read_objects(endpoint, CREDENTIALS, ["SALES"])

    assert kinds == {
        ("SALES", "ORDERS"): ObjectKind("table", "Table"),
        ("SALES", "STAGING"): ObjectKind("table", "Transient table"),
        ("SALES", "DT"): ObjectKind("table", "Dynamic table"),
        ("SALES", "ICE"): ObjectKind("table", "Iceberg table"),
        ("SALES", "HYB"): ObjectKind("table", "Hybrid table"),
        ("SALES", "EXT"): ObjectKind("table", "External table"),
        ("SALES", "V"): ObjectKind("view", "View"),
        ("SALES", "SV"): ObjectKind("view", "Secure view"),
        ("SALES", "MV"): ObjectKind("materialized_view", "Materialized view"),
    }
    assert all(sql.endswith(' IN DATABASE "NOVA_DB"') for sql in seen["sql"])
    connect = seen["connect"]
    assert (connect["account"], connect["user"], connect["password"], connect["role"]) == (
        "acct", "reader", PASSWORD, "READER"
    )
    # SHOW needs no warehouse: a sync never resumes one.
    assert "warehouse" not in connect
    assert seen["closed"] is True


async def test_starrocks_reads_kinds_from_the_other_clusters_information_schema(monkeypatch):
    import asyncmy

    seen: dict = {}

    class Cursor:
        def __init__(self):
            self.result: list = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql, params):
            seen.setdefault("sql", []).append((sql, params))
            if "materialized_views" in sql:
                self.result = [("sales", "daily_mv")]
            else:
                self.result = [("sales", "orders", "BASE TABLE"), ("sales", "v_orders", "VIEW"),
                               ("sales", "daily_mv", "VIEW")]

        async def fetchall(self):
            return self.result

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            seen["closed"] = True

    async def connect(**kwargs):
        seen["connect"] = kwargs
        return Connection()

    monkeypatch.setattr(asyncmy, "connect", connect)
    kinds = await source_for("starrocks").read_objects(
        parse_endpoint("starrocks", "sr-fe"), CREDENTIALS, ["sales"]
    )
    assert kinds == {
        ("sales", "orders"): ObjectKind("table", "Table"),
        ("sales", "v_orders"): ObjectKind("view", "View"),
        ("sales", "daily_mv"): ObjectKind("materialized_view", "Materialized view"),
    }
    assert seen["connect"]["port"] == 9030
    assert all(params == ["sales"] for _, params in seen["sql"])
    assert seen["closed"] is True


@pytest.mark.parametrize("connector_type", sorted(SOURCES))
async def test_no_databases_means_no_source_round_trip(connector_type):
    source = SOURCES[connector_type]
    endpoint = Endpoint("h", source.default_port, "db", {"warehouse": "WH"})
    assert await source.read_objects(endpoint, CREDENTIALS, []) == {}
