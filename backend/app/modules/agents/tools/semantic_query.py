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
from dataclasses import replace as dataclass_replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.common.sql_guard import redact_sql_credentials
from app.core.database import configured_timezone
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.coverage import (
    DATA_PERIOD,
    covered_period,
    future_period,
    looks_empty,
    period_without_data,
)
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
    PlanSource,
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


def _normalize_terms(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def matched_alias(question: str, field: dict[str, Any]) -> str | None:
    """The metric's name or synonym that the question names as whole words."""
    normalized = " " + _normalize_terms(question) + " "
    return next((alias for alias in [field.get("name"), *(field.get("synonyms") or [])]
                 if isinstance(alias, str) and _normalize_terms(alias)
                 and " " + _normalize_terms(alias) + " " in normalized), None)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": (
                "The business question to answer from the semantic model, in "
                "natural language. Example: 'revenue by region in the previous quarter'. "
                "Keep the user's limits, thresholds, and periods, and add none they did "
                "not state (no 'today' or 'this month' of your own). Ask for every metric "
                "in one question ('marketing spend and revenue by channel this month'): "
                "Nova joins metrics from different facts on their shared dimensions."
            ),
        },
        "semantic_view": {
            "type": "string",
            "description": (
                "The Semantic View to query, by its name in the agent scope or in the "
                "workspace's semantic_views_your_role_can_open. Omit it only when one view "
                "is available."
            ),
        },
    },
    "required": ["question"],
}

_SEMANTIC_QUERY_PROMPT_VERSION = "v1"


