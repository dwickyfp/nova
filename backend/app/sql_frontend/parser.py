from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any

from antlr4 import CommonTokenStream, Token
from antlr4.error.ErrorListener import ErrorListener

from app.sql_dialect.grammar import StarRocksLexer, StarRocksParser
from app.sql_frontend.antlr_utils import CaseInsensitiveInputStream
from app.sql_frontend.errors import SQLSyntaxError, SyntaxDiagnostic
from app.sql_frontend.source import SourceSpan

_REQUEST_PARSES: ContextVar[dict[str, ParsedStatement] | None] = ContextVar(
    "nova_sql_request_parses", default=None
)


@contextmanager
def parsing_scope() -> Iterator[None]:
    """Reuse trees within a request without retaining source after that request."""
    if _REQUEST_PARSES.get() is not None:
        yield
        return
    token = _REQUEST_PARSES.set({})
    try:
        from app.sql_frontend.analysis.semantics import semantic_scope

        with semantic_scope():
            yield
    finally:
        _REQUEST_PARSES.reset(token)


class _Diagnostics(ErrorListener):
    def __init__(self) -> None:
        self.errors: list[SyntaxDiagnostic] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        # ANTLR messages can echo passwords or storage keys from rejected SQL.
        self.errors.append(SyntaxDiagnostic(line, column, "Unexpected or missing SQL token"))


def tokenize_sql(sql: str) -> tuple[CommonTokenStream, list[SyntaxDiagnostic]]:
    listener = _Diagnostics()
    lexer = StarRocksLexer(CaseInsensitiveInputStream(sql))
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener)
    stream = CommonTokenStream(lexer)
    stream.fill()
    return stream, listener.errors


def parse_tree(sql: str) -> tuple[CommonTokenStream, Any, list[SyntaxDiagnostic]]:
    stream, errors = tokenize_sql(sql)
    listener = _Diagnostics()
    parser = StarRocksParser(stream)
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    tree = parser.sqlStatements()
    return stream, tree, errors + listener.errors


@dataclass(frozen=True, slots=True)
class ParsedStatement:
    original_sql: str = field(repr=False)
    normalized_sql: str = field(repr=False)
    parse_tree: Any = field(repr=False, compare=False)
    tokens: CommonTokenStream = field(repr=False, compare=False)
    statement_context: Any = field(repr=False, compare=False)
    span: SourceSpan

    @property
    def visible_tokens(self) -> list[Any]:
        return [
            token
            for token in self.tokens.tokens
            if token.channel == Token.DEFAULT_CHANNEL and token.type != Token.EOF
        ]


def parse_statement(sql: str, *, original_sql: str | None = None) -> ParsedStatement:
    cache = _REQUEST_PARSES.get()
    if cache is not None and sql in cache:
        return replace(cache[sql], original_sql=original_sql if original_sql is not None else sql)
    stream, tree, errors = parse_tree(sql)
    if errors:
        raise SQLSyntaxError(tuple(errors))
    statements = [item.statement() for item in tree.singleStatement() if item.statement()]
    if len(statements) != 1:
        raise SQLSyntaxError((SyntaxDiagnostic(1, 0, "Expected exactly one SQL statement"),))
    root = statements[0].getChild(0)
    parsed = ParsedStatement(
        original_sql if original_sql is not None else sql,
        sql,
        tree,
        stream,
        root,
        SourceSpan.from_context(root),
    )
    if cache is not None:
        cache[sql] = parsed
    return parsed
