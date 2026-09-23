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


class TestStageVersusVariable:
    """A stage and a user variable are told apart by *context*, not by a dot.

    NOVA-25's first fix banned the dot-free form to stop ``SELECT @x`` being
    read as a stage. That also killed the documented bare and directory forms
    (``@stage1``, ``@stage1/``) and silently disabled ``LIST`` — a form the
    docs show and ``CommandType.STAGE_BROWSE`` is named for. The two are
    spelled identically, so only the position can decide:

    * ``FROM`` / ``JOIN`` / ``INTO`` / ``LIST`` introduce a stage;
    * an operator, comma or opening paren introduces a variable operand;
    * a dotted path or a trailing ``/`` is always a stage.

    A test that only parametrised the dotted forms could not see the
    regression, which is why the cases below separate the two groups.
    """

    # --- stage positions -------------------------------------------------

    @pytest.mark.parametrize(
        ("sql", "stage_name"),
        [
            ("SELECT * FROM @stage1", "stage1"),
            ("SELECT * FROM @stage1/", "stage1"),
            ("SELECT * FROM @stage1.data.csv", "stage1"),
            ("LIST @stage1", "stage1"),
            ("LIST FILES @stage1", "stage1"),
            ("SELECT * FROM t JOIN @stage1 ON t.id = 1", "stage1"),
            ("COPY INTO tbl FROM @stage1", "stage1"),
            ("COPY INTO @stage1 FROM tbl", "stage1"),
            ("SELECT * FROM\n@stage1", "stage1"),
            ("select * from @stage1", "stage1"),
        ],
    )
    def test_bare_and_directory_stage_forms_are_stages(self, sql, stage_name):
        result = parse_sql(sql)
        assert len(result.stage_refs) == 1, f"{sql!r} lost its stage reference"
        assert result.stage_refs[0].stage_name == stage_name

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM @stage1",
            "SELECT * FROM @stage1/",
            "SELECT * FROM @stage1.data.csv",
        ],
    )
    def test_stage_position_yields_a_stage_query(self, sql):
        assert parse_sql(sql).command_type == CommandType.STAGE_QUERY

    def test_list_is_stage_browse_with_and_without_the_files_keyword(self):
        assert parse_sql("LIST @stage1").command_type == CommandType.STAGE_BROWSE
        assert parse_sql("LIST FILES @stage1").command_type == CommandType.STAGE_BROWSE

    # --- variable positions ----------------------------------------------

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT @x",
            "SELECT 1 + @n",
            "SELECT @threshold FROM t WHERE amount > @threshold",
            "SELECT @x AS val",
            "SELECT COALESCE(@x, 1)",
            "SELECT @x IS NULL",
            "SELECT (SELECT @x)",
            "SELECT * FROM t WHERE a BETWEEN @lo AND @hi",
        ],
    )
    def test_expression_operand_is_a_variable(self, sql):
        result = parse_sql(sql)
        assert result.stage_refs == [], f"{sql!r} was read as a stage"

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
        "sql",
        [
            "SET @my_stage = 1",
            "SET @x = 'foo'",
        ],
    )
    def test_a_variable_named_like_a_stage_is_still_a_variable(self, sql):
        """The name must not decide it.

        ``SET @my_stage = 1`` shares its name with a plausible stage; the
        ``SET`` keyword puts it in an assignment, so it is a variable. A fix
        that keyed on the name would get this wrong.
        """
        result = parse_sql(sql)
        assert result.stage_refs == []
        assert result.command_type == CommandType.REGULAR

    # --- both at once ----------------------------------------------------

    def test_a_variable_and_a_dotted_stage_in_one_statement(self):
        result = parse_sql("SELECT @x, * FROM @stage1.data.csv")
        assert [ref.stage_name for ref in result.stage_refs] == ["stage1"]

    def test_a_variable_and_a_bare_stage_in_one_statement(self):
        result = parse_sql("SELECT @x, * FROM @stage1")
        assert [ref.stage_name for ref in result.stage_refs] == ["stage1"]

    def test_two_stages_in_one_statement(self):
        result = parse_sql("SELECT * FROM @stage1 JOIN @stage2.data.csv")
        assert [ref.stage_name for ref in result.stage_refs] == ["stage1", "stage2"]

    # --- comma-separated table lists --------------------------------------

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("SELECT * FROM @stage1, @stage2", ["stage1", "stage2"]),
            (
                "SELECT * FROM @stage1 AS a, @stage2 AS b",
                ["stage1", "stage2"],
            ),
            (
                "SELECT * FROM @stage1, @stage2, @stage3",
                ["stage1", "stage2", "stage3"],
            ),
            ("SELECT * FROM t, @stage1", ["stage1"]),
            ("SELECT * FROM @stage1, t, @stage2", ["stage1", "stage2"]),
            (
                "SELECT * FROM @stage1 a, @stage2 b, @stage3 c",
                ["stage1", "stage2", "stage3"],
            ),
        ],
    )
    def test_a_comma_in_a_from_list_introduces_another_stage(self, sql, expected):
        """A comma in a table list inherits the clause, it does not end it.

        Treating every comma as an expression separator lost the second
        reference in ``FROM @stage1, @stage2`` — one of the forms the previous
        regex handled, so it was a regression. The distinguishing question is
        which clause the comma sits in, and it is answered by
        ``_comma_is_in_table_clause``.
        """
        result = parse_sql(sql)
        assert [ref.stage_name for ref in result.stage_refs] == expected

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("SELECT @x, @y", []),
            ("SELECT @x, @y FROM @stage1", ["stage1"]),
            ("SELECT 1 + @n, @m", []),
            ("SELECT @x, @y FROM @stage1, @stage2", ["stage1", "stage2"]),
            ("SELECT COALESCE(@x, @y)", []),
            ("SELECT * FROM t WHERE a IN (@x, @y)", []),
            ("SELECT @x FROM t GROUP BY @y", []),
        ],
    )
    def test_a_comma_in_an_expression_list_does_not_create_stages(
        self, sql, expected
    ):
        """The other half: an expression comma must stay a variable operand."""
        result = parse_sql(sql)
        assert [ref.stage_name for ref in result.stage_refs] == expected

    def test_a_join_condition_operand_is_not_a_stage(self):
        """``ON a.id = @x`` — ``@x`` is a comparison operand, not a table."""
        result = parse_sql("SELECT * FROM t JOIN @stage1 ON a.id = @x")
        assert [ref.stage_name for ref in result.stage_refs] == ["stage1"]

    def test_a_comma_after_a_join_condition_returns_to_the_table_list(self):
        """``FROM a JOIN b ON 1=1, c`` is valid StarRocks.

        The comma ends the join condition and resumes the table list, so the
        reference after it is a stage.
        """
        result = parse_sql("SELECT * FROM @s1 JOIN @s2 ON 1=1, @s3")
        assert [ref.stage_name for ref in result.stage_refs] == ["s1", "s2", "s3"]

    def test_a_subquery_has_its_own_clause_context(self):
        """A nested ``SELECT`` must not govern the outer ``FROM``.

        ``FROM (SELECT @x, @y FROM t), @stage2`` — the inner comma is an
        expression separator, the outer one introduces a table reference.
        """
        inner = parse_sql("SELECT * FROM (SELECT @x, @y FROM t), @stage2")
        assert [ref.stage_name for ref in inner.stage_refs] == ["stage2"]

        both = parse_sql("SELECT * FROM (SELECT * FROM @stage1), @stage2")
        assert [ref.stage_name for ref in both.stage_refs] == ["stage1", "stage2"]

    # --- comments must not change the answer -----------------------------

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM /* c */ @stage1",
            "SELECT * FROM -- c\n @stage1",
            "SELECT * FROM -- c\n@stage1",
            "SELECT * FROM /* a */ /* b */ @stage1",
        ],
    )
    def test_a_comment_before_a_stage_does_not_demote_it(self, sql):
        """The engine skips comments; the classifier has to as well.

        Without comment handling the preceding token is comment text, which is
        neither a stage keyword nor an operator, and the reference silently
        becomes a variable.
        """
        assert len(parse_sql(sql).stage_refs) == 1

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT -- c\n@x",
            "SELECT /* c */ @x",
        ],
    )
    def test_a_comment_before_a_variable_does_not_promote_it(self, sql):
        assert parse_sql(sql).stage_refs == []

    def test_a_comment_is_not_a_stage(self):
        """A reference inside a comment is not executed, so it is not a stage."""
        assert parse_sql("-- @notastage\nSELECT 1").stage_refs == []
        assert parse_sql("SELECT /* @notastage */ 1").stage_refs == []

    def test_a_terminated_comment_does_not_hide_the_stage_keyword(self):
        """``SELECT 1 -- c\\nFROM @stage1`` — ``FROM`` is the live keyword."""
        result = parse_sql("SELECT 1 -- c\nFROM @stage1")
        assert [ref.stage_name for ref in result.stage_refs] == ["stage1"]

    def test_double_dash_inside_a_literal_is_not_a_comment(self):
        result = parse_sql("SELECT * FROM @stage1 WHERE a = '-- x'")
        assert len(result.stage_refs) == 1

    # --- text inside a literal is data, not SQL --------------------------

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 'data from @here' AS note",
            "SELECT * FROM logs WHERE msg = 'imported from @src'",
            "SELECT * FROM t WHERE name = 'FROM @x'",
            "INSERT INTO t VALUES ('COPY INTO @s FROM tbl')",
            "SELECT 'JOIN @stg'",
            "SELECT 'USING @stg'",
            "SELECT 'LIST @stg'",
            "SELECT 'it''s from @x'",
            'SELECT "FROM @x"',
            "SELECT `FROM @x`",
            "SELECT * FROM t -- FROM @x",
            "SELECT 1 /* FROM @x */",
            "SELECT * FROM t /* JOIN @shadow */ WHERE 1",
            "SELECT * FROM t /*\nFROM @x\n*/ WHERE 1",
        ],
    )
    def test_a_stage_keyword_inside_a_literal_or_comment_is_not_a_stage(self, sql):
        """The text of a literal is data the engine never parses as SQL.

        NOVA-29: the context classifier scanned the raw text backwards and read
        the ``FROM`` in ``'FROM @x'`` as the live keyword, so a plain string
        literal was rewritten into ``FILES(...)`` with storage credentials
        injected into it. A match inside a literal or comment must be dropped
        before classification runs, not merely classified differently.
        """
        result = parse_sql(sql)
        assert result.stage_refs == [], f"{sql!r} was read as a stage reference"
        assert result.command_type == CommandType.REGULAR

    def test_a_real_stage_after_a_literal_still_resolves(self):
        """Only the literal is data; the live ``FROM @stage1`` still counts."""
        result = parse_sql("SELECT 'FROM @x' FROM @stage1.data.csv")
        assert [ref.stage_name for ref in result.stage_refs] == ["stage1"]

    def test_an_unterminated_literal_swallows_the_rest_of_the_statement(self):
        """An unclosed quote makes the tail data, so no stage is claimed."""
        assert parse_sql("SELECT 'FROM @x").stage_refs == []
        assert parse_sql("SELECT 1 /* FROM @x").stage_refs == []

    # --- LIST without a stage --------------------------------------------

    @pytest.mark.parametrize("sql", ["LIST", "LIST FILES", "LIST TABLES"])
    def test_list_without_a_stage_is_not_a_stage_command(self, sql):
        """A ``LIST`` with nothing to browse gets no stage reference.

        It is reported as REGULAR so the statement travels the ordinary path
        untouched and the engine answers with its own syntax error naming
        ``LIST`` — visible, not silent. Nova has no ``LIST`` implementation and
        StarRocks has no ``LIST`` statement (measured: every form is a syntax
        error at the engine), which is stated in ``app/proxy/README.md``.
        """
        result = parse_sql(sql)
        assert result.stage_refs == []
        assert result.command_type == CommandType.REGULAR

    # --- substrings ------------------------------------------------------

    def test_variable_name_does_not_match_a_longer_one(self):
        """``@xy`` is not ``@x``; the token must not truncate at the boundary."""
        assert parse_sql("SELECT @xy").stage_refs == []

    def test_stage_name_does_not_match_a_longer_one(self):
        assert parse_sql("SELECT * FROM @stage1x").stage_refs[0].stage_name == "stage1x"

    def test_a_dotted_reference_in_expression_position_is_not_a_stage(self):
        """A stage is a *table-position* operand, and the grammar now enforces it.

        The regex parser read any dotted ``@name`` as a stage, wherever it stood.
        That is the same class of false positive as ``@@version`` and a ``@stage``
        in a comment: ``SELECT @stage1.data.csv`` is an expression list, not a
        ``FROM`` list, so ``@stage1.data.csv`` there is a user variable reference
        (dotted variable names are legal) and must not be rewritten into a
        credential-bearing ``FILES()`` call. The grammar's ``stageReference``
        lives under ``relationPrimary``, so it cannot appear in a select list —
        which is exactly the guarantee NOVA-126 is about.
        """
        result = parse_sql("SELECT @stage1.data.csv")
        assert result.stage_refs == []
        assert result.command_type == CommandType.REGULAR


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

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM t WHERE name = 'FROM @stage1'",
            "SELECT 'data from @stage1' AS note",
            "SELECT 1 /* FROM @stage1 */",
        ],
    )
    def test_translation_leaves_a_literal_naming_a_stage_untouched(self, sql):
        """The engine must receive the user's SQL byte for byte.

        NOVA-29: a literal that named a real stage was rewritten *inside the
        literal* into ``FILES(...)`` with the access key and secret key
        injected, and the unbalanced quotes made the statement unrunnable.
        """
        config = self._make_config()
        parsed = parse_sql(sql)
        translated, warnings = translate_stage_query(parsed, {"stage1": config})
        assert translated == sql
        assert warnings == []
        assert "testsecret" not in translated
        assert "testkey" not in translated
        assert "FILES(" not in translated

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT '@stage1.data.csv' AS label, * FROM @stage1.data.csv",
            "SELECT /* @stage1.data.csv */ * FROM @stage1.data.csv",
            "SELECT * FROM @stage1.data.csv JOIN @stage1.data.csv ON 1=1",
        ],
    )
    def test_translation_uses_parser_spans_for_repeated_stage_text(self, sql):
        parsed = parse_sql(sql)
        translated, _ = translate_stage_query(parsed, {"stage1": self._make_config()})
        assert translated.count("FILES(") == len(parsed.stage_refs)
        assert translated.count("@stage1.data.csv") == (
            sql.count("@stage1.data.csv") - len(parsed.stage_refs)
        )

    def test_translation_preserves_interleaved_comment_in_stage_reference(self):
        sql = "SELECT * FROM @stage1 /* gap */ .data.csv"
        parsed = parse_sql(sql)
        translated, _ = translate_stage_query(parsed, {"stage1": self._make_config()})
        assert parsed.stage_refs[0].full_match == "@stage1 /* gap */ .data.csv"
        assert "@stage1" not in translated
        assert "FILES(" in translated

    def test_files_properties_escape_sql_quotes(self):
        config = self._make_config()
        config.secret_key = "sec'ret"
        sql = build_files_function("s3://bucket/it's.csv", "csv", config)
        assert "s3://bucket/it''s.csv" in sql
        assert "sec''ret" in sql

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


