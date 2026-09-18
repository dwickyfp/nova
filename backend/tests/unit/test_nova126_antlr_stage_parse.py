"""Regression tests for the NOVA-126 (109-B) ANTLR4 stage-parse swap.

The slice moves the ``@stage`` registry off regex-on-text and onto the vendored
ANTLR4 grammar. Each test here pins one of the defects the issue names, plus the
invariant that surviving the swap means the statement the engine receives has no
dangling fragment where a reference used to be.

The defects:

* slash path ``@stage1/folder/x.csv`` was not detected — the old pattern stopped
  at ``@stage1`` and left ``/folder/x.csv`` in the statement, corrupting the SQL;
* glob ``@stage1.data/*.csv`` was not detected — the same, leaving ``*.csv``;
* ``@@version`` was read as a stage named ``version``;
* a ``@stage`` inside a comment was read as a stage;
* a syntax error had no ``line:col`` position.

The last one is asserted through :class:`StageParseError`, which is what the
parser raises when the grammar rejects the statement.
"""

from __future__ import annotations

import pytest

from app.modules.query.dialect.parser import (
    CommandType,
    StageParseError,
    parse_sql,
)
from app.modules.query.dialect.translator import StorageConfig, translate_stage_query


def _config(prefix: str = "datalake/bronze/stage1") -> StorageConfig:
    return StorageConfig(
        storage_type="s3",
        endpoint="http://minio:9000",
        bucket="nova-stages",
        base_prefix=prefix,
        access_key="testkey",
        secret_key="testsecret",
    )


def _translate(sql: str, stages: dict[str, StorageConfig] | None = None) -> str:
    parsed = parse_sql(sql)
    translated, _warnings = translate_stage_query(parsed, stages or {"stage1": _config()})
    return translated


# ---------------------------------------------------------------------------
# Defect 1: slash path
# ---------------------------------------------------------------------------


def test_slash_path_is_one_reference() -> None:
    """``@stage1/folder/x.csv`` is a single reference, not ``@stage1`` + tail."""
    result = parse_sql("SELECT * FROM @stage1/folder/x.csv")

    assert len(result.stage_refs) == 1
    ref = result.stage_refs[0]
    assert ref.stage_name == "stage1"
    assert ref.path_parts == ["folder"]
    assert ref.file_name == "x.csv"


def test_slash_path_translates_to_the_whole_object_key() -> None:
    """No ``/folder/x.csv`` fragment survives after the reference is rewritten."""
    result = parse_sql("SELECT * FROM @stage1/folder/x.csv")
    translated = _translate("SELECT * FROM @stage1/folder/x.csv")

    assert "@stage1" not in translated
    assert "/folder/x.csv" in translated  # inside the FILES() path, not dangling
    assert "s3://nova-stages/datalake/bronze/stage1/folder/x.csv" in translated
    assert translated.count("FILES(") == 1
    # The reference span covers the slash path, so nothing is appended after it.
    assert result.stage_refs[0].full_match == "@stage1/folder/x.csv"


# ---------------------------------------------------------------------------
# Defect 2: glob
# ---------------------------------------------------------------------------


def test_glob_is_one_reference_with_the_star_kept() -> None:
    """``@stage1.data/*.csv`` keeps the ``*`` in the path rather than dropping it."""
    result = parse_sql("SELECT * FROM @stage1.data/*.csv")

    assert len(result.stage_refs) == 1
    ref = result.stage_refs[0]
    assert ref.stage_name == "stage1"
    assert ref.full_match == "@stage1.data/*.csv"


def test_glob_translates_from_the_stage_root() -> None:
    """The glob becomes one FILES() path rooted at the stage, star included."""
    translated = _translate("SELECT * FROM @stage1.data/*.csv")

    assert "@stage1" not in translated
    assert "s3://nova-stages/datalake/bronze/stage1/data/*.csv" in translated
    assert translated.count("FILES(") == 1
    # No dangling ``/data/*.csv`` outside the FILES() call.
    after_files = translated.split("FILES(", 1)[1]
    assert "/data/*.csv" not in after_files.split(")", 1)[1]


