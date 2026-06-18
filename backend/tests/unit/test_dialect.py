"""Unit tests for @stage SQL dialect — parser, translator, injector, detector."""

import pytest

from app.modules.query.dialect.detector import (
    detect_format_from_content,
    detect_format_from_key,
    detect_format_from_listing,
)
from app.modules.query.dialect.injector import get_credential_params
from app.modules.query.dialect.parser import (
    CommandType,
    parse_sql,
    parse_stage_reference,
)
from app.modules.query.dialect.translator import (
    StorageConfig,
    build_files_function,
    build_s3_path,
    detect_format_from_filename,
    translate_stage_query,
)


# --- Parser Tests ---


class TestParser:
    def test_simple_select_no_stage(self):
        result = parse_sql("SELECT * FROM my_table")
        assert result.command_type == CommandType.REGULAR
        assert result.stage_refs == []

    def test_select_with_stage(self):
        result = parse_sql("SELECT * FROM @stage1.data.csv")
        assert result.command_type == CommandType.STAGE_QUERY
        assert len(result.stage_refs) == 1
        assert result.stage_refs[0].stage_name == "stage1"
        assert result.stage_refs[0].file_name == "data.csv"

    def test_stage_with_nested_path(self):
        result = parse_sql("SELECT * FROM @silver.stage1.folder.file.parquet")
        assert len(result.stage_refs) == 1
        ref = result.stage_refs[0]
        assert ref.stage_name == "silver"
        assert ref.file_name == "file.parquet"

    def test_stage_directory_reference(self):
        result = parse_sql("LIST @stage1.data/")
        assert result.command_type == CommandType.STAGE_BROWSE
        assert len(result.stage_refs) == 1

    def test_multiple_stage_refs(self):
        result = parse_sql(
            "SELECT * FROM @stage1.a.csv JOIN @stage2.b.csv ON id = id"
        )
        assert len(result.stage_refs) == 2
        assert result.stage_refs[0].stage_name == "stage1"
        assert result.stage_refs[1].stage_name == "stage2"

    def test_explain_with_stage(self):
        result = parse_sql("EXPLAIN SELECT * FROM @stage1.data.csv")
        assert result.command_type == CommandType.STAGE_QUERY

    def test_copy_into_load(self):
        result = parse_sql("COPY INTO my_table FROM @stage1.data.csv")
        assert result.command_type == CommandType.STAGE_LOAD

    def test_copy_into_export(self):
        result = parse_sql("COPY INTO @stage1.export FROM my_table")
        assert result.command_type == CommandType.STAGE_EXPORT


# --- Translator Tests ---


class TestTranslator:
    def _make_config(self, prefix="datalake/bronze/stage1"):
        return StorageConfig(
            storage_type="s3",
            endpoint="http://localhost:9000",
            bucket="nova-stages",
            base_prefix=prefix,
            access_key="testkey",
            secret_key="testsecret",
        )

    def test_build_s3_path_simple(self):
        config = self._make_config("datalake/bronze/stage1")
        ref = parse_sql("SELECT * FROM @stage1.data.csv").stage_refs[0]
        path = build_s3_path(config, ref)
        assert path == "s3://nova-stages/datalake/bronze/stage1/data.csv"

    def test_build_s3_path_nested(self):
        config = self._make_config("datalake/bronze/stage1")
        ref = parse_sql("SELECT * FROM @stage1.folder.subfolder.file.parquet").stage_refs[0]
        path = build_s3_path(config, ref)
        assert path == "s3://nova-stages/datalake/bronze/stage1/folder/subfolder/file.parquet"

    def test_build_files_function(self):
        config = self._make_config()
        func = build_files_function(
            "s3://bucket/path/data.csv", "csv", config
        )
        assert "FILES(" in func
        assert "'path'='s3://bucket/path/data.csv'" in func
        assert "'format'='csv'" in func
        assert "'aws.s3.access_key'='testkey'" in func

    def test_translate_simple_query(self):
        config = self._make_config()
        parsed = parse_sql("SELECT * FROM @stage1.data.csv")
        sql, warnings = translate_stage_query(parsed, {"stage1": config})
        assert "FILES(" in sql
        assert "@stage1" not in sql
        assert "s3://nova-stages/datalake/bronze/stage1/data.csv" in sql
        assert len(warnings) > 0

    def test_translate_missing_stage_raises(self):
        parsed = parse_sql("SELECT * FROM @nonexistent.data.csv")
        with pytest.raises(ValueError, match="nonexistent"):
            translate_stage_query(parsed, {})

    def test_translate_preserves_non_stage_sql(self):
        config = self._make_config()
        parsed = parse_sql("SELECT * FROM @stage1.data.csv WHERE id > 10")
        sql, _ = translate_stage_query(parsed, {"stage1": config})
        assert "WHERE id > 10" in sql

    def test_detect_format_csv(self):
        assert detect_format_from_filename("data.csv") == "csv"

    def test_detect_format_parquet(self):
        assert detect_format_from_filename("events.parquet") == "parquet"

    def test_detect_format_json(self):
        assert detect_format_from_filename("config.json") == "json"

    def test_detect_format_compressed(self):
        assert detect_format_from_filename("data.csv.gz") == "csv"
        assert detect_format_from_filename("events.parquet.snappy") == "parquet"

    def test_detect_format_unknown_defaults_csv(self):
        assert detect_format_from_filename("data") == "csv"


# --- Detector Tests ---


class TestDetector:
    def test_detect_from_key_csv(self):
        assert detect_format_from_key("datalake/data/file.csv") == "csv"

    def test_detect_from_key_parquet(self):
        assert detect_format_from_key("datalake/data/events.parquet") == "parquet"

    def test_detect_from_content_parquet(self):
        assert detect_format_from_content(b'PAR1') == "parquet"

    def test_detect_from_content_json(self):
        assert detect_format_from_content(b'{"key": "value"}') == "json"

    def test_detect_from_content_csv(self):
        assert detect_format_from_content(b'id,name,value\n1,test,100') == "csv"

    def test_detect_from_listing(self):
        keys = ["a.csv", "b.csv", "c.csv", "d.parquet"]
        assert detect_format_from_listing(keys) == "csv"

    def test_detect_from_empty_listing(self):
        assert detect_format_from_listing([]) == "csv"


# --- Injector Tests ---


class TestInjector:
    def test_get_s3_credentials(self):
        creds = get_credential_params("s3")
        # Should have access_key and secret_key from settings
        assert "aws.s3.access_key" in creds
        assert "aws.s3.secret_key" in creds

    def test_get_unknown_type_returns_empty(self):
        creds = get_credential_params("unknown")
        assert creds == {}