class TestBareAndDirectoryStagesReachTranslation:
    """The reference must *reach* ``translate_stage_query``, not just parse.

    ``refs != []`` is a weaker claim than "the statement is translated": a
    reference that no caller acts on is still a dead feature. These run the
    translation itself, which is the step that turns ``@stage1`` into
    ``FILES(...)`` — the step that ``LIST`` and the bare forms silently skipped
    when the pattern required a dot.
    """

    @staticmethod
    def _config(prefix="stages/products"):
        return StorageConfig(
            storage_type="s3",
            endpoint="http://minio:9000",
            bucket="stages",
            base_prefix=prefix,
            access_key="testkey",
            secret_key="testsecret",
        )

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM @stage1",
            "SELECT * FROM @stage1/",
            "SELECT * FROM @stage1.data.csv",
            "LIST @stage1",
            "LIST FILES @stage1",
        ],
    )
    def test_the_stage_is_rewritten_to_a_files_call(self, sql):
        parsed = parse_sql(sql)
        translated, _ = translate_stage_query(parsed, {"stage1": self._config()})

        assert "@stage1" not in translated, f"{sql!r} reached the engine untranslated"
        assert "FILES(" in translated

    def test_every_stage_in_a_comma_list_is_rewritten(self):
        """Both references must reach the engine as ``FILES(...)``.

        The count assertion in the parser tests is not enough on its own: a
        reference that parses but is never substituted still ships ``@stage2``
        to StarRocks, where no such table exists. This runs the translation and
        checks the text the engine would actually receive.
        """
        parsed = parse_sql("SELECT * FROM @stage1, @stage2")
        translated, _ = translate_stage_query(
            parsed, {"stage1": self._config("a"), "stage2": self._config("b")}
        )

        assert "@stage" not in translated, translated
        assert translated.count("FILES(") == 2, translated
        assert "s3://stages/a" in translated
        assert "s3://stages/b" in translated

    def test_translation_builds_the_directory_path_for_a_bare_stage(self):

        """A bare stage names the prefix itself, not a file under it."""
        parsed = parse_sql("SELECT * FROM @stage1")
        translated, warnings = translate_stage_query(parsed, {"stage1": self._config()})

        assert "s3://stages/stages/products" in translated
        # No file name, so the format falls back and says so.
        assert any("No file extension" in warning for warning in warnings)

    def test_translation_of_a_list_statement_reaches_the_engine(self):
        """``LIST @stage1`` is translated even though the engine cannot run it.

        Nova has no ``LIST`` implementation and StarRocks has no ``LIST``
        statement, so this statement fails at the engine either way. What
        matters for the regression is *where* it fails: previously the reference
        was not recognised, so the user's ``LIST @stage1`` text went to the
        engine verbatim and the stage was never resolved. Now the rewrite
        happens and the failure is the engine rejecting ``LIST`` — the missing
        feature, not a missing reference.
        """
        parsed = parse_sql("LIST @stage1")
        translated, _ = translate_stage_query(parsed, {"stage1": self._config()})

        assert "FILES(" in translated
        assert translated.startswith("SELECT * FROM FILES(")
        assert "'list_files_only'='true'" in translated

    def test_an_unknown_stage_still_raises(self):
        """Resolution is not weakened: a stage with no config is still refused."""
        parsed = parse_sql("SELECT * FROM @nope")
        with pytest.raises(ValueError, match="Stage 'nope' not found"):
            translate_stage_query(parsed, {})

    def test_a_variable_never_reaches_translation(self):
        """A variable-position ``@x`` produces no reference, so nothing is rewritten."""
        parsed = parse_sql("SELECT @x FROM @stage1")
        translated, _ = translate_stage_query(parsed, {"stage1": self._config()})

        assert translated.startswith("SELECT @x FROM FILES(")


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
