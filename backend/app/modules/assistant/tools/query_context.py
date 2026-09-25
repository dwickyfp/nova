"""Read current SQL Workspace failure and repair events without executing SQL."""

from __future__ import annotations

import re
from typing import Any

from app.modules.assistant.app_context import NoveAppContext, NoveApplicationEvent, _safe_text
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

_SECRET_WORD = re.compile(
    r"\b(?:password|passwd|secret|token|credential|api[_-]?key|private[_-]?key|"
    r"identified\s+by)\b",
    re.IGNORECASE,
)


def _current_app(context: Any) -> NoveAppContext | None:
    if getattr(context, "agent_id", None) is not None:
        return None
    app = getattr(context, "app_context", None)
    return app if isinstance(app, NoveAppContext) else None


def _event_execution_id(event: NoveApplicationEvent) -> str | None:
    value = event.execution_id or event.payload.get("executionId")
    return value if isinstance(value, str) else None


def _safe_sql(event: NoveApplicationEvent | None) -> str | None:
    if event is None:
        return None
    value = event.payload.get("sql")
    if not isinstance(value, str) or not value or len(value) > 4_000:
        return None
    if _SECRET_WORD.search(value):
        return None
    safe = _safe_text(value)
    return safe if safe != "***" else None


def _error_category(message: str) -> tuple[str, str]:
    lower = message.casefold()
    if any(term in lower for term in ("access denied", "permission denied", "not authorized")):
        return "authorization", "Inspect the active role and object privileges."
    if "unknown column" in lower or "column not found" in lower:
        return "column", "Check the selected column name and aliases."
    if "unknown table" in lower or "table not found" in lower or "doesn't exist" in lower:
        return "table", "Check the current database, schema, and table name."
    if "syntax error" in lower or "parse error" in lower:
        return "syntax", "Check SQL near the reported position."
    if "timeout" in lower or "timed out" in lower:
        return "timeout", "Inspect the query plan and execution cost."
    return "unknown", "Use the reported error and current SQL to investigate."


class InspectQueryErrorTool:
    name = "inspect_query_error"
    description = (
        "Inspect the current SQL Workspace execution error and its matching, "
        "redacted query event. Returns a bounded diagnosis hint, never a claim "
        "that a repair has been applied or verified."
    )
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    classification: ToolClassification = "read_only"
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        return "Inspect the current SQL execution error"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        app = _current_app(context)
        execution = app.execution if app else None
        if app is None:
            return ToolOutcome(ok=False, summary="", error="No current SQL execution is available.")
        events = app.current_events()
        latest_terminal = next(
            (item for item in reversed(events) if item.type in {"query_failed", "query_completed"}),
            None,
        )
        # Assistant code-card runs publish outcomes without changing the page's
        # own execution state. The newest card outcome is the current evidence.
        card_event = (
            latest_terminal
            if latest_terminal
            and latest_terminal.artifact_id
            and latest_terminal.status
            == ("failure" if latest_terminal.type == "query_failed" else "success")
            else None
        )
        if card_event is not None and card_event.type == "query_completed":
            return ToolOutcome(
                ok=False,
                summary="",
                error="The current SQL execution has no error to inspect.",
            )
        if card_event is None and (execution is None or execution.type not in {"sql", "query"}):
            return ToolOutcome(ok=False, summary="", error="No current SQL execution is available.")
        if card_event is None and execution.status != "error":
            return ToolOutcome(
                ok=False,
                summary="",
                error="The current SQL execution has no error to inspect.",
            )
        event = card_event or next(
            (
                item for item in reversed(events)
                if item.type == "query_failed"
                and item.status == "failure"
                and execution is not None
                and execution.execution_id is not None
                and _event_execution_id(item) == execution.execution_id
            ),
            None,
        )
        execution_id = _event_execution_id(card_event) if card_event else execution.execution_id
        error = (execution.error_message if execution and card_event is None else None) or (
            event.payload.get("errorMessage") if event else None
        )
        message = _safe_text(error) if isinstance(error, str) else "Query execution failed."
        category, next_step = _error_category(message)
        sql = _safe_sql(event)
        return ToolOutcome(
            ok=True,
            summary=f"Inspected failed SQL execution {execution_id or 'current'}.",
            data={
                "surface_id": app.surface.id,
                "execution_id": execution_id,
                "status": "observed_failure",
                "error_code": (
                    _safe_text(execution.error_code)
                    if execution and card_event is None and execution.error_code
                    else None
                ),
                "error_message": message,
                "error_category": category,
                "suggested_check": next_step,
                "sql": sql,
                "sql_available": sql is not None,
                "repair_applied": False,
                "verification": "not_run",
            },
            evidence={"source": "current_surface_execution_event"},
        )


class VerifyQueryRepairTool:
    name = "verify_query_repair"
    description = (
        "Verify an applied Nove SQL editor patch from current Workspace events. "
        "Requires a correlated successful rerun; an applied patch alone is not success."
    )
    parameters = {
        "type": "object",
        "properties": {
            "correlation_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": "Patch artifact id. Omit to verify the current rerun.",
            }
        },
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        return "Verify the current SQL patch and rerun"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        app = _current_app(context)
        execution = app.execution if app else None
        if (
            execution is None
            or execution.type not in {"sql", "query"}
            or execution.status != "success"
            or not execution.execution_id
        ):
            return ToolOutcome(
                ok=False,
                summary="",
                error="No successful current SQL rerun is available for verification.",
                error_class="POSTCONDITION_UNVERIFIED",
            )
        requested = invocation.arguments.get("correlation_id")
        if requested is not None and (
            not isinstance(requested, str) or not 1 <= len(requested) <= 128
        ):
            return ToolOutcome(ok=False, summary="", error="Invalid patch correlation id.")
        events = app.current_events()
        completion = next(
            (
                item
                for item in reversed(events)
                if item.type == "query_completed"
                and item.status == "success"
                and _event_execution_id(item) == execution.execution_id
                and item.correlation_id
                and (requested is None or requested == item.correlation_id)
            ),
            None,
        )
        if completion is None:
            return ToolOutcome(
                ok=False,
                summary="",
                error="The current successful query is not linked to a Nove patch.",
                error_class="POSTCONDITION_UNVERIFIED",
            )
        completion_index = events.index(completion)
        patch = next(
            (
                item
                for item in reversed(events[:completion_index])
                if item.type == "editor_patch_applied"
                and item.status == "success"
                and item.correlation_id == completion.correlation_id
            ),
            None,
        )
        if patch is None:
            return ToolOutcome(
                ok=False,
                summary="",
                error="A matching applied patch is not visible for this successful query.",
                error_class="POSTCONDITION_UNVERIFIED",
            )
        persisted = patch.payload.get("persisted") is True
        return ToolOutcome(
            ok=True,
            summary=(
                "The patched SQL ran successfully and was saved."
                if persisted
                else "The patched SQL ran successfully; the editor change was not saved."
            ),
            data={
                "surface_id": app.surface.id,
                "execution_id": execution.execution_id,
                "correlation_id": completion.correlation_id,
                "status": "verified_success",
                "persisted": persisted,
                "row_count": execution.row_count,
                "verification_source": "correlated_current_surface_events",
            },
            evidence={
                "source": "correlated_current_surface_events",
                "patch_event_id": patch.id,
                "execution_event_id": completion.id,
            },
        )


inspect_query_error_tool = InspectQueryErrorTool()
verify_query_repair_tool = VerifyQueryRepairTool()
