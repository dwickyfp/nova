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
* **The current database.** The executor validates ``USE <db>`` on the user's
  engine connection before recording it as the context for subsequent queries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.common.sql_guard import split_sql_statements, strip_sql_comments
from app.sql_frontend.session_functions import QueryCorrelationSession

#: Role assignment, e.g. ``SET ROLE ACCOUNTADMIN`` / ``SET ROLE 'analyst'``.
#: StarRocks spells this without an ``=``, so it needs its own rule.
_SET_ROLE = re.compile(
    r"^\s*SET\s+ROLE\s+(?:TO\s+)?(?P<role>.+?)\s*;?\s*$",
    re.IGNORECASE,
)

_USE_ROLE = re.compile(
    r"^\s*USE\s+ROLE\s+(?P<role>.+?)\s*;?\s*$",
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

_PINNED = re.compile(
    r"^\s*SET\s+(?:NAMES|CHARACTER\s+SET|CHARSET)\b",
    re.IGNORECASE,
)
#: A UTF-8 ``COLLATE`` is accepted with ``SET NAMES`` because drivers send it
#: on connect (Connector/J ``connectionCollation``, Go ``collation``); the engine
#: accepts it too. The proxy always speaks UTF-8, so only UTF-8 collations fit.
_UTF8_CHARSET = re.compile(
    r"^\s*SET\s+(?:NAMES|CHARACTER\s+SET|CHARSET)\s+"
    r"(?:UTF8|UTF8MB3|UTF8MB4|DEFAULT|'(?:UTF8|UTF8MB3|UTF8MB4|DEFAULT)')"
    r"(?:\s+COLLATE\s+(?:'?UTF8(?:MB[34])?_[A-Z0-9_]+'?|DEFAULT))?\s*;?\s*$",
    re.IGNORECASE,
)

_AUTOCOMMIT_ON = re.compile(
    r"^\s*SET\s+(?:(?:SESSION|LOCAL)\s+|@@(?:(?:SESSION|LOCAL)\s*\.\s*)?)?"
    r"AUTOCOMMIT\s*=\s*(?:1|ON|TRUE)\s*;?\s*$",
    re.IGNORECASE,
)
#: ``SET [SESSION|LOCAL] TRANSACTION ISOLATION LEVEL …/READ ONLY|WRITE`` is not
#: listed: drivers send it on connect, and the engine accepts it as a no-op, so
#: it is forwarded like any other session setting.
_UNSUPPORTED_SESSION = re.compile(
    r"^\s*(?:(?:BEGIN|COMMIT|ROLLBACK)\b|START\s+TRANSACTION\b|SET\s+GLOBAL\s+TRANSACTION\b|"
    r"SET\s+(?:(?:SESSION|LOCAL|GLOBAL)\s+)?(?:AUTOCOMMIT|FOREIGN_KEY_CHECKS)\b)",
    re.IGNORECASE,
)

_QUOTED_VALUE = re.compile(r"^'(?P<body>.*)'$|^\"(?P<body2>.*)\"$|^`(?P<body3>.*)`$", re.DOTALL)


def _strip_quotes(value: str) -> str:
    match = _QUOTED_VALUE.match(value.strip())
    if not match:
        return value.strip()
    body = match.group("body")
    if body is None:
        body = match.group("body2")
    if body is None:
        return match.group("body3").replace("``", "`")
    # MySQL doubles a quote to escape it inside a literal.
    return body.replace("''", "'").replace('""', '"')


@dataclass
class SessionState:
    """Mutable per-connection state.

    Deliberately small: the roadmap lists full session tracking as its own
    item, so this holds only what the current command set actually needs.
    """

    correlation: QueryCorrelationSession = field(default_factory=QueryCorrelationSession)
    database: str | None = None
    principal: str | None = None
    assigned_roles: tuple[str, ...] = ()
    default_role: str | None = None
    active_role: str | None = None
    security_context_version: int = 1
    user_variables: dict[str, str] = field(default_factory=dict)
    #: Variables whose typed value lives on the engine session (ARRAY, MAP,
    #: STRUCT, JSON, LARGEINT); references to them are left for the engine.
    engine_variables: set[str] = field(default_factory=set)

    def set_database(self, database: str) -> None:
        self.database = database or None

    def establish_security(
        self,
        *,
        principal: str,
        assigned_roles: tuple[str, ...],
        default_role: str,
        active_role: str,
    ) -> None:
        if active_role not in assigned_roles or default_role not in assigned_roles:
            raise ValueError("Invalid proxy security state")
        self.principal = principal
        self.assigned_roles = assigned_roles
        self.default_role = default_role
        self.active_role = active_role
        self.security_context_version = 1

    def commit_role(self, active_role: str) -> None:
        if active_role not in self.assigned_roles:
            raise ValueError("Role is not assigned to this connection")
        self.active_role = active_role
        self.security_context_version += 1


class SetStatementResult:
    """Outcome of inspecting one statement for proxy-local handling."""

    __slots__ = ("handled", "error", "error_code", "assignment")

    def __init__(
        self,
        handled: bool,
        error: str | None = None,
        error_code: int = 1064,
        assignment: tuple[str, str] | None = None,
    ) -> None:
        self.handled = handled
        self.error = error
        self.error_code = error_code
        self.assignment = assignment


def handle_set_statement(statement: str, session: SessionState) -> SetStatementResult:
    """Apply a ``SET`` statement to ``session`` when the proxy owns it.

    Returns whether the statement was consumed. A consumed statement must not
    reach ``QueryService`` — that is the whole point. Anything the proxy does
    not recognise returns ``handled=False`` and is executed by the engine,
    which keeps unknown session syntax from being silently swallowed.

    ``SET @x = 1`` is stored and later substituted back by
    :func:`substitute_user_variables`. ``SET ROLE`` updates the active role,
    which is the one session variable with real security weight: it is what the
    engine receives as the role for subsequent queries. Charset and autocommit-on
    commands are compatibility commands; other session settings reach the engine.
    """
    text = strip_sql_comments(statement).strip()

    if _PINNED.match(text):
        if _UTF8_CHARSET.fullmatch(text):
            return SetStatementResult(True)
        return SetStatementResult(
            False,
            "The Nova MySQL proxy supports UTF-8 client encodings without collation overrides",
            error_code=1235,
        )

    if _AUTOCOMMIT_ON.fullmatch(text):
        return SetStatementResult(True)

    if _UNSUPPORTED_SESSION.match(text):
        return SetStatementResult(
            False,
            "Transactions and this session setting are not supported through the Nova MySQL proxy",
            error_code=1235,
        )

    role_match = _SET_ROLE.match(text)
    if role_match:
        return SetStatementResult(
            False,
            "SET ROLE must be validated by the role activation service",
        )

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
        if assignment.group("scope").startswith("@@"):
            if name in {"autocommit", "foreign_key_checks"}:
                return SetStatementResult(
                    False,
                    "This session setting is not supported through the Nova MySQL proxy",
                    error_code=1235,
                )
            return SetStatementResult(False)
        if not _LITERAL_VALUE.fullmatch(raw_value):
            return SetStatementResult(True, assignment=(name, raw_value))
        session.user_variables[name] = _store_value(raw_value)
        session.engine_variables.discard(name)
        return SetStatementResult(True)

    return SetStatementResult(False)


_LITERAL_VALUE = re.compile(
    r"(?:[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?|NULL|TRUE|FALSE|"
    r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\")",
    re.IGNORECASE | re.DOTALL,
)


def parse_role_statement(statement: str) -> str | None:
    """Return a single requested role for SET/USE ROLE, or ``None``.

    ``DEFAULT`` is returned as a token for the caller to resolve from explicit
    session state. ALL, NONE, and role lists are rejected before any state can
    change.
    """
    match = _SET_ROLE.match(statement) or _USE_ROLE.match(statement)
    if not match:
        return None
    role = _strip_quotes(match.group("role").strip().rstrip(";"))
    if not role:
        raise ValueError("A role name is required")
    if "," in role or role.upper() in {"ALL", "NONE"}:
        raise ValueError("Exactly one named role must be active")
    return role


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
#: excludes ``@@name`` (a system variable, which the engine resolves), and the
#: accept-set matches ``_ASSIGNMENT`` — including ``$``, which is legal inside a
#: name on both sides of the session, so ``SET @x$abc = …`` is readable as
#: ``@x$abc`` rather than being split into ``@x`` + ``$abc``.
_USER_VARIABLE_REFERENCE = re.compile(r"(?<!@)@(?P<name>[A-Za-z_][\w$]*)")


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
      from the central frontend tree. A name that is *both* a session
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

    stage_ends: dict[int, int] = {}
    if "@" in statement:
        from app.sql_frontend.parser import parse_statement
        from app.sql_frontend.stages import stage_view

        try:
            parsed = parse_statement(statement)
            stage_ends = {ref.start: ref.end for ref in stage_view(parsed).stage_refs}
        except ValueError:
            # Execution rejects invalid input; substitution cannot guess a stage span.
            pass

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
                if index in stage_ends:
                    # ``@stage1`` in ``FROM``/``LIST``/``JOIN``/``INTO`` position,
                    # or any dotted/slashed ``@stage1.data.csv``: leave it for the
                    # dialect engine, which is the only thing that can resolve it.
                    # The stage wins even when a session variable has the same
                    # name, because this is the same classification the engine is
                    # about to apply and a substitution here would change what it
                    # sees.
                    end = stage_ends[index]
                    out.append(statement[index:end])
                    index = end
                    continue
                if name in session.user_variables:
                    out.append(session.user_variables[name])
                    substituted.append(name)
                    index = match.end()
                    continue
                if name in session.engine_variables:
                    out.append(statement[index : match.end()])
                    index = match.end()
                    continue
                unknown.append(name)
                out.append(statement[index : match.end()])
                index = match.end()
                continue

        out.append(char)
        index += 1

    return SubstitutionResult("".join(out), substituted, unknown)


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

    The proxy splits before ``QueryService`` does because it must classify
    each statement first: a script of ``SET @x = 1; SELECT @x`` has to have its
    ``SET`` removed before the rest is executed, or the engine sees it too.

    The boundaries must be exactly the ones ``QueryService`` and the engine
    use, so this delegates to the guard's splitter. A separate scanner that
    knew only single-quoted literals cut ``SELECT "a;b"``, ``'it\\'s; x'`` and
    backquoted names at the inner ``;``; the pieces were then re-joined with a
    different separator, which silently changed the user's data.
    """
    return split_sql_statements(sql)


#: Separator used to hand proxy-classified statements back to ``QueryService``
#: as one script. The newline before ``;`` ends a trailing ``--`` comment so the
#: boundary is not swallowed by it.
STATEMENT_SEPARATOR = "\n;\n"


_USE_PATTERN = re.compile(r"^\s*USE\s+(?P<database>.+?)\s*;?\s*$", re.IGNORECASE | re.DOTALL)


#: One segment of a ``USE`` target: a backquoted name (``` `` ``` escapes a
#: backquote) or a bare name.
_USE_SEGMENT = re.compile(r"\s*(?:`(?P<quoted>(?:[^`]|``)+)`|(?P<bare>[^\s.`'\";]+))\s*")

_CATALOG_SWITCH = re.compile(
    r"^\s*(?:USE\s+(?:'[^']*'|\"[^\"]*\")|SET\s+CATALOG\s+\S+)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def parse_use_statement(statement: str) -> str | None:
    """Return the target of ``USE <db>`` or ``USE <catalog>.<db>``, or ``None``.

    The qualified form is StarRocks' ``catalog.database`` and is kept whole:
    the proxy re-selects this value before every statement, and the engine's
    database selection resolves ``catalog.database`` itself. Recording only the
    first segment selected a database named after the catalog and broke every
    later statement. ``USE 'catalog'`` switches catalogs and is not a database
    (see :func:`is_catalog_switch`).
    """
    match = _USE_PATTERN.match(statement)
    if not match:
        return None
    raw = match.group("database").strip().rstrip(";").strip()
    parts: list[str] = []
    index = 0
    while index < len(raw):
        segment = _USE_SEGMENT.match(raw, index)
        if segment is None or segment.end() == index:
            return None
        quoted = segment.group("quoted")
        parts.append(quoted.replace("``", "`") if quoted is not None else segment.group("bare"))
        index = segment.end()
        if index < len(raw):
            if raw[index] != ".":
                return None
            index += 1
            if index == len(raw):
                return None
    if not parts or len(parts) > 2:
        return None
    return ".".join(parts)


def is_catalog_switch(statement: str) -> bool:
    """True for ``USE 'catalog'`` and ``SET CATALOG <name>``.

    After a catalog switch the previously selected database belongs to another
    catalog, so the proxy must stop re-selecting it.
    """
    return bool(_CATALOG_SWITCH.match(strip_sql_comments(statement)))


def quote_database_target(target: str) -> str:
    """``USE`` target text for a ``COM_INIT_DB`` name (``db`` or ``catalog.db``)."""
    parts = target.split(".", 1) if target.count(".") == 1 else [target]
    return ".".join("`" + part.replace("`", "``") + "`" for part in parts)


def is_show_databases(statement: str) -> bool:
    """True for ``SHOW DATABASES`` and its ``SHOW SCHEMAS`` alias."""
    normalized = " ".join(statement.strip().rstrip(";").split())
    return normalized.upper() in ("SHOW DATABASES", "SHOW SCHEMAS")


#: Database names the client must never see. ``NOVA_SYSTEM`` holds Nova's own
#: configuration and audit tables; ``information_schema``, ``sys`` and
#: ``_statistics_`` are engine internals the HTTP API already hides.
HIDDEN_DATABASES = frozenset({"NOVA_SYSTEM", "information_schema", "sys", "_statistics_"})
