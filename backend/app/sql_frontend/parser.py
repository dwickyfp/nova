from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any

from antlr4 import CommonTokenStream, Token
from antlr4.atn.PredictionMode import PredictionMode
from antlr4.error.ErrorListener import ErrorListener
from antlr4.error.Errors import ParseCancellationException
from antlr4.error.ErrorStrategy import BailErrorStrategy

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
        # ANTLR messages can echo passwords or storage keys from rejected SQL, so
        # only a keyword or punctuation token is named; identifiers and literals
        # never are.
        self.errors.append(
            SyntaxDiagnostic(line, column, _diagnostic_message(recognizer, offendingSymbol))
        )


def _diagnostic_message(recognizer, token) -> str:
    message = "Unexpected or missing SQL token"
    if token is None or not hasattr(recognizer, "literalNames"):
        return message
    if token.type == Token.EOF:
        return message + " at end of input"
    names = recognizer.literalNames
    if 0 < token.type < len(names) and names[token.type] not in (None, "<INVALID>"):
        return f"{message} near {names[token.type]}"
    return message


def tokenize_sql(sql: str) -> tuple[CommonTokenStream, list[SyntaxDiagnostic]]:
    listener = _Diagnostics()
    lexer = StarRocksLexer(CaseInsensitiveInputStream(sql))
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener)
    stream = CommonTokenStream(lexer)
    stream.fill()
    return stream, listener.errors


def parse_tree(sql: str) -> tuple[CommonTokenStream, Any, list[SyntaxDiagnostic]]:
    """Parse with SLL prediction, falling back to full LL only when SLL fails.

    This is the two-stage strategy the engine's own parser uses: SLL is much
    cheaper on large statements and gives the same tree whenever it succeeds;
    a statement SLL cannot parse (a genuine error or an SLL conflict) is
    parsed again with LL, which also produces the diagnostics.
    """
    stream, errors = tokenize_sql(sql)
    parser = StarRocksParser(stream)
    parser.removeErrorListeners()
    parser._interp.predictionMode = PredictionMode.SLL
    parser._errHandler = BailErrorStrategy()
    try:
        return stream, parser.sqlStatements(), errors
    except ParseCancellationException:
        pass
    stream.seek(0)
    listener = _Diagnostics()
    parser = StarRocksParser(stream)
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    parser._interp.predictionMode = PredictionMode.LL
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
