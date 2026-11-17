"""Session statements of a worksheet run: settings, user variables, transactions.

A worksheet run that contains one of these statements runs on one held
connection (``app.modules.query.worksheet_session``). This module only reads
the grammar context of a parsed statement; it executes nothing.

What a statement does to the session is decided here, from grammar contexts
and source spans, never from text matching:

* ``SET <variable> = <value>`` at session scope changes a setting the next run
  of the same worksheet gets back; ``= DEFAULT`` clears it.
* ``SET @name = <expression>`` assigns user variables; their values are read
  back after the run, never taken from the statement text.
* ``SET NAMES`` and ``SET TRANSACTION`` apply to the run that sets them.
* ``BEGIN``/``START TRANSACTION`` opens and ``COMMIT``/``ROLLBACK`` ends an
  engine transaction on the held connection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.sql_frontend.errors import CapabilityUnsupportedError

#: A user-variable name Nova keeps between runs (also the read-back alias).
USER_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,63}")

GLOBAL_AND_SESSION_MIXED = (
    "Run SET GLOBAL on its own: one SET cannot change a server-wide setting "
    "and this worksheet's session together"
)

_BEGIN = "BeginStatementContext"
_END = frozenset({"CommitStatementContext", "RollbackStatementContext"})


@dataclass(frozen=True)
class SettingChange:
    """One session system variable a successful ``SET`` changed.

    ``assignment`` is the ``name = value`` source slice as written, or ``None``
    when the statement set it back to ``DEFAULT``.
    """

    name: str
    assignment: str | None


@dataclass(frozen=True)
class SessionStatement:
    """What one statement does to the worksheet session."""

    #: ``"begin"``, ``"end"`` (COMMIT/ROLLBACK), ``"set"`` or ``None``.
    kind: str | None = None
    settings: tuple[SettingChange, ...] = ()
    user_variables: tuple[str, ...] = ()
    #: User-variable assignments whose names cannot be kept between runs.
    unnamed_variables: int = 0
    #: Source spans (start, end) of every user-variable value, for redaction.
    value_spans: tuple[tuple[int, int], ...] = field(default=(), repr=False)

    @property
    def needs_session(self) -> bool:
        return self.kind is not None


def _scope(variable: Any) -> Any:
    scope = variable.varType()
    if scope is None and variable.systemVariable() is not None:
        scope = variable.systemVariable().varType()
    return scope


def _is_global(variable: Any) -> bool:
    kind = type(variable).__name__
    if kind not in {"SetSystemVarContext", "SetTransactionContext"}:
        return False
    scope = _scope(variable) if kind == "SetSystemVarContext" else variable.varType()
    return scope is not None and scope.GLOBAL() is not None


def _system_name(variable: Any) -> str:
    identifier = variable.identifier() or variable.systemVariable().identifier()
    return identifier.getText().strip("`").casefold()


def _user_name(variable: Any) -> str | None:
    node = variable.userVariable().identifierOrString()
    text = node.getText()
    if text[:1] in {"`", "'", '"'}:
        text = text[1:-1]
    return text if USER_VARIABLE_NAME.fullmatch(text) else None


def _slice(source: str, node: Any) -> str:
    return source[node.start.start : node.stop.stop + 1]


def session_statement(statement: Any) -> SessionStatement:
    """Describe what ``statement`` (a built AST statement) does to the session.

    Raises ``CapabilityUnsupportedError`` for one ``SET`` that mixes server-wide
    (``GLOBAL``) and session items: the worksheet would have to keep half of it.
    """
    parsed = statement.parsed
    root = parsed.statement_context
    kind = type(root).__name__
    if kind == _BEGIN:
        return SessionStatement(kind="begin")
    if kind in _END:
        return SessionStatement(kind="end")
    if kind != "SetStatementContext":
        return SessionStatement()
    source = parsed.normalized_sql
    settings: list[SettingChange] = []
    names: list[str] = []
    spans: list[tuple[int, int]] = []
    unnamed = 0
    session_items = 0
    global_items = 0
    for variable in root.setVar():
        variable_kind = type(variable).__name__
        if variable_kind == "SetPasswordContext":
            continue
        if _is_global(variable):
            global_items += 1
            continue
        session_items += 1
        if variable_kind == "SetSystemVarContext":
            value = variable.setExprOrDefault()
            settings.append(
                SettingChange(
                    _system_name(variable),
                    None if value.DEFAULT() is not None else _slice(source, variable),
                )
            )
        elif variable_kind == "SetUserVarContext":
            expression = variable.expression()
            spans.append((expression.start.start, expression.stop.stop + 1))
            name = _user_name(variable)
            if name is None:
                unnamed += 1
            elif name.casefold() not in {known.casefold() for known in names}:
                names.append(name)
    if global_items and session_items:
        raise CapabilityUnsupportedError(GLOBAL_AND_SESSION_MIXED)
    if not session_items:
        return SessionStatement()
    return SessionStatement(
        kind="set",
        settings=tuple(settings),
        user_variables=tuple(names),
        unnamed_variables=unnamed,
        value_spans=tuple(spans),
    )


def redact_user_variable_values(statement: Any) -> str | None:
    """The statement with every user-variable value replaced, or ``None``.

    ``SET @token = 'abc'`` is recorded in audit and history as
    ``SET @token = ?``: a user variable is session state that can hold data.
    ``None`` means the statement assigns no user variable.
    """
    try:
        spans = session_statement(statement).value_spans
    except CapabilityUnsupportedError:
        spans = ()
    if not spans:
        return None
    source = statement.parsed.normalized_sql
    parts: list[str] = []
    position = 0
    for start, end in sorted(spans):
        parts.append(source[position:start])
        parts.append("?")
        position = end
    parts.append(source[position:])
    return "".join(parts)


_SET_USER_VARIABLE = re.compile(r"^\s*SET\s+@", re.IGNORECASE)


def redact_user_variable_text(sql: str) -> str:
    """``redact_user_variable_values`` for raw text that may not parse.

    Used where only the statement text is at hand (a refusal before parsing).
    A ``SET @...`` that cannot be parsed keeps only its first keyword.
    """
    if "@" not in sql or not _SET_USER_VARIABLE.match(sql):
        return sql
    from app.sql_frontend.ast.builder import ast_builders
    from app.sql_frontend.parser import parse_statement

    try:
        redacted = redact_user_variable_values(ast_builders.build(parse_statement(sql)))
    except ValueError:
        return "SET @... = ?"
    return sql if redacted is None else redacted


def setting_statement(assignment: str) -> Any:
    """Re-check one remembered ``name = value`` before it is applied again.

    Returns the built statement for ``SET <assignment>`` when it is exactly one
    session system-variable assignment, and raises ``ValueError`` otherwise.
    """
    from app.sql_frontend.analysis.validation import _native_session_settings
    from app.sql_frontend.ast.builder import ast_builders
    from app.sql_frontend.parser import parse_statement

    statement = ast_builders.build(parse_statement("SET " + assignment))
    root = statement.parsed.statement_context
    if type(root).__name__ != "SetStatementContext":
        raise ValueError("Not a SET statement")
    items = root.setVar()
    if len(items) != 1 or type(items[0]).__name__ != "SetSystemVarContext" or _is_global(
        items[0]
    ):
        raise ValueError("Not one session setting")
    _native_session_settings(statement)
    return statement