def test_slash_glob_mix_is_one_reference() -> None:
    """Dots and slashes mix in one path (``@stage1/data/*.csv``)."""
    result = parse_sql("SELECT * FROM @stage1/data/*.csv")

    assert len(result.stage_refs) == 1
    assert result.stage_refs[0].full_match == "@stage1/data/*.csv"


# ---------------------------------------------------------------------------
# Defect 2b (NOVA-132): a numeric segment in a dotted path
#
# The lexer fuses the leading ``.`` of a numeric segment into the token itself
# (``.2024`` is one ``DECIMAL_VALUE``; ``.2024_01`` is one ``DOT_IDENTIFIER``),
# so the grammar's ``(stageSeparator stageSegment)*`` loop sees a segment with
# no preceding separator token and could not continue. The reference was then
# dropped, ``command_type`` fell to ``REGULAR`` and the raw ``@...`` token was
# forwarded to the engine untranslated — the silent dangling-SQL class NOVA-17
# exists to close. These inputs all resolved under the regex parser on ``main``,
# so their loss is a regression.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "stage_name", "path_parts", "file_name"),
    [
        ("SELECT * FROM @stage1.2024.csv", "stage1", [], "2024.csv"),
        ("SELECT * FROM @stage1.2024.01.data.csv", "stage1", ["2024", "01"], "data.csv"),
        ("SELECT * FROM @stage1.folder.2024.csv", "stage1", ["folder"], "2024.csv"),
        ("SELECT * FROM @stage1.data.2024.csv", "stage1", ["data"], "2024.csv"),
        ("SELECT * FROM @stage1.2.csv", "stage1", [], "2.csv"),
        ("SELECT * FROM @stage1.2024.data.parquet", "stage1", ["2024"], "data.parquet"),
        ("SELECT * FROM @stage1.2024_01.csv", "stage1", [], "2024_01.csv"),
        ("SELECT * FROM @stage1.2024.01.csv", "stage1", ["2024"], "01.csv"),
    ],
)
def test_numeric_dotted_path_resolves(
    sql: str,
    stage_name: str,
    path_parts: list[str],
    file_name: str,
) -> None:
    """A numeric segment in a dotted path is one reference, split like ``main``."""
    result = parse_sql(sql)

    assert result.errors == [], f"{sql!r} did not parse: {result.errors}"
    assert result.command_type == CommandType.STAGE_QUERY
    assert len(result.stage_refs) == 1
    ref = result.stage_refs[0]
    assert ref.stage_name == stage_name
    assert ref.path_parts == path_parts
    assert ref.file_name == file_name
    assert ref.full_match == sql.split("FROM ", 1)[1].split(" ", 1)[0]


def test_numeric_dotted_path_without_a_file_extension_is_a_directory() -> None:
    """``@stage1.2024`` names a directory; the digit is a path segment."""
    result = parse_sql("SELECT * FROM @stage1.2024")

    assert result.errors == []
    ref = result.stage_refs[0]
    assert (ref.stage_name, ref.path_parts, ref.file_name) == ("stage1", ["2024"], None)
    assert ref.is_directory is True


def test_numeric_dotted_path_translates_to_the_whole_object_key() -> None:
    """The FILES() path keeps the numeric segment; nothing is left dangling."""
    translated = _translate("SELECT * FROM @stage1.2024.01.data.csv")

    assert "@stage1" not in translated
    assert "s3://nova-stages/datalake/bronze/stage1/2024/01/data.csv" in translated
    assert translated.count("FILES(") == 1


