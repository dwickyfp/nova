"""``ml_execute``: deterministic, user-scoped on-the-fly ML for Nova agents."""

from __future__ import annotations

from typing import Any

from app.core.security import decrypt_password
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome, report_tool_progress
from app.modules.ml_engine.service import ml_engine_service
from app.modules.ml_engine.spec import MLExecutionSpec, MLMode, MLSecurityContext, MLTask
from app.modules.query.sql_pipeline import redact_for_output


class MLExecuteTool:
    name = "ml_execute"
    description = (
        "Run Nova's bounded ML runtime for forecast, classification, regression, "
        "anomaly detection, or clustering over caller-authorized SQL features. "
        "Do not approximate these tasks with prose or arbitrary SQL. Use persist=false "
        "for on-the-fly analysis."
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
                ],
            },
            "input_sql": {"type": "string"},
            "feature_columns": {"type": "array", "items": {"type": "string"}},
            "target": {"type": "string"},
            "timestamp": {"type": "string"},
            "series": {"type": "string"},
            "row_identifier": {"type": "string"},
            "horizon": {"type": "integer", "minimum": 1},
            "frequency": {"type": "string"},
            "mode": {"type": "string", "enum": ["interactive", "balanced", "best"]},
            "persist": {"type": "boolean", "default": False},
            "model_name": {"type": "string"},
            "parameters": {"type": "object"},
        },
        "required": ["task", "input_sql"],
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
        report_tool_progress(context, stage="ml_extracting", text="Reading ML features")
        try:
            result = await ml_engine_service.execute(
                MLExecutionSpec(
                    task=MLTask(str(arguments.get("task"))),
                    input_sql=str(arguments.get("input_sql") or ""),
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
                    parameters=dict(arguments.get("parameters") or {}),
                )
            )
        except Exception as exc:
            return ToolOutcome(
                ok=False,
                summary="",
                error=(f"ML execution failed: {type(exc).__name__}: {redact_for_output(str(exc))}"),
                error_class="ML_EXECUTION_ERROR",
                recoverable=isinstance(exc, (TypeError, ValueError)),
                safe_detail="Check the task-specific ML parameters and feature schema.",
                repair_context={
                    "task": arguments.get("task"),
                    "required": self.parameters.get("required", []),
                },
            )
        report_tool_progress(context, stage="ml_completed", text="ML execution completed")
        table = None
        if result.results:
            columns = list(result.results[0])
            table = {
                "title": f"{result.task} results",
                "columns": columns,
                "rows": [[row.get(column) for column in columns] for row in result.results[:1000]],
            }
        return ToolOutcome(
            ok=True,
            summary=(
                f"{result.task} completed with {result.selected_algorithm}; "
                f"{result.training_rows} rows processed (run {result.run_id})"
            ),
            table=table,
            data={
                "run_id": result.run_id,
                "task": result.task,
                "algorithm": result.selected_algorithm,
                "training_rows": result.training_rows,
                "results": result.results[:1000],
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
