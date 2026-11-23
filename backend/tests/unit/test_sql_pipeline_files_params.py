"""Unit tests for ``sql_pipeline`` FILES() property lowering.

A stage query must reach the engine with both its CSV properties and its
credentials; an earlier "already injected?" guard keyed on ``access_key`` made
the CSV pass a no-op and returned the file's raw line text as one column.
These assert the prepared statement directly, without MinIO or an engine.
"""

from app.modules.query.dialect.parser import parse_sql
from app.modules.query.dialect.translator import StorageConfig
from app.modules.query.sql_pipeline import prepare_stage_sql

CSV_PARAMS = {
    "csv.column_separator": ",",
    "csv.trim_space": "true",
    "csv.skip_header": "1",
}


async def test_csv_headers_are_lowered_per_reference_and_preserve_user_projection_aliases():
    sql = "SELECT a.id AS chosen,b.label FROM @left.data.csv a JOIN @right.data.csv b ON a.id=b.id"
    parsed = parse_sql(sql)
    configs = {
        name: StorageConfig("s3", "", "stages", name, access_key="K", secret_key="S")
        for name in ("left", "right")
    }
    prepared = await prepare_stage_sql(
        sql, parsed=parsed, stage_configs=configs,
        csv_columns_by_ref={ref.start: ["id", "label"] for ref in parsed.stage_refs},
    )
    assert prepared.engine_sql.startswith("SELECT a.id AS chosen,b.label FROM ")
    assert prepared.engine_sql.count("`$1` AS `id`, `$2` AS `label`") == 2
    assert prepared.engine_sql.count(") a") == 1 and prepared.engine_sql.count(") b") == 1
    assert "AS `left`" not in prepared.engine_sql and "AS `right`" not in prepared.engine_sql
    assert "'S'" not in prepared.redacted_sql


async def test_csv_lowering_supplies_an_alias_only_when_the_user_has_none():
    sql = "SELECT id FROM @data.values.csv"
    parsed = parse_sql(sql)
    prepared = await prepare_stage_sql(
        sql, parsed=parsed, stage_configs={"data": StorageConfig("s3", "", "stages", "data")},
        csv_columns_by_ref={parsed.stage_refs[0].start: ["id", "label"]},
    )
    assert prepared.engine_sql.endswith(") AS `data`")


async def test_aliased_parquet_stages_keep_qualified_columns_in_a_derived_relation():
    sql = "SELECT a.id FROM @left.data.parquet a JOIN @right.data.parquet b ON a.id=b.id"
    prepared = await prepare_stage_sql(
        sql,
        stage_configs={
            name: StorageConfig("s3", "", "stages", name) for name in ("left", "right")
        },
    )
    assert prepared.engine_sql.count("(SELECT * FROM FILES(") == 2
    assert ")) a JOIN" in prepared.engine_sql and ")) b ON a.id=b.id" in prepared.engine_sql


class TestPrepareStageSqlOrdering:
    """End to end through the shared pipeline, without an engine."""

    def test_stage_query_carries_both_csv_params_and_credentials(self):
        import asyncio

        stage_configs = {
            "products": StorageConfig(
                storage_type="s3",
                endpoint="http://minio:9000",
                bucket="stages",
                base_prefix="NOVA_ANALYTICS/public/products",
                access_key="K",
                secret_key="S",
                region="us-east-1",
            )
        }

        prepared = asyncio.run(
            prepare_stage_sql(
                "SELECT * FROM @products.products_new.csv LIMIT 5",
                stage_configs=stage_configs,
                csv_params=CSV_PARAMS,
                csv_columns=["id", "name", "amount"],
            )
        )

        assert "@products" not in prepared.engine_sql
        assert "FILES(" in prepared.engine_sql
        assert "csv.column_separator" in prepared.engine_sql
        assert "csv.skip_header" in prepared.engine_sql
        assert prepared.csv_columns == ["id", "name", "amount"]
        # The redacted form must not carry the credential values.
        assert "'K'" not in prepared.redacted_sql
        assert "'***'" in prepared.redacted_sql

    def test_stage_params_do_not_modify_literal_or_native_files_call(self):
        import asyncio

        config = StorageConfig(
            storage_type="s3",
            endpoint="",
            bucket="stages",
            base_prefix="products",
            access_key="K",
            secret_key="S",
        )
        sql = (
            "SELECT 'FILES(x)' AS note FROM @products.data.csv p "
            "JOIN FILES('path'='s3://other/data.csv') f ON p.id = f.id"
        )
        prepared = asyncio.run(
            prepare_stage_sql(sql, stage_configs={"products": config}, csv_params=CSV_PARAMS)
        )
        assert "'FILES(x)' AS note" in prepared.engine_sql
        assert "FILES('path'='s3://other/data.csv')" in prepared.engine_sql
        assert prepared.engine_sql.count("csv.column_separator") == 1

    def test_csv_params_do_not_reach_parquet_stage(self):
        import asyncio

        configs = {
            name: StorageConfig(
                storage_type="s3",
                endpoint="",
                bucket="stages",
                base_prefix=name,
                access_key="K",
                secret_key="S",
            )
            for name in ("csv_stage", "parquet_stage")
        }
        prepared = asyncio.run(
            prepare_stage_sql(
                "SELECT * FROM @csv_stage.a.csv a "
                "JOIN @parquet_stage.b.parquet b ON 1=1",
                stage_configs=configs,
                csv_params=CSV_PARAMS,
            )
        )
        assert prepared.engine_sql.count("csv.column_separator") == 1

    def test_fallback_credentials_only_touch_the_generated_stage_call(self, monkeypatch):
        import asyncio

        config = StorageConfig(
            storage_type="s3",
            endpoint="",
            bucket="stages",
            base_prefix="first",
            storage_connection="first",
        )
        monkeypatch.setattr(
            "app.modules.query.sql_pipeline.get_credential_params",
            lambda storage_type, connection: {"aws.s3.access_key": connection},
        )
        prepared = asyncio.run(
            prepare_stage_sql(
                "SELECT * FROM @first.a.csv a "
                "JOIN FILES('path'='s3://other/b.csv') b ON 1=1",
                stage_configs={"first": config},
            )
        )
        assert prepared.engine_sql.count("'aws.s3.access_key'='first'") == 1
        assert "FILES('path'='s3://other/b.csv')" in prepared.engine_sql
