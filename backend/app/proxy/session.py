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

    ``SET @x = 1`` is stored. ``SET ROLE`` updates the active role, which is
    the one session variable with real security weight: it is what the engine
    receives as the role for subsequent queries. ``SET NAMES`` and the
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
        session.user_variables[assignment.group("name").lower()] = _strip_quotes(raw_value)
        return SetStatementResult(True)

    if any(upper.startswith(prefix.upper()) for prefix in _NOOP_PREFIXES):
        return SetStatementResult(True)

    return SetStatementResult(False)


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