def test_numeric_dotted_path_is_never_a_table_alias() -> None:
    """The trailing identifier stays the alias; the fused segment cannot eat it."""
    result = parse_sql("SELECT * FROM @stage1.2024.csv t")

    assert result.errors == []
    assert [ref.full_match for ref in result.stage_refs] == ["@stage1.2024.csv"]


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        # The slash path is the control: it was never broken, and must stay so.
        ("COPY INTO t FROM @stage1/2024/data.csv", ("@stage1/2024/data.csv", ["2024"], "data.csv")),
        ("COPY INTO t FROM @stage1.2024.csv", ("@stage1.2024.csv", [], "2024.csv")),
        (
            "COPY INTO t FROM @stage1.2024.01.data.csv",
            ("@stage1.2024.01.data.csv", ["2024", "01"], "data.csv"),
        ),
        ("COPY INTO t FROM @stage1.2024_01.csv", ("@stage1.2024_01.csv", [], "2024_01.csv")),
        (
            "COPY INTO @stage1.2024.csv FROM t",
            ("@stage1.2024.csv", [], "2024.csv"),
        ),
    ],
)
def test_numeric_dotted_path_on_the_nova_surfaces(
    sql: str,
    expected: tuple[str, list[str], str],
) -> None:
    """``COPY INTO`` / ``LIST`` recover the same shape from the token scan.

    The grammar cannot parse these Nova surfaces, so their references come from
    the token scan in :func:`_nova_surface_stage_refs`; it must expand a fused
    numeric token exactly as the grammar's ``decimalAtom`` does.
    """
    result = parse_sql(sql)

    assert result.errors == []
    full_match, path_parts, file_name = expected
    assert [(r.full_match, r.path_parts, r.file_name) for r in result.stage_refs] == [
        (full_match, path_parts, file_name)
    ]


def test_list_files_numeric_dotted_path_resolves() -> None:
    """``LIST FILES @stage1.2024.csv`` is one reference, not ``@stage1``."""
    result = parse_sql("LIST FILES @stage1.2024.csv")

    assert result.command_type == CommandType.STAGE_BROWSE
    assert [ref.full_match for ref in result.stage_refs] == ["@stage1.2024.csv"]


def test_numeric_dotted_path_is_not_a_stage_outside_table_position() -> None:
    """The fix stays positional: ``SELECT @stage1.2024.csv`` is a variable."""
    assert parse_sql("SELECT @stage1.2024.csv").stage_refs == []


@pytest.mark.parametrize(
    ("sql", "stage_name", "path_parts", "file_name"),
    [
        ("SELECT * FROM @stage1.2e3.csv", "stage1", [], "2e3.csv"),
        ("SELECT * FROM @stage1.a.2e3.csv", "stage1", ["a"], "2e3.csv"),
    ],
)
def test_numeric_dotted_path_exponent_form_resolves(
    sql: str, stage_name: str, path_parts: list[str], file_name: str
) -> None:
    """The exponent form (``.2e3``, a ``DOUBLE_VALUE``) is a fused separator too.

    Without it the token scan sees a bare ``DOUBLE_VALUE`` where a separator is
    expected and drops the whole reference, forwarding ``@stage1.2e3.csv``
    untranslated — the same class as the DECIMAL_VALUE regression.
    """
    result = parse_sql(sql)

    assert result.errors == []
    assert len(result.stage_refs) == 1
    ref = result.stage_refs[0]
    assert (ref.stage_name, ref.path_parts, ref.file_name) == (
        stage_name,
        path_parts,
        file_name,
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM @stage1.2024 t",
        "SELECT * FROM @stage1.2024.01 t",
        "SELECT * FROM @stage1.2024 AS t",
    ],
)
def test_a_trailing_numeric_segment_does_not_swallow_the_alias(sql: str) -> None:
    """A separator-fused decimal is one segment; it must not absorb the alias.

    ``@stage1.2024 t`` used to parse the alias ``t`` as a path segment
    (``@stage1.2024t``), drop the reference and forward the statement
    untranslated. The glued separator carries exactly one segment, so the alias
    stays outside the reference and the statement is translated.
    """
    result = parse_sql(sql)
    reference = sql.split("FROM ", 1)[1].split(" ", 1)[0]

    assert result.errors == []
    assert [ref.full_match for ref in result.stage_refs] == [reference]
    assert result.stage_refs[0].path_parts == reference.lstrip("@stage1.").split(".")


def test_a_trailing_numeric_segment_alias_form_translates() -> None:
    """The alias case is rewritten into ``FILES()`` rather than passed through."""
    translated = _translate("SELECT * FROM @stage1.2024 t")

    assert "@stage1" not in translated
    assert "s3://nova-stages/datalake/bronze/stage1/2024" in translated
    assert translated.endswith(" t")


