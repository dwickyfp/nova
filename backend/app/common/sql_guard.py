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
        rf"\bREVOKE\b[^;]*?\bFROM{_GAP}ROLE{_GAP}(?:IF{_GAP}EXISTS{_GAP})?ACCOUNTADMIN\b",
        "Cannot revoke privileges from ACCOUNTADMIN",
    ),
    (
        rf"\bREVOKE\b[^;]*?\bFROM{_GAP}(?:IF{_GAP}EXISTS{_GAP})?ACCOUNTADMIN\b",
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

#: Data-egress clauses (NOVA-83). StarRocks' grammar is
#: ``(explainDesc | optimizerTrace)? queryRelation outfile?`` and
#: ``outfile : INTO OUTFILE file=string …``, so a ``SELECT`` can carry a clause
#: that writes the result set to object storage. The lead keyword is read-only,
#: the statement is not; the guard is the last line before a statement reaches
#: the engine. ``INTO FILES(...)`` and the Nova ``INTO @stage`` spelling are the
#: same class of engine-side write.
#:
#: These are matched separately from ``BLOCKED_PATTERNS`` because a *string
#: literal* may legitimately spell the clause (``SELECT 'INTO OUTFILE' AS
#: note``), and the other patterns deliberately never look inside literals.
#: ``_blank_string_literals`` removes literals first, so only real grammar
#: matches. ``_GAP`` between the keywords tolerates the leftover comment markers
#: the guard's normalization leaves behind.
_EGRESS_PATTERNS: list[tuple[str, str]] = [
    (
        rf"\bINTO{_GAP}OUTFILE\b",
        "INTO OUTFILE exports query results to a file and is not read-only.",
    ),
    (
        rf"\bINTO{_GAP}FILES{_GAP_OPT}\(",
        "INTO FILES writes to a file path and is not read-only.",
    ),
    (
        rf"\bINTO{_GAP}@",
        "INTO @stage exports query results and is not read-only.",
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
    for pattern, message in _EGRESS_PATTERNS:
        if re.search(pattern, _blank_string_literals(normalized), BLOCKED_PATTERN_FLAGS):
            raise ForbiddenSQLError(message)


def _blank_string_literals(sql: str) -> str:
    """Replace every single-quoted literal's body with spaces.

    Data-egress clause detection must not fire on a *value* that merely spells
    a clause: ``SELECT 'INTO OUTFILE' AS note`` is read-only. The engine never
    reads a keyword inside a literal as grammar, so this scan does not either.
    Literal length is preserved so offsets stay aligned, and ``''`` escapes are
    honoured as everywhere else in this module.
    """
    out: list[str] = []
    i = 0
    length = len(sql)
    while i < length:
        if sql[i] == "'":
            out.append(" ")
            i += 1
            while i < length:
                if sql[i] == "'":
                    if i + 1 < length and sql[i + 1] == "'":
                        out.append("  ")
                        i += 2
                        continue
                    out.append(" ")
                    i += 1
                    break
                out.append(" ")
                i += 1
            continue
        out.append(sql[i])
        i += 1
    return "".join(out)


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

#: Parameter suffix (the part after the provider prefix) that holds a secret.
#: Matching on the suffix covers the whole family in one rule:
#: ``aws.s3.access_key``, ``azure.account_key``, ``gcs.service_account_key`` …
#:
#: The last four suffixes are the catalog surface (NOVA-62). ``SHOW CREATE
#: CATALOG`` on StarRocks 4.1.4 masks most secret keys itself, but *not*
#: ``aws.s3.session_token`` or ``hive.metastore.password`` — both come back in
#: full — and it labels the rest with a ``******`` marker that still preserves
#: two leading and two trailing characters. Redaction must not depend on the
#: engine's own masking, so these names are covered here like any other.
CREDENTIAL_PARAM_SUFFIXES: tuple[str, ...] = (
    "access_key",
    "secret_key",
    "session_token",
    "account_key",
    "sas_token",
    "service_account_key",
    "shared_key",
    "private_key",
    "password",
    "credential",
)

#: A credential suffix written with either separator between its words, so
#: ``account_key`` and ``account.key`` are the same parameter. StarRocks accepts
#: both spellings, and the dotted one was a live gap: the suffix list matches on
#: the *last* segment, so ``azure.account.key`` ended in ``key`` and matched
#: nothing. Built from the same ``CREDENTIAL_PARAM_SUFFIXES`` tuple so a new
#: suffix cannot be added to one side only.
_CREDENTIAL_SUFFIX_PATTERN = "|".join(
    rf"{suffix.replace('_', '[._]')}" for suffix in CREDENTIAL_PARAM_SUFFIXES
)

#: A full credential parameter key: any number of separator-terminated segments
#: followed by a suffix. The leading segments are consumed greedily and the
#: suffix alternation backtracks into the tail, so both the dotted provider
#: shape (``gcp.gcs.private_key``) and the underscore-joined catalog shape
#: (``gcp.gcs.service_account_private_key``, ``hive.metastore.password``) match
#: with one pattern. ``[._]`` between segments is what keeps ``..._key`` from
#: needing a literal dot the catalog keys do not have.
_CREDENTIAL_KEY = (
    rf"(?:[A-Za-z_][\w$]*[._])*(?:{_CREDENTIAL_SUFFIX_PATTERN})"
)

#: One whole property name is a credential when it *is* a credential key — the
#: full key, anchored, not just its final segment. ``is_credential_property``
#: and the statement redactor are two callers of the same rule now: the redactor
#: masks the value, the request validator refuses to accept the key. Splitting
#: ``_CREDENTIAL_KEY`` into a second segmentation rule is what let a dotted
#: spelling (``azure.account.key``) pass validation while the redactor still
#: recognised it — the request side matched only the last segment, ``key``, so
#: it agreed with the redactor on underscore spellings and diverged on dotted
#: ones. There is one pattern here for both.
_CREDENTIAL_PROPERTY = re.compile(rf"^{_CREDENTIAL_KEY}$", re.IGNORECASE)


def is_credential_property(key: str) -> bool:
    """True when ``key`` names a credential-bearing property.

    The dotted and underscore spellings StarRocks accepts are the same parameter
    here, so ``azure.account.key`` and ``azure.account_key`` both match. Used to
    reject a credential at the API boundary; the same rule redacts its value in
    statements, so the two sides cannot disagree about which names are secrets.
    """
    return bool(_CREDENTIAL_PROPERTY.match(key or ""))

#: The placeholder written in place of a redacted value.
REDACTED_VALUE = "***"

#: A quoted string value: the opening quote is captured so the closing quote can
#: be required to *match* it (backreference) instead of accepting a fixed ``'``.
#: The body accepts a doubled opening quote (``''`` / ``""``) as an escaped
#: quote, which is how StarRocks spells a quote inside a string literal; without
#: that a value like ``'it''s a secret'`` stops at the first inner quote and the
#: match ends mid-literal. A mismatched closer is never matched, so the value
#: cannot be truncated and leave its tail looking like live SQL.
_QUOTED_VALUE = (
    r"(?P<val_quote>['\"`])"
    r"(?P<val>(?:[^'\"`]|(?!(?P=val_quote))['\"`]|(?P=val_quote)(?P=val_quote))*)"
    r"(?P=val_quote)"
)


class CredentialsRedactionError(RuntimeError):
    """Raised when a credential-bearing statement cannot be redacted safely.

    Callers must treat this as fatal for the response they were building: the
    only alternative to redacting is shipping the raw statement, and a malformed
    or unrecognised credential form must never turn into a credential leak.
    """

# Quoted key: ``'aws.s3.secret_key'='AKIA…'`` — what the injector emits.
# Named groups: ``key_quote``, ``key``, ``operator``, plus the value groups from
# ``_QUOTED_VALUE``. ``=>`` is an alternative of the same operator capture rather
# than a second pattern, so the two operators cannot both match the same
# assignment and mint a second ``***`` in the middle of the first replacement.
_QUOTED_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(?P<key_quote>['\"`])"
    rf"(?P<key>{_CREDENTIAL_KEY})(?P=key_quote)"
    rf"(?P<operator>\s*(?:=>|=)\s*)"
    rf"{_QUOTED_VALUE}",
    re.IGNORECASE,
)

# Bare or backquoted key: ``aws.s3.secret_key='AKIA…'`` / ``FILES(aws.s3.secret_key=…)``.
_BARE_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(?<![\w$.'\"`])(?P<key>`?{_CREDENTIAL_KEY}`?)"
    rf"(?P<operator>\s*(?:=>|=)\s*)"
    rf"{_QUOTED_VALUE}",
    re.IGNORECASE,
)

#: Both key shapes share one value grammar (``_QUOTED_VALUE``), so a value is
#: redacted whichever combination of key quoting and value quoting is written —
#: ``'key'="value"`` and ``key="value"`` included. A pattern is needed per *key*
#: shape only; ``uses_quoted_key`` tells ``_redacted_assignment`` whether the key
#: carries a quote to preserve.
_CREDENTIAL_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (_QUOTED_CREDENTIAL_ASSIGNMENT, True),
    (_BARE_CREDENTIAL_ASSIGNMENT, False),
)

#: Verification pass: any credential assignment left with a populated value,
#: whatever its quoting or operator. The value is *not* captured with a
#: backreferenced quote run — ``(?P<q>['"]).*?(?P=q)`` lets ``.*?`` match empty
#: and then satisfies the backreference with zero-width, so the alternative
#: branch swallows the opening quote and ``strip("'\"")`` turns a live
#: ``"VALUE"`` into an empty string. The value is taken as a plain run instead
#: and the quotes are stripped before the comparison, which cannot be fooled
#: that way.
_POPULATED_CREDENTIAL = re.compile(
    rf"(?<![\w$.'\"`])(?:['\"`]?{_CREDENTIAL_KEY}['\"`]?\s*(?:=>|=)\s*)"
    r"(?P<value>[^\s,)]*)",
    re.IGNORECASE,
)


def _normalized_value(raw: str) -> str:
    """The value with any wrapping quotes removed, empty if it is only quotes."""
    value = raw.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"`":
        value = value[1:-1]
    return value.strip("'\"`")


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

    The redaction patterns above cover the forms the injector emits. Rather
    than trusting them, this re-scans for a populated credential assignment of
    any shape — single, double or backticked key and value, bare or with an
    unquoted value — and reports the statement as unredactable if one is still
    there.
    """
    for match in _POPULATED_CREDENTIAL.finditer(sql):
        value = _normalized_value(match.group("value"))
        if value and value != REDACTED_VALUE:
            return False
    return True


def _redacted_assignment(match: re.Match[str], quoted_key: bool) -> str:
    """Rebuild one credential assignment with the value replaced by ``***``.

    The parameter name, its quoting and the operator are preserved verbatim so
    the redacted statement stays syntactically identical to the executed one.
    Always writes the ``***`` back in single quotes: that is the form the
    injector emits, and it is what the single-quoted patterns recognise, so a
    second pass over an already-redacted statement is a no-op.
    """
    if quoted_key:
        key_quote = match.group("key_quote")
        key = match.group("key")
        operator = match.group("operator")
        return f"{key_quote}{key}{key_quote}{operator}'{REDACTED_VALUE}'"
    key = match.group("key")
    operator = match.group("operator")
    return f"{key}{operator}'{REDACTED_VALUE}'"


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL into individual statements, respecting strings and comments.

    The separators are the ``;`` the *engine* would treat as statement
    boundaries, so a semicolon inside a single-quoted literal, a ``--`` line
    comment, or a ``/* ... */`` block comment is text and not a boundary.

    Recognising block comments is not optional: the engine reads
    ``SELECT /* ; */ 42`` as one statement, and ``DROP /*;*/ ROLE x`` as a single
    ``DROP ROLE``. A splitter that cut at that ``;`` would hand ``guard_sql``
    fragments the engine never sees as statements at all — splitting
    ``DROP /*;*/ ROLE ACCOUNTADMIN`` produced ``DROP /*`` and
    ``*/ ROLE ACCOUNTADMIN``, so no pattern ever saw the two keywords adjacent
    and the guard's ``_GAP`` tolerance (written for exactly this shape) was
    defeated. Splitting on a boundary the engine does not have is a bypass, and
    it over-blocks clean scripts whose comments merely mention the keywords.

    Comment handling matches :func:`strip_sql_comments`, including the
    single-close-marker rule: the first ``*/`` closes the region, so
    ``/* a /* b */`` ends at the first marker and what follows is live SQL.
    Getting that wrong in the other direction would let a comment swallow a
    statement boundary the engine honours.

    Empty statements are filtered out.
    """
    statements: list[str] = []
    current: list[str] = []
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]

        if ch == "'":
            # Copy the whole literal verbatim, handling '' escapes.
            current.append(ch)
            i += 1
            while i < length:
                lit = sql[i]
                current.append(lit)
                if lit == "'":
                    if i + 1 < length and sql[i + 1] == "'":
                        current.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        if ch == "/" and sql.startswith("/*", i):
            # Skip the comment region rather than copying it: a `;`, a quote or a
            # line comment inside it is all plain text. The first `*/` closes it
            # (see strip_sql_comments), so any inner `/*` is ordinary content.
            j = i + 2
            while j < length and not sql.startswith("*/", j):
                j += 1
            if j >= length:
                # Unterminated comment swallows the remainder of the script.
                current.append(sql[i:])
                i = length
                continue
            current.append(sql[i : j + 2])
            i = j + 2
            continue

        if ch == "-" and sql.startswith("--", i):
            newline = sql.find("\n", i)
            if newline == -1:
                current.append(sql[i:])
                i = length
                continue
            current.append(sql[i:newline])
            i = newline
            continue

        if ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    # Last statement (no trailing semicolon)
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements
