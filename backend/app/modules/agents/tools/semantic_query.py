"""``semantic_query``: a model-planned, Nova-compiled governed query.

The agent gives a business question. The model writes a structured
``SemanticPlan`` against the view's catalog, in whatever language the question
is in (the turn planner's plan is used when it has one). Nova validates the
plan, applies the user's intent frame to the turn's first query, resolves the
relationship graph, checks grain, additivity, and fanout, and compiles
StarRocks SQL. The model never supplies the SQL or the join path.

Non-negotiables, identical to ``query_execute``:

* **Delegate-first.** The generated SQL runs on the user's connection, so
  StarRocks RBAC decides. There is no service identity.
* **Grounded on metadata, never rows.** The planner sees the catalog (datasets,
  fields, metrics, relationships, sample values), not table data.
* **Guarded.** Nova's compiled SQL is run through the *same* pipeline as any user SQL:
  ``QueryService`` applies the SQL guard, ``@stage`` translation, credential
  redaction, and audit. This tool does not execute the model's statement any
  other way.
* **Read-only by construction.** A compiled statement that is not read-only is
  refused by the same per-statement policy ``query_execute`` uses before the
  engine sees it.
* **Bounded and honest.** A row cap applies at fetch time. The trace records
  where the plan came from (``plan_source``), not a model's self-reported
  confidence.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.common.sql_guard import redact_sql_credentials
from app.core.config import settings
from app.core.database import configured_timezone
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import (
    SemanticPlan,
    SemanticPlanError,
    validate_plan,
)
from app.modules.agents.semantic.runtime import (
    VerifiedQuery,
)
from app.modules.agents.semantic.time_ranges import resolve_execution_time
from app.modules.assistant.evidence_health import (
    EvidenceFacts,
    assess_evidence,
    attach_evidence_health,
)
from app.modules.assistant.provider import (
    AssistantProviderClient,
    AssistantProviderError,
)
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.security import session_security
from app.modules.assistant.tools import (
    ToolInvocation,
    ToolOutcome,
    policy,
    report_tool_progress,
)
from app.modules.assistant.tools.query_execute import (
    ASSISTANT_MAX_PREVIEW_CHARS,
    ASSISTANT_MAX_ROWS,
    _active_role,
)

logger = logging.getLogger(__name__)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": (
                "The business question to answer from the semantic model, in "
                "natural language. Example: 'revenue by region in the previous quarter'. "
                "Keep the user's limits, thresholds, and periods. Ask for every metric "
                "in one question ('marketing spend and revenue by channel this month'): "
                "Nova joins metrics from different facts on their shared dimensions."
            ),
        },
        "semantic_view": {
            "type": "string",
            "description": (
                "The Semantic View to query, by its name in the agent scope. Omit it when "
                "the agent has one view."
            ),
        },
    },
    "required": ["question"],
}

_SEMANTIC_QUERY_PROMPT_VERSION = "v1"


class SemanticQueryTool:
    """Compiles SQL from an active Semantic View and runs it, delegate-first."""

    name = "semantic_query"
    description = (
        "Answer a governed business metric question. Nova selects a published "
        "Semantic View, validates a SemanticPlan, resolves joins and grain, compiles "
        "StarRocks SQL, and runs it read-only. Do not use for explicit SQL or "
        "schema inspection. Returns verified rows and semantic evidence."
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
        self._compiler = SemanticCompiler()

    def preview(self, invocation: ToolInvocation) -> str:
        question = _question_from(invocation)
        return f"semantic_query: {question}" if question else "semantic_query"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        outcome = await self._run(invocation, context)
        if (
            not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False)
            or outcome.metadata.get("evidence_health")
        ):
            return outcome
        trace = outcome.trace_detail or {}
        view = trace.get("semantic_view") or {}
        facts = EvidenceFacts(
            semantic_grounding=trace.get("semantic_grounding", "unknown"),
            semantic_view_id=view.get("id") or None,
            semantic_version=view.get("version"),
            semantic_fingerprint=view.get("fingerprint") or None,
            execution_status=(
                "failed" if outcome.error_class == "SEMANTIC_EXECUTION_ERROR" else "not_run"
            ),
            semantic_ambiguity=(
                "unresolved" if outcome.error_class == "CLARIFICATION_REQUIRED" else "unknown"
            ),
        )
        return attach_evidence_health(
            outcome, assess_evidence(facts, assessed_at=datetime.now(UTC))
        )

    async def _run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
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

        requested_view = invocation.arguments.get("semantic_view")
        if isinstance(requested_view, str) and requested_view.strip() and hasattr(
            context, "requested_semantic_view"
        ):
            context.requested_semantic_view = requested_view.strip()
        semantic_model = await self._resolve_model(context, question)
        if semantic_model is None:
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    "No published Semantic View is available to this agent. "
                    "Attach an accessible published View in the agent settings."
                ),
            )

        definition = semantic_model.get("definition") or {}
        if not definition.get("datasets"):
            return ToolOutcome(
                ok=False,
                summary="",
                error="The selected Semantic View defines no datasets.",
            )

        semantic_ir = semantic_model.get("_scoped_ir") or SemanticModelIR.from_ossie(definition)
        model_id = str(semantic_model.get("semantic_model_id") or "")
        generation_started = time.perf_counter()
        report_tool_progress(
            context,
            stage="planning_semantics",
            text="Planning with the semantic catalog",
        )
        frame = getattr(context, "intent_frame", None)
        try:
            from app.modules.agents.semantic.model_planner import (
                generate_plan,
                planning_catalog,
                primary_plan_from,
            )

            first_query = not getattr(context, "primary_query_done", False)
            plan = None
            plan_from_turn = False
            primary = getattr(context, "primary_plan", None)
            if first_query and isinstance(primary, dict) and primary.get("view") in {
                None, model_id,
            }:
                # The turn planner already read the question; its plan is checked,
                # never trusted, and a rejected one is simply planned again here.
                plan = primary_plan_from(primary.get("plan"), semantic_ir)
                plan_from_turn = plan is not None
            if plan is None:
                plan = await generate_plan(
                    self._provider,
                    semantic_ir,
                    await planning_catalog(semantic_ir, question),
                    question,
                    context,
                    prior_plan=_prior_plan(semantic_ir, context),
                )
            material = [item.text for item in plan.unresolved_concepts if item.material]
            if material:
                return _clarification(semantic_ir, material)
            notes: list[str] = []
            if first_query and frame is not None:
                plan, notes = reconcile_with_frame(plan, frame, semantic_ir)
            period_note = "; ".join(notes) or None
            verified_hit = _verified_match(definition, model_id, semantic_ir, plan)
            errors = validate_plan(semantic_ir, plan)
            if errors:
                raise SemanticPlanError("; ".join(errors))
            time_context = None
            if (getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False)
                    and plan.time and plan.time.range):
                execution_now = getattr(context, "execution_now", None) or datetime.now(UTC)
                if callable(getattr(context, "business_clock_hook", None)):
                    execution_now = await context.business_clock_hook(invocation, context)
                execution_timezone = getattr(context, "execution_timezone", None)
                time_context = resolve_execution_time(
                    plan.time.range, plan.time.compare,
                    now=execution_now,
                    timezone=(configured_timezone() if execution_timezone is None
                              else execution_timezone),
                )
            compiled = self._compiler.compile(semantic_ir, plan, time_context=time_context)
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
        except SemanticPlanError as exc:
            generation_duration_ms = (time.perf_counter() - generation_started) * 1000
            return ToolOutcome(
                ok=False,
                summary="",
                error="The semantic plan could not be compiled safely.",
                error_class="INVALID_SEMANTIC_PLAN",
                recoverable=True,
                safe_detail=str(exc),
                repair_context={
                    "available_metrics": [item.name for item in semantic_ir.metrics],
                    "available_dimensions": [
                        field.name
                        for dataset in semantic_ir.datasets
                        for field in dataset.fields
                        if field.kind.value == "dimension"
                    ],
                },
                trace_detail=_semantic_trace(
                    semantic_model,
                    question=question,
                    generated_sql="",
                    confidence=None,
                    generation_duration_ms=generation_duration_ms,
                    execution_duration_ms=None,
                ),
            )
        generation_duration_ms = (time.perf_counter() - generation_started) * 1000

        sql = compiled.sql
        explanation = "Compiled from governed semantic metrics and dimensions."
        # Where the plan came from, not a model's opinion of itself: a verified
        # query is exact, a validated plan is the model's reading of the question.
        plan_source = "verified_query" if verified_hit else (
            "turn_planner" if plan_from_turn else "model_planner"
        )
        confidence = 1.0 if verified_hit else 0.8

        if time_context:
            from app.modules.intelligence.contracts import fingerprint

            pinned_context = {
                "semantic_view_id": model_id, "semantic_version": semantic_model.get("version"),
                "semantic_fingerprint": semantic_model.get("fingerprint"),
                "validated_plan_fingerprint": fingerprint(plan.as_dict()),
                "execution_time": time_context.as_dict(),
            }
            if callable(getattr(context, "business_time_hook", None)):
                await context.business_time_hook(invocation, pinned_context, context)
            if callable(getattr(context, "execution_time_sink", None)):
                await context.execution_time_sink(pinned_context)

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
        security = session_security(user)
        try:
            results = await query_service.execute_statements(
                sql=sql,
                username=username,
                encrypted_password=encrypted_password,
                database=_context_value(context, "database"),
                schema=_context_value(context, "schema_name"),
                role=_active_role(context),
                security_context_version=security.security_context_version,
                max_rows=self.max_rows,
                session_id=_context_value(context, "audit_session_id"),
                confirm_destructive=False,
                file_id=_context_value(context, "workspace_file_id"),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a tool failure
            logger.warning("semantic_query execution failed: %s", type(exc).__name__)
            execution_duration_ms = (time.perf_counter() - execution_started) * 1000
            await _record_usage(
                security, semantic_model, semantic_ir, plan, execution_duration_ms, succeeded=False,
                context=context,
            )
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
                error_class="SEMANTIC_EXECUTION_ERROR",
                recoverable=True,
                safe_detail=(
                    "The compiled query failed. Repair the semantic plan, not authorization."
                ),
                trace_detail=trace,
            )
        execution_duration_ms = (time.perf_counter() - execution_started) * 1000

        failed = next((r for r in results if r.error), None)
        if failed is not None:
            await _record_usage(
                security, semantic_model, semantic_ir, plan, execution_duration_ms, succeeded=False,
                context=context,
            )
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
                error_class="SEMANTIC_EXECUTION_ERROR",
                recoverable=True,
                safe_detail="The compiled query failed against the current schema.",
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
        await _record_usage(
            security, semantic_model, semantic_ir, plan, execution_duration_ms, succeeded=True,
            context=context,
        )

        if hasattr(context, "primary_query_done"):
            context.primary_query_done = True
        summary = _render(
            question=question, sql=safe_sql, explanation=explanation,
            confidence=confidence, results=results,
        )
        health = None
        if getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
            health = assess_evidence(
                _execution_facts(
                    semantic_model, results, plan_source, verified_hit, plan,
                    preview_truncated=summary.endswith("… [truncated]"),
                ),
                assessed_at=datetime.now(UTC),
            )
        outcome = ToolOutcome(
            ok=True,
            summary=_render(
                question=question,
                sql=safe_sql,
                explanation=explanation,
                confidence=confidence,
                results=results,
                evidence_health=health.model_dump(mode="json") if health else None,
            ),
            table=table,
            data={
                "semantic_plan": plan.as_dict(),
                "sql": safe_sql,
                "row_count": row_count,
            },
            evidence={
                "source": "nova_semantic_view",
                "view_id": model_id,
                "version": semantic_model.get("version"),
                "semantic_model_id": semantic_model.get("semantic_model_id"),
                "semantic_model_fingerprint": semantic_ir.fingerprint,
                "metrics": list(plan.metrics),
                "dimensions": list(plan.dimensions),
            },
            metadata={
                "relationship_path": list(compiled.relationship_path),
                "plan_source": plan_source,
                "warnings": list(compiled.warnings),
                "period_from_user": period_note,
                "verified_query_hit": verified_hit.verified_query_id if verified_hit else None,
                **({"complete": health.facts.coverage == "complete"} if health else {}),
            },
            state_patch={
                "semantic_plan": plan.as_dict(),
                "active_semantic_view": model_id,
                "active_semantic_model": str(
                    semantic_model.get("semantic_model_id") or semantic_ir.name
                ),
                "selected_metrics": list(plan.metrics),
                "filters": {item.field: str(item.value) for item in plan.filters},
                "time_context": (
                    {
                        "primary": plan.time.range or "",
                        "comparison": plan.time.compare or "",
                    }
                    if plan.time
                    else {}
                ),
                "unresolved_ambiguities": [item.text for item in plan.unresolved_concepts],
            },
            warnings=[*compiled.warnings, *([period_note] if period_note else [])],
            trace_detail={
                **_semantic_trace(
                    semantic_model,
                    question=question,
                    generated_sql=safe_sql,
                    confidence=confidence,
                    generation_duration_ms=generation_duration_ms,
                    execution_duration_ms=execution_duration_ms,
                ),
                # Kept on the step so a liked answer can become a verified query.
                "semantic_plan": plan.as_dict(),
            },
        )
        if health:
            from app.modules.intelligence.evidence import (
                execution_envelope,
                investigation_seed,
                semantic_population_fingerprint,
            )

            envelope = execution_envelope(
                semantic_model, semantic_ir, plan, health, time_context,
                evidence_id=invocation.tool_call_id,
            )
            seed, requirements = investigation_seed(envelope, semantic_ir, plan, time_context)
            outcome.trace_detail["evidence_envelope"] = envelope.model_dump(mode="json")
            if time_context:
                outcome.trace_detail["execution_time_context"] = time_context.as_dict()
            outcome = replace(outcome, business_result={
                "envelope": envelope, "seed": seed, "required_inputs": requirements,
                "population_fingerprint": semantic_population_fingerprint(plan),
                "time_dimension": plan.time.dimension if plan.time else None,
            })
            return attach_evidence_health(outcome, health)
        return outcome

    async def _resolve_model(self, context: Any, question: str) -> dict[str, Any] | None:
        from app.modules.agents.semantic.access import load_authorized_models

        models = await load_authorized_models(context)
        if len(models) == 1:
            return models[0]
        # The view is chosen by name: the turn planner's primary view, or the one
        # the loop model names. Word overlap with the question is not a choice.
        primary = getattr(context, "primary_plan", None) or {}
        wanted = getattr(context, "requested_semantic_view", None) or primary.get("view")
        chosen = next(
            (model for model in models
             if wanted and wanted in {str(model["semantic_model_id"]), str(model.get("name"))}),
            None,
        )
        return chosen or (models[0] if models else None)


def reconcile_with_frame(
    plan: SemanticPlan, frame: Any, model: SemanticModelIR
) -> tuple[SemanticPlan, list[str]]:
    """The user's own constraints win over the loop model's rewrite of the question.

    The loop model often rewrites the question it passes here ("this month vs last
    month" became "revenue by month for the last 3 months"). The turn planner read
    the user's words once, in whatever language, into an ``IntentFrame``; the
    turn's first query is brought back in line with it.
    """
    notes = []
    for step in (_frame_period, _frame_rank, _frame_threshold):
        plan, note = step(plan, frame, model)
        if note:
            notes.append(note)
    return plan, notes


def _frame_period(
    plan: SemanticPlan, frame: Any, model: SemanticModelIR
) -> tuple[SemanticPlan, str | None]:
    from app.modules.agents.semantic.planning import SemanticTime

    if not frame.range:
        return plan, None
    current = plan.time
    # "3 bulan terakhir" asks for one total; a series needs the user to ask for it.
    drop_grain = bool(current and current.grain and not frame.asks_series)
    # A raw date column groups by day as surely as a day grain does.
    drop_date = bool(current and current.dimension in plan.dimensions and not frame.asks_series)
    if (
        current is not None and not drop_grain and not drop_date
        and (current.range, current.compare) == (frame.range, frame.compare)
        and (frame.grain is None or current.grain == frame.grain)
    ):
        return plan, None
    dimension = current.dimension if current is not None else None
    if dimension is None and plan.metrics:
        metric = model.metric(plan.metrics[0])
        dimension = metric.default_time_dimension if metric else None
    if not dimension:
        return plan, None
    grain = frame.grain or (current.grain if current is not None and not drop_grain else None)
    if frame.compare:
        grain = None  # a two-period comparison groups by period, not by grain
    dimensions = plan.dimensions if frame.asks_series else tuple(
        name for name in plan.dimensions if name != dimension
    )
    time = SemanticTime(dimension, grain=grain, range=frame.range, compare=frame.compare)
    note = f"Used the period the user asked for ({frame.range}"
    note += f", compared {frame.compare})" if frame.compare else ")"
    return replace(plan, time=time, dimensions=dimensions), note


def _frame_rank(
    plan: SemanticPlan, frame: Any, model: SemanticModelIR
) -> tuple[SemanticPlan, str | None]:
    from app.modules.agents.semantic.planning import SemanticOrder, SemanticTopN

    if (
        not (frame.top_n or frame.order)
        or plan.limit is not None or plan.top_n_per_group is not None
        or not plan.dimensions or not plan.metrics
        or (plan.time is not None and (plan.time.compare or plan.time.grain))
    ):
        return plan, None
    direction = frame.order or "desc"
    if frame.per_group_dimension:
        # A per-group ranking is never a flat LIMIT; without a clear group, leave it.
        group = frame.per_group_dimension
        if not frame.top_n or group not in plan.dimensions or len(plan.dimensions) < 2:
            return plan, None
        top = SemanticTopN(frame.top_n, (group,), plan.metrics[0])
        return replace(plan, top_n_per_group=top), f"Kept the user's top {frame.top_n} per {group}"
    order = plan.order_by or (SemanticOrder(plan.metrics[0], direction),)
    if frame.top_n:
        return (
            replace(plan, limit=frame.top_n, order_by=order),
            f"Kept the user's top {frame.top_n}",
        )
    if plan.order_by:
        return plan, None
    # "Which channel sold the most?": rank every group, leader first, no LIMIT.
    return replace(plan, order_by=order), "Ranked by the user's superlative"


def _frame_threshold(
    plan: SemanticPlan, frame: Any, _model: SemanticModelIR
) -> tuple[SemanticPlan, str | None]:
    from app.modules.agents.semantic.planning import SemanticHaving

    threshold = frame.threshold
    if (
        threshold is None or plan.having or not plan.dimensions or not plan.metrics
        or any(item.operator in {">", ">=", "<", "<="} for item in plan.filters)
    ):
        return plan, None
    metric = threshold.metric if threshold.metric in plan.metrics else plan.metrics[0]
    value = threshold.value
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    condition = SemanticHaving(metric, threshold.operator, value)
    return (
        replace(plan, having=(condition,)),
        f"Kept the user's condition ({metric} {threshold.operator} {value})",
    )


def _verified_match(
    definition: dict[str, Any], model_id: str, model: SemanticModelIR, plan: SemanticPlan
) -> VerifiedQuery | None:
    """A verified query with exactly this plan; matched by meaning, not by words."""
    for row in definition.get("verified_queries") or []:
        if not isinstance(row, dict) or not (
            row.get("question") and row.get("semantic_plan") and row.get("verified_sql")
        ):
            continue
        try:
            candidate = SemanticPlan.from_dict(row["semantic_plan"])
        except (SemanticPlanError, TypeError, ValueError):
            continue
        if candidate != plan:
            continue
        return VerifiedQuery(
            verified_query_id=str(row.get("verified_query_id") or ""),
            semantic_model_id=model_id,
            model_fingerprint=model.fingerprint,
            question=str(row["question"]),
            semantic_plan=candidate,
            verified_sql=str(row["verified_sql"]),
            tags=tuple(row.get("tags") or []),
            usage_count=int(row.get("usage_count") or 0),
            success_count=int(row.get("success_count") or 0),
        )
    return None


def _prior_plan(model: SemanticModelIR, context: Any) -> SemanticPlan | None:
    active = getattr(context, "active_state", None) or {}
    raw = active.get("semantic_plan") if isinstance(active, dict) else None
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        plan = SemanticPlan.from_dict(raw)
    except (SemanticPlanError, TypeError, ValueError):
        return None
    return None if validate_plan(model, plan) else plan


def _clarification(model: SemanticModelIR, unresolved: list[str]) -> ToolOutcome:
    """A request the catalog cannot express is a question back, not a failure."""
    return ToolOutcome(
        ok=False,
        summary="",
        error="The question needs clarification.",
        error_class="CLARIFICATION_REQUIRED",
        recoverable=True,
        safe_detail=(
            "These parts of the request are not covered by the Semantic View: "
            + ", ".join(unresolved)
            + ". Ask the user one short question in their language, naming what "
            "is available instead. Do not guess a value."
        ),
        repair_context={
            "unresolved": unresolved,
            "available_metrics": [item.name for item in model.metrics][:40],
            "available_dimensions": [
                field.name
                for dataset in model.datasets
                for field in dataset.fields
                if field.kind.value == "dimension"
            ][:60],
        },
    )


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


def _question_similarity(left: str, right: str) -> float:
    left_words = set(re.findall(r"[a-z0-9]+", left.lower()))
    right_words = set(re.findall(r"[a-z0-9]+", right.lower()))
    return len(left_words & right_words) / max(len(left_words | right_words), 1)


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
    evidence_health: dict | None = None,
) -> str:
    """The bounded text the model reads back: SQL, rows, and honesty markers."""
    from app.modules.assistant.tools.redaction import redact_rows

    lines = [f"question: {question}", f"sql:\n{sql}"]
    if explanation:
        lines.append(f"explanation: {explanation}")
    if evidence_health:
        lines.append(f"evidence: {evidence_health['label']}")
        lines.append(f"freshness: {evidence_health['data_freshness']['status']}")
        lines.append(f"causal status: {evidence_health['facts']['causal_strength']}")
    for result in results:
        columns = list(getattr(result, "columns", []) or [])
        rows = list(getattr(result, "rows", []) or [])
        lines.append(f"columns: {', '.join(columns)}")
        lines.append(f"row_count: {getattr(result, 'row_count', len(rows))}")
        preview = _preview_rows(columns, redact_rows(columns, rows))
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
            "truncated": bool(getattr(result, "truncated", False)) or len(safe_rows) > 200,
        }
    return None


def _execution_facts(
    model: dict[str, Any], results: list[Any], plan_source: str, verified_hit: VerifiedQuery | None,
    plan: SemanticPlan, *, preview_truncated: bool = False,
) -> EvidenceFacts:
    truncated = preview_truncated or any(
        getattr(result, "truncated", None) is True or len(result.rows) > 50
        or (plan.limit is not None and bool(
            plan.dimensions or (plan.time and (plan.time.grain or plan.time.compare))
        )
            and len(result.rows) >= plan.limit)
        for result in results
    )
    coverage = (
        "truncated" if truncated else "complete"
        if results and all(getattr(result, "truncated", None) is False for result in results)
        else "unknown"
    )
    # Query execution has no source watermark contract yet. Unknown is retained
    # until the data adapter supplies a validated watermark and freshness bound.
    return EvidenceFacts(
        semantic_grounding=(
            "published" if model.get("status") in {"ACTIVE", "DEPRECATED"} else "unknown"
        ),
        semantic_view_id=str(model.get("id") or model.get("semantic_model_id") or "") or None,
        semantic_version=model.get("version"),
        semantic_fingerprint=model.get("fingerprint") or None,
        plan_source=plan_source,
        verified_query_hit=verified_hit is not None,
        verified_query_id=verified_hit.verified_query_id if verified_hit else None,
        execution_status="success" if results else "unknown",
        coverage=coverage,
        semantic_ambiguity="unresolved" if plan.unresolved_concepts else "none",
        unsupported_numeric_claims=False,
        causal_strength="unknown",
    )


async def _record_usage(
    security: SecurityContext, model: dict[str, Any], semantic_ir: SemanticModelIR,
    plan: SemanticPlan, duration_ms: float, *, succeeded: bool, context: Any = None,
) -> None:
    if _context_value(context, "learning_enabled") is False:
        return
    try:
        thread_id = _context_value(context, "thread_id")
        if thread_id:
            from app.modules.assistant.repository import assistant_repository

            if not await assistant_repository.learning_enabled(
                thread_id, user_name=security.principal,
            ):
                return
        await agent_repository.record_semantic_usage(
            owner_name=security.principal, active_role=security.active_role,
            security_context_version=security.security_context_version,
            semantic_version=model.get("version"),
            semantic_model_id=str(model.get("semantic_model_id") or ""),
            model_fingerprint=semantic_ir.fingerprint, metrics=list(plan.metrics),
            dimensions=list(plan.dimensions),
            filter_shape=[
                {"field": item.field, "operator": item.operator} for item in plan.filters
            ],
            time_grain=plan.time.grain if plan.time else None,
            execution_latency_ms=round(duration_ms), succeeded=succeeded, verified_query_id=None,
        )
    except Exception as exc:  # noqa: BLE001 - telemetry must not fail a query
        logger.warning("semantic usage telemetry failed: %s", type(exc).__name__)


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
        "semantic_view": {
            "id": str(model.get("id") or model.get("semantic_model_id") or ""),
            "version": model.get("version"),
            "fingerprint": str(model.get("fingerprint") or ""),
        },
        "question": question[:2000],
        "datasets": datasets,
        "metrics": metrics,
        "generated_sql": generated_sql,
        "confidence": confidence if isinstance(confidence, int | float) else None,
        "semantic_grounding": (
            "published" if model.get("status") in {"ACTIVE", "DEPRECATED"} else "unknown"
        ),
        "validation_warnings": [],
        "generation_duration_ms": round(generation_duration_ms, 3),
        "execution_duration_ms": (
            round(execution_duration_ms, 3) if execution_duration_ms is not None else None
        ),
    }
    return payload
