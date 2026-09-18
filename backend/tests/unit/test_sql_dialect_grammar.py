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
    assert StarRocksLexer.LOGICAL_OR == 520
    assert StarRocksParser.LOGICAL_OR == 520
    assert StarRocksParser.CONCAT == 551


def test_pipes_lex_as_logical_or_on_the_nova_path() -> None:
    types = [t for t, _ in _tokens("SELECT a||b")]
    assert StarRocksParser.LOGICAL_OR in types
    assert StarRocksParser.CONCAT not in types


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