def test_numeric_dotted_exponent_form_on_the_nova_surfaces() -> None:
    """``LIST``/``COPY INTO`` also expand the exponent-form fused token."""
    load = parse_sql("COPY INTO t FROM @stage1.2e3.csv")
    browse = parse_sql("LIST FILES @stage1.2e3.csv")

    assert [ref.full_match for ref in load.stage_refs] == ["@stage1.2e3.csv"]
    assert [ref.full_match for ref in browse.stage_refs] == ["@stage1.2e3.csv"]


# ---------------------------------------------------------------------------
# Defect 3: @@version is not a stage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT @@version",
        "SELECT @@version_comment",
        "SELECT @@global.x",
        "SELECT @@session.time_zone",
    ],
)
def test_system_variables_are_never_stages(sql: str) -> None:
    """``@@name`` is the engine's ``systemVariable``; the grammar never sees a stage."""
    result = parse_sql(sql)
    assert result.stage_refs == []
    assert result.command_type == CommandType.REGULAR


# ---------------------------------------------------------------------------
# Defect 4: @stage inside a comment / literal is not a stage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "-- FROM @comment\nSELECT 1",
        "SELECT 1 -- @trailing\n",
        "SELECT /* @block */ 1",
        "SELECT * FROM t /* JOIN @shadow */ WHERE 1",
        "SELECT 'FROM @literal'",
        "SELECT * FROM t WHERE note = '@stage1.data.csv'",
        'SELECT "@quoted"',
        "SELECT `@backticked`",
    ],
)
def test_a_stage_in_a_comment_or_literal_is_not_a_stage(sql: str) -> None:
    """Comment and literal text is one token to the lexer, so no node is created."""
    result = parse_sql(sql)
    assert result.stage_refs == [], f"{sql!r} produced a false stage"


def test_a_real_stage_after_a_comment_still_resolves() -> None:
    """Only the comment text is data; the live reference still counts."""
    result = parse_sql("SELECT 1 -- note\nFROM @stage1.data.csv")
    assert [ref.stage_name for ref in result.stage_refs] == ["stage1"]


# ---------------------------------------------------------------------------
# Defect 5: exact line:col on a syntax error
# ---------------------------------------------------------------------------


def _first_error(sql: str) -> StageParseError:
    parsed = parse_sql(sql)
    assert parsed.errors, f"expected a syntax error for {sql!r}"
    return parsed.errors[0]


def test_syntax_error_carries_line_and_column() -> None:
    """The failure itself is not swallowed; it is *positioned*.

    ``SELECT * FROM @stage1 FROM`` is malformed at the second ``FROM`` (line 1,
    column 22) — the position the grammar reports for the trailing token. (The
    statement carries an ``@`` deliberately: ``@``-free SQL now short-circuits
    before the grammar, and a token-position assertion needs the grammar path.)
    """
    error = _first_error("SELECT * FROM @stage1 FROM")

    assert (error.line, error.column) == (1, 22)
    assert str(error).startswith("1:22 ")
    assert error.message


def test_error_position_is_not_always_the_first_line() -> None:
    """The position is the real one, not a hard-coded ``1:0``."""
    error = _first_error("SELECT 1\nFROM (@stage1.data.csv")

    assert error.line == 2
    assert error.column > 0


def test_a_malformed_statement_registers_no_stage() -> None:
    """A partial tree must not drive a credential-bearing rewrite."""
    result = parse_sql("SELECT * FROM @stage1 FROM")

    assert result.stage_refs == []
    assert result.command_type == CommandType.REGULAR
    assert result.errors


# ---------------------------------------------------------------------------
# Invariant: the registry is the grammar's, and the pipeline is unchanged
# ---------------------------------------------------------------------------


def test_the_registry_comes_from_the_grammar_not_a_text_scan() -> None:
    """A reference is whatever the grammar's ``stageReference`` rule parsed.

    ``@stage1`` in an expression list is a user variable to the grammar, so the
    registry must not claim it — the old regex did.
    """
    assert parse_sql("SELECT @stage1.data.csv").stage_refs == []
    # And a reference the grammar does see is claimed whole.
    result = parse_sql("SELECT * FROM @stage1.data.csv")
    assert [ref.full_match for ref in result.stage_refs] == ["@stage1.data.csv"]


