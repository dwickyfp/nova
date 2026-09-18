"""Foundation tests for the vendored StarRocks 4.1.1 ANTLR4 grammar (NOVA-54).

These pin the three properties PR 1 promises and nothing else:

* the generated Python target **imports** on the supported interpreter -- the
  upstream lexer ships a Java ``@members`` block that ANTLR copies verbatim and
  which made the file unimportable before the NOVA patch,
* ``||`` lexes as ``LOGICAL_OR`` on the Nova path (``MODE_DEFAULT``), not as
  ``CONCAT`` -- the patched ``LOGICAL_OR`` action must not flip that,
* a real parser round-trips, and the engine's ``submitTaskStatement`` body
  restriction (CTAS / INSERT / CACHE SELECT only) still holds, so the 9b
  follow-on patch is written against the documented shape.

No engine and no Redis; this is pure parser behaviour.
"""

from __future__ import annotations

import pytest
from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from app.sql_dialect.grammar import StarRocksLexer, StarRocksParser


class _RecordingErrorListener(ErrorListener):
    """Collect syntax errors so a test can assert on them instead of stdout."""

    def __init__(self) -> None:
        super().__init__()
        self.errors: list[str] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        self.errors.append(f"{line}:{column} {msg}")


def _tokens(sql: str) -> list[tuple[int, str]]:
    stream = CommonTokenStream(StarRocksLexer(InputStream(sql)))
    stream.fill()
    return [(t.type, t.text) for t in stream.tokens if t.type != -1]


def _parse(sql: str) -> tuple[StarRocksParser, list[str]]:
    lexer = StarRocksLexer(InputStream(sql))
    stream = CommonTokenStream(lexer)
    parser = StarRocksParser(stream)
    listener = _RecordingErrorListener()
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    parser.sqlStatements()
    return parser, listener.errors


def test_generated_lexer_and_parser_are_importable() -> None:
    # Token *numbers* are positional and shift whenever a token is added, so no
    # specific integer is pinned here. What matters is the semantic relation:
    # they are distinct tokens and the lexer/parser agree on them.
    assert StarRocksParser.LOGICAL_OR == StarRocksLexer.LOGICAL_OR
    assert StarRocksParser.LOGICAL_OR != StarRocksParser.CONCAT


def test_pipes_lex_as_logical_or_on_the_nova_path() -> None:
    types = [t for t, _ in _tokens("SELECT a||b")]
    assert StarRocksParser.LOGICAL_OR in types
    assert StarRocksParser.CONCAT not in types


@pytest.mark.parametrize(
    "token", ["SCHEDULE", "AFTER", "WHEN", "FINALIZE", "CRON", "OVERLAP_POLICY"]
)
def test_lexer_maps_keyword_spellings_to_tokens(token: str) -> None:
    # `literalNames` is token-type indexed (ANTLR's Python target), so this reads
    # the spelling at the token's numeric value. The number itself is positional
    # and shifts; the spelling is the stable contract the lowering depends on.
    token_type = getattr(StarRocksLexer, token)
    assert StarRocksLexer.literalNames[token_type] == f"'{token}'"


def test_logical_or_expression_parses() -> None:
    _, errors = _parse("SELECT a||b")
    assert errors == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT * FROM t WHERE a = 1",
        "SUBMIT TASK x AS CREATE TABLE t AS SELECT 1",
        "SUBMIT TASK x AS INSERT INTO t SELECT 1",
    ],
)
def test_engine_statements_still_parse(sql: str) -> None:
    _, errors = _parse(sql)
    assert errors == []


def test_submit_task_rejects_bare_select_body() -> None:
    # `AS SELECT` is not in the engine grammar; 9b keeps that restriction rather
    # than widening the body rules. Pinned so a future change has to be explicit.
    _, errors = _parse("SUBMIT TASK x AS SELECT 1")
    assert errors, "expected a syntax error for a bare SELECT task body"


def test_regex_parser_is_untouched_by_this_pr() -> None:
    # The production parse path is still the regex dialect parser; the ANTLR
    # parser is foundation only until 9b wires it in.
    from app.modules.query.dialect.parser import parse_sql

    result = parse_sql("SELECT * FROM @stage1.data.csv")
    assert result is not None


# ---------------------------------------------------------------------------
# PR 2 -- the Nova CREATE TASK clause surface (NOVA-54 / 9b stage 1)
# ---------------------------------------------------------------------------


def test_new_tokens_are_present() -> None:
    assert StarRocksLexer.CRON != 0
    assert StarRocksLexer.FINALIZE != 0
    assert StarRocksLexer.OVERLAP_POLICY != 0
    # `SCHEDULE` must not have been redefined: it stays the upstream token.
    assert StarRocksLexer.SCHEDULE != 0


