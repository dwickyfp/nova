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


class TestStagePatternDoesNotClaimVariables:
    """A bare ``@name`` is a MySQL variable, not a stage — NOVA-25.

    Nova's stage syntax always carries at least one dotted segment
    (``@stage1.data.csv``; see ``docs/02-sql-worksheet.md``). Treating a bare
    ``@name`` as a stage made the dialect engine answer every
    ``SET @x = 1; SELECT @x`` with ``Stage 'x' not found`` — a message about a
    feature the user never touched, and the standard way drivers keep a value
    across queries on one connection.

    ``@stage`` used to match with zero dotted segments, so these cases failed
    before the pattern required one.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT @x",
            "SELECT 1 + @n",
            "SELECT @threshold FROM t WHERE amount > @threshold",
            "SET @my_stage = 1",
            "SELECT @x AS val",
        ],
    )
    def test_bare_at_name_is_not_a_stage(self, sql):
        result = parse_sql(sql)
        assert result.stage_refs == []
        assert result.command_type == CommandType.REGULAR

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT @@version_comment",
            "SELECT @@global.x",
            "SELECT @@session.time_zone",
        ],
    )
    def test_system_variables_are_not_stages(self, sql):
        """``@@name`` never was a stage; the lookbehind keeps it that way."""
        assert parse_sql(sql).stage_refs == []

    @pytest.mark.parametrize(
        ("sql", "stage_name", "file_name"),
        [
            ("SELECT * FROM @stage1.data.csv", "stage1", "data.csv"),
            ("SELECT * FROM @stage1.folder.file.parquet", "stage1", "file.parquet"),
            ("SELECT * FROM @silver.stage1.data.csv", "silver", "data.csv"),
            ("SELECT * FROM @DATALAKE.bronze.stage1.data.json", "DATALAKE", "data.json"),
            ("SELECT * FROM @my-stage.data-2026-06-19.csv", "my-stage", "data-2026-06-19.csv"),
        ],
    )
    def test_documented_stage_forms_still_parse(self, sql, stage_name, file_name):
        """Requiring a dot must not narrow the real syntax.

        Every form here is taken from ``docs/02-sql-worksheet.md`` and
        ``docs/04-stage-manager.md``; each carries at least one dotted segment.
        """
        result = parse_sql(sql)
        assert result.command_type == CommandType.STAGE_QUERY
        assert len(result.stage_refs) == 1
        assert result.stage_refs[0].stage_name == stage_name
        assert result.stage_refs[0].file_name == file_name

    def test_a_variable_and_a_stage_in_one_statement(self):
        """The two must not shadow each other."""
        result = parse_sql("SELECT @x, * FROM @stage1.data.csv")
        assert [ref.stage_name for ref in result.stage_refs] == ["stage1"]

    def test_variable_name_does_not_match_a_longer_one(self):
        """``@xy`` is not ``@x``; the pattern must not truncate at the boundary."""
        assert parse_sql("SELECT @xy").stage_refs == []


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