class SemanticQueryTool:
    """Compiles SQL from an active Semantic View and runs it, delegate-first."""

    #: Model calls, if any, go through the in-process ``AssistantProviderClient``,
    #: so a measured attempt counts their tokens.
    nested_requests_measured = True

    name = "semantic_query"
    #: Every query resolves the question's business terms in the ontology
    #: first (docs/arch-18-business-ontology.md); a grounded result therefore
    #: settles a separately required ``ontology_resolve``.
    may_settle = frozenset({"ontology_resolve"})
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
        if outcome.metadata.get("evidence_health"):
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

    async def primary_plan_usable(self, context: Any) -> bool:
        """Whether the turn planner's plan for the first query passes validation.

        Studio runs the first query before the model's first call only from such a
        plan; otherwise the model writes its own data question. A rejected plan
        would otherwise be planned again from the whole request (steps, maps and
        all), which the catalog cannot answer.
        """
        from app.modules.agents.semantic.model_planner import primary_plan_from

        primary = getattr(context, "primary_plan", None)
        if not isinstance(primary, dict) or not isinstance(primary.get("plan"), dict):
            return False
        # The same view the query itself would use: the planner's, by name.
        model = await self._resolve_model(context, "")
        if model is None or (
            primary.get("view") not in {None, str(model.get("semantic_model_id") or "")}
            and primary.get("view") != model.get("name")
        ):
            return False
        definition = model.get("definition") or {}
        if not isinstance(definition, dict) or not definition.get("datasets"):
            return False
        semantic_ir = model.get("_scoped_ir") or SemanticModelIR.from_ossie(definition)
        return primary_plan_from(primary["plan"], semantic_ir) is not None

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
        from app.modules.agents.semantic.access import load_authorized_models
        from app.modules.ontology.grounding import ground, missing_metrics

        grounding = await ground(question, context, lambda: load_authorized_models(context))
        if isinstance(getattr(context, "quality_facts", None), dict):
            context.quality_facts["concept_grounding"] = {
                "status": grounding.status,
                "release_id": grounding.resolution.release_id if grounding.resolution else None,
                "concepts": sorted(
                    item["qualified_name"]
                    for item in (grounding.resolution.concepts if grounding.resolution else [])
                ),
                "ambiguous_terms": sorted(
                    item.term
                    for item in (grounding.resolution.ambiguities if grounding.resolution else [])
                ),
                "metrics": sorted(grounding.required),
            }
        if grounding.blocking:
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    "The question needs clarification."
                    if grounding.error_class == "CLARIFICATION_REQUIRED"
                    else grounding.user_message
                ),
                error_class=grounding.error_class,
                recoverable=grounding.error_class == "CLARIFICATION_REQUIRED",
                safe_detail=grounding.detail,
                repair_context={"business_concepts": grounding.repair},
            )
        semantic_model = await self._resolve_model(
            context, question, grounding.view_id, guess=_is_agent_turn(context)
        )
        if semantic_model is None and not _is_agent_turn(context):
            choice = await _view_choice(context)
            if choice is not None:
                return choice
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
                ground_literals,
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
                if plan is not None and missing_metrics(grounding, plan.metrics, semantic_ir):
                    plan = None  # it missed an official metric; plan with the concepts
                plan_from_turn = plan is not None
                if plan is not None:
                    # The turn planner's filter values get the same check as a plan made
                    # here: a value the field never holds is looked up as the caller.
                    plan = await ground_literals(semantic_ir, plan, question, context)
            if plan is None:
                plan = await generate_plan(
                    self._provider,
                    semantic_ir,
                    await planning_catalog(semantic_ir, question),
                    question,
                    context,
                    prior_plan=_prior_plan(semantic_ir, context),
                    business_concepts=grounding.planner_hints(),
                )
            material = [item.text for item in plan.unresolved_concepts if item.material]
            uncovered: list[str] = []
            if material and plan.metrics and _multi_step(context):
                # A turn with steps after its query (a model, a chart, a comparison)
                # asks with all of them in it. The data part that resolved runs; the
                # rest is named, for the agent's other tools or another query.
                uncovered = material
                plan = dataclass_replace(plan, unresolved_concepts=tuple(
                    item for item in plan.unresolved_concepts if not item.material
                ))
            elif material:
                return _clarification(semantic_ir, material)
            if grounding.status == "none":
                official = await _official_view(plan, model_id, context)
                if official is not None:
                    # The planned measure is, by its exact name, an approved concept
                    # computed in another view the caller can read: the model asks
                    # there itself, so its own call names the source it will cite.
                    return _use_official_view(official, plan)
            notes: list[str] = []
            if first_query and frame is not None:
                plan, notes = reconcile_with_frame(plan, frame, semantic_ir)
            period_note = "; ".join(notes) or None
            verified_hit = _verified_match(definition, model_id, semantic_ir, plan)
            errors = [
                *missing_metrics(grounding, plan.metrics, semantic_ir),
                *validate_plan(semantic_ir, plan),
            ]
            if errors:
                raise SemanticPlanError("; ".join(errors))
            time_context = None
            if plan.time and plan.time.range:
                execution_now = getattr(context, "execution_now", None) or datetime.now(UTC)
                if callable(getattr(context, "business_clock_hook", None)):
                    execution_now = await context.business_clock_hook(invocation, context)
                execution_timezone = getattr(context, "execution_timezone", None)
                not_yet = future_period(plan, _local_day(
                    execution_now,
                    configured_timezone() if execution_timezone is None else execution_timezone,
                ))
                if not_yet:
                    return ToolOutcome(
                        ok=False,
                        summary="",
                        error="The requested period has not started yet.",
                        error_class="OUTSIDE_DATA_WINDOW",
                        # The agent answers in the user's words from the detail, as for a
                        # clarification; the detail forbids answering another period.
                        recoverable=True,
                        safe_detail=not_yet,
                        repair_context={"period": "future"},
                    )
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
        if uncovered:
            explanation += (
                " This query answers the data part only. Not covered by it: "
                + "; ".join(uncovered)[:600]
                + ". Run those steps with your other tools, or ask another query per part."
            )
        explanation += _readings_note(semantic_ir, plan)
        # Where the plan came from, not a model's opinion of itself: a verified
        # query is exact, a validated plan is the model's reading of the question.
        plan_source: PlanSource = "verified_query" if verified_hit else (
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

        last = results[-1] if results else None
        last_columns = list(getattr(last, "columns", None) or [])
        last_rows = list(getattr(last, "rows", None) or [])

        def run_window(window_plan: dict[str, Any]) -> Any:
            return self._window_rows(
                semantic_ir, window_plan, username, encrypted_password, context, security
            )

        gap = await period_without_data(plan, last_columns, last_rows, time_context, run_window)
        if gap is not None:
            await _record_usage(
                security, semantic_model, semantic_ir, plan, execution_duration_ms,
                succeeded=True, context=context,
            )
            return ToolOutcome(
                ok=False,
                summary="",
                error="The requested period is outside the data.",
                error_class="OUTSIDE_DATA_WINDOW",
                # The agent answers in the user's words from the detail, as for a
                # clarification; the detail forbids answering another period.
                recoverable=True,
                safe_detail=gap.detail(plan.metrics[0]),
                repair_context={
                    "data_window": {
                        "first": gap.window.first.isoformat(),
                        "last": gap.window.last.isoformat(),
                    },
                    "period": gap.period,
                    # The asked-for days with no data, so an answer saying "2025 has
                    # no data" is checked against them.
                    "missing": {"first": gap.start.isoformat(), "last": gap.end.isoformat()},
                },
                trace_detail=_semantic_trace(
                    semantic_model,
                    question=question,
                    generated_sql=safe_sql,
                    confidence=confidence,
                    generation_duration_ms=generation_duration_ms,
                    execution_duration_ms=execution_duration_ms,
                ),
            )

        covered: dict[str, str] | None = None
        if not looks_empty(last_columns, last_rows):
            # Only a question that asks which dates the data covers reads them.
            window = await covered_period(plan, semantic_ir, run_window)
            if window is not None:
                covered = {"first": window.first.isoformat(), "last": window.last.isoformat()}
                explanation += (
                    f" The data covers {covered['first']} to {covered['last']}; say which "
                    "period the answer covers."
                )
                plan = dataclass_replace(plan, unresolved_concepts=tuple(
                    item for item in plan.unresolved_concepts if item.type_hint != DATA_PERIOD
                ))
        if any(item.type_hint == DATA_PERIOD for item in plan.unresolved_concepts):
            explanation += " The dates the data covers could not be read for this answer."

        table = _table_payload(results, title=question)
        # The data's own range spans everything there is; a queried range only what was asked.
        period = {**covered, "scope": "data"} if covered else _queried_period(time_context)
        if table is not None:
            units = {
                name: metric.unit for name in plan.metrics
                if (metric := semantic_ir.metric(name)) is not None and metric.unit
                and name in table["columns"]
            }
            if units:
                table["units"] = units
        if table is not None and period is not None:
            # The dates the rows are about travel with them, so a year the answer
            # names from them is checked against the data, not taken on trust.
            table["period"] = period
        if table is not None:
            # Expose the fetched rows to ``data_to_chart`` in the same turn,
            # with the compiled statement the viz core needs for persistence.
            context.last_result = {
                "title": question,
                "columns": table["columns"],
                "rows": table["rows"],
                "sql": safe_sql,
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
        # The view that answered, named in the result: the model may have asked
        # for another one, and the answer names its source from what it reads.
        source = str(semantic_model.get("name") or semantic_ir.name)
        summary = _render(
            question=question, source=source, sql=safe_sql, explanation=explanation,
            confidence=confidence, results=results,
        )
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
                source=source,
                sql=safe_sql,
                explanation=explanation,
                confidence=confidence,
                results=results,
                evidence_health=health.model_dump(mode="json"),
            ),
            table=table,
            data={
                "semantic_plan": plan.as_dict(),
                "sql": safe_sql,
                "row_count": row_count,
                **(
                    {"business_concepts": grounding.planner_hints()}
                    if grounding.status == "grounded" else {}
                ),
            },
            evidence={
                "source": "nova_semantic_view",
                "business_context": grounding.provenance(),
                "view_id": model_id,
                "view_name": source,
                "version": semantic_model.get("version"),
                "semantic_model_id": semantic_model.get("semantic_model_id"),
                "semantic_model_fingerprint": semantic_ir.fingerprint,
                "metrics": list(plan.metrics),
                "dimensions": list(plan.dimensions),
            },
            metadata={
                "settles_capabilities": (
                    ["ontology_resolve"] if grounding.provenance() else []
                ),
                "relationship_path": list(compiled.relationship_path),
                "plan_source": plan_source,
                "warnings": list(compiled.warnings),
                "period_from_user": period_note,
                "verified_query_hit": verified_hit.verified_query_id if verified_hit else None,
                "complete": health.facts.coverage == "complete",
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
        trace_detail = dict(outcome.trace_detail or {})
        trace_detail["evidence_envelope"] = envelope.model_dump(mode="json")
        if time_context:
            trace_detail["execution_time_context"] = time_context.as_dict()
        outcome = replace(outcome, trace_detail=trace_detail, business_result={
            "envelope": envelope, "seed": seed, "required_inputs": requirements,
            "population_fingerprint": semantic_population_fingerprint(plan),
            "time_dimension": plan.time.dimension if plan.time else None,
        })
        return attach_evidence_health(outcome, health)

    async def _window_rows(
        self,
        semantic_ir: SemanticModelIR,
        window_plan: dict[str, Any],
        username: str,
        encrypted_password: str,
        context: Any,
        security: Any,
    ) -> tuple[list, list, str | None]:
        """Run one data-window plan as the caller, through the same compiler and session."""
        from app.modules.query.service import query_service

        try:
            sql = self._compiler.compile(semantic_ir, SemanticPlan.from_dict(window_plan)).sql
            results = await query_service.execute_statements(
                sql=sql,
                username=username,
                encrypted_password=encrypted_password,
                database=_context_value(context, "database"),
                schema=_context_value(context, "schema_name"),
                role=_active_role(context),
                security_context_version=security.security_context_version,
                max_rows=1,
                session_id=_context_value(context, "audit_session_id"),
                confirm_destructive=False,
                file_id=_context_value(context, "workspace_file_id"),
            )
        except Exception as exc:  # noqa: BLE001 - no window means no claim about one
            logger.info("Data window check skipped: %s", type(exc).__name__)
            return [], [], type(exc).__name__
        result = results[-1] if results else None
        if result is None or result.error:
            return [], [], "query error"
        return list(result.columns or []), [list(row) for row in result.rows or []], None

    async def _resolve_model(
        self,
        context: Any,
        question: str,
        grounded_view: str | None = None,
        *,
        guess: bool = True,
    ) -> dict[str, Any] | None:
        from app.modules.agents.semantic.access import load_authorized_models

        models = await load_authorized_models(context)
        if grounded_view is not None:
            # The ontology names the view that computes the official metric.
            return next(
                (model for model in models if str(model["semantic_model_id"]) == grounded_view),
                None,
            )
        if len(models) == 1:
            return models[0]
        # The view is chosen by name: the turn planner's primary view, or the one
        # the loop model names. Word overlap with the question is not a choice.
        primary = getattr(context, "primary_plan", None) or {}
        # In order: the view the loop model named (when it exists), the one view whose
        # declared metrics the question names ("planned vs actual distance"), and the
        # turn planner's view. A made-up view name falls through to the next.
        for wanted in (
            getattr(context, "requested_semantic_view", None),
            _view_naming_metric(models, question),
            primary.get("view"),
        ):
            chosen = next(
                (model for model in models
                 if wanted and wanted in {str(model["semantic_model_id"]), str(model.get("name"))}),
                None,
            )
            if chosen is not None:
                return chosen
        # A Studio agent's first view is its own business. Neva reads every view
        # the role can open, so the first one is not an answer to anything.
        return models[0] if models and guess else None


async def _official_view(plan: SemanticPlan, model_id: str, context: Any) -> str | None:
    """Another readable view that computes the plan's measure as an approved concept.

    Used when the question's words named no concept ("how many orders") but the
    planned metric's own name is one exactly (``order_count`` is the approved
    Order count). Only a whole metric name counts: ``net_revenue_target`` does
    not name Net revenue. ``None`` keeps the view the plan was made in.
    """
    from app.modules.agents.semantic.access import load_authorized_models
    from app.modules.ontology.grounding import ground
    from app.modules.ontology.resolver import normalise

    if not plan.metrics:
        return None
    names = {
        form for metric in plan.metrics
        for form in (normalise(metric), normalise(metric.replace("_", " ")))
    }
    official = await ground(
        " | ".join(plan.metrics), context, lambda: load_authorized_models(context)
    )
    if official.status != "grounded" or not official.view_id or official.view_id == model_id:
        return None
    resolution = official.resolution
    terms = [term for found in (resolution.terms.values() if resolution else []) for term in found]
    if not terms or any(normalise(term) not in names for term in terms):
        return None
    models = await load_authorized_models(context)
    return next(
        (str(model.get("name") or model["semantic_model_id"]) for model in models
         if str(model["semantic_model_id"]) == official.view_id),
        None,
    )


def _use_official_view(view: str, plan: SemanticPlan) -> ToolOutcome:
    """Send the question to the view that computes the measure's approved definition."""
    from app.modules.assistant.plain_language import source_label

    measures = ", ".join(plan.metrics)
    return ToolOutcome(
        ok=False,
        summary="",
        error="This measure has an approved definition in another Semantic View.",
        error_class="OFFICIAL_VIEW_REQUIRED",
        recoverable=True,
        safe_detail=(
            f"{measures} is the organisation's approved definition, computed in the "
            f"Semantic View {view}; the view asked for only has a measure of the same "
            f"name. Call semantic_query again with semantic_view set to {view}, and name "
            f"the source to the reader as {source_label(view)} data."
        ),
        repair_context={"semantic_view": view, "metrics": list(plan.metrics)},
    )


def _is_agent_turn(context: Any) -> bool:
    return bool(getattr(context, "agent_scope", None) or getattr(context, "agent_id", None))


async def _view_choice(context: Any) -> ToolOutcome | None:
    """Several views and none named: ask the model to name one, never guess."""
    from app.modules.agents.semantic.access import load_authorized_models

    models = await load_authorized_models(context)
    if len(models) < 2:
        return None
    return ToolOutcome(
        ok=False,
        summary="",
        error="Several Semantic Views are readable and none was named.",
        error_class="VIEW_CHOICE_REQUIRED",
        recoverable=True,
        safe_detail=(
            "Call semantic_query again with semantic_view set to the view whose "
            "metrics answer the question. If no view fits, tell the person which "
            "business areas are available and ask which one they mean."
        ),
        repair_context={"semantic_views": readable_view_summaries(models)},
    )


def readable_view_summaries(models: list[dict[str, Any]], limit: int = 16) -> list[dict[str, Any]]:
    """Name, purpose and main metrics of each readable view, for choosing one."""
    from app.modules.agents.tools.describe_agent import semantic_catalog

    try:
        catalog = semantic_catalog(models)
    except (KeyError, AttributeError):
        return []
    return [{
        "name": view["name"],
        "about": view["description"][:240],
        "metrics": [metric["name"] for metric in view["metrics"][:10]],
        "dimensions": [field["name"] for field in view["dimensions"][:8]],
    } for view in catalog[:limit]]


def _view_naming_metric(models: list[dict[str, Any]], question: str) -> str | None:
    """The one view whose declared metric names or synonyms the question uses."""
    hits = []
    for model in models:
        definition = model.get("definition")
        if isinstance(definition, str):
            try:
                definition = json.loads(definition)
            except ValueError:
                continue
        metrics = (definition or {}).get("metrics") or []
        if any(isinstance(metric, dict) and matched_alias(question, metric) for metric in metrics):
            hits.append(str(model["semantic_model_id"]))
    return hits[0] if len(hits) == 1 else None


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


#: Tools that work on a query's result: a turn that selected one has steps after it.
_RESULT_STEPS = frozenset({"ml_execute", "data_to_chart", "compute_metrics", "diagnose_change"})


def _multi_step(context: Any) -> bool:
    """Whether this turn does more than one query: a model, chart or comparison after it."""
    route = getattr(context, "route", None) or {}
    return bool(
        route.get("intent") in {"machine_learning", "compound_analytics"}
        or route.get("ml_task") or route.get("needs_ml") or route.get("needs_chart")
        or _RESULT_STEPS & set(getattr(context, "selected_tools", None) or ())
    )


def _queried_period(time_context: Any) -> dict[str, str] | None:
    """The days a dated query asked for, and its comparison period, as ISO dates."""
    if time_context is None:
        return None

    def days(window: Any) -> tuple[str, str]:
        # Windows are half-open; the last day is the one before the end.
        last = window.end - timedelta(microseconds=1)
        return window.start.date().isoformat(), last.date().isoformat()

    period = dict(zip(("first", "last"), days(time_context.current), strict=True))
    if getattr(time_context, "baseline", None) is not None:
        period.update(zip(
            ("compared_first", "compared_last"), days(time_context.baseline), strict=True,
        ))
    return period


def _readings_note(model: SemanticModelIR, plan: SemanticPlan) -> str:
    """Tell the answer which reading of a measure word it used, and what else fits.

    The person gets the usual reading answered at once, sees which one it was,
    and can ask for another; a wrong guess is never presented as the only one.
    """
    notes: list[str] = []
    for reading in plan.readings:
        others = [name for name in reading.alternatives
                  if name != reading.metric and model.metric(name) is not None]
        note = f"'{reading.text}' was read as {_metric_phrase(model, reading.metric)}"
        if others:
            note += "; also available: " + ", ".join(
                _metric_phrase(model, name) for name in others[:4]
            )
        notes.append(note)
    unshown = [
        item.text for item in plan.unresolved_concepts
        if not item.material and item.type_hint != DATA_PERIOD
    ]
    text = ""
    if notes:
        text += (
            " " + ". ".join(notes) + ". Say in one short sentence, in the user's words, "
            "which measure the answer uses and offer the others."
        )
    if unshown:
        text += " Not shown by this query: " + "; ".join(unshown)[:400] + "."
    return text


def _metric_phrase(model: SemanticModelIR, name: str) -> str:
    """A metric named with its description, so the answer can say it in plain words."""
    metric = model.metric(name)
    description = " ".join(((metric.description if metric else "") or "").split())[:120]
    return f"{name} ({description})" if description else name


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
    source: str | None = None,
) -> str:
    """The bounded text the model reads back: source, SQL, rows, honesty markers."""
    from app.modules.assistant.tools.redaction import redact_rows

    lines = [f"question: {question}"]
    if source:
        from app.modules.assistant.plain_language import source_label

        lines.append(
            f"source: {source} answered. Name it to the reader as {source_label(source)} "
            "data, in their language, never by this name or as a Semantic View."
        )
    lines.append(f"sql:\n{sql}")
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


def _local_day(now: datetime, timezone: str) -> date | None:
    """Today on the business calendar; an invalid zone is rejected by the time resolver."""
    try:
        return now.astimezone(ZoneInfo(timezone)).date()
    except (KeyError, ValueError):
        return None


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
    model: dict[str, Any],
    results: list[Any],
    plan_source: PlanSource,
    verified_hit: VerifiedQuery | None,
    plan: SemanticPlan,
    *,
    preview_truncated: bool = False,
) -> EvidenceFacts:
    truncated = preview_truncated or any(
        getattr(result, "truncated", None) is True or len(result.rows) > 50
        or (plan.limit is not None and bool(
            plan.dimensions or (plan.time and (plan.time.grain or plan.time.compare))
        )
            and len(result.rows) >= plan.limit)
        for result in results
    )
    coverage: Literal["complete", "truncated", "partial", "unknown"] = (
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
