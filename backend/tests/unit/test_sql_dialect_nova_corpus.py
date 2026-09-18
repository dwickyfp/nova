"""The 18-case NOVA-17 parse corpus against the vendored grammar (NOVA-125).

`README.md` records the NOVA-17 decision as verified on 18 cases, and the
research deliverable's options matrix (`§3.2` of the NOVA-17 audit) lists them
with the exact failure positions the pristine StarRocks 4.1 grammar produced.
The runs at that time were against an unpatched compile, so `@stage` and
`CREATE ML_MODEL` failed with `no viable alternative at input 'FROM @'` and
`'CREATE ML_MODEL'`.

This module is the **109-A** entry point for that corpus: the same cases, run
against the vendored grammar with the Nova `@stage` and `CREATE ML_MODEL` rules
in `StarRocks.g4`, plus the assertions the issue fixes:

* the Nova surfaces now parse (no production caller switches in this slice),
* the engine surfaces that parsed before still parse,
* the malformed cases still fail, and every failure carries a real ``line:col``
  rather than a bare "parse failed",
* `@@name` stays a system variable, never a stage,
* the new keywords remain usable as ordinary identifiers.

The failure-position assertions pin the *shape* (a leading ``<line>:<col>`` and a
non-empty message), not one exact column, so the corpus keeps testing parse
behaviour rather than ANTLR's message wording.
"""

from __future__ import annotations

import re

import pytest
from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from app.sql_dialect.grammar import StarRocksLexer, StarRocksParser

#: ``line:col message`` -- the position prefix the corpus asserts on.
_POSITION = re.compile(r"^(?P<line>\d+):(?P<col>\d+) (?P<message>.+)$", re.DOTALL)


class _RecordingErrorListener(ErrorListener):
    """Collect syntax errors so a test can assert on them instead of stdout."""

    def __init__(self) -> None:
        super().__init__()
        self.errors: list[str] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        self.errors.append(f"{line}:{column} {msg}")


def _parse(sql: str) -> list[str]:
    lexer = StarRocksLexer(InputStream(sql))
    parser = StarRocksParser(CommonTokenStream(lexer))
    listener = _RecordingErrorListener()
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    parser.sqlStatements()
    return listener.errors


def _assert_positioned(errors: list[str], *, expected: tuple[int, int] | None = None) -> None:
    """Every error must start ``line:col``; optionally pin the first one."""
    assert errors, "expected at least one syntax error"
    first = _POSITION.match(errors[0])
    assert first is not None, f"error carries no line:col position: {errors[0]!r}"
    assert first.group("message"), f"error carries no message: {errors[0]!r}"
    if expected is not None:
        assert (int(first.group("line")), int(first.group("col"))) == expected, errors[0]


# ---------------------------------------------------------------------------
# The corpus table from NOVA-17 §3.2 -- cases that must now parse
# ---------------------------------------------------------------------------

#: The six engine cases the audit recorded as ``OK``. They must not regress.
ENGINE_CASES: tuple[str, ...] = (
    # `starrocks valid`
    "SELECT * FROM db.table WHERE a = 1",
    # `SR: SUBMIT TASK` -- sqlglot could not parse this; the FE grammar can.
    "SUBMIT TASK t1 AS INSERT INTO tbl SELECT 1",
    # `SR: CREATE PIPE` -- sqlglot silently fell back to `exp.Command`.
    "CREATE PIPE p PROPERTIES('x'='y') AS INSERT INTO t SELECT 1",
    # `SR: EXPLAIN COSTS` -- sqlglot raised.
    "EXPLAIN COSTS SELECT * FROM t",
    "SELECT * FROM t TABLET(1,2,3)",
    "SELECT * FROM FILES('path'='s3://b/f.csv','format'='csv')",
)

#: The three Nova dialect cases the audit recorded as ``OK`` before any patch:
#: `X(...)` is already a function-call expression, so these need no grammar work.
NOVA_AI_CASES: tuple[str, ...] = (
    "SELECT AI_COMPLETE('hi') FROM t",
    "SELECT AI_CLASSIFY(x) FROM t",
    "SELECT ML_PREDICT('m') FROM t",
)

#: The two Nova surfaces this slice adds: `@stage` and `CREATE ML_MODEL`. The
#: audit recorded these as ``ERR`` with positions `1:14` and `1:7`; 109-A makes
#: them parse.
NOVA_GRAMMAR_CASES: tuple[str, ...] = (
    "SELECT * FROM @stage1",
    "WITH c AS (SELECT * FROM @stage1.data.csv) SELECT * FROM c",
    "CREATE ML_MODEL m TYPE = FORECAST TARGET = 'y' AS SELECT a, y FROM t",
)

#: `db.default.table` parses because `default` is a nonReserved identifier.
DEFAULT_SCHEMA_CASE = "SELECT * FROM db.default.tbl"

#: The three malformed cases the audit recorded as ``ERR`` with positions.
MALFORMED_CASES: tuple[tuple[str, str], ...] = (
    ("SELECT * FROM (t", "1:16"),
    ("SELECT FROM t", "1:7"),
)


@pytest.mark.parametrize("sql", ENGINE_CASES)
def test_engine_cases_still_parse(sql: str) -> None:
    assert _parse(sql) == [], sql


@pytest.mark.parametrize("sql", NOVA_AI_CASES)
def test_nova_ai_cases_parse_without_grammar_changes(sql: str) -> None:
    assert _parse(sql) == [], sql


@pytest.mark.parametrize("sql", NOVA_GRAMMAR_CASES)
def test_nova_stage_and_ml_model_cases_parse(sql: str) -> None:
    # These are the three lines the NOVA-17 audit recorded as failures; 109-A
    # turns them green without switching a production caller.
    assert _parse(sql) == [], sql


