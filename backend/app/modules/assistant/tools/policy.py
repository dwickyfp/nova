"""Per-statement allow/deny policy for the assistant's ``query_execute`` tool.

This is a layer **above** the unchanged ``sql_guard`` (spec §5.2). The guard
remains the enforcement for the ACCOUNTADMIN/root class; this policy decides
which statements the assistant may run **at all**, statement by statement, so a
multi-statement payload cannot smuggle a denied statement behind an allowed one.

Deny wins: any statement whose leading keyword is not on the allow list is
refused before the engine is reached, and a payload containing one denied
statement is refused as a whole — the tool never executes the allowed prefix of
a mixed payload. The two Nova DDL interceptions (``CREATE ML_MODEL``,
``CREATE TASK``) are denied explicitly, because they would otherwise be caught
by the generic ``CREATE`` denial anyway but must stay denied if that changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.common.sql_guard import strip_sql_comments
from app.modules.assistant.schemas import ToolClassification

#: Row prefix a read-only statement may start with. ``WITH`` is handled
#: separately: only a CTE that resolves to a ``SELECT`` is read-only, because
#: ``WITH … DELETE/INSERT/UPDATE`` is the same destructive statement with a
#: prefix that would otherwise slip through.
_ALLOWED_LEADING = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")

#: ``WITH [RECURSIVE] name [(cols)] AS (…) [, name AS (…)]… <body>``. The body
#: is what decides allow/deny, so the CTE header is matched non-greedily and the
#: first keyword after the final CTE is the body's.
_WITH_HEADER_RE = re.compile(
    r"^WITH\s+(?:RECURSIVE\s+)?[`\"]?[A-Za-z_][\w$]*[`\"]?\s*(?:\([^)]*\))?\s+AS\s*\(",
    re.IGNORECASE,
)

#: A CTE body's leading keyword once the header(s) are removed.
_CTE_LEADING = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")

#: Denied leading keywords, in the order the spec lists them. Kept explicit so a
#: statement that does not match any known form fails *closed* (denied) rather
#: than falling through the allow list by accident.
_DENIED_PREFIXES = (
    "DROP",
    "TRUNCATE",
    "DELETE",
    "UPDATE",
    "ALTER",
    "GRANT",
    "REVOKE",
    "SET",
    "CREATE",
    "INSERT",
    "COPY",
    "REPLACE",
    "MERGE",
    "CALL",
    "EXPORT",
    "LOAD",
    "USE",
    "KILL",
    "SUBMIT",
    "INSTALL",
    "UNINSTALL",
)

#: ``ALTER … DROP <object>`` is destructive regardless of the object.
_ALTER_DROP_RE = re.compile(r"^ALTER\b.*\bDROP\b", re.IGNORECASE | re.DOTALL)

#: Nova's own DDL interceptions, denied before they reach the pipeline.
_NOVA_DDL_RE = re.compile(r"^CREATE\s+(?:ML_MODEL|TASK)\b", re.IGNORECASE)

#: ``COPY`` is only dangerous in the ``COPY INTO`` (load) form; ``COPY`` is not
#: a read-only statement in StarRocks either way, so the whole family is denied
#: by the leading-keyword rule above. This pattern exists to make the spec's
#: explicit ``COPY INTO`` entry self-documenting and testable.
_COPY_INTO_RE = re.compile(r"^COPY\s+INTO\b", re.IGNORECASE)

#: ``SET`` is the session-variable statement; ``SET ROLE`` included, since a
#: model must not change its own role mid-conversation.
_SET_RE = re.compile(r"^SET\b", re.IGNORECASE)

_LEADING_KEYWORD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)")

#: Write/export clauses that can appear *after* a read-only leading keyword and
#: turn the statement into a data-egress primitive (NOVA-83). ``SELECT … INTO
#: OUTFILE 's3://…'`` is the canonical one: StarRocks writes the query result to
#: object storage, so a statement classified ``read_only`` can copy any table
#: the user can read to a bucket the attacker controls. The clause grammar is
#: ``outfile : INTO OUTFILE file=string …``; the ``FILES``/``@stage`` forms
#: below are the same class of write and are denied for the same reason.
#:
#: Detection is a whole-statement scan, not a leading-keyword test: the clause
#: sits *inside* a ``SELECT``/``EXPLAIN`` body, so a prefix rule can never see
#: it. Comments are stripped first (below), because ``SELECT /*x*/ INTO OUTFILE``
#: must be denied exactly like the plain form.
_OUTFILE_RE = re.compile(r"\bINTO\s+OUTFILE\b", re.IGNORECASE)
_INSERT_INTO_FILES_RE = re.compile(r"\bINTO\s+FILES\s*\(", re.IGNORECASE)
_INTO_STAGE_RE = re.compile(r"\bINTO\s+@", re.IGNORECASE)


def _leading_keyword(sql: str) -> str:
    match = _LEADING_KEYWORD_RE.match(sql.lstrip())
    return match.group(1).upper() if match else ""


def _blank_string_literals(sql: str) -> str:
    """Replace every single-quoted literal's body with spaces.

    Clause detection must not fire on a *value* that merely spells a clause:
    ``SELECT 'INTO OUTFILE' AS note`` is a read-only string. The engine's own
    parsing never treats a keyword inside a literal as grammar, so neither may
    this scan. Literal *length* is preserved (the body becomes spaces) so any
    later offset-based logic would still line up; ``''`` escapes are honoured
    the same way :func:`app.common.sql_guard.strip_sql_comments` honours them.
    """
    out: list[str] = []
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]
        if ch == "'":
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
        out.append(ch)
        i += 1
    return "".join(out)


def is_nova_ddl(sql: str) -> bool:
    """True for Nova's own DDL surface (``CREATE ML_MODEL`` / ``CREATE TASK``)."""
    return bool(_NOVA_DDL_RE.match(sql.lstrip()))


