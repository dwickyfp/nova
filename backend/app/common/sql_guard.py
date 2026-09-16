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

A block comment is delimited the way StarRocks delimits it — by the first ``*/``
after the opening ``/*``, never by a matching depth — so the text that survives
comment removal is the text the engine would parse as SQL. Depth tracking used to
let a *second* ``*/`` close the comment, which normalized
``DROP /* a /* b */ ROLE ACCOUNTADMIN`` to ``DROP ROLE`` and blinded every
pattern. An inner ``/*`` marker is copied through as ``*/`` for the same reason:
the region it sits in is dropped, and the markers are the only part of it that is
not plain comment content. An unterminated comment still swallows the remainder
of the script.

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

#: Presentation noise that may survive normalisation between two tokens of a
#: single statement. An unbalanced block-comment region leaves its leftover
#: marker behind (``DROP ROLE /* a /* b */ ACCOUNTADMIN`` → ``DROP ROLE */ …``)
#: and the engine parses straight through it, so the patterns below must too.
#: Also used for the whitespace-separated joins inside the patterns themselves.
_GAP = r"(?:\s|/\*|\*/)+"

#: Zero-or-more variant, for a gap that may legitimately be empty (``FUNCTION
#: AI_COMPLETE(`` has none before the paren).
_GAP_OPT = r"(?:\s|/\*|\*/)*"

BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (
        rf"\bDROP{_GAP}ROLE{_GAP}(?:IF{_GAP}EXISTS{_GAP})?ACCOUNTADMIN\b",
        "ACCOUNTADMIN role cannot be dropped",
    ),
    # `FROM ROLE <role>` and the bare `FROM <role>` form StarRocks also accepts.
    # The privilege list is bounded by `[^;]*?` rather than `.*` so a match can
    # never walk past the end of the current statement into the next one.
    (
        rf"\bREVOKE\b[^;]*?\bFROM{_GAP}ROLE{_GAP}ACCOUNTADMIN\b",
        "Cannot revoke privileges from ACCOUNTADMIN",
    ),
    (
        rf"\bREVOKE\b[^;]*?\bFROM{_GAP}ACCOUNTADMIN\b",
        "Cannot revoke privileges from ACCOUNTADMIN",
    ),
    (
        rf"\bALTER{_GAP}ROLE{_GAP}(?:IF{_GAP}EXISTS{_GAP})?ACCOUNTADMIN\b",
        "ACCOUNTADMIN role cannot be altered",
    ),
    # StarRocks drops roles via ALTER ROLE ... RENAME TO ... as well. The source
    # role is `\S+` (it may be a quoted identifier, which normalization has
    # already unquoted) and ACCOUNTADMIN is the rename *target*.
    (
        rf"\bALTER{_GAP}ROLE{_GAP}(?:IF{_GAP}EXISTS{_GAP})?\S+{_GAP}RENAME{_GAP}TO{_GAP}ACCOUNTADMIN\b",
        "ACCOUNTADMIN role cannot be renamed to",
    ),
    (
        rf"\bDROP{_GAP}USER\b[^;]*?\broot\b",
        "root user cannot be dropped",
    ),
    # Guard: prevent dropping Nova built-in UDFs (any signature)
    (
        rf"\bDROP{_GAP}GLOBAL{_GAP}FUNCTION{_GAP}(?:IF{_GAP}EXISTS{_GAP})?"
        rf"({_BUILTIN_UDF_ALTERNATION}){_GAP_OPT}\(",
        "Cannot drop Nova built-in function. These are managed by the system and "
        "auto-registered on startup.",
    ),
    # Also guard DROP without signature
    (
        rf"\bDROP{_GAP}GLOBAL{_GAP}FUNCTION{_GAP}(?:IF{_GAP}EXISTS{_GAP})?"
        rf"({_BUILTIN_UDF_ALTERNATION}){_GAP_OPT};",
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
            # StarRocks block comments do NOT nest: the first `*/` after the
            # opening `/*` closes the comment, an inner `/*` is ordinary text.
            # Tracking nesting depth instead — letting the *second* `*/` close —
            # meant ``DROP /* a /* b */ ROLE ACCOUNTADMIN`` normalized to
            # ``DROP ROLE`` and slipped past every guard pattern, and made an
            # unterminated comment swallow the rest of the script untouched.
            #
            # Closing at the marker the engine closes at is what keeps the
            # normalized text equal to the SQL the engine actually parses. The
            # markers still bound the region that gets dropped, and they are the
            # only thing in it that is not plain comment content: a `*/` before
            # the closing marker is copied through, since the upstream stream
            # never sees the text around it as SQL either and dropping it would
            # be the bypass again — a guard pattern reading a leftover marker
            # that a real engine accepts.
            j = i + 2
            while j < length and not sql.startswith("*/", j):
                if sql.startswith("/*", j):
                    # Marker inside the comment: keep it, so the content that
                    # follows cannot fuse with a keyword outside the region.
                    out.append("*/")
                    j += 2
                else:
                    j += 1
            if j >= length:
                out.append(" ")
                break  # unterminated comment swallows the remainder
            out.append(" ")
            i = j + 2
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

    Args:
        sql: The SQL statement to check.

    Raises:
        ForbiddenSQLError: If the SQL matches a blocked pattern.
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


class CredentialsRedactionError(RuntimeError):
    """Raised when a credential-bearing statement cannot be redacted safely.

    Callers must treat this as fatal for the response they were building: the
    only alternative to redacting is shipping the raw statement, and a malformed
    or unrecognised credential form must never turn into a credential leak.
    """

# Single-quoted form: ``'aws.s3.secret_key'='AKIA…'`` — what the injector emits.
# Group 1 = the key's opening quote, group 2 = key, group 3 = the operator.
# ``=>`` is a separate alternative of the same pattern rather than a second
# pattern, so the two operators cannot both match the same assignment and mint
# a second ``***`` in the middle of the first replacement.
_QUOTED_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(['\"])({_PROVIDER_PREFIX}\.(?:{'|'.join(CREDENTIAL_PARAM_SUFFIXES)}))\1"
    r"(\s*(?:=>|=)\s*)'(?:[^']|'')*'",
    re.IGNORECASE,
)

# Bare or backquoted key: ``aws.s3.secret_key='AKIA…'`` / ``FILES(aws.s3.secret_key=…)``.
# Group 1 = key, group 2 = the operator.
_BARE_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(?<![\w$.'\"`])(`?{_PROVIDER_PREFIX}\.(?:{'|'.join(CREDENTIAL_PARAM_SUFFIXES)})`?)"
    r"(\s*(?:=>|=)\s*)'(?:[^']|'')*'",
    re.IGNORECASE,
)

#: (pattern, uses_quoted_key) — the quoted-key patterns keep the key's own
#: quoting, so their group 1 is the quote character and group 2 the key; the
#: bare pattern has no quoting to preserve, so its group 1 is the key.
_CREDENTIAL_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (_QUOTED_CREDENTIAL_ASSIGNMENT, True),
    (_BARE_CREDENTIAL_ASSIGNMENT, False),
)

#: Verification pass: any credential assignment left with a populated value,
#: whatever its quoting or operator. Group 1 is the *value* as written — quoted
#: (with the quote included, so ``''`` reads as empty) or bare/unterminated.
#: This is deliberately looser than the redaction patterns: it must find a
#: survivor even when those patterns mis-parsed the value.
_POPULATED_CREDENTIAL = re.compile(
    rf"(?<![\w$.'\"`])(?:['\"`]?{_PROVIDER_PREFIX}\."
    rf"(?:{'|'.join(CREDENTIAL_PARAM_SUFFIXES)})['\"`]?\s*(?:=>|=)\s*)"
    r"(?:(?P<q>['\"]).*?(?P=q)|(?P<bare>[^\s,)]+))?",
    re.IGNORECASE | re.DOTALL,
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

    Raises:
        CredentialsRedactionError: if a credential *value* would survive the
            substitution. Fails closed — a statement that cannot be redacted
            must never be returned in its raw form.
    """
    if not sql:
        return sql
    for pattern, quoted_key in _CREDENTIAL_PATTERNS:
        sql = pattern.sub(
            lambda m, quoted=quoted_key: _redacted_assignment(m, quoted), sql
        )
    if not _redaction_is_complete(sql):
        raise CredentialsRedactionError(
            "credential parameters remain populated after redaction; refusing to "
            "return the statement"
        )
    return sql


def _redaction_is_complete(sql: str) -> bool:
    """True when no credential parameter is still bound to a real value.

    The three patterns above cover the forms the injector emits, including a
    double quote it does not escape inside a single-quoted value (which stops
    ``\'[^\']*\'`` early and leaves the tail of the value behind). Rather than
    trusting them, this re-scans for a populated credential assignment of any
    shape — quoted, bare, backticked, or with an unquoted value — and reports
    the statement as unredactable if one is still there.
    """
    for match in _POPULATED_CREDENTIAL.finditer(sql):
        value = match.group("q") or match.group("bare") or ""
        if value.strip("'\"") and value.strip("'\"") != REDACTED_VALUE:
            return False
    return True


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
