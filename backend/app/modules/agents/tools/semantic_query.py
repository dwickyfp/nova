"""``semantic_query`` — text-to-SQL grounded on a semantic model.

The agent gives a business question; the tool asks the LLM to produce SQL from
the semantic model's defined metrics and dimensions, then runs that SQL through
``QueryService`` on the requesting user's connection. It is the Nova-native
counterpart to Cortex Analyst, without the managed service.

Non-negotiables, identical to ``query_execute``:

* **Delegate-first.** The generated SQL runs on the user's connection, so
  StarRocks RBAC decides. There is no service identity.
* **Grounded on metadata, never rows.** The prompt carries the semantic model's
  datasets/fields/metrics and relationships, not table data.
* **Guarded.** The LLM's SQL is run through the *same* pipeline as any user SQL:
  ``QueryService`` applies the SQL guard, ``@stage`` translation, credential
  redaction, and audit. This tool does not execute the model's statement any
  other way.
* **Read-only by construction.** A generated statement that is not read-only is
  refused by the same per-statement policy ``query_execute`` uses, *before* the
  engine sees it. A text-to-SQL model is not trusted to stay read-only.
* **Bounded and honest.** A row cap applies at fetch time; the model's
  ``confidence`` is surfaced with the result so a low-confidence answer is
  visibly low, not silently wrong.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.common.sql_guard import redact_sql_credentials
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.grounding import build_semantic_messages
from app.modules.assistant.provider import (
    AssistantProviderClient,
    AssistantProviderError,
)
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import (
    ToolInvocation,
    ToolOutcome,
    policy,
    record_provider_usage,
    report_tool_progress,
)
from app.modules.assistant.tools.query_execute import (
    ASSISTANT_MAX_PREVIEW_CHARS,
    ASSISTANT_MAX_ROWS,
)

logger = logging.getLogger(__name__)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": (
                "The business question to answer from the semantic model, in "
                "natural language. Example: 'revenue by region last quarter'."
            ),
        },
    },
    "required": ["question"],
}

_SEMANTIC_QUERY_PROMPT_VERSION = "v1"


class SemanticQueryTool:
    """Generates SQL from a semantic model and runs it, delegate-first."""

    name = "semantic_query"
    description = (
        "Answer a business question from the agent's semantic model. It "
        "translates the question into SQL using defined metrics and dimensions, "
        "then runs it read-only on your connection."
    )
    parameters = _PARAMETERS
    #: Read-only only; the loop may auto-approve under an auto_read_only policy.
    classification: ToolClassification = "read_only"
    requires_consent = True

    def __init__(
        self,
        *,
        max_rows: int = ASSISTANT_MAX_ROWS,
        provider: AssistantProviderClient | None = None,
    ) -> None:
        self.max_rows = max_rows
        self._provider = provider or AssistantProviderClient()

    def preview(self, invocation: ToolInvocation) -> str:
        question = _question_from(invocation)
        return f"semantic_query: {question}" if question else "semantic_query"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        question = _question_from(invocation)
        if not question:
            return ToolOutcome(ok=False, summary="", error="No question was provided.")

        user = getattr(context, "user", None) or {}
        username = user.get("username")
        encrypted_password = user.get("encrypted_password")
        if not username or not encrypted_password:
            return ToolOutcome(
                ok=False,
                summary="",
                error="No user connection is available for this tool call.",
            )

        semantic_model = await self._resolve_model(context)
        if semantic_model is None:
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    "No semantic model is configured for this agent. "
                    "Attach one in the agent settings before asking business questions."
                ),
            )

        definition = semantic_model.get("definition") or {}
        if not definition.get("datasets"):
            return ToolOutcome(
                ok=False,
                summary="",
                error="The agent's semantic model defines no datasets.",
            )

        generation_started = time.perf_counter()
        report_tool_progress(
            context,
            stage="generating_sql",
            text="Generating SQL from the semantic model",
        )
        try:
            generated = await self._generate_sql(definition, question, context)
        except AssistantProviderError as exc:
            generation_duration_ms = (time.perf_counter() - generation_started) * 1000
            trace = _semantic_trace(
                semantic_model,
                question=question,
                generated_sql="",
                confidence=None,
                generation_duration_ms=generation_duration_ms,
                execution_duration_ms=None,
            )
            trace["error"] = "The semantic SQL generator failed."
            return ToolOutcome(
                ok=False,
                summary="",
                error=str(exc),
                trace_detail=trace,
            )
        generation_duration_ms = (time.perf_counter() - generation_started) * 1000

        sql = (generated.get("sql") or "").strip()
        explanation = (generated.get("explanation") or "").strip()
        confidence = generated.get("confidence")

        if not sql:
            # The model declined because the question is not answerable from the
            # model. That is a *successful* outcome, not a tool failure: the
            # agent should tell the user what is missing, not surface an error
            # that terminates the turn. Return ok=True with the explanation so
            # the loop folds it into the answer.
            return ToolOutcome(
                ok=True,
                summary=(
                    "The semantic model cannot answer this question. "
                    + (explanation or "No matching metric or dimension is defined.")
                ),
                trace_detail=_semantic_trace(
                    semantic_model,
                    question=question,
                    generated_sql="",
                    confidence=confidence,
                    generation_duration_ms=generation_duration_ms,
                    execution_duration_ms=None,
                ),
            )

        safe_sql = _safe_sql_preview(sql)
        report_tool_progress(
            context,
            stage="sql_generated",
            text="Generated SQL",
            sql_preview=safe_sql,
        )
        report_tool_progress(
            context,
            stage="validating_sql",
            text="Validating the generated SQL",
            sql_preview=safe_sql,
        )

        # Never trust the model to stay read-only. Classify with the same policy
        # the assistant uses; a non-read-only statement is refused before the
        # engine is reached.
        from app.common.sql_guard import split_sql_statements

        statements = split_sql_statements(sql)
        if not statements:
            trace = _semantic_trace(
                semantic_model,
                question=question,
                generated_sql=safe_sql,
                confidence=confidence,
                generation_duration_ms=generation_duration_ms,
                execution_duration_ms=None,
            )
            trace["error"] = "The model produced no SQL."
            return ToolOutcome(
                ok=False,
                summary="",
                error="The model produced no SQL.",
                trace_detail=trace,
            )
        classification, decisions = policy.classify_statements(statements)
        if classification != "read_only":
            offending = next((d for d in decisions if not d.allowed), None)
            reason = offending.reason if offending else "The generated SQL is not read-only."
            trace = _semantic_trace(
                semantic_model,
                question=question,
                generated_sql=safe_sql,
                confidence=confidence,
                generation_duration_ms=generation_duration_ms,
                execution_duration_ms=None,
            )
            trace["error"] = f"The generated query was refused: {reason}"
            return ToolOutcome(
                ok=False,
                summary="",
                error=f"The generated query was refused: {reason}",
                trace_detail=trace,
            )

        from app.modules.query.service import query_service

        report_tool_progress(
            context,
            stage="executing_sql",
            text="Running the generated query",
            sql_preview=safe_sql,
        )
        execution_started = time.perf_counter()
        try:
            results = await query_service.execute_statements(
                sql=sql,
                username=username,
                encrypted_password=encrypted_password,
                database=_context_value(context, "database"),
                schema=_context_value(context, "schema_name"),
                role=_context_value(context, "role"),
                max_rows=self.max_rows,
                session_id=_context_value(context, "audit_session_id"),
                confirm_destructive=False,
                file_id=_context_value(context, "workspace_file_id"),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a tool failure
            logger.warning("semantic_query execution failed: %s", type(exc).__name__)
            execution_duration_ms = (time.perf_counter() - execution_started) * 1000
            trace = _semantic_trace(
                semantic_model,
                question=question,
                generated_sql=safe_sql,
                confidence=confidence,
                generation_duration_ms=generation_duration_ms,
                execution_duration_ms=execution_duration_ms,
            )
            trace["error"] = "The generated query failed to run."
            return ToolOutcome(
                ok=False,
                summary="",
                error="The generated query failed to run.",
                trace_detail=trace,
            )
        execution_duration_ms = (time.perf_counter() - execution_started) * 1000

        failed = next((r for r in results if r.error), None)
        if failed is not None:
            trace = _semantic_trace(
                semantic_model,
                question=question,
                generated_sql=safe_sql,
                confidence=confidence,
                generation_duration_ms=generation_duration_ms,
                execution_duration_ms=execution_duration_ms,
            )
            trace["error"] = "The generated query failed to run."
            return ToolOutcome(
                ok=False,
                summary="",
                error="The generated query failed to run.",
                trace_detail=trace,
            )

        table = _table_payload(results, title=question)
        if table is not None:
            # Expose the fetched rows to ``data_to_chart`` in the same turn.
            context.last_result = {
                "title": question,
                "columns": table["columns"],
                "rows": table["rows"],
            }

        row_count = len(table["rows"]) if table is not None else 0
        report_tool_progress(
            context,
            stage="query_completed",
            text=f"Retrieved {row_count} row{'s' if row_count != 1 else ''}",
            sql_preview=safe_sql,
        )

        return ToolOutcome(
            ok=True,
            summary=_render(
                question=question,
                sql=safe_sql,
                explanation=explanation,
                confidence=confidence,
                results=results,
            ),
            table=table,
            trace_detail=_semantic_trace(
                semantic_model,
                question=question,
                generated_sql=safe_sql,
                confidence=confidence,
                generation_duration_ms=generation_duration_ms,
                execution_duration_ms=execution_duration_ms,
            ),
        )

    async def _resolve_model(self, context: Any) -> dict[str, Any] | None:
        """Load the agent's semantic model from the caller's own records.

        When an agent binds more than one model, the first one that parses is
        used; grounding each candidate and letting the model route across all of
        them is a larger feature than v1 needs, and a single-model agent (the
        common case) is unaffected.
        """
        ids = _context_value(context, "semantic_model_ids") or []
        scalar = _context_value(context, "semantic_model_id")
        if not ids and scalar:
            ids = [scalar]
        owner = _context_value(context, "user_name")
        if not ids or not owner:
            return None
        for model_id in ids:
            model = await agent_repository.get_semantic_model(model_id, owner_name=owner)
            if model and (model.get("definition") or {}).get("datasets"):
                return model
        return None

    async def _generate_sql(
        self, definition: dict[str, Any], question: str, context: Any
    ) -> dict[str, Any]:
        messages = build_semantic_messages(definition, question)
        provider_id = _context_value(context, "model_provider_id")
        model = _context_value(context, "model_name")
        config = await self._provider.resolve(provider_id=provider_id, model=model)
        message = await self._provider.complete(messages=messages, provider=config)
        record_provider_usage(context, message)
        content = message.get("content") or ""
        return _parse_model_json(content)


def _parse_model_json(content: str) -> dict[str, Any]:
    """Parse the model's JSON reply, tolerating a fenced code block."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"sql": "", "explanation": "The model returned an unreadable response."}
    if not isinstance(parsed, dict):
        return {"sql": "", "explanation": "The model returned an unexpected shape."}
    return parsed


def _safe_sql_preview(sql: str) -> str:
    """Return generated SQL only after the shared credential redactor accepts it."""
    try:
        return redact_sql_credentials(sql)
    except Exception:
        logger.warning("Generated semantic SQL could not be redacted; withholding it")
        return "[statement withheld: it could not be redacted]"


def _render(
    *,
    question: str,
    sql: str,
    explanation: str,
    confidence: Any,
    results: list[Any],
) -> str:
    """The bounded text the model reads back: SQL, rows, and honesty markers."""
    lines = [f"question: {question}", f"sql:\n{sql}"]
    if explanation:
        lines.append(f"explanation: {explanation}")
    if isinstance(confidence, int | float):
        lines.append(f"confidence: {confidence}")
    for result in results:
        columns = list(getattr(result, "columns", []) or [])
        rows = list(getattr(result, "rows", []) or [])
        lines.append(f"columns: {', '.join(columns)}")
        lines.append(f"row_count: {getattr(result, 'row_count', len(rows))}")
        preview = _preview_rows(columns, rows)
        if preview:
            lines.append("rows:")
            lines.append(preview)
    return _truncate("\n".join(lines), ASSISTANT_MAX_PREVIEW_CHARS)


def _table_payload(results: list[Any], *, title: str = "") -> dict[str, Any] | None:
    """Build the ``table`` content block, value-redacted.

    ``title`` is the business question the rows answer, so the grid renders with
    a header a reader recognises instead of a generic label. Uses the same
    value-level redactor the assistant uses, so a credential-shaped cell never
    reaches the client through a chart's data either.
    """
    from app.modules.assistant.tools.redaction import redact_rows

    for result in results:
        columns = list(getattr(result, "columns", []) or [])
        rows = list(getattr(result, "rows", []) or [])
        if not columns:
            continue
        safe_rows = redact_rows(columns, rows)
        # Bound what a chart block carries; the grid is a view, not an export.
        return {
            "title": title,
            "columns": columns,
            "rows": [list(r) for r in safe_rows[:200]],
        }
    return None