def test_lowercase_statements_parse_the_same_as_uppercase() -> None:
    """StarRocks keywords are case-insensitive; the lexer input must fold them."""
    lower = parse_sql("select * from @stage1.data.csv")
    upper = parse_sql("SELECT * FROM @stage1.data.csv")

    assert [ref.full_match for ref in lower.stage_refs] == [
        ref.full_match for ref in upper.stage_refs
    ]
    assert lower.command_type == upper.command_type == CommandType.STAGE_QUERY


def test_stage_name_case_is_preserved() -> None:
    """Folding the input for keyword *matching* must not fold the token text."""
    result = parse_sql("SELECT * FROM @Stage1.Data.CSV")

    assert result.stage_refs[0].stage_name == "Stage1"
    assert result.stage_refs[0].full_match == "@Stage1.Data.CSV"


def test_a_statement_without_a_stage_is_left_untouched() -> None:
    """The swap must not rewrite ordinary SQL."""
    sql = "SELECT id, name FROM users WHERE amount > 100"
    translated = _translate(sql)
    assert translated == sql


def test_a_variable_and_a_stage_in_one_statement() -> None:
    """A variable operand and a table-position stage survive together."""
    result = parse_sql("SELECT @x, * FROM @stage1.data.csv")

    assert [ref.stage_name for ref in result.stage_refs] == ["stage1"]
    translated = _translate("SELECT @x, * FROM @stage1.data.csv")
    assert translated.startswith("SELECT @x, * FROM FILES(")


# ---------------------------------------------------------------------------
# Defect 2d (NOVA-109 review): the trailing-dot hyphen form
#
# ``@stage-2.data.csv`` lexes the ``2.`` as a single ``DECIMAL_VALUE`` that
# carries a ``.`` separator. The grammar's ``decimalAtom`` absorbed the following
# atom into the same ``stageSegment``, so the reference read as ``stage-2.data``
# with path ``['csv']`` — the stage name was wrong and the file was lost. The
# regex parser on ``main`` segmented it as ``stage-2`` / ``data.csv``, so this is
# a regression: the dot inside the fused token is a separator, not segment text.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "stage_name", "path_parts", "file_name"),
    [
        ("SELECT * FROM @stage-2.data.csv", "stage-2", [], "data.csv"),
        ("SELECT * FROM @daily-load-2.data.csv", "daily-load-2", [], "data.csv"),
        ("SELECT * FROM @a-1.b.csv", "a-1", [], "b.csv"),
        ("SELECT * FROM @stage-2.2024.csv", "stage-2", [], "2024.csv"),
        ("SELECT * FROM @stage-2.2024", "stage-2", ["2024"], None),
        ("SELECT * FROM @stage-a.b.c", "stage-a", ["b", "c"], None),
    ],
)
def test_trailing_dot_hyphen_form_resolves(
    sql: str,
    stage_name: str,
    path_parts: list[str],
    file_name: str | None,
) -> None:
    """The fused dot splits segments; the hyphenated name matches ``main``."""
    result = parse_sql(sql)

    assert result.errors == [], f"{sql!r} did not parse: {result.errors}"
    assert result.command_type == CommandType.STAGE_QUERY
    assert len(result.stage_refs) == 1
    ref = result.stage_refs[0]
    assert (ref.stage_name, ref.path_parts, ref.file_name) == (stage_name, path_parts, file_name)


def test_trailing_dot_hyphen_form_translates_to_the_whole_object_key() -> None:
    """The translated key is ``.../stage-2/data.csv``; no fragment is left over."""
    translated = _translate(
        "SELECT * FROM @stage-2.data.csv",
        {"stage-2": _config("datalake/bronze/stage-2")},
    )

    assert "@stage-2" not in translated
    assert "s3://nova-stages/datalake/bronze/stage-2/data.csv" in translated
    assert translated.count("FILES(") == 1


