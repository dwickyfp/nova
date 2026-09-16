"""SQL guard — blocks dangerous operations on system objects.

Protects ACCOUNTADMIN role and root user from being dropped/modified.

Matching happens on a *normalized* form of the SQL so that the guard cannot be
shifted by presentation-level noise in the source text:

* block comments ``/* ... */`` are removed and line comments ``-- ...`` are
  stripped to end-of-line,
* identifier quoting (backtick / double-quote / bracket / single-quote) around
  the tokens the patterns look for is collapsed, and
* runs of whitespace are squeezed to a single space.

The result is that the patterns below never have to tolerate presentation-level
noise themselves: ``DROP /*x*/ ROLE\\n`ACCOUNTADMIN``` and
``DROP ROLE ACCOUNTADMIN`` are the same string to every one of them.

Comments go first, since a comment can hide the very keyword or quote a pattern
is looking for. The remaining two steps are commutative — the quoted-identifier
rule's lookbehind accepts any run of whitespace, so squeezing before or after it
yields the same string — but the squeeze is kept last so the ordering is
unambiguous.

Matching runs with ``re.DOTALL`` so a ``.*`` between keywords also spans a
newline that survived normalization. ``DESTRUCTIVE_SQL_PATTERN`` and
``UNSCOPED_MUTATION_PATTERN`` below already used it; the guard now matches.
"""

from __future__ import annotations

import re

from app.core.exceptions import ForbiddenSQLError

# Nova built-in UDFs — these cannot be dropped by users
BUILTIN_UDFS = [
    "AI_COMPLETE",
    "AI_SENTIMENT",
    "AI_CLASSIFY",
    "AI_SUMMARIZE",
    "AI_EXTRACT",
    "AI_TRANSLATE",
    "AI_FILTER",
    "ML_PREDICT",
]

_BUILTIN_UDF_ALTERNATION = "|".join(BUILTIN_UDFS)

BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (
        r"\bDROP\s+ROLE\s+(?:IF\s+EXISTS\s+)?ACCOUNTADMIN\b",
        "ACCOUNTADMIN role cannot be dropped",
    ),
    # `FROM ROLE <role>` and the bare `FROM <role>` form StarRocks also accepts.
    # The privilege list is bounded by `[^;]*?` rather than `.*` so a match can
    # never walk past the end of the current statement into the next one.
    (
        r"\bREVOKE\b[^;]*?\bFROM\s+ROLE\s+ACCOUNTADMIN\b",
        "Cannot revoke privileges from ACCOUNTADMIN",
    ),
    (
        r"\bREVOKE\b[^;]*?\bFROM\s+ACCOUNTADMIN\b",
        "Cannot revoke privileges from ACCOUNTADMIN",
    ),
    (
        r"\bALTER\s+ROLE\s+(?:IF\s+EXISTS\s+)?ACCOUNTADMIN\b",
        "ACCOUNTADMIN role cannot be altered",
    ),
    # StarRocks drops roles via ALTER ROLE ... RENAME TO ... as well. The source
    # role is `\S+` (it may be a quoted identifier, which normalization has
    # already unquoted) and ACCOUNTADMIN is the rename *target*.
    (
        r"\bALTER\s+ROLE\s+(?:IF\s+EXISTS\s+)?\S+\s+RENAME\s+TO\s+ACCOUNTADMIN\b",
        "ACCOUNTADMIN role cannot be renamed to",
    ),
    (
        r"\bDROP\s+USER\s+[^;]*?\broot\b",
        "root user cannot be dropped",
    ),
    # Guard: prevent dropping Nova built-in UDFs (any signature)
    (
        rf"\bDROP\s+GLOBAL\s+FUNCTION\s+(?:IF\s+EXISTS\s+)?({_BUILTIN_UDF_ALTERNATION})\s*\(",
        "Cannot drop Nova built-in function. These are managed by the system and "
        "auto-registered on startup.",
    ),
    # Also guard DROP without signature
    (
        rf"\bDROP\s+GLOBAL\s+FUNCTION\s+(?:IF\s+EXISTS\s+)?({_BUILTIN_UDF_ALTERNATION})\s*;",
        "Cannot drop Nova built-in function. These are managed by the system and "
        "auto-registered on startup.",
    ),
]

#: Flags every ``BLOCKED_PATTERNS`` entry is matched with. ``re.DOTALL`` is
#: required (not optional): the ``.*``-style spans above must be able to cross a
#: newline, and it keeps this module consistent with
#: ``DESTRUCTIVE_SQL_PATTERN`` / ``UNSCOPED_MUTATION_PATTERN`` below.
BLOCKED_PATTERN_FLAGS = re.IGNORECASE | re.DOTALL

