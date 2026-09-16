"""SQL guard — blocks dangerous operations on system objects.

Protects ACCOUNTADMIN role and root user from being dropped/modified.

Matching happens on a *normalized* form of the SQL so that the guard cannot be
shifted by presentation-level noise in the source text:

* block comments ``/* ... */`` are removed and line comments ``-- ...`` are
  stripped to end-of-line, and
* identifier quoting (backtick / double-quote / bracket) around the tokens the
  patterns look for is collapsed.

Both transformations preserve string literals verbatim, so a comment marker or
quote character inside ``'...'`` is never mistaken for syntax.
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
        r"\bDROP\s+ROLE\s+ACCOUNTADMIN\b",
        "ACCOUNTADMIN role cannot be dropped",
    ),
    (
        r"\bREVOKE\b.*\bFROM\s+ROLE\s+ACCOUNTADMIN\b",
        "Cannot revoke privileges from ACCOUNTADMIN",
    ),
    # StarRocks also accepts the `REVOKE <priv> ON <obj> FROM <role>` form
    # without the ROLE keyword.
    (
        r"\bREVOKE\b.*\bFROM\s+ACCOUNTADMIN\b",
        "Cannot revoke privileges from ACCOUNTADMIN",
    ),
    (
        r"\bALTER\s+ROLE\s+ACCOUNTADMIN\b",
        "ACCOUNTADMIN role cannot be altered",
    ),
    # StarRocks drops roles via ALTER ROLE ... RENAME TO ... as well.
    (
        r"\bALTER\s+ROLE\s+\S+\s+RENAME\s+TO\s+ACCOUNTADMIN\b",
        "ACCOUNTADMIN role cannot be renamed to",
    ),
    (
        r"\bDROP\s+USER\s+.*root\b",
        "root user cannot be dropped",
    ),
    # Guard: prevent dropping Nova built-in UDFs (any signature)
    (
        rf"\bDROP\s+GLOBAL\s+FUNCTION\s+(IF\s+EXISTS\s+)?({_BUILTIN_UDF_ALTERNATION})\s*\(",
        "Cannot drop Nova built-in function. These are managed by the system and "
        "auto-registered on startup.",
    ),
    # Also guard DROP without signature
    (
        rf"\bDROP\s+GLOBAL\s+FUNCTION\s+(IF\s+EXISTS\s+)?({_BUILTIN_UDF_ALTERNATION})\s*;",
        "Cannot drop Nova built-in function. These are managed by the system and "
        "auto-registered on startup.",
    ),
]

# Keywords that terminate an identifier, so quoting only wraps the identifier
# itself and not the surrounding SQL grammar.
_KEYWORD_BOUNDARY = (
    r"ALL|ALTER|AS|BY|CASCADE|CREATE|DEFAULT|DROP|EXISTS|FROM|GLOBAL|GRANT|IF|IN|ON|OPTION|"
    r"RENAME|RESTRICT|REVOKE|ROLE|ROLES|SELECT|SYSTEM|TO|USER|USING|WITH"
)

# Backticks and double quotes wrap an identifier; brackets are accepted too but
# only when the content is a plain identifier so T-SQL-ish text is not mangled.
_QUOTED_IDENTIFIER = re.compile(
    rf"(?:(?<=[\s,(=])|^)(?:`([A-Za-z_][\w$]*)`|\"([A-Za-z_][\w$]*)\"|\[([A-Za-z_][\w$]*)\])"
    rf"(?=[\s,;)]|$|\.|(?:(?:{_KEYWORD_BOUNDARY})\b))",
    re.IGNORECASE,
)

_LINE_COMMENT = re.compile(r"--[^\n]*")


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

    Comments are stripped and quoting around identifiers is removed so that
    ``DROP /*x*/ ROLE `ACCOUNTADMIN``` and ``DROP ROLE ACCOUNTADMIN`` are the
    same string to every pattern below.
    """
    without_comments = strip_sql_comments(sql)
    return _QUOTED_IDENTIFIER.sub(
        lambda m: m.group(1) or m.group(2) or m.group(3), without_comments
    )


def guard_sql(sql: str) -> None:
    """Check SQL for dangerous operations. Raises ForbiddenSQLError if blocked.

    Args:
        sql: The SQL statement to check.

    Raises:
        ForbiddenSQLError: If the SQL matches a blocked pattern.
    """
    normalized = normalize_sql(sql).strip().upper()
    if not normalized:
        return
    for pattern, message in BLOCKED_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
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
