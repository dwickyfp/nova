"""``query_execute`` — the assistant's one read-only tool (T-C1/T-C2/T-C3).

Non-negotiable properties (spec §5, §7):

* **Delegate-first.** The tool runs on the *requesting user's* connection —
  ``username`` / ``encrypted_password`` / ``active_role`` / ``session_id`` from
  the authenticated request — so StarRocks RBAC is the authorization source of
  truth. There is no service-identity fallback.
* **In-process.** It calls ``query_service.execute_statements`` directly. It
  never imports ``asyncmy`` or ``app.core.database``, never opens a socket, and
  never calls ``POST /api/v1/query/execute``.
* **No auto-confirm.** ``confirm_destructive=False`` is hard-coded; a
  destructive statement can never be approved by a grant.
* **Bounded.** ``ASSISTANT_MAX_ROWS`` is passed as ``max_rows`` so the cap is
  applied at fetch time, not after the rows are in memory.
* **Redacted everywhere.** The SQL preview is redacted SQL; the model
  context receives columns + a bounded preview + a row count, with
  credential-shaped values redacted value-level before they enter it; a
  result-channel engine error is redacted with the same shared helper before it
  reaches either the audit row or the tool outcome; the audit row carries the
  redacted statement and never result rows.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.common.audit import write_audit_log
from app.common.sql_guard import redact_sql_credentials
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import (
    ToolInvocation,
    ToolOutcome,
    policy,
    report_tool_progress,
)
from app.modules.assistant.tools.redaction import redact_rows

logger = logging.getLogger(__name__)

#: Result-row cap (spec §9). Below the API's 500 default, because rows are
#: context tokens. Applied at fetch time via ``max_rows``.
ASSISTANT_MAX_ROWS = 100

#: Characters of the preview handed to the model. The preview is bounded
#: independently of ``ASSISTANT_MAX_ROWS`` so a wide result cannot spend the
#: context window on one call; truncation is marked explicitly.
ASSISTANT_MAX_PREVIEW_CHARS = 4000

#: A conservative upper bound on a single cell, so one enormous cell cannot
#: dominate the preview. Truncated cells are marked.
ASSISTANT_MAX_CELL_CHARS = 500

#: The engine's privilege-denied error numbers. 5203 is "Access denied"; the
#: neighbouring 52xx family is StarRocks' authorization range. A privilege
#: error terminates the tool for that statement — no retry with elevated
#: credentials (spec §5.3).
_PRIVILEGE_ERROR_CODES = (5203,)

#: MySQLError carries ``args[0]`` as the numeric code; StarRocks' own messages
#: also embed ``(5203)`` in text. Both forms are checked.
_ENGINE_DENIED_MARKERS = ("access denied", "denied", "permission")

_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "sql": {
            "type": "string",
            "description": (
                "A single read-only SQL statement (SELECT, WITH … SELECT, SHOW, "
                "DESCRIBE, EXPLAIN) to run on the user's connection."
            ),
        },
        "purpose": {
            "type": "string",
            "description": "One short sentence on why this query answers the question.",
        },
    },
    "required": ["sql"],
}


class QueryExecuteTool:
    """The ``query_execute`` assistant tool.

    ``classification`` is a *property* (not a stored attribute) because it is
    per-invocation: a payload containing a denied statement must not be
    pre-classified as read-only. The loop reads it once, before consent, which
    is exactly the point of the Stage B boundary.

    The property reads state written by the most recent :meth:`preview`. That
    is safe on the single-threaded event loop the loop runs on: ``service.py``
    calls ``tool.preview(invocation)`` and then ``tool.classification`` with no
    ``await`` between them, so no other task can interleave a different
    invocation. :meth:`run` re-classifies from the invocation it is given and
    never relies on the property, so a stale value could never change what is
    executed — only the card the user sees.
    """

    name = "query_execute"
    description = (
        "Run a single read-only SQL statement against StarRocks on the user's "
        "own connection. Use it to inspect schemas and data. Destructive or "
        "DDL statements are refused."
    )
    parameters = _TOOL_PARAMETERS
    #: Explicit: running SQL touches the user's data and always needs approval
    #: (unless a conversation grant covers it). Stated rather than relying on the
    #: default so the contract is visible next to the tool it governs.
    requires_consent = True

    def __init__(self, *, max_rows: int = ASSISTANT_MAX_ROWS) -> None:
        self.max_rows = max_rows
        self._classification: ToolClassification = "read_only"

    @property
    def classification(self) -> ToolClassification:
        return self._classification

    def preview(self, invocation: ToolInvocation) -> str:
        """Return the redacted SQL shown in the approval card.

        The preview is redacted with the shared helper; the statement never
        leaves this method in raw form. Classification is computed here so the
        loop can consult ``classification`` straight after ``preview``.
        """
        sql = _sql_from(invocation)
        self._classification = policy.tool_classification(sql) if sql else "denied"
        return _safe_redact(sql)

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        """Execute the payload on the requesting user's connection."""
        sql = _sql_from(invocation)
        if not sql or not sql.strip():
            return ToolOutcome(ok=False, summary="", error="No SQL was provided.")

        user = getattr(context, "user", None) or {}
        username = user.get("username")
        encrypted_password = user.get("encrypted_password")
        if not username or not encrypted_password:
            # Without the requesting user's credential there is no delegate-first
            # path. Refuse rather than fall back to a service identity.
            return ToolOutcome(
                ok=False,
                summary="",
                error="No user connection is available for this tool call.",
            )

        from app.common.sql_guard import split_sql_statements

        statements = split_sql_statements(sql)
        if not statements:
            return ToolOutcome(ok=False, summary="", error="No SQL was provided.")

        classification, decisions = policy.classify_statements(statements)
        self._classification = classification

        safe_sql = _safe_redact(sql)
        report_tool_progress(
            context,
            stage="validating_sql",
            text="Validating the query",
            sql_preview=safe_sql,
        )

        if classification != "read_only":
            offending = next((d for d in decisions if not d.allowed), None)
            reason = offending.reason if offending else "The statement is not read-only."
            redacted_sql = safe_sql
            await self._audit(
                context=context,
                username=username,
                sql=redacted_sql,
                status="DENIED",
                decision="denied",
                error_message=reason,
                rows_affected=0,
            )
            return ToolOutcome(ok=False, summary="", error=reason)

        from app.modules.query.service import query_service

        report_tool_progress(
            context,
            stage="executing_sql",
            text="Running the query",
            sql_preview=safe_sql,
        )
        try:
            results = await query_service.execute_statements(
                sql=sql,
                username=username,
                encrypted_password=encrypted_password,
                database=_context_value(context, "database"),
                schema=_context_value(context, "schema_name"),
                role=_active_role(context),
                max_rows=self.max_rows,
                session_id=_context_value(context, "audit_session_id"),
                confirm_destructive=False,
                file_id=_context_value(context, "workspace_file_id"),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a tool failure
            # An engine privilege error terminates: no elevated retry (§5.3).
            await self._audit(
                context=context,
                username=username,
                sql=_safe_redact(sql),
                status="ERROR",
                decision="approved",
                error_message=_engine_error_text(exc),
                rows_affected=0,
            )
            return ToolOutcome(
                ok=False,
                summary="",
                error="The query failed before it could run.",
            )

        if not results:
            return ToolOutcome(ok=False, summary="", error="The query returned no result.")

        # Serialized per-thread: the loop sends one statement at a time, but a
        # payload may still be multi-statement. Feed the model a bounded summary
        # of every result, and fail if any statement errored.
        rendered: list[str] = []
        failed: str | None = None
        total_rows = 0
        for result in results:
            if result.error:
                failed = result.error
                break
            rendered.append(_render_result(result))
            total_rows += result.row_count or 0

        if failed is not None:
            # One redacted value feeds both sinks. The engine message can echo
            # the executed statement, which for an ``@stage`` query carries the
            # injected credentials, so it is redacted at the source — the audit
            # row and the SSE frame both read this variable and cannot drift.
            # Classification reads the raw message: redaction is value-only and
            # must not be able to flip ``_is_privilege_error`` (spec §5.3).
            privileged = _is_privilege_error(failed)
            redacted_error = _safe_redact(failed)
            await self._audit(
                context=context,
                username=username,
                sql=_safe_redact(sql),
                status="ERROR",
                decision="approved",
                error_message=redacted_error,
                rows_affected=0,
            )
            if privileged:
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error="The database denied this query for your user.",
                )
            return ToolOutcome(ok=False, summary="", error=redacted_error)

        await self._audit(
            context=context,
            username=username,
            sql=_safe_redact(sql),
            status="SUCCESS",
            decision="approved",
            error_message=None,
            rows_affected=total_rows,
        )

        summary = "\n".join(rendered)
        report_tool_progress(
            context,
            stage="query_completed",
            text=f"Retrieved {total_rows} row{'s' if total_rows != 1 else ''}",
            sql_preview=safe_sql,
        )
        # Expose the first tabular result to Agent Studio's ``data_to_chart`` in
        # the same turn. Redacted at the source, like everything else that can
        # leave this tool. A context without the slot is left untouched.
        _expose_last_result(context, results)
        # The grid the user reads has to come from here too: without it a plain
        # assistant answer that ran SQL has no table at all, only prose.
        return ToolOutcome(
            ok=True,
            summary=summary,
            table=_table_from_results(results),
        )

    async def _audit(
        self,
        *,
        context: Any,
        username: str,
        sql: str,
        status: str,
        decision: str,
        error_message: str | None,
        rows_affected: int,
    ) -> None:
        """Write the optional ``assistant_tool`` audit row (spec §7).

        Best-effort: an audit outage must not change the tool's outcome. The SQL
        passed here is already redacted, and no result rows are ever written.
        The conversation id is the ``session_id``, so these rows correlate with
        the execution rows ``QueryService`` writes.
        """
        try:
            await write_audit_log(
                event_type="assistant_tool",
                user_name=username,
                action=decision,
                object_type="assistant_tool",
                object_name=self.name,
                status=status,
                sql_text=sql,
                rewritten_sql=None,
                error_message=error_message,
                rows_affected=rows_affected,
                session_id=_context_value(context, "audit_session_id"),
                database_name=_context_value(context, "database"),
                schema_name=_context_value(context, "schema_name"),
            )
        except Exception:
            logger.exception("Could not write the assistant_tool audit row")