@pytest.mark.parametrize(
    "sql",
    [
        # One clause at a time, then all five together, in the design doc's order.
        "SUBMIT TASK t AFTER a AS INSERT INTO x SELECT 1",
        "SUBMIT TASK t AFTER a, b AS INSERT INTO x SELECT 1",
        "SUBMIT TASK t FINALIZE b AS INSERT INTO x SELECT 1",
        "SUBMIT TASK t FINALIZE = b AS INSERT INTO x SELECT 1",
        "SUBMIT TASK t WHEN c > 0 AS INSERT INTO x SELECT 1",
        "SUBMIT TASK t WHEN a > 1 AND b < 2 AS INSERT INTO x SELECT 1",
        "SUBMIT TASK t WHEN a > 1 OR b < 2 AS INSERT INTO x SELECT 1",
        "SUBMIT TASK t OVERLAP_POLICY = 'SKIP' AS INSERT INTO x SELECT 1",
        "SUBMIT TASK t OVERLAP_POLICY 'ALLOW_CHILD_OVERLAP' AS INSERT INTO x SELECT 1",
        "SUBMIT TASK t SCHEDULE = 'USING CRON 0 2 * * * Asia/Jakarta' AS INSERT INTO x SELECT 1",
        (
            "SUBMIT TASK t AFTER a FINALIZE b WHEN c > 0 "
            "OVERLAP_POLICY = 'SKIP' SCHEDULE = 'USING CRON 0 2 * * * UTC' "
            "AS INSERT INTO x SELECT 1"
        ),
    ],
)
def test_nova_task_clauses_parse(sql: str) -> None:
    _, errors = _parse(sql)
    assert errors == []


@pytest.mark.parametrize(
    "sql",
    [
        # 9b's actual user-facing surface: `CREATE TASK`, not `SUBMIT TASK`.
        # This is the acceptance sentence from the issue, which failed with
        # `1:7 no viable alternative at input 'CREATE TASK'` before PR 2.
        "CREATE TASK t1 AFTER a FINALIZE b AS INSERT INTO t SELECT 1",
        "CREATE TASK t1 AFTER a AS INSERT INTO t SELECT 1",
        "CREATE TASK t1 AFTER a, b AS INSERT INTO t SELECT 1",
        "CREATE TASK t1 FINALIZE b AS INSERT INTO t SELECT 1",
        "CREATE TASK t1 FINALIZE = b AS INSERT INTO t SELECT 1",
        "CREATE TASK t1 WHEN c > 0 AS INSERT INTO t SELECT 1",
        "CREATE TASK t1 WHEN a > 1 AND b < 2 AS INSERT INTO t SELECT 1",
        "CREATE TASK t1 OVERLAP_POLICY = 'SKIP' AS INSERT INTO t SELECT 1",
        "CREATE TASK t1 SCHEDULE = 'USING CRON 0 2 * * * Asia/Jakarta' AS INSERT INTO t SELECT 1",
        (
            "CREATE TASK t1 AFTER a FINALIZE b WHEN c > 0 "
            "OVERLAP_POLICY = 'SKIP' SCHEDULE = 'USING CRON 0 2 * * * UTC' "
            "AS INSERT INTO t SELECT 1"
        ),
    ],
)
def test_create_task_clauses_parse(sql: str) -> None:
    _, errors = _parse(sql)
    assert errors == []


def test_create_task_body_restriction_reports_a_precise_position() -> None:
    # The body stays `CTAS | INSERT | CACHE SELECT`; `AS SELECT` is rejected by
    # design, and the failure must carry an exact position, not a vague one.
    _, errors = _parse("CREATE TASK t1 AS SELECT 1")
    assert len(errors) == 1
    assert errors[0].startswith("1:18 "), errors[0]


def test_submit_task_still_parses_after_adding_create() -> None:
    # The engine statement must not regress when `CREATE` joins the rule.
    for sql in (
        "SUBMIT TASK x AS INSERT INTO t SELECT 1",
        "SUBMIT TASK x AS CREATE TABLE t AS SELECT 1",
        "SUBMIT TASK x AS CACHE SELECT a FROM t",
    ):
        assert _parse(sql)[1] == [], sql


def test_engine_schedule_form_is_not_shadowed() -> None:
    # `SCHEDULE START(...) EVERY(...)` is the engine's own form; the Nova
    # `SCHEDULE = '<cron>'` alternative must not break it.
    _, errors = _parse(
        "SUBMIT TASK t SCHEDULE START('2026-09-18 02:00:00') EVERY(INTERVAL 1 DAY) "
        "AS CREATE TABLE x AS SELECT 1"
    )
    assert errors == []


def test_engine_body_restriction_is_unchanged() -> None:
    # The Nova clauses do not widen the body: `AS SELECT` is still rejected,
    # with or without a clause in front of it.
    assert _parse("SUBMIT TASK t AFTER a AS SELECT 1")[1]
    assert _parse("SUBMIT TASK t WHEN x > 1 AS SELECT 1")[1]


@pytest.mark.parametrize(
    "sql",
    [
        # `nonReserved` membership is the whole point of adding these tokens:
        # an unquoted `cron`/`finalize`/`overlap_policy` identifier must still
        # lex and resolve as a column, table or alias.
        "SELECT cron FROM t",
        "SELECT finalize, overlap_policy FROM t",
        "SELECT a FROM t WHERE cron = 1",
        "SELECT * FROM finalize",
        "SELECT cron AS finalize FROM overlap_policy",
    ],
)
def test_new_tokens_do_not_break_identifiers(sql: str) -> None:
    _, errors = _parse(sql)
    assert errors == []
