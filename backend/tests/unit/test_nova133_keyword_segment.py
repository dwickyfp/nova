"""Regression tests for NOVA-133: a reserved keyword as a ``@stage`` path segment.

The defect (NOVA-17 D1):

``@stage1.data.default.csv`` was normalised correctly (109-C kept the path) but
lost its stage reference at the **parse** stage. The grammar's ``stagePathAtom``
accepted only ``identifier`` / ``*`` / an integer / a decimal, and a *reserved*
keyword is in none of those, so the segment did not match, the reference
collapsed, ``parse_sql`` returned ``REGULAR`` with ``stage_refs=[]``, and the raw
``@…`` token was forwarded to the engine without the ``FILES()`` rewrite
(``sql_pipeline``). The regex parser this grammar replaced accepted any
``[A-Za-z_][A-Za-z0-9_$]*`` segment, so this is a regression, not a new surface.

Two classes of assertions live here:

* the nine keyword categories from the issue, asserted end to end
  (``_normalize_default_schema_qualification`` → ``parse_sql`` →
  ``prepare_stage_sql`` yields ``FILES(...)``, never a raw ``@``);
* a completeness sweep over **every** keyword-like token the vendored lexer
  defines, so a keyword added later cannot silently reopen the defect;
* the properties that were already correct and must stay so: ``@@version``, a
  ``@stage`` inside a comment or literal, the numeric dotted form (NOVA-132),
  and table-alias non-consumption.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules.query.dialect.parser import parse_sql
from app.modules.query.dialect.translator import StorageConfig
from app.modules.query.service import QueryService
from app.modules.query.sql_pipeline import prepare_stage_sql
from app.sql_dialect.grammar.StarRocksLexer import StarRocksLexer

STAGE_NAME = "stage1"
STAGE_CONFIG = StorageConfig(
    storage_type="s3",
    endpoint="http://minio:9000",
    bucket="nova-stages",
    base_prefix="datalake/bronze/stage1",
    access_key="testkey",
    secret_key="testsecret",
)

# The nine category keywords the issue names, each also the token whose lexer
# rule shadows ``identifier`` in a path position.
D1_KEYWORDS = (
    "default",
    "order",
    "group",
    "select",
    "from",
    "table",
    "values",
    "key",
    "index",
)


def _normalize(sql: str) -> str:
    return QueryService._normalize_default_schema_qualification(sql)


def _stage_path(sql: str) -> str:
    """The FILES() path the translated statement points at."""
    match = re.search(r"'path'='([^']*)'", sql)
    assert match, f"no FILES() path in {sql!r}"
    return match.group(1)


# ---------------------------------------------------------------------------
# The nine category keywords, end to end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("keyword", D1_KEYWORDS)
def test_keyword_segment_survives_normalize_and_parse(keyword: str) -> None:
    sql = f"SELECT * FROM @{STAGE_NAME}.data.{keyword}.csv"

    normalized = _normalize(sql)
    assert normalized == sql, "the normalizer must leave the stage path intact"

    parsed = parse_sql(normalized)
    assert parsed.command_type.value == "stage_query"
    assert not parsed.errors, [e.message for e in parsed.errors]
    assert len(parsed.stage_refs) == 1
    ref = parsed.stage_refs[0]
    assert (ref.stage_name, ref.path_parts, ref.file_name) == (
        STAGE_NAME,
        ["data"],
        f"{keyword}.csv",
    )


@pytest.mark.parametrize("keyword", D1_KEYWORDS)
async def test_keyword_segment_reaches_engine_as_files(keyword: str) -> None:
    """The whole pipeline: no raw ``@`` token may leave the translator."""
    sql = f"SELECT * FROM @{STAGE_NAME}.data.{keyword}.csv"
    prepared = await prepare_stage_sql(sql, stage_configs={STAGE_NAME: STAGE_CONFIG})

    assert "FILES(" in prepared.engine_sql
    assert f"@{STAGE_NAME}" not in prepared.engine_sql
    assert _stage_path(prepared.engine_sql).endswith(f"/data/{keyword}.csv")


async def test_keyword_segment_at_the_stage_name_position() -> None:
    prepared = await prepare_stage_sql(
        "SELECT * FROM @stage1.default.csv", stage_configs={STAGE_NAME: STAGE_CONFIG}
    )
    assert "FILES(" in prepared.engine_sql
    assert _stage_path(prepared.engine_sql).endswith("/stage1/default.csv")


async def test_keyword_segment_as_a_directory() -> None:
    prepared = await prepare_stage_sql(
        "SELECT * FROM @stage1.default.data.csv",
        stage_configs={STAGE_NAME: STAGE_CONFIG},
    )
    assert "FILES(" in prepared.engine_sql
    assert _stage_path(prepared.engine_sql).endswith("/stage1/default/data.csv")


# ---------------------------------------------------------------------------
# Completeness: every keyword-like token the lexer defines
# ---------------------------------------------------------------------------


def _keyword_literal_tokens() -> list[str]:
    """Every token whose lexer literal is a bare identifier-shaped keyword.

    ``StarRocksLexer.literalNames`` holds the quoted spelling for each token
    (``"'DEFAULT'"``); tokens without one (identifiers, literals, punctuation)
    are skipped, and operators spelled as words (``INT_DIV`` → ``'DIV'``,
    ``BIT_SHIFT_LEFT`` → ``'BITSHIFTLEFT'``) are kept because they shadow the
    same path text in the lexer.
    """
    keywords: list[str] = []
    for literal in StarRocksLexer.literalNames:
        if not literal or not literal.startswith("'"):
            continue
        word = literal.strip("'")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", word):
            keywords.append(word)
    return keywords


def test_lexer_exposes_the_keyword_set() -> None:
    """Guard the sweep itself: an empty or tiny set would make it vacuous."""
    keywords = _keyword_literal_tokens()
    assert len(keywords) > 400
    assert "DEFAULT" in keywords
    assert "SELECT" in keywords


def test_every_keyword_token_is_a_legal_path_segment() -> None:
    """No keyword spelling may drop the reference.

    This is the completeness backstop for the grammar's ``stageKeyword`` rule:
    if a later re-vendor adds a reserved keyword and the rule is not extended,
    the new token shows up here as a dropped reference.
    """
    dropped: list[str] = []
    for keyword in _keyword_literal_tokens():
        parsed = parse_sql(f"SELECT * FROM @{STAGE_NAME}.data.{keyword}.csv")
        if not parsed.stage_refs or parsed.errors:
            dropped.append(keyword)
    assert not dropped, f"keyword path segments dropped: {dropped}"


def test_stage_keyword_rule_covers_the_lexer_keywords() -> None:
    """The grammar must not have lost an alternative relative to the lexer."""
    grammar = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "sql_dialect"
        / "grammar"
        / "StarRocks.g4"
    ).read_text(encoding="utf-8")
    block = re.search(r"stageKeyword\s*:(.*?);", grammar, re.DOTALL)
    assert block, "stageKeyword rule not found in StarRocks.g4"
    alternatives = set(re.findall(r"[A-Z_][A-Z0-9_]*", block.group(1)))
    assert {"DEFAULT", "ORDER", "SELECT", "FROM", "TABLE"} <= alternatives


# ---------------------------------------------------------------------------
# Properties that were already correct — they must stay correct
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT @@version",
        "SELECT @@global.time_zone",
    ],
)
def test_double_at_is_not_a_stage(sql: str) -> None:
    assert parse_sql(sql).stage_refs == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 -- @stage1.data.default.csv",
        "SELECT '@stage1.data.default.csv'",
        'SELECT "@stage1.data.default.csv"',
        "SELECT 1 /* @stage1.data.default.csv */",
    ],
)
def test_stage_inside_comment_or_literal_is_not_a_stage(sql: str) -> None:
    assert parse_sql(sql).stage_refs == []


@pytest.mark.parametrize(
    ("sql", "path_parts", "file_name"),
    [
        ("SELECT * FROM @stage1.2024.csv", [], "2024.csv"),
        ("SELECT * FROM @stage1.2024_01.csv", [], "2024_01.csv"),
        ("SELECT * FROM @stage1.2e3.csv", [], "2e3.csv"),
        ("SELECT * FROM @stage1.2024.01.data.csv", ["2024", "01"], "data.csv"),
    ],
)
def test_numeric_dotted_paths_still_resolve(
    sql: str, path_parts: list[str], file_name: str
) -> None:
    """NOVA-132 must not regress while NOVA-133 is fixed."""
    ref = parse_sql(sql).stage_refs[0]
    assert ref.path_parts == path_parts
    assert ref.file_name == file_name


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM @stage1.data.csv t",
        "SELECT * FROM @stage1.data.default.csv t",
        "SELECT * FROM @stage1.2024 t",
    ],
)
def test_table_alias_is_not_absorbed_into_the_path(sql: str) -> None:
    parsed = parse_sql(sql)
    assert len(parsed.stage_refs) == 1
    # The alias keyword/identifier is never a fourth path segment.
    assert parsed.stage_refs[0].file_name != "t"


def test_glob_and_slash_forms_still_resolve() -> None:
    glob = parse_sql("SELECT * FROM @stage1/data/*.csv").stage_refs[0]
    assert (glob.path_parts, glob.file_name) == (["data"], "*.csv")
    slash = parse_sql("SELECT * FROM @stage1/folder/x.csv").stage_refs[0]
    assert (slash.path_parts, slash.file_name) == (["folder"], "x.csv")


# ---------------------------------------------------------------------------
# The Nova surfaces (LIST / COPY INTO) share the token-level segment test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "command_type"),
    [
        ("LIST @stage1.data.default.csv", "stage_browse"),
        ("COPY INTO t FROM @stage1.data.default.csv", "stage_load"),
        ("COPY INTO @stage1.data.default.csv FROM t", "stage_export"),
    ],
)
def test_keyword_segment_on_nova_surfaces(sql: str, command_type: str) -> None:
    parsed = parse_sql(sql)
    assert parsed.command_type.value == command_type
    ref = parsed.stage_refs[0]
    assert (ref.path_parts, ref.file_name) == (["data"], "default.csv")
