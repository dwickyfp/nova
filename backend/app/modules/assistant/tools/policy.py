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

#: ``EXPLAIN [ANALYZE] <body>``. ``EXPLAIN`` is not itself read-only: the body
#: decides, exactly as the CTE body does for ``WITH``. ``EXPLAIN ANALYZE
#: DELETE FROM t`` is a DELETE the engine may execute, so the text after the
#: optional ``ANALYZE`` must be re-classified by the same deny rules instead of
#: being waved through on the leading keyword. Every other ``EXPLAIN`` modifier
#: StarRocks accepts (``COSTS``, ``VERBOSE``, ``LOGICAL``…, comma-separated) is
#: consumed here as well, so ``EXPLAIN COSTS DELETE FROM t`` cannot slip through
#: by placing a modifier the pattern does not know between ``EXPLAIN`` and the
#: body.
_EXPLAIN_HEADER_RE = re.compile(
    r"^EXPLAIN\b(?:\s+(?:ANALYZE|COSTS|VERBOSE|LOGICAL|COST|UUID|"
    r"FORMAT\s*=\s*\S+)\b)?",
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


def _leading_keyword(sql: str) -> str:
    match = _LEADING_KEYWORD_RE.match(sql.lstrip())
    return match.group(1).upper() if match else ""


def is_nova_ddl(sql: str) -> bool:
    """True for Nova's own DDL surface (``CREATE ML_MODEL`` / ``CREATE TASK``)."""
    return bool(_NOVA_DDL_RE.match(sql.lstrip()))


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


def explain_body(sql: str) -> str:
    """Return the statement body of an ``EXPLAIN`` statement, after its header.

    ``EXPLAIN`` is a wrapper, not a read-only statement: ``EXPLAIN DROP TABLE x``
    and ``EXPLAIN ANALYZE DELETE FROM t`` carry destructive statements inside.
    The body decides, so the header (``EXPLAIN`` plus any modifier such as
    ``ANALYZE``) is consumed and the remainder returned for re-classification.
    Returns ``""`` when the input is not a recognisable ``EXPLAIN``.
    """
    match = _EXPLAIN_HEADER_RE.match(sql.lstrip())
    if match is None:
        return ""
    return sql.lstrip()[match.end() :].lstrip()


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
    keyword = _leading_keyword(stripped)
    if keyword == "WITH":
        return _leading_keyword(cte_body(stripped)) in ("SELECT",)
    if keyword == "EXPLAIN":
        body = explain_body(stripped)
        # An ``EXPLAIN`` with no body, or a body that is itself only further
        # ``EXPLAIN`` wrappers, is not read-only: fail closed rather than
        # allowing a bare wrapper through on the strength of its keyword.
        return bool(body) and is_allowed_statement(body)
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
    keyword = _leading_keyword(stripped)
    if keyword == "WITH":
        return not is_allowed_statement(stripped)
    if keyword == "EXPLAIN":
        # The body, not the wrapper, decides — mirroring the CTE rule. A body
        # that is not read-only makes the whole ``EXPLAIN`` a denied statement.
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
    keyword = _leading_keyword(stripped)
    if keyword == "WITH":
        body_keyword = _leading_keyword(cte_body(stripped))
        if body_keyword:
            return f"WITH … {body_keyword} is not a read-only statement."
    if keyword == "EXPLAIN":
        body = explain_body(stripped)
        # A ``WITH`` body is itself decided by its CTE body, so peel it the same
        # way ``is_allowed_statement`` does before naming the offender.
        if _leading_keyword(body) == "WITH":
            body = cte_body(body)
        body_keyword = _leading_keyword(body)
        if body_keyword:
            return f"EXPLAIN … {body_keyword} is not a read-only statement."
        return "EXPLAIN without a read-only body is not a read-only statement."
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
    "explain_body",
    "is_allowed_statement",
    "is_denied_statement",
    "is_nova_ddl",
    "tool_classification",
]
