"""``ml_execute``: deterministic, user-scoped on-the-fly ML for Nova agents."""

from __future__ import annotations

from typing import Any

from app.core.security import decrypt_password
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome, report_tool_progress
from app.modules.assistant.tools.redaction import redact_rows
from app.modules.ml_engine.service import ml_engine_service
from app.modules.ml_engine.spec import (
    MLError,
    MLExecutionSpec,
    MLMode,
    MLSecurityContext,
    MLTask,
)
from app.modules.query.sql_pipeline import redact_for_output


class MLExecuteTool:
    #: Model calls, if any, go through the in-process ``AssistantProviderClient``,
    #: so a measured attempt counts their tokens.
    nested_requests_measured = True
    name = "ml_execute"
    description = (
        "Run Nova's bounded ML runtime for forecast, classification, regression, "
        "anomaly detection, clustering, or route_optimization over caller-authorized SQL "
        "features. route_optimization orders stops (row_identifier, latitude, longitude, "
        "one row per stop) into the shortest visiting sequence and returns one row per stop "
        "(route, stop_seq, stop, latitude, longitude, leg_km, route_km, saving_pct) for a "
        "route map; when the request asks to group stops (groups, areas, clusters, "
        "'kelompok'), pass parameters.clusters=k (about one area per 5-8 stops), which "
        "first groups the "
        "stops into k delivery areas by location (k-means), target sums each route's "
        "demand, and series keeps existing groups such as a driver. "
        "Do not approximate these tasks with prose or arbitrary SQL. Use persist=false "
        "for on-the-fly analysis. Pass evidence_id of a semantic_query result from this "
        "turn to use that governed query as the input instead of writing input_sql."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "enum": [
                    "classification",
                    "regression",
                    "forecast",
                    "anomaly_detection",
                    "clustering",
                    "route_optimization",
                ],
            },
            "input_sql": {"type": "string"},
            "evidence_id": {
                "type": "string",
                "description": "A semantic_query result of this turn whose compiled query "
                "is the input; preferred over input_sql.",
            },
            "feature_columns": {"type": "array", "items": {"type": "string"}},
            "target": {"type": "string"},
            "timestamp": {"type": "string"},
            "series": {"type": "string"},
            "row_identifier": {"type": "string"},
            "latitude": {"type": "string", "description": "route_optimization: latitude column."},
            "longitude": {
                "type": "string", "description": "route_optimization: longitude column.",
            },
            "horizon": {"type": "integer", "minimum": 1},
            "frequency": {"type": "string"},
            "mode": {"type": "string", "enum": ["interactive", "balanced", "best"]},
            "persist": {"type": "boolean", "default": False},
            "model_name": {"type": "string"},
            "parameters": {"type": "object"},
        },
        "required": ["task"],
    }
    requires_consent = True

    classification: ToolClassification = "read_only"

    def classification_for(self, invocation: ToolInvocation) -> ToolClassification:
        return "destructive" if bool(invocation.arguments.get("persist", False)) else "read_only"

    def preview(self, invocation: ToolInvocation) -> str:
        persist = bool(invocation.arguments.get("persist", False))
        task = invocation.arguments.get("task", "ml")
        mode = invocation.arguments.get("mode", "interactive")
        return f"ml_execute: {task} ({mode}, {'persistent' if persist else 'ephemeral'})"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        arguments = invocation.arguments
        user = getattr(context, "user", None) or {}
        username = user.get("username")
        encrypted_password = user.get("encrypted_password")
        if not username or not encrypted_password:
            return ToolOutcome(
                ok=False,
                summary="",
                error="No user connection is available for this ML execution.",
            )
        try:
            password = decrypt_password(encrypted_password)
        except (TypeError, ValueError, KeyError) as exc:
            return ToolOutcome(
                ok=False, summary="", error=f"User connection is unavailable: {type(exc).__name__}"
            )
        governed = getattr(context, "evidence_sql", None) or {}
        chosen = str(arguments.get("evidence_id") or "")
        input_sql = str(arguments.get("input_sql") or "")
        if chosen:
            # The compiled query of a result this turn already ran under the caller.
            input_sql = governed.get(chosen, "")
        if not input_sql.strip():
            return ToolOutcome(
                ok=False, summary="", error="No input query for this ML run.",
                error_class="INVALID_TOOL_ARGUMENTS", recoverable=True,
                safe_detail="Pass evidence_id of a semantic_query result from this turn.",
                repair_context={"evidence_ids": list(governed)[-8:]},
            )
        if chosen:
            # A governed result names its columns as the semantic plan did
            # ("sales_orders.order_date"); the model may write the bare field.
            table = (getattr(context, "evidence_tables", None) or {}).get(chosen) or {}
            resolved, unknown = _resolve_columns(arguments, list(table.get("columns") or []))
            if unknown:
                grouping = (
                    " To group stops into delivery areas, omit series and pass "
                    "parameters.clusters (for example 3); no cluster column exists before that."
                    if str(arguments.get("task")) == "route_optimization" else ""
                )
                return ToolOutcome(
                    ok=False, summary="", error="Unknown ML column(s): " + ", ".join(unknown),
                    error_class="INVALID_TOOL_ARGUMENTS", recoverable=True,
                    safe_detail=(
                        "These are not columns of the evidence result: " + ", ".join(unknown)
                        + ". Its columns are: "
                        + ", ".join(str(column) for column in (table.get("columns") or [])[:40])
                        + ". Pass column names, not argument names." + grouping
                    ),
                    repair_context={"columns": list(table.get("columns") or [])[:40]},
                )
            arguments = {**arguments, **resolved}
        report_tool_progress(context, stage="ml_extracting", text="Reading ML features")
        try:
            result = await ml_engine_service.execute(
                MLExecutionSpec(
                    task=MLTask(str(arguments.get("task"))),
                    input_sql=input_sql,
                    security=MLSecurityContext(
                        username=username,
                        password=password,
                        database=getattr(context, "database", None),
                        schema=getattr(context, "schema_name", None),
                        role=user.get("active_role"),
                        tenant=user.get("tenant", "default"),
                        security_context_version=user.get("security_context_version", 1),
                    ),
                    mode=MLMode(str(arguments.get("mode") or "interactive")),
                    persist=bool(arguments.get("persist", False)),
                    model_name=arguments.get("model_name"),
                    feature_columns=tuple(arguments.get("feature_columns") or ()),
                    target_column=arguments.get("target"),
                    timestamp_column=arguments.get("timestamp"),
                    series_column=arguments.get("series"),
                    row_identifier=arguments.get("row_identifier"),
                    horizon=arguments.get("horizon"),
                    frequency=arguments.get("frequency"),
                    parameters={
                        **dict(arguments.get("parameters") or {}),
                        **{
                            f"{name}_column": arguments[name]
                            for name in ("latitude", "longitude")
                            if arguments.get(name)
                        },
                    },
                )
            )
        except Exception as exc:
            return ToolOutcome(
                ok=False,
                summary="",
                error=(f"ML execution failed: {type(exc).__name__}: {redact_for_output(str(exc))}"),
                error_class="ML_EXECUTION_ERROR",
                # A query the engine rejected can be replaced; other runtime faults cannot.
                recoverable=isinstance(exc, (TypeError, ValueError))
                or "during extraction" in str(exc),
                safe_detail=(
                    # Nova's own ML errors say what to change; anything else stays generic.
                    redact_for_output(str(exc)) + " " if isinstance(exc, MLError) else ""
                ) + "Check the task-specific ML parameters and feature schema. If the "
                "input query failed, pass evidence_id of a semantic_query result instead.",
                repair_context={
                    "task": arguments.get("task"),
                    "evidence_ids": list(governed)[-8:],
                    "required": self.parameters.get("required", []),
                },
            )
        report_tool_progress(context, stage="ml_completed", text="ML execution completed")
        table = None
        safe_results: list[dict[str, Any]] = []
        if result.results:
            columns = list(dict.fromkeys(
                column for row in result.results[:1000] for column in row
            ))
            safe_rows = redact_rows(
                columns,
                [[row.get(column) for column in columns] for row in result.results[:1000]],
            )
            safe_results = [dict(zip(columns, row, strict=True)) for row in safe_rows]
            table = {
                "title": f"{result.task} results",
                "columns": columns,
                "rows": safe_rows,
            }
        return ToolOutcome(
            ok=True,
            summary=(
                f"{result.task} completed with {result.selected_algorithm}; "
                f"{result.training_rows} rows processed (run {result.run_id})"
                + _distance_note(result)
            ),
            table=table,
            data={
                "run_id": result.run_id,
                "task": result.task,
                "algorithm": result.selected_algorithm,
                "training_rows": result.training_rows,
                "results": safe_results,
            },
            evidence={
                "run_id": result.run_id,
                "task": result.task,
                "algorithm": result.selected_algorithm,
            },
            metadata={
                "mode": result.mode,
                "engine": result.selected_engine,
                "cache_hit": result.cache_hit,
            },
            trace_detail={
                "kind": "ml_execution",
                "run_id": result.run_id,
                "task": result.task,
                "mode": result.mode,
                "engine": result.selected_engine,
                "algorithm": result.selected_algorithm,
                "training_rows": result.training_rows,
                "cache_hit": result.cache_hit,
            },
        )