def _table_from_results(results: list[Any]) -> dict[str, Any] | None:
    """Build the ``table`` content block for a plain SQL answer.

    The rows are redacted with the shared value-level redactor, so a
    credential-shaped cell never reaches the grid. ``title`` is left empty on
    purpose: a redacted statement is not a title, and the panel falls back to a
    neutral header rather than printing a mangled query.
    """
    from app.modules.assistant.tools.redaction import redact_rows

    for result in results:
        columns = list(getattr(result, "columns", []) or [])
        rows = list(getattr(result, "rows", []) or [])
        if not columns:
            continue
        safe_rows = redact_rows(columns, rows)
        return {
            "title": "",
            "columns": columns,
            "rows": [list(r) for r in safe_rows[:200]],
        }
    return None


def _expose_last_result(context: Any, results: list[Any]) -> None:
    """Record the first tabular result on the turn context, value-redacted.

    Used by Agent Studio's ``data_to_chart``. Best-effort: a context without the
    attribute is a no-op, so the plain assistant path is unaffected.
    """
    if not hasattr(context, "last_result"):
        return
    for result in results:
        columns = list(getattr(result, "columns", []) or [])
        rows = list(getattr(result, "rows", []) or [])
        if not columns:
            continue
        context.last_result = {
            "title": "",
            "columns": columns,
            "rows": redact_rows(columns, rows),
        }
        return