def _preview_rows(columns: list[str], rows: list[list]) -> str:
    if not rows:
        return ""
    header = " | ".join(columns)
    body = []
    for row in rows[:50]:
        body.append(" | ".join("" if v is None else str(v) for v in row))
    return "\n".join([header, *body])


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 18] + "\n… [truncated]"


def _question_from(invocation: ToolInvocation) -> str:
    value = invocation.arguments.get("question")
    return value.strip() if isinstance(value, str) else ""


def _context_value(context: Any, name: str) -> Any:
    return getattr(context, name, None)


#: Process-wide instance, registered by ``app.modules.agents.registry``.
semantic_query_tool = SemanticQueryTool()


def _semantic_trace(
    model: dict[str, Any],
    *,
    question: str,
    generated_sql: str,
    confidence: Any,
    generation_duration_ms: float,
    execution_duration_ms: float | None,
) -> dict[str, Any]:
    """Bounded metadata snapshot for the Observability semantic inspector.

    The snapshot deliberately excludes rows, field expressions, instructions,
    and credentials. It records the model identity and physical sources that
    actually grounded this call, so editing the semantic model later cannot
    rewrite the historical trace.
    """
    definition = model.get("definition") or {}
    datasets = [
        {
            "name": str(dataset.get("name") or ""),
            "source": str(dataset.get("source") or ""),
        }
        for dataset in (definition.get("datasets") or [])[:50]
        if isinstance(dataset, dict)
    ]
    metrics = [
        str(metric.get("name"))
        for metric in (definition.get("metrics") or [])[:100]
        if isinstance(metric, dict) and metric.get("name")
    ]
    payload: dict[str, Any] = {
        "kind": "semantic_context",
        "semantic_model": {
            "id": str(model.get("semantic_model_id") or ""),
            "name": str(model.get("name") or definition.get("name") or ""),
            "ossie_version": str(model.get("ossie_version") or definition.get("version") or ""),
        },
        "question": question[:2000],
        "datasets": datasets,
        "metrics": metrics,
        "generated_sql": generated_sql,
        "confidence": confidence if isinstance(confidence, int | float) else None,
        "validation_warnings": [],
        "generation_duration_ms": round(generation_duration_ms, 3),
        "execution_duration_ms": (
            round(execution_duration_ms, 3) if execution_duration_ms is not None else None
        ),
    }
    return payload