ml_execute_tool = MLExecuteTool()


def _distance_note(result: Any) -> str:
    """What a route's kilometres measure, so the answer describes them correctly."""
    metrics = getattr(result, "metrics", None) or {}
    source = metrics.get("distance")
    notes = {
        "road_km_osrm": "; distances are driving distances along roads (OSRM)",
        "great_circle_km": "; distances are straight-line (great-circle), not roads",
        "mixed": "; some routes use road distances (OSRM), others straight lines",
    }
    return notes.get(str(source), "")


_COLUMN_ARGUMENTS = ("timestamp", "target", "series", "row_identifier", "latitude", "longitude")


def _resolve_columns(arguments: dict, columns: list[str]) -> tuple[dict, list[str]]:
    """Map the model's column names to the result's, qualified or bare.

    Returns the resolved arguments and the names that match no column. Without
    known columns nothing is checked here; the engine validates as before.
    """
    if not columns:
        return {}, []

    def resolve(name: str) -> str | None:
        if name in columns:
            return name
        bare = name.rsplit(".", 1)[-1]
        matches = [column for column in columns if column.rsplit(".", 1)[-1] == bare]
        return matches[0] if len(matches) == 1 else None

    resolved: dict = {}
    unknown: list[str] = []
    for key in _COLUMN_ARGUMENTS:
        value = arguments.get(key)
        if isinstance(value, str) and value:
            match = resolve(value)
            if match is None:
                unknown.append(value)
            else:
                resolved[key] = match
    features = arguments.get("feature_columns")
    if isinstance(features, list) and features:
        mapped = []
        for value in features:
            match = resolve(str(value))
            if match is None:
                unknown.append(str(value))
            else:
                mapped.append(match)
        resolved["feature_columns"] = mapped
    return resolved, unknown
