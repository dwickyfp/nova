from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.sql_frontend.binding.catalog import Binder
from app.sql_frontend.binding.models import BoundColumn, BoundTable, TableName
from app.sql_frontend.binding.starrocks import StarRocksCatalogProvider
from app.sql_frontend.capabilities.starrocks import EngineCapabilities, StarRocksCapabilityProvider
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.errors import SemanticError


async def test_binding_is_lazy_ordered_cached_and_preserves_metadata():
    name = TableName("t", "db")
    catalog = AsyncMock()
    catalog.resolve_table.return_value = BoundTable(name, "BASE TABLE")
    columns = (
        BoundColumn("derived", "BIGINT", 2, True, generated_expression="id+1"),
        BoundColumn("id", "INT", 1, False, default="0"),
    )
    catalog.get_columns.return_value = columns
    catalog.get_details.return_value = BoundTable(
        name,
        "BASE TABLE",
        key_type="PRIMARY",
        primary_key_columns=("id",),
        partition_sql="PARTITION BY RANGE(id)",
    )
    binder = Binder(catalog)
    assert not catalog.mock_calls
    assert (await binder.resolve_table(name)).table_type == "BASE TABLE"
    assert catalog.get_columns.await_count == catalog.get_details.await_count == 0
    assert [column.name for column in await binder.get_columns(name)] == ["id", "derived"]
    assert (await binder.resolve_column(name, "id")).default == "0"
    assert (await binder.resolve_column(name, "derived")).generated_expression == "id+1"
    assert (await binder.get_details(name)).primary_key_columns == ("id",)
    await binder.get_details(name)
    catalog.resolve_table.assert_awaited_once()
    catalog.get_columns.assert_awaited_once()
    catalog.get_details.assert_awaited_once()
    other_request = Binder(catalog)
    await other_request.resolve_table(name)
    assert catalog.resolve_table.await_count == 2


async def test_missing_objects_are_clean_errors():
    catalog = AsyncMock()
    catalog.resolve_table.return_value = None
    binder = Binder(catalog)
    with pytest.raises(SemanticError, match="not accessible"):
        await binder.get_columns(TableName("hidden", "db"))
    catalog.get_columns.assert_not_awaited()
    catalog.resolve_table.return_value = BoundTable(TableName("t", "db"), "VIEW")
    catalog.get_columns.return_value = ()
    with pytest.raises(SemanticError, match="Column does not exist"):
        await binder.resolve_column(TableName("t", "db"), "hidden")


async def test_starrocks_columns_use_documented_fields_and_caller_identity():
    repo = AsyncMock()
    repo.execute_as_user.return_value = QueryResult(
        rows=[["id", "decimal(12,2)", 1, "NO", "0", ""]]
    )
    connection = object()

    def password():
        raise AssertionError("relay password decrypted")

    provider = StarRocksCatalogProvider(
        repo,
        ExecutionContext("alice", database="db", role="analyst", connection=connection),
        password,
    )
    columns = await provider.get_columns(TableName("t", catalog="external_catalog"))
    assert columns == (BoundColumn("id", "decimal(12,2)", 1, False, "0", None),)
    kwargs = repo.execute_as_user.await_args.kwargs
    assert kwargs["username"] == "alice" and kwargs["role"] == "analyst"
    assert kwargs["connected"] is connection and kwargs["password"] == ""
    assert "`external_catalog`.information_schema.columns" in kwargs["sql"]
    assert "COLUMN_TYPE" in kwargs["sql"] and "GENERATION_EXPRESSION" in kwargs["sql"]
    assert "ORDER BY ORDINAL_POSITION" in kwargs["sql"]


async def test_show_create_details_preserve_key_and_partition():
    repo = AsyncMock()
    ddl = (
        "CREATE TABLE db.t (id INT NOT NULL, x INT) PRIMARY KEY(id) PARTITION BY RANGE(id) "
        "(PARTITION p1 VALUES LESS THAN ('10')) DISTRIBUTED BY HASH(id) BUCKETS 1"
    )
    repo.execute_as_user.return_value = QueryResult(rows=[["t", ddl]])
    provider = StarRocksCatalogProvider(
        repo, ExecutionContext("alice", database="db", role="analyst"), lambda: "pw"
    )
    details = await provider.get_details(TableName("t", "db"))
    assert details.key_type == "PRIMARY"
    assert details.primary_key_columns == ("id",)
    assert details.partition_sql.startswith("PARTITION BY RANGE(id)")
    assert "VALUES LESS THAN ('10')" in details.partition_sql


async def test_metadata_failure_does_not_echo_engine_sql():
    repo = AsyncMock()
    repo.execute_as_user.return_value = QueryResult(error="sensitive engine detail")
    provider = StarRocksCatalogProvider(
        repo, ExecutionContext("alice", database="db"), lambda: "pw"
    )
    with pytest.raises(SemanticError) as error:
        await provider.resolve_table(TableName("t"))
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("version", ["4.1.4", "unknown", "4.99.0"])
def test_capabilities_are_conservative(version):
    profile = StarRocksCapabilityProvider().for_version(version)
    assert not profile.native_merge
    assert not profile.merge_all_by_name
    assert not profile.merge_schema_evolution


async def test_detection_is_optional_and_cached_per_target():
    provider = StarRocksCapabilityProvider()
    reader = AsyncMock(return_value="4.1.4")
    assert provider.for_version().version == "4.1.4"
    reader.assert_not_called()
    first = await provider.detect("cluster-one", reader)
    assert await provider.detect("cluster-one", reader) is first
    reader.assert_awaited_once()
    await provider.detect("cluster-two", reader)
    assert reader.await_count == 2
    with pytest.raises(ValueError):
        provider.register(EngineCapabilities())