# ---------------------------------------------------------------------------
# Defect 2e (NOVA-136): the Nova-surface token scan dropped hyphen + fused-dot
#
# ``LIST`` and ``COPY INTO`` have no rule in the StarRocks grammar, so their
# references come from the token scan in ``_nova_surface_stage_refs``. That scan
# was built before NOVA-109 taught the grammar ``stageSegment : stagePathAtom
# (MINUS_SYMBOL stagePathAtom)*``, so it stopped at the first hyphen: on
# ``LIST @stage-2.data.csv`` it returned ``('stage', [], None)`` while the
# ``FROM``-table path correctly returned ``('stage-2', [], 'data.csv')``. The
# scan must reconstruct the same registry the grammar does, so the two entry
# points cannot disagree.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reference", "stage_name", "path_parts", "file_name"),
    [
        # hyphen-joined segment; the ``2.`` token's dot is the separator
        ("@stage-2.data.csv", "stage-2", [], "data.csv"),
        ("@daily-load-2.data.csv", "daily-load-2", [], "data.csv"),
        ("@stage-2.2024.csv", "stage-2", [], "2024.csv"),
        ("@stage-2.2024", "stage-2", ["2024"], None),
        ("@stage-a.b.c", "stage-a", ["b", "c"], None),
        # reserved keyword as a path segment (``DEFAULT`` lexes by keyword type)
        ("@stage-2.data.default.csv", "stage-2", ["data"], "default.csv"),
        ("@stage.2024_01.csv", "stage", [], "2024_01.csv"),
        # leading fused decimal after a plain stage name
        ("@stage1.2024.csv", "stage1", [], "2024.csv"),
        ("@stage1.2024.01.data.csv", "stage1", ["2024", "01"], "data.csv"),
    ],
)
def test_nova_surface_token_scan_matches_the_grammar(
    reference: str,
    stage_name: str,
    path_parts: list[str],
    file_name: str | None,
) -> None:
    """``LIST`` / ``COPY INTO`` read a reference exactly as ``FROM @...`` does.

    The grammar path is the control: the Nova-surface scan has to return the
    same ``stage_name`` / ``path_parts`` / ``file_name`` from the same text, or
    the dialect disagrees with itself depending on which command the reference
    sits in.
    """
    expected = (stage_name, path_parts, file_name)
    from_control = parse_sql(f"SELECT * FROM {reference}")
    assert from_control.command_type == CommandType.STAGE_QUERY
    assert (
        from_control.stage_refs[0].stage_name,
        from_control.stage_refs[0].path_parts,
        from_control.stage_refs[0].file_name,
    ) == expected, f"grammar path drifted: {reference}"

    listed = parse_sql(f"LIST {reference}")
    assert listed.command_type == CommandType.STAGE_BROWSE
    assert listed.errors == []
    assert len(listed.stage_refs) == 1, f"LIST {reference} lost its reference"
    ref = listed.stage_refs[0]
    assert (ref.stage_name, ref.path_parts, ref.file_name) == expected
    assert ref.full_match == reference

    loaded = parse_sql(f"COPY INTO t FROM {reference}")
    assert loaded.command_type == CommandType.STAGE_LOAD
    assert loaded.errors == []
    assert len(loaded.stage_refs) == 1, f"COPY INTO ... {reference} lost its reference"
    ref = loaded.stage_refs[0]
    assert (ref.stage_name, ref.path_parts, ref.file_name) == expected
    assert ref.full_match == reference


def test_nova_surface_token_scan_keeps_the_whole_reference_span() -> None:
    """The scan does not stop short, so no ``data.csv`` fragment is left behind."""
    for sql, full_match in [
        ("LIST @stage-2.data.csv", "@stage-2.data.csv"),
        ("COPY INTO t FROM @stage-2.data.csv", "@stage-2.data.csv"),
        ("LIST @daily-load-2.data.csv", "@daily-load-2.data.csv"),
        ("COPY INTO t FROM @stage-2.2024.csv", "@stage-2.2024.csv"),
    ]:
        assert [ref.full_match for ref in parse_sql(sql).stage_refs] == [full_match], sql