def _render_result(result: Any) -> str:
    """Render one ``QueryResult`` as bounded, value-redacted text.

    Columns + a bounded preview + a row count — never an unbounded set. Values
    are passed through the value-level redactor first, so a credential-shaped
    cell never reaches the model. The engine **warning** carries the same egress
    risk as the error: on a statement without a result set it can echo the
    executed engine SQL, so it is redacted with the shared redactor too.
    """
    if not result.columns:
        # A statement with no result set (affected rows only).
        return json.dumps(
            {
                "affected_rows": result.affected_rows,
                "warning": _safe_redact((result.warnings or [None])[0]),
            },
            default=str,
        )

    columns = list(result.columns)
    safe_rows = redact_rows(columns, list(result.rows))

    preview_rows: list[list] = []
    used = 0
    truncated = False
    for row in safe_rows:
        bounded_row = [_bounded_cell(cell) for cell in row]
        serialized = json.dumps(bounded_row, default=str)
        if used + len(serialized) > ASSISTANT_MAX_PREVIEW_CHARS:
            truncated = True
            break
        used += len(serialized)
        preview_rows.append(bounded_row)

    if truncated or len(preview_rows) < len(safe_rows):
        truncated = True

    return json.dumps(
        {
            "columns": columns,
            "row_count": result.row_count,
            "rows_returned": len(preview_rows),
            "truncated": truncated,
            "rows": preview_rows,
            # The first warning carries the role the statement actually ran
            # under when Nova auto-selected a different granted role. Redacted
            # like the error, because a warning can echo the executed SQL.
            "warning": _safe_redact((result.warnings or [None])[0]),
        },
        default=str,
    )


