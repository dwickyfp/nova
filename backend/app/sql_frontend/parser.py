from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

from antlr4 import CommonTokenStream, Token
from antlr4.atn.LexerATNSimulator import LexerATNSimulator
from antlr4.atn.ParserATNSimulator import ParserATNSimulator
from antlr4.atn.PredictionMode import PredictionMode
from antlr4.dfa.DFA import DFA
from antlr4.error.ErrorListener import ErrorListener
from antlr4.error.Errors import ParseCancellationException
from antlr4.error.ErrorStrategy import BailErrorStrategy
from antlr4.PredictionContext import PredictionContextCache

from app.core.config import settings
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
        from app.common.sql_guard import redaction_scope
        from app.sql_frontend.analysis.semantics import semantic_scope

        with semantic_scope(), redaction_scope():
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


class _PredictionState(threading.local):
    """Prediction caches owned by one parse worker thread.

    The generated lexer and parser keep their DFA caches on the class, and the
    Python ANTLR runtime grows them without a lock. A worker thread therefore
    predicts against its own caches and never touches the ones the event loop
    thread uses for inline parses.
    """

    def __init__(self) -> None:
        self.lexer_dfa = [DFA(s, i) for i, s in enumerate(StarRocksLexer.atn.decisionToState)]
        self.lexer_contexts = PredictionContextCache()
        self.parser_dfa = [DFA(s, i) for i, s in enumerate(StarRocksParser.atn.decisionToState)]
        self.parser_contexts = PredictionContextCache()


class _WorkerThread(threading.local):
    """Whether the current thread is one of the SQL worker pool's."""

    active = False


_worker_prediction = _PredictionState()
_worker_thread = _WorkerThread()
_parse_pool: ThreadPoolExecutor | None = None
_parse_pool_guard = threading.Lock()


def _mark_worker_thread() -> None:
    _worker_thread.active = True


def _thread_prediction() -> _PredictionState | None:
    """This thread's private caches on a worker thread, the shared ones otherwise."""
    return _worker_prediction if _worker_thread.active else None


def new_parser(stream: CommonTokenStream) -> StarRocksParser:
    """A parser that is safe on the calling thread; build parsers through this."""
    parser = StarRocksParser(stream)
    prediction = _thread_prediction()
    if prediction is not None:
        parser._interp = ParserATNSimulator(
            parser, parser.atn, prediction.parser_dfa, prediction.parser_contexts
        )
    parser.removeErrorListeners()
    return parser


def tokenize_sql(sql: str) -> tuple[CommonTokenStream, list[SyntaxDiagnostic]]:
    listener = _Diagnostics()
    lexer = StarRocksLexer(CaseInsensitiveInputStream(sql))
    prediction = _thread_prediction()
    if prediction is not None:
        lexer._interp = LexerATNSimulator(
            lexer, lexer.atn, prediction.lexer_dfa, prediction.lexer_contexts
        )
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
    parser = new_parser(stream)
    parser._interp.predictionMode = PredictionMode.SLL
    parser._errHandler = BailErrorStrategy()
    try:
        return stream, parser.sqlStatements(), errors
    except ParseCancellationException:
        pass
    stream.seek(0)
    listener = _Diagnostics()
    parser = new_parser(stream)
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
    return _parsed_statement(sql, original_sql, parse_tree(sql), cache)


async def parse_statement_async(sql: str, *, original_sql: str | None = None) -> ParsedStatement:
    """``parse_statement`` that moves a large parse off the event loop.

    The parser is pure Python, so a long statement would otherwise stall every
    other request in the process for the whole parse. Short statements, and ones
    this request has already parsed, stay inline.
    """
    cache = _REQUEST_PARSES.get()
    if cache is not None and sql in cache:
        return parse_statement(sql, original_sql=original_sql)
    return await run_sql_cpu(len(sql), parse_statement, sql, original_sql=original_sql)


async def run_sql_cpu(sql_length: int, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run synchronous SQL processing, on a worker thread when the SQL is large.

    Guarding, parsing, building and planning a long statement are all pure
    Python and would hold the event loop for their whole duration. Below
    ``SQL_PARSE_OFFLOAD_MIN_CHARS`` the hand-off costs more than the work, so
    ``func`` runs inline.

    ``func`` runs in a copy of the caller's context: request-scoped caches are
    shared with the event loop, but a context variable it *sets* is not seen by
    the caller. Anything it parses must go through this module, which gives
    worker threads their own prediction caches.
    """
    threshold = settings.SQL_PARSE_OFFLOAD_MIN_CHARS
    if not threshold or sql_length < threshold:
        return func(*args, **kwargs)
    context = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        _pool(), partial(context.run, partial(func, *args, **kwargs))
    )


class NeedsEventLoop(Exception):
    """A coroutine driven by ``run_without_awaiting`` reached a real suspension."""


def run_without_awaiting(make_coroutine: Callable[[], Any]) -> Any:
    """Run a coroutine that is expected to finish without suspending.

    Planning is declared ``async`` but does no I/O for the statements Nova plans
    today, so it can run on a worker thread like any other CPU work. If a
    planner does suspend, the caller gets ``NeedsEventLoop`` and plans on the
    event loop instead.
    """
    coroutine = make_coroutine()
    try:
        coroutine.send(None)
    except StopIteration as finished:
        return finished.value
    coroutine.close()
    raise NeedsEventLoop


def _pool() -> ThreadPoolExecutor:
    global _parse_pool
    with _parse_pool_guard:
        if _parse_pool is None:
            _parse_pool = ThreadPoolExecutor(
                max_workers=settings.SQL_PARSE_THREADS,
                thread_name_prefix="nova-sql-parse",
                initializer=_mark_worker_thread,
            )
        return _parse_pool


def _parsed_statement(
    sql: str,
    original_sql: str | None,
    result: tuple[CommonTokenStream, Any, list[SyntaxDiagnostic]],
    cache: dict[str, ParsedStatement] | None,
) -> ParsedStatement:
    stream, tree, errors = result
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