def has_write_clause(sql: str) -> bool:
    """True when a statement carries a write/export clause (NOVA-83).

    The allow list is a *leading-keyword* test, so a statement can start with a
    perfectly read-only keyword and still write data: StarRocks'
    ``queryStatement`` grammar is ``(explainDesc | optimizerTrace)? queryRelation
    outfile?``, and ``SELECT … INTO OUTFILE 's3://…'`` is a valid ``SELECT``
    whose ``outfile`` clause egresses the result to object storage. The same
    class covers ``INSERT INTO FILES('path'=…)`` (an engine-side write to a file
    path) and the Nova ``INTO @stage`` spelling.

    Comments are stripped before matching, so a clause cannot be hidden behind
    one — the guard module's own normalisation applies the same rule — and
    string literals are blanked, so a value that merely spells the clause is not
    mistaken for it. Denying is deliberate: the policy's contract is
    "read-only", and none of these clauses read anything.
    """
    stripped = _blank_string_literals(strip_sql_comments(sql))
    return bool(
        _OUTFILE_RE.search(stripped)
        or _INTO_STAGE_RE.search(stripped)
        or _INSERT_INTO_FILES_RE.search(stripped)
    )


def cte_body(sql: str) -> str:
    """Return the statement body of a ``WITH`` statement, after its CTEs.

    A ``WITH`` prefix is not itself read-only: ``WITH x AS (…) DELETE FROM t``
    is a DELETE. The body decides. This walks the comma-separated CTE headers
    (each ``name AS (…)``) using a paren-depth scan so a nested paren in a CTE
    body does not end the header early, and returns the text after the last
    header. Returns ``""`` when the input is not a recognisable ``WITH``.

    A ``)``/``(`` inside a string literal is respected: it does not change the
    depth, so ``AS (SELECT '(')`` does not desynchronise the scan.
    """
    match = _WITH_HEADER_RE.match(sql)
    if match is None:
        return ""

    index = match.end()  # just past the first "AS ("
    depth = 1
    length = len(sql)
    while index < length and depth > 0:
        char = sql[index]
        if char == "'":
            # Skip a string literal verbatim, honouring '' escapes.
            index += 1
            while index < length:
                if sql[index] == "'":
                    if index + 1 < length and sql[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        index += 1

    remainder = sql[index:].lstrip()
    # Another CTE follows when a comma and another "name AS (" are present.
    while remainder.startswith(","):
        candidate = remainder[1:].lstrip()
        if _WITH_HEADER_RE.match("WITH " + candidate):
            return cte_body("WITH " + candidate)
        remainder = candidate
    return remainder


def is_allowed_statement(sql: str) -> bool:
    """True when one statement is on the read-only allow list.

    The statement is expected to be a single statement (already produced by
    :func:`app.common.sql_guard.split_sql_statements`); callers that have a raw
    blob must split first.
    """
    stripped = sql.strip()
    if not stripped:
        return False
    if is_nova_ddl(stripped):
        return False
    if _ALTER_DROP_RE.match(stripped):
        return False
    # A leading keyword is not enough: ``SELECT … INTO OUTFILE`` starts with
    # SELECT and still writes (NOVA-83). Deny any write/export clause anywhere
    # in the statement before the leading-keyword test runs.
    if has_write_clause(stripped):
        return False
    keyword = _leading_keyword(stripped)
    if keyword == "WITH":
        return _leading_keyword(cte_body(stripped)) in ("SELECT",)
    return keyword in _ALLOWED_LEADING


def is_denied_statement(sql: str) -> bool:
    """True when a single statement matches a denied form."""
    stripped = sql.strip()
    if not stripped:
        return False
    if is_nova_ddl(stripped) or _COPY_INTO_RE.match(stripped) or _SET_RE.match(stripped):
        return True
    if _ALTER_DROP_RE.match(stripped):
        return True
    if has_write_clause(stripped):
        return True
    keyword = _leading_keyword(stripped)
    if keyword == "WITH":
        return not is_allowed_statement(stripped)
    return keyword in _DENIED_PREFIXES


@dataclass(frozen=True)
class StatementDecision:
    """The policy's verdict for one statement."""

    statement: str
    allowed: bool
    reason: str | None = None


def classify_statements(
    statements: list[str],
) -> tuple[ToolClassification, list[StatementDecision]]:
    """Classify a split payload.

    Returns ``(classification, decisions)`` where ``classification`` is the
    strictest verdict over the payload:

    * ``"read_only"`` — every statement is allowed;
    * ``"denied"`` — every statement is denied;
    * ``"destructive"`` — a mix (e.g. ``SELECT 1; DROP TABLE x``). The mixed
      payload is refused as a whole, but the classification records that it
      contained destructive intent so consent cannot auto-approve it and the
      tool card surfaces why.
    """
    decisions = [
        StatementDecision(
            statement=stmt,
            allowed=is_allowed_statement(stmt),
            reason=None if is_allowed_statement(stmt) else denial_reason(stmt),
        )
        for stmt in statements
    ]
    allowed = [d for d in decisions if d.allowed]
    denied = [d for d in decisions if not d.allowed]
    if not denied:
        return "read_only", decisions
    if not allowed:
        return "denied", decisions
    return "destructive", decisions


def denial_reason(sql: str) -> str:
    """A short, value-free reason for refusing a statement.

    The message names the statement *kind* only — never the full statement,
    which could carry a literal (and therefore a credential-shaped value) into
    an error string.
    """
    stripped = sql.strip()
    if is_nova_ddl(stripped):
        return "Nova DDL (CREATE ML_MODEL / CREATE TASK) is not available to the assistant."
    if _COPY_INTO_RE.match(stripped):
        return "COPY INTO is not a read-only statement."
    if _SET_RE.match(stripped):
        return "SET is not a read-only statement."
    if _ALTER_DROP_RE.match(stripped):
        return "ALTER … DROP is not a read-only statement."
    scan = _blank_string_literals(strip_sql_comments(stripped))
    if _OUTFILE_RE.search(scan):
        return "INTO OUTFILE exports query results and is not a read-only statement."
    if _INTO_STAGE_RE.search(scan):
        return "INTO @stage exports query results and is not a read-only statement."
    if _INSERT_INTO_FILES_RE.search(scan):
        return "INSERT INTO FILES writes to a file path and is not a read-only statement."
    keyword = _leading_keyword(stripped)
    if keyword == "WITH":
        body_keyword = _leading_keyword(cte_body(stripped))
        if body_keyword:
            return f"WITH … {body_keyword} is not a read-only statement."
    if keyword:
        return f"{keyword} is not a read-only statement."
    return "The statement could not be classified as read-only."


def tool_classification(sql: str) -> ToolClassification:
    """Classify a payload without exposing the per-statement decisions.

    Convenience for ``preview``-time classification; execution uses
    :func:`classify_statements` so the refusal can name the offending statement.
    """
    from app.common.sql_guard import split_sql_statements

    statements = split_sql_statements(sql)
    if not statements:
        return "denied"
    classification, _ = classify_statements(statements)
    return classification


__all__ = [
    "StatementDecision",
    "classify_statements",
    "cte_body",
    "denial_reason",
    "has_write_clause",
    "is_allowed_statement",
    "is_denied_statement",
    "is_nova_ddl",
    "tool_classification",
]