# ---------------------------------------------------------------------------
# AC-5 mitigation: @-free SQL never builds the ANTLR4 tree
# ---------------------------------------------------------------------------


def test_an_at_free_statement_does_not_invoke_the_antlr_parse(monkeypatch) -> None:
    """The ordinary path is short-circuited before ``_parse_tree`` runs.

    The guard is sound because a stage always contains a literal ``@``; this
    pins the other half — that the guard actually fires, so the request path
    does not pay ANTLR for SQL the dialect can never rewrite.
    """
    from app.modules.query.dialect import parser as parser_module

    def _fail(*_args, **_kwargs):
        raise AssertionError("_parse_tree must not run for @-free SQL")

    monkeypatch.setattr(parser_module, "_parse_tree", _fail)

    result = parser_module.parse_sql("SELECT id, name FROM users WHERE amount > 100")

    assert result.command_type == CommandType.REGULAR
    assert result.stage_refs == []
    assert result.errors == []
    assert result.original_sql == "SELECT id, name FROM users WHERE amount > 100"
    assert result.base_sql == result.original_sql


def test_the_guard_is_the_literal_character_not_a_stage_name(monkeypatch) -> None:
    """A lone ``@`` still routes through the grammar — the guard is not a name test."""
    from app.modules.query.dialect import parser as parser_module

    calls: list[str] = []
    real_parse_tree = parser_module._parse_tree

    def _spy(sql: str):
        calls.append(sql)
        return real_parse_tree(sql)

    monkeypatch.setattr(parser_module, "_parse_tree", _spy)

    parser_module.parse_sql("SELECT @x FROM t")

    assert calls == ["SELECT @x FROM t"]


# ---------------------------------------------------------------------------
# Defect 6 (NOVA-134): a reserved keyword as an @stage path segment
#
# A path segment is data — a file or folder name the user already owns — so its
# reserved-ness is irrelevant: `@stage1.data.default.csv` must resolve exactly
# like `@stage1.data.foo.csv`. The `identifier` rule reaches only
# `LETTER_IDENTIFIER`, `DIGIT_IDENTIFIER`, a backquoted name and `nonReserved`,
# so spelling the segment atom as `identifier` dropped every reserved keyword
# (`DEFAULT`, `ORDER`, `GROUP`, `SELECT`, `TABLE`, `LIMIT`, `PRIMARY`, `VALUES`,
# …). The reference then vanished, `command_type` fell to `REGULAR` and the raw
# `@…` token was forwarded to the engine untranslated — the silent dangling-SQL
# class NOVA-17 exists to close. These inputs all resolved under the regex
# parser on `main@0a8a7fc`, so their loss is a regression against the bar in
# NOVA-109 AC #1 ("tanpa regresi").
# ---------------------------------------------------------------------------


#: The 45 reserved keywords QA measured as broken (NOVA-109 comment
#: `01a0b69e`). Kept whole so the coverage gap cannot silently reopen: a future
#: grammar change that re-narrows `stagePathAtom` fails this on the first run.
_RESERVED_PATH_KEYWORDS = [
    "default",
    "order",
    "group",
    "select",
    "table",
    "limit",
    "primary",
    "values",
    "where",
    "join",
    "union",
    "insert",
    "update",
    "delete",
    "create",
    "drop",
    "alter",
    "from",
    "into",
    "on",
    "as",
    "by",
    "having",
    "distinct",
    "case",
    "when",
    "then",
    "else",
    "left",
    "right",
    "inner",
    "outer",
    "cross",
    "using",
    "index",
    "key",
    "check",
    "grant",
    "revoke",
    "set",
    "show",
    "use",
    "describe",
    "explain",
    "analyze",
]


@pytest.mark.parametrize("keyword", _RESERVED_PATH_KEYWORDS)
def test_reserved_keyword_as_a_trailing_path_segment_resolves(keyword: str) -> None:
    """``@stage1.data.<keyword>.csv`` is one reference split like the baseline."""
    sql = f"SELECT * FROM @stage1.data.{keyword}.csv"
    result = parse_sql(sql)

    assert result.errors == [], f"{sql!r} did not parse: {result.errors}"
    assert result.command_type == CommandType.STAGE_QUERY
    assert len(result.stage_refs) == 1
    ref = result.stage_refs[0]
    assert (ref.stage_name, ref.path_parts, ref.file_name) == (
        "stage1",
        ["data"],
        f"{keyword}.csv",
    )
    assert ref.full_match == f"@stage1.data.{keyword}.csv"


@pytest.mark.parametrize(
    ("sql", "stage_name", "path_parts", "file_name"),
    [
        ("SELECT * FROM @stage1.data.default.csv", "stage1", ["data"], "default.csv"),
        ("SELECT * FROM @stage1.exports.default.parquet", "stage1", ["exports"], "default.parquet"),
        ("SELECT * FROM @stage1.default.csv", "stage1", [], "default.csv"),
        ("SELECT * FROM @stage1.data.order.csv", "stage1", ["data"], "order.csv"),
        ("SELECT * FROM @stage1.data.values.csv", "stage1", ["data"], "values.csv"),
        ("SELECT * FROM @stage1.data.primary.csv", "stage1", ["data"], "primary.csv"),
        # A reserved word as the *stage name* itself is a segment too;
        # `data.csv` is the two-part file, so the path is empty (baseline).
        ("SELECT * FROM @default.data.csv", "default", [], "data.csv"),
        # Reserved keyword on the slash path and on the mixed path.
        ("SELECT * FROM @stage1/order/data.csv", "stage1", ["order"], "data.csv"),
        ("SELECT * FROM @stage1.order/data.csv", "stage1", ["order"], "data.csv"),
    ],
)
def test_reserved_keyword_segment_shapes_match_the_baseline(
    sql: str, stage_name: str, path_parts: list[str], file_name: str
) -> None:
    """The shapes QA named, pinned to the ``main@0a8a7fc`` segmentation."""
    result = parse_sql(sql)

    assert result.errors == [], f"{sql!r} did not parse: {result.errors}"
    assert result.command_type == CommandType.STAGE_QUERY
    assert len(result.stage_refs) == 1
    ref = result.stage_refs[0]
    assert (ref.stage_name, ref.path_parts, ref.file_name) == (
        stage_name,
        path_parts,
        file_name,
    )


@pytest.mark.parametrize(
    "keyword",
    ["default", "order", "group", "select", "table", "values", "primary", "limit"],
)
def test_reserved_keyword_segment_translates_to_one_files_call(keyword: str) -> None:
    """End-to-end: the reference is rewritten into exactly one ``FILES()``.

    This is the half the string normalizer cannot see (NOVA-124 passes while the
    parser drops the reference), so it asserts on the *engine* SQL: no dangling
    ``@`` survives and the keyword stays a literal path segment.
    """
    sql = f"SELECT * FROM @stage1.data.{keyword}.csv"
    translated = _translate(sql)

    assert "@stage1" not in translated
    assert f"s3://nova-stages/datalake/bronze/stage1/data/{keyword}.csv" in translated
    assert translated.count("FILES(") == 1
    # Nothing dangles after the FILES() call.
    assert "@" not in translated


def test_reserved_keyword_segment_is_still_positional() -> None:
    """The fix stays positional: a variable operand is never claimed as a stage."""
    assert parse_sql("SELECT @stage1.data.default.csv").stage_refs == []


def test_reserved_keyword_segment_does_not_swallow_a_table_alias() -> None:
    """The keyword segment ends at the separator; a following alias stays outside."""
    result = parse_sql("SELECT * FROM @stage1.data.default.csv t")

    assert result.errors == []
    assert [ref.full_match for ref in result.stage_refs] == ["@stage1.data.default.csv"]


def test_non_keyword_segment_control_still_resolves() -> None:
    """The control QA named: an ordinary segment behaves exactly as before."""
    result = parse_sql("SELECT * FROM @stage1.data.foo.csv")

    assert result.errors == []
    assert result.command_type == CommandType.STAGE_QUERY
    ref = result.stage_refs[0]
    assert (ref.stage_name, ref.path_parts, ref.file_name) == ("stage1", ["data"], "foo.csv")