def _bounded_cell(cell: Any) -> Any:
    if not isinstance(cell, str):
        return cell
    if len(cell) <= ASSISTANT_MAX_CELL_CHARS:
        return cell
    return cell[:ASSISTANT_MAX_CELL_CHARS] + "…[truncated]"


def _sql_from(invocation: ToolInvocation) -> str:
    value = invocation.arguments.get("sql")
    return value if isinstance(value, str) else ""


def _context_value(context: Any, name: str) -> str | None:
    value = getattr(context, name, None)
    return value if isinstance(value, str) and value else None


def _active_role(context: Any) -> str | None:
    """The role the assistant tool must execute as.

    The authenticated session's ``active_role`` is the single source of truth
    (the same value the HTTP query path resolves), so the assistant cannot run
    under a different role than the one the UI shows as active. Falls back to
    the workbook context role only when no session is attached — a direct
    unit-test call — so the tool still has a role in that path.
    """
    user = getattr(context, "user", None)
    if isinstance(user, dict):
        active = user.get("active_role")
        if isinstance(active, str) and active:
            return active
        granted = user.get("roles") or []
        if granted:
            return granted[0]
    return _context_value(context, "role")


def _safe_redact(sql: str) -> str:
    """Redact, falling back to a refusal marker when redaction cannot complete.

    ``redact_sql_credentials`` fails closed when a credential value would
    survive; the tool must not turn that into a leak, and must not raise out of
    a preview. The marker says "unavailable", never the raw SQL.
    """
    if not sql:
        return sql
    try:
        return redact_sql_credentials(sql)
    except Exception:
        logger.warning("Assistant SQL preview could not be redacted; withholding it")
        return "[statement withheld: it could not be redacted]"


def _engine_error_text(error: Exception) -> str:
    """A short, value-free error message.

    The exception type plus, when present, the engine's numeric code. The raw
    message is not forwarded: it can echo the statement.
    """
    code = error.args[0] if error.args and isinstance(error.args[0], int) else None
    if code is not None:
        return f"{type(error).__name__} ({code})"
    return type(error).__name__


def _is_privilege_error(message: str) -> bool:
    lowered = message.lower()
    return "5203" in lowered or any(marker in lowered for marker in _ENGINE_DENIED_MARKERS)


#: Process-wide instance, registered by ``app.modules.assistant.registry``.
query_execute_tool = QueryExecuteTool()