# Keywords that terminate an identifier, so quoting only wraps the identifier
# itself and not the surrounding SQL grammar.
_KEYWORD_BOUNDARY = (
    r"ALL|ALTER|AS|BY|CASCADE|CREATE|DEFAULT|DROP|EXISTS|FROM|GLOBAL|GRANT|IF|IN|ON|OPTION|"
    r"RENAME|RESTRICT|REVOKE|ROLE|ROLES|SELECT|SYSTEM|TO|USER|USING|WITH"
)

# An identifier may be wrapped in backticks, double quotes, single quotes, or
# T-SQL brackets. StarRocks accepts the single-quoted form too, and treating it
# as a string literal rather than an identifier is exactly what let
# ```REVOKE ... FROM ROLE 'ACCOUNTADMIN'``` slip past the patterns.
#
# Only an identifier-shaped body is stripped (``[A-Za-z_][\w$]*``), so a real
# string literal such as ``'hello world'`` keeps its quotes and stays visible
# to the patterns as a literal.
_QUOTED_IDENTIFIER = re.compile(
    r"""(?:(?<=[\s,(=])|^)(?:`([A-Za-z_][\w$]*)`|"([A-Za-z_][\w$]*)"|\[([A-Za-z_][\w$]*)\]|'([A-Za-z_][\w$]*)')"""
    rf"(?=[\s,;)]|$|\.|(?:(?:{_KEYWORD_BOUNDARY})\b))",
    re.IGNORECASE,
)

#: Collapse every run of whitespace (including newlines) to one space. Applied
#: after comment removal and quote collapsing, so patterns can assume single
#: spaces and never need their own ``\s+`` tolerance for layout.
_WHITESPACE_RUN = re.compile(r"\s+")

_LINE_COMMENT = re.compile(r"--[^\n]*")


#: A single fully-quoted identifier, with no surrounding SQL grammar. Distinct
#: from ``_QUOTED_IDENTIFIER`` below, which is a *search* pattern that relies on
#: the lookbehind/lookahead context of a real statement.
_BARE_QUOTED_IDENTIFIER = re.compile(
    r"`([A-Za-z_][\w$.]*)`|\"([A-Za-z_][\w$.]*)\"|\[([A-Za-z_][\w$.]*)\]|'([A-Za-z_][\w$.]*)'"
)


def unquote_identifier(value: str) -> str:
    """Collapse identifier quoting around a bare name, using the same rules as
    :func:`normalize_sql`.

    ``'ACCOUNTADMIN'``, ``` `ACCOUNTADMIN` ```, ``"ACCOUNTADMIN"`` and
    ``[ACCOUNTADMIN]`` all reduce to ``ACCOUNTADMIN``; an unquoted name is
    returned unchanged.

    This exists so that a second control can ask "is this the same identifier?"
    without re-deriving the quoting rules. ``UserService._protect_role`` and
    ``guard_sql`` disagreeing about what counts as ACCOUNTADMIN is exactly the
    kind of drift that turns one bypass into two, so both go through here.

    Only the *identifier-shaped* body is unquoted — the same restriction
    ``_QUOTED_IDENTIFIER`` applies — so a real string literal keeps its quotes
    and a value such as ``'ACCOUNT ADMIN'`` is never mistaken for a name.
    """
    if not value:
        return value
    stripped = value.strip()
    if not stripped:
        return stripped
    match = _BARE_QUOTED_IDENTIFIER.fullmatch(stripped)
    if match is None:
        return stripped
    # Exactly one quoting branch can match, so one group holds the body.
    return next(group for group in match.groups() if group is not None)


def normalize_role_name(role: str) -> str:
    """Normalize a role name (or a quoted spelling of one) to its bare form.

    Thin alias over :func:`unquote_identifier` carrying the intent for role
    comparisons, so a reader does not have to know that role identity is
    expressed as identifier quoting.
    """
    return unquote_identifier(role)