def test_default_schema_qualified_table_parses() -> None:
    assert _parse(DEFAULT_SCHEMA_CASE) == []


@pytest.mark.parametrize(("sql", "position"), MALFORMED_CASES)
def test_malformed_cases_fail_with_a_line_col(sql: str, position: str) -> None:
    errors = _parse(sql)
    _assert_positioned(errors)
    assert errors[0].startswith(position + " "), errors[0]


def test_every_corpus_failure_reports_a_real_position() -> None:
    # The issue's third acceptance sentence: failures carry `line:col`, not just
    # "parse failed". Assert the shape across every deliberately-broken input.
    for sql, _ in MALFORMED_CASES:
        _assert_positioned(_parse(sql))


# ---------------------------------------------------------------------------
# `@stage` -- all five documented reference forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # bare stage name
        "SELECT * FROM @stage1",
        # bare directory
        "SELECT * FROM @stage1/",
        # dotted path
        "SELECT * FROM @stage1.data.csv",
        # slash path
        "SELECT * FROM @stage1/folder/x.csv",
        # glob
        "SELECT * FROM @stage1.data/*.csv",
        # fully-qualified crossing schemas (docs/arch-01-sql-dialect-engine.md)
        "SELECT * FROM @silver.stage1.data.parquet",
        # hyphenated stage name -- lexes as MINUS_SYMBOL, one segment
        "SELECT * FROM @daily-load-2.data.csv",
    ],
)
def test_stage_reference_forms_parse(sql: str) -> None:
    assert _parse(sql) == [], sql


@pytest.mark.parametrize(
    "sql",
    [
        # stage in an aliased join
        "SELECT * FROM @stage1.transactions.csv a JOIN t ON a.id = t.id",
        # stage after INSERT ... SELECT and in a CTAS
        "INSERT INTO existing SELECT * FROM @stage1.daily.parquet",
        "CREATE TABLE new_table AS SELECT * FROM @stage1.import.csv",
        # stage inside a subquery
        "SELECT * FROM (SELECT * FROM @stage1.data.csv) s",
    ],
)
def test_stage_reference_in_engine_contexts(sql: str) -> None:
    assert _parse(sql) == [], sql


@pytest.mark.parametrize(
    "sql",
    [
        # A trailing comma'd stage list: both are table references.
        "SELECT * FROM @stage1.data.csv, @stage2.data.csv",
        # Stage with an explicit alias.
        "SELECT * FROM @stage1.data.csv AS a WHERE a.x > 1",
    ],
)
def test_multiple_and_aliased_stages(sql: str) -> None:
    assert _parse(sql) == [], sql


# ---------------------------------------------------------------------------
# `@@name` stays a system variable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT @@version",
        "SELECT @@version_comment",
        "SELECT @@global.time_zone",
        "SELECT @@session.sql_mode",
        "SELECT @@version FROM t",
    ],
)
def test_system_variables_are_not_stages(sql: str) -> None:
    # `@@name` must reach `systemVariable`, never `stageReference`. Two AT tokens
    # can only start `@@`; the stage alternative consumes a single AT.
    assert _parse(sql) == [], sql


def test_stage_does_not_shadow_user_variable() -> None:
    # The same `@name` spelling is a variable in expression position and a stage
    # in table position: the two must not conflict.
    assert _parse("SELECT @foo") == []
    assert _parse("SELECT * FROM @foo") == []


# ---------------------------------------------------------------------------
# `CREATE ML_MODEL`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # The documented clause form (AGENTS.md:245, docs/19-machine-learning.md).
        (
            "CREATE ML_MODEL m TYPE = FORECAST INPUT = (SELECT a FROM t) "
            "TIMESTAMP = 'date' TARGET = 'y' SERIES = 's' "
            "CONFIG = ('method' = 'best') AS SELECT a FROM t"
        ),
        # The compact form the existing regex parser accepts.
        (
            "CREATE ML_MODEL churn TYPE = CLASSIFICATION TARGET = 'churned' "
            "ALGORITHM = random_forest TEST_SIZE = 0.2 FEATURES = (a, b) "
            "HYPERPARAMETERS = JSON '{\"n_estimators\": 100}' AS SELECT a FROM t"
        ),
        # The shortest documented compact form.
        "CREATE ML_MODEL f TYPE = FORECAST TARGET = 'y' AS SELECT a, y FROM t",
    ],
)
def test_create_ml_model_forms_parse(sql: str) -> None:
    assert _parse(sql) == [], sql


def test_create_ml_model_without_type_fails_with_a_position() -> None:
    # `TYPE` is required by both documented shapes; the missing clause is a real
    # syntax error, not an empty-but-valid body.
    errors = _parse("CREATE ML_MODEL m AS SELECT 1")
    _assert_positioned(errors)
    assert errors[0].startswith("1:18 "), errors[0]


# ---------------------------------------------------------------------------
# The new keywords must not become reserved spellings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT target, input, series, algorithm, features FROM t",
        "SELECT test_size, hyperparameters FROM t",
        "SELECT * FROM hyperparameters",
        "SELECT cron, finalize, overlap_policy FROM t",
    ],
)
def test_ml_model_keywords_still_parse_as_identifiers(sql: str) -> None:
    assert _parse(sql) == [], sql


# ---------------------------------------------------------------------------
# Drift guard: this is grammar-only, the regex parser is untouched
# ---------------------------------------------------------------------------


def test_regex_parser_is_not_switched_by_this_slice() -> None:
    # 109-A is substrate only; `parser.py` still runs the regex path. 109-B
    # (NOVA-126) is the slice that swaps it.
    from app.modules.query.dialect.parser import parse_sql

    result = parse_sql("SELECT * FROM @stage1.data.csv")
    assert result is not None
