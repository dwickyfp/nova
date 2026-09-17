"""Per-connection session state for the MySQL proxy.

The proxy keeps a small amount of state per client connection. This module owns
it so the connection loop does not have to, and so the rules are testable
without a socket.

Two things live here, and both exist because Nova's dialect engine would get
them wrong if they were passed through:

* **User variables set with ``SET``.** ``QueryService.execute`` runs every
  statement through ``parse_sql``, which treats ``@name`` as a *stage*
  reference. ``SET @x = 1`` would therefore fail with ``Stage 'x' not found``,
  and ``SET @my_stage = 1`` would be rewritten into a ``FILES()`` call — a
  silent mutation of the user's statement, not an error. ``SET`` is a session
  concern, so the proxy tracks the assignments itself and never forwards them.
* **The current database.** ``USE <db>`` and a ``database`` in the client
  handshake are context, not queries. The proxy records the name and hands it
  to ``QueryService`` as the ``database`` argument, which is the same channel
  the HTTP API uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Role assignment, e.g. ``SET ROLE ACCOUNTADMIN`` / ``SET ROLE 'analyst'``.
#: StarRocks spells this without an ``=``, so it needs its own rule.
_SET_ROLE = re.compile(
    r"^\s*SET\s+ROLE\s+(?:TO\s+)?(?P<role>[^\s=;]+)\s*$",
    re.IGNORECASE,
)

#: Single-variable assignment. ``:=`` and ``=`` are both accepted, and the
#: name may be ``@x``, ``@@x`` or ``@@session.x`` — the last two are also
#: handled as system variables by the proxy.
_ASSIGNMENT = re.compile(
    r"^\s*SET\s+(?P<scope>@@(?:(?:GLOBAL|SESSION|LOCAL)\s*\.\s*)?|@)"
    r"(?P<name>[A-Za-z_][\w$]*)\s*(?P<op>:=|=)\s*(?P<value>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)

#: A single ``SET`` statement whose target is not a variable at all, e.g.
#: ``SET NAMES utf8mb4`` and ``SET character_set_results = NULL``. These are
#: pinned without a stored value because the client is describing its own
#: encoder, and echoing the value back would be a lie about server state.
_PINNED = re.compile(
    r"^\s*SET\s+(?:NAMES|CHARACTER\s+SET|CHARSET)\b",
    re.IGNORECASE,
)

#: Statements the proxy answers itself with an OK and no session effect. They
#: are valid MySQL, carry no meaning for Nova's engine, and sending them on
#: would bounce.
_NOOP_PREFIXES = (
    "SET AUTOCOMMIT",
    "SET TRANSACTION",
    "SET SESSION TRANSACTION",
    "SET GLOBAL TRANSACTION",
    "BEGIN",
    "START TRANSACTION",
    "COMMIT",
    "ROLLBACK",
    "SET sql_mode",
    "SET SESSION sql_mode",
    "SET time_zone",
    "SET SESSION time_zone",
    "SET foreign_key_checks",
)

_QUOTED_VALUE = re.compile(r"^'(?P<body>.*)'$|^\"(?P<body2>.*)\"$", re.DOTALL)


def _strip_quotes(value: str) -> str:
    match = _QUOTED_VALUE.match(value.strip())
    if not match:
        return value.strip()
    body = match.group("body")
    if body is None:
        body = match.group("body2")
    # MySQL doubles a quote to escape it inside a literal.
    return body.replace("''", "'").replace('""', '"')


@dataclass
class SessionState:
    """Mutable per-connection state.

    Deliberately small: the roadmap lists full session tracking as its own
    item, so this holds only what the current command set actually needs.
    """

    database: str | None = None
    active_role: str | None = None
    user_variables: dict[str, str] = field(default_factory=dict)

    def set_database(self, database: str) -> None:
        self.database = database or None


class SetStatementResult:
    """Outcome of inspecting one statement for proxy-local handling."""

    __slots__ = ("handled", "error")

    def __init__(self, handled: bool, error: str | None = None) -> None:
        self.handled = handled
        self.error = error


def handle_set_statement(statement: str, session: SessionState) -> SetStatementResult:
    """Apply a ``SET`` statement to ``session`` when the proxy owns it.

    Returns whether the statement was consumed. A consumed statement must not
    reach ``QueryService`` — that is the whole point. Anything the proxy does
    not recognise returns ``handled=False`` and is executed by the engine,
    which keeps unknown session syntax from being silently swallowed.

    ``SET @x = 1`` is stored and later substituted back by
    :func:`substitute_user_variables`. ``SET ROLE`` updates the active role,
    which is the one session variable with real security weight: it is what the
    engine receives as the role for subsequent queries. ``SET NAMES`` and the
    transaction/no-op family are accepted and dropped.
    """
    text = statement.strip()
    upper = text.upper()

    if _PINNED.match(text):
        return SetStatementResult(True)

    role_match = _SET_ROLE.match(text)
    if role_match:
        role = _strip_quotes(role_match.group("role"))
        if role:
            session.active_role = role
        return SetStatementResult(True)

    assignment = _ASSIGNMENT.match(text)
    if assignment:
        raw_value = assignment.group("value").rstrip(";").strip()
        # ``SET @@global.x = ...`` is a server-scope change the proxy cannot
        # honour per connection, so it is refused rather than accepted and
        # quietly ignored — a client that believes it changed a global and
        # did not is worse off than one that gets an error.
        if assignment.group("scope").lower().startswith("@@global"):
            return SetStatementResult(
                False,
                "SET GLOBAL is not supported through the Nova MySQL proxy",
            )
        name = assignment.group("name").lower()
        # The *literal* value is stored, not the raw text: the client's own
        # spelling may be an expression (``SET @x = 1 + 2``) the proxy cannot
        # evaluate, and the engine never sees the SET. Storing the text and
        # splicing it back is what the client expects of a session variable, and
        # it keeps the value usable as a literal at every later call site.
        session.user_variables[name] = _store_value(raw_value)
        return SetStatementResult(True)

    if any(upper.startswith(prefix.upper()) for prefix in _NOOP_PREFIXES):
        return SetStatementResult(True)

    return SetStatementResult(False)


def _store_value(raw_value: str) -> str:
    """Normalise a ``SET`` right-hand side into the text to splice back in.

    A quoted value keeps its quotes so the literal round-trips: ``SET @x =
    'abc'`` must come back as ``'abc'``, not ``abc``, or substituting it into
    ``SELECT @x`` would produce a bare identifier. An unquoted value is kept
    verbatim so ``SET @x = 5`` substitutes as the number ``5``.
    """
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        # MySQL accepts double quotes as string delimiters by default; the
        # engine's SQL mode decides, so the double-quoted form is rewritten to
        # the single-quoted one, which is unambiguous everywhere.
        return "'" + value[1:-1].replace("'", "''") + "'"
    return value


#: A user-variable reference: a single ``@`` followed by a name. The lookbehind
#: excludes ``@@name`` (a system variable, which the engine resolves).
_USER_VARIABLE_REFERENCE = re.compile(
    r"(?<!@)@(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)


def _parser_classifies_as_stage(statement: str, position: int) -> bool:
    """Whether ``parser.classify_at_token`` reads ``@name`` at ``position`` as a stage.

    The proxy and the engine must agree on this or one of them rewrites the
    other's work. ``@x`` in ``SELECT @x`` is a value and must be substituted;
    ``@stage1`` in ``SELECT * FROM @stage1`` is a stage and must be left alone —
    the two are spelled identically, so position is the only thing that can
    separate them, which is exactly what ``parser._classify_at_token`` decides.

    The import is local because ``parser`` pulls in the dialect layer, which the
    proxy otherwise never touches; keeping it inside the function means a proxy
    that never sees ``@`` does not pay for it, and the two modules stay
    independently importable.
    """
    try:
        from app.modules.query.dialect import parser
    except Exception:
        # If the dialect layer is unavailable the proxy cannot resolve the
        # ambiguity; treat it as a variable, which is the pre-existing behaviour.
        return False
    stage_match = parser._AT_TOKEN.match(statement, position)
    if stage_match is None:
        return False
    return parser._classify_at_token(statement, stage_match)


class SubstitutionResult:
    """The outcome of substituting session variables into a statement."""

    __slots__ = ("sql", "substituted", "unknown")

    def __init__(self, sql: str, substituted: list[str], unknown: list[str]) -> None:
        self.sql = sql
        self.substituted = substituted
        self.unknown = unknown


def substitute_user_variables(statement: str, session: SessionState) -> SubstitutionResult:
    """Replace ``@name`` references with the values the client set on this session.

    This is the read half of ``SET @x = 1``. Without it the proxy accepts the
    ``SET``, stores the value, and then forwards ``SELECT @x`` to the engine,
    where Nova's own ``@stage`` pattern claims ``@x`` as a stage named ``x`` and
    the query fails with ``Stage 'x' not found``. QA filed that as NOVA-25: the
    write was green and the read path had never been wired.

    The scan is a tokenizer rather than a regex over the whole statement because
    substitution must not touch text that only *looks* like a reference:

    * ``'@not_a_var'`` — inside a single-quoted literal, so it is data;
    * ``-- @x`` and ``/* @x */`` — inside a comment, so the engine never sees
      the reference at all;
    * ``@@version`` — a system variable, excluded by the pattern's lookbehind;
    * ``@stage.col`` and ``SELECT * FROM @stage`` — a stage reference, decided by
      *position* with the same classifier the dialect engine uses
      (:func:`_parser_classifies_as_stage`). A name that is *both* a session
      variable and a stage is not resolvable from the text alone; the stage wins,
      because that is what the engine would otherwise have seen and the ambiguity
      is the client's. Deciding this by position rather than by a trailing dot is
      what keeps ``SELECT * FROM @stage1`` and ``LIST FILES @stage1`` from being
      rewritten into ``SELECT * FROM 'CSV_FILE'`` when the client happens to have
      a variable of the same name.

    A reference with no stored value is left verbatim and reported in
    ``unknown``. Leaving it is deliberate: the engine's own error for an unset
    variable is a better diagnosis than anything the proxy can invent, and
    rewriting it to ``NULL`` would silently change query semantics.
    """
    out: list[str] = []
    substituted: list[str] = []
    unknown: list[str] = []

    index = 0
    length = len(statement)
    while index < length:
        char = statement[index]

        if char == "'":
            end = _skip_single_quoted(statement, index)
            out.append(statement[index:end])
            index = end
            continue

        if char == '"':
            end = _skip_double_quoted(statement, index)
            out.append(statement[index:end])
            index = end
            continue

        if char == "`":
            end = _skip_backquoted(statement, index)
            out.append(statement[index:end])
            index = end
            continue

        if char == "/" and statement.startswith("/*", index):
            end = statement.find("*/", index + 2)
            end = length if end < 0 else end + 2
            out.append(statement[index:end])
            index = end
            continue

        if char == "-" and statement.startswith("--", index):
            newline = statement.find("\n", index)
            end = length if newline < 0 else newline
            out.append(statement[index:end])
            index = end
            continue

        if char == "@" and not statement.startswith("@@", index):
            match = _USER_VARIABLE_REFERENCE.match(statement, index)
            if match:
                name = match.group("name").lower()
                if _parser_classifies_as_stage(statement, index):
                    # ``@stage1`` in ``FROM``/``LIST``/``JOIN``/``INTO`` position,
                    # or any dotted/slashed ``@stage1.data.csv``: leave it for the
                    # dialect engine, which is the only thing that can resolve it.
                    # The stage wins even when a session variable has the same
                    # name, because this is the same classification the engine is
                    # about to apply and a substitution here would change what it
                    # sees.
                    end = _stage_reference_end(statement, index)
                    out.append(statement[index:end])
                    index = end
                    continue
                if name in session.user_variables:
                    out.append(session.user_variables[name])
                    substituted.append(name)
                    index = match.end()
                    continue
                unknown.append(name)
                out.append(statement[index : match.end()])
                index = match.end()
                continue

        out.append(char)
        index += 1

    return SubstitutionResult("".join(out), substituted, unknown)


def _stage_reference_end(statement: str, start: int) -> int:
    """Index just past a stage reference beginning at ``start``.

    Consumes the dotted path (and any trailing slash) so the whole
    ``@stage1.data.csv`` is emitted as one span rather than only ``@stage1``
    followed by the remaining characters, which would be appended verbatim
    anyway but would misreport the boundary to anything reading spans.
    """
    try:
        from app.modules.query.dialect import parser
    except Exception:
        return start
    match = parser._AT_TOKEN.match(statement, start)
    if match is None:
        return start
    return match.end()


def _skip_single_quoted(statement: str, start: int) -> int:
    """Index just past a single-quoted literal that starts at ``start``.

    Handles the doubled-quote escape (``'it''s'``) and a backslash escape, since
    StarRocks accepts both by default.
    """
    index = start + 1
    length = len(statement)
    while index < length:
        char = statement[index]
        if char == "\\" and index + 1 < length:
            index += 2
            continue
        if char == "'":
            if index + 1 < length and statement[index + 1] == "'":
                index += 2
                continue
            return index + 1
        index += 1
    return length


def _skip_double_quoted(statement: str, start: int) -> int:
    index = start + 1
    length = len(statement)
    while index < length:
        char = statement[index]
        if char == "\\" and index + 1 < length:
            index += 2
            continue
        if char == '"':
            if index + 1 < length and statement[index + 1] == '"':
                index += 2
                continue
            return index + 1
        index += 1
    return length


def _skip_backquoted(statement: str, start: int) -> int:
    index = start + 1
    length = len(statement)
    while index < length:
        if statement[index] == "`":
            if index + 1 < length and statement[index + 1] == "`":
                index += 2
                continue
            return index + 1
        index += 1
    return length


def split_statements(sql: str) -> list[str]:
    """Split a ``COM_QUERY`` payload into statements, respecting literals.

    The proxy needs its own splitter (rather than reusing
    ``app.common.sql_guard.split_sql_statements``) because it must classify
    each statement *before* deciding whether to hand it to ``QueryService``:
    a script of ``SET @x = 1; SELECT @x`` has to have its ``SET`` removed
    before the rest is executed, or the engine sees it too.

    Semantics match the guard's splitter — semicolons inside single-quoted
    literals, ``--`` line comments and ``/* */`` block comments are text.
    """
    statements: list[str] = []
    current: list[str] = []
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]

        if char == "'":
            current.append(char)
            index += 1
            while index < length:
                literal = sql[index]
                current.append(literal)
                if literal == "'":
                    if index + 1 < length and sql[index + 1] == "'":
                        current.append("'")
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue

        if char == "/" and sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                current.append(sql[index:])
                index = length
                continue
            current.append(sql[index : end + 2])
            index = end + 2
            continue

        if char == "-" and sql.startswith("--", index):
            newline = sql.find("\n", index)
            if newline < 0:
                current.append(sql[index:])
                index = length
                continue
            current.append(sql[index:newline])
            index = newline
            continue

        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


_USE_PATTERN = re.compile(r"^\s*USE\s+(?P<database>.+?)\s*;?\s*$", re.IGNORECASE | re.DOTALL)


def parse_use_statement(statement: str) -> str | None:
    """Return the database named by ``USE <db>``, or ``None``.

    Also accepts ``USE \`db\``` and ``USE db.schema`` — the latter because
    Nova's UI writes ``USE DATALAKE.bronze`` (see ``docs/arch-07-mysql-proxy.md``),
    and the engine resolves the qualified form itself, so only the first
    segment is tracked locally.
    """
    match = _USE_PATTERN.match(statement)
    if not match:
        return None
    raw = match.group("database").strip().strip(";").strip()
    if not raw:
        return None
    raw = raw.strip("`")
    if "." in raw:
        raw = raw.split(".", 1)[0].strip().strip("`")
    return raw or None


def is_show_databases(statement: str) -> bool:
    """True for ``SHOW DATABASES`` and its ``SHOW SCHEMAS`` alias."""
    normalized = " ".join(statement.strip().rstrip(";").split())
    return normalized.upper() in ("SHOW DATABASES", "SHOW SCHEMAS")


#: Database names the client must never see. ``NOVA_SYSTEM`` holds Nova's own
#: configuration and audit tables; ``information_schema``, ``sys`` and
#: ``_statistics_`` are engine internals the HTTP API already hides.
HIDDEN_DATABASES = frozenset({"NOVA_SYSTEM", "information_schema", "sys", "_statistics_"})