def strip_sql_comments(sql: str) -> str:
    """Remove block and line comments from SQL, preserving string literals.

    ``/* ... */`` spans are dropped entirely (they may themselves contain
    ``--`` or further ``/*``), and ``--`` terminates the current line unless it
    sits inside a single-quoted literal.
    """
    out: list[str] = []
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]

        if ch == "'":
            # Copy the whole literal verbatim, handling '' escapes.
            out.append(ch)
            i += 1
            while i < length:
                lit = sql[i]
                out.append(lit)
                if lit == "'":
                    if i + 1 < length and sql[i + 1] == "'":
                        out.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        if ch == "/" and sql.startswith("/*", i):
            # Scan with nesting depth so an inner `*/` cannot terminate the
            # comment early and leave the rest of the text behind as "SQL".
            depth = 1
            j = i + 2
            while j < length and depth:
                if sql.startswith("/*", j):
                    depth += 1
                    j += 2
                elif sql.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            out.append(" ")
            # Unterminated comment swallows the remainder of the script.
            if depth:
                break
            i = j
            continue

        if ch == "-" and sql.startswith("--", i):
            newline = sql.find("\n", i)
            if newline == -1:
                break
            out.append(" ")
            i = newline
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def normalize_sql(sql: str) -> str:
    """Return the canonical form the guard patterns match against.

    Three presentation-level transformations are applied, in this order:

    1. comments are stripped (a comment can hide a quote or a keyword),
    2. identifier quoting is collapsed — backtick, double-quote, single-quote,
       and bracket forms alike,
    3. runs of whitespace are squeezed to a single space.

    Steps 2 and 3 commute (the quoted-identifier lookbehind accepts a whitespace
    run), but the squeeze is applied last so the result does not depend on that.
    The upshot is that ``DROP ROLE\\n'ACCOUNTADMIN'`` and
    ``DROP ROLE ACCOUNTADMIN`` are the same string to every pattern below.
    """
    without_comments = strip_sql_comments(sql)
    unquoted = _QUOTED_IDENTIFIER.sub(
        lambda m: next((g for g in m.groups() if g), m.group(0)), without_comments
    )
    return _WHITESPACE_RUN.sub(" ", unquoted)


def guard_sql(sql: str) -> None:
    """Check SQL for dangerous operations. Raises ForbiddenSQLError if blocked.

    The whole script is checked statement by statement. A caller that hands over
    a raw multi-statement blob — every router that passes a DDL string straight
    through — must not be able to lose the guard for statement 2..N, and a
    caller that has *already* split (``QueryService.execute``) must not have its
    statements split a second time. Both hold because splitting is anchored on
    ``;``: a single statement carries none outside a string literal, so it comes
    back unchanged. See ``split_sql_statements``.

    Args:
        sql: The SQL statement, or script, to check.

    Raises:
        ForbiddenSQLError: If any statement matches a blocked pattern.
    """
    for statement in split_sql_statements(sql) or [sql]:
        _guard_single_statement(statement)


def _guard_single_statement(sql: str) -> None:
    """Match one already-split statement against ``BLOCKED_PATTERNS``.

    The patterns are anchored with ``[^;]*?`` between keywords so a match cannot
    walk out of the statement it started in; feeding a concatenation of
    statements here would let the first one borrow the second one's privileged
    tail. Callers go through ``guard_sql`` instead.
    """
    normalized = normalize_sql(sql).strip().upper()
    if not normalized:
        return
    for pattern, message in BLOCKED_PATTERNS:
        if re.search(pattern, normalized, BLOCKED_PATTERN_FLAGS):
            raise ForbiddenSQLError(message)


DESTRUCTIVE_SQL_PATTERN = re.compile(
    r"^\s*(DROP|TRUNCATE|ALTER\s+TABLE\s+.+\s+DROP|DELETE\s+FROM|UPDATE\s+)\b",
    re.IGNORECASE | re.DOTALL,
)


UNSCOPED_MUTATION_PATTERN = re.compile(
    r"^\s*(DELETE\s+FROM|UPDATE\s+)(?!.*\bWHERE\b)",
    re.IGNORECASE | re.DOTALL,
)


def is_destructive_sql(sql: str) -> bool:
    """True when the statement is one that must be confirmed before execution.

    Comments are stripped first so a trailing ``-- ...`` cannot hide the
    statement's leading keyword.
    """
    return bool(DESTRUCTIVE_SQL_PATTERN.search(strip_sql_comments(sql)))


def is_unscoped_mutation(sql: str) -> bool:
    """True for ``DELETE``/``UPDATE`` with no WHERE clause.

    Comments are stripped first: ``DELETE FROM t -- WHERE id=1`` is unscoped,
    the ``WHERE`` only appears inside a comment.
    """
    return bool(UNSCOPED_MUTATION_PATTERN.search(strip_sql_comments(sql)))


# ── Credential redaction ────────────────────────────────────────────────────

#: FILES() parameter suffix (the part after the provider prefix) that holds a
#: secret. Matching on the suffix covers the whole family in one rule:
#: ``aws.s3.access_key``, ``azure.account_key``, ``gcs.service_account_key`` …
CREDENTIAL_PARAM_SUFFIXES: tuple[str, ...] = (
    "access_key",
    "secret_key",
    "session_token",
    "account_key",
    "sas_token",
    "service_account_key",
)

#: Provider prefixes accepted before a credential suffix. Dots are allowed so
#: two-segment providers (``aws.s3``, ``azure.blob``, ``gcs.s3``) match.
_PROVIDER_PREFIX = r"[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*"

#: The placeholder written in place of a redacted value.
REDACTED_VALUE = "***"

# Single-quoted form: ``'aws.s3.secret_key'='AKIA…'`` — what the injector emits.
# Group 1 = the key's opening quote, group 2 = key, group 3 = the operator.
_QUOTED_CREDENTIAL_LITERAL = re.compile(
    rf"(['\"])({_PROVIDER_PREFIX}\.(?:{'|'.join(CREDENTIAL_PARAM_SUFFIXES)}))\1"
    r"(\s*=\s*)'[^']*'",
    re.IGNORECASE,
)

# Postgres-ish form: ``"aws.s3.secret_key" => 'AKIA…'``.
_QUOTED_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(['\"])({_PROVIDER_PREFIX}\.(?:{'|'.join(CREDENTIAL_PARAM_SUFFIXES)}))\1"
    r"(\s*=>\s*)'[^']*'",
    re.IGNORECASE,
)

# Bare or backquoted key: ``aws.s3.secret_key='AKIA…'`` / ``FILES(aws.s3.secret_key=…)``.
# Group 1 = key, group 2 = the operator.
_BARE_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(?<![\w$.'\"`])(`?{_PROVIDER_PREFIX}\.(?:{'|'.join(CREDENTIAL_PARAM_SUFFIXES)})`?)"
    r"(\s*=\s*)'[^']*'",
    re.IGNORECASE,
)

#: (pattern, uses_quoted_key) — the quoted-key patterns keep the key's own
#: quoting, so their group 1 is the quote character and group 2 the key; the
#: bare pattern has no quoting to preserve, so its group 1 is the key.
_CREDENTIAL_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (_QUOTED_CREDENTIAL_LITERAL, True),
    (_QUOTED_CREDENTIAL_ASSIGNMENT, True),
    (_BARE_CREDENTIAL_ASSIGNMENT, False),
)


def redact_sql_credentials(sql: str) -> str:
    """Replace credential values in ``sql`` with ``***``.

    Used on everything derived from the statement that actually reaches the
    engine (``QueryResponse.executed_sql``) *before* it is persisted to
    ``NOVA_SYSTEM.AUDIT_LOG`` or returned to a client. Redaction is value-only:
    parameter names, paths, formats and the rest of the statement stay intact,
    so an audit row still documents what was run.

    ``'aws.s3.access_key'='AKIA…'`` becomes ``'aws.s3.access_key'='***'``.

    No-op for SQL that carries no credential parameters.
    """
    if not sql:
        return sql
    for pattern, quoted_key in _CREDENTIAL_PATTERNS:
        sql = pattern.sub(
            lambda m, quoted=quoted_key: _redacted_assignment(m, quoted), sql
        )
    return sql


def _redacted_assignment(match: re.Match[str], quoted_key: bool) -> str:
    """Rebuild one credential assignment with the value replaced by ``***``.

    The parameter name, its quoting and the operator are preserved verbatim so
    the redacted statement stays syntactically identical to the executed one.
    """
    if quoted_key:
        quote, key, operator = match.group(1), match.group(2), match.group(3)
        return f"{quote}{key}{quote}{operator}'{REDACTED_VALUE}'"
    key, operator = match.group(1), match.group(2)
    return f"{key}{operator}'{REDACTED_VALUE}'"


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL into individual statements, respecting single-quoted strings.

    Semicolons inside single-quoted strings are not treated as separators.
    Empty statements are filtered out.
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_string:
            in_string = True
            current.append(ch)
        elif ch == "'" and in_string:
            # Check for escaped quote ''
            if i + 1 < len(sql) and sql[i + 1] == "'":
                current.append("''")
                i += 2
                continue
            in_string = False
            current.append(ch)
        elif ch == ";" and not in_string:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1
    # Last statement (no trailing semicolon)
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements
