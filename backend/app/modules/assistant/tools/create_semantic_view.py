"""Create, validate, and publish a Semantic View from authorized table metadata."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import HTTPException

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.runtime import validate_semantic_model_ir
from app.modules.agents.semantic.serialize import build_ossie_yaml
from app.modules.agents.tools.create_semantic_model import CreateSemanticModelTool
from app.modules.assistant.provider import AssistantProviderError
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.intelligence.semantic_views import SemanticViewCreate, semantic_view_service
from app.modules.query.service import query_service

logger = logging.getLogger(__name__)


class CreateSemanticViewTool:
    name = "create_semantic_view"
    description = (
        "Create a real Nova Semantic View from named tables: inspect authorized "
        "columns, generate an Ossie definition, validate it, and publish it when "
        "valid. This is an actual write, not a draft answer. Ask which tables to "
        "use if the user has not identified them. Requires explicit approval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "New Semantic View name."},
            "database": {"type": "string", "description": "Database that owns the view."},
            "schema_name": {"type": "string"},
            "description": {"type": "string"},
            "request": {"type": "string", "description": "Metrics and dimensions the user wants."},
            "tables": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 8,
                "description": "Exact database.table names verified under the user's role.",
            },
            "publish": {
                "type": "boolean",
                "description": "Publish after validation; defaults to true.",
            },
        },
        "required": ["name", "tables"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "destructive"
    requires_consent = True

    def __init__(self, *, designer: CreateSemanticModelTool | None = None) -> None:
        self._designer = designer or CreateSemanticModelTool()

    def preview(self, invocation: ToolInvocation) -> str:
        args = invocation.arguments
        tables = args.get("tables") if isinstance(args.get("tables"), list) else []
        action = (
            "create, validate, and publish" if args.get("publish", True) else "create and validate"
        )
        return (
            f"{action} Semantic View {str(args.get('name') or '')[:128]} "
            f"from {', '.join(str(table) for table in tables[:8])}. "
            f"Goal: {str(args.get('request') or '')[:300]}"
        )

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        args = invocation.arguments
        name = args.get("name")
        tables = args.get("tables")
        publish = args.get("publish", True)
        user = getattr(context, "user", None)
        if (
            not isinstance(user, dict)
            or not user.get("username")
            or not user.get("encrypted_password")
            or user.get("username") != getattr(context, "user_name", None)
        ):
            return ToolOutcome(
                ok=False,
                summary="",
                error="An authenticated user connection is required.",
                error_class="AUTHORIZATION_FAILURE",
            )
        if (
            not isinstance(name, str)
            or not isinstance(tables, list)
            or not 1 <= len(tables) <= 8
            or any(not isinstance(table, str) for table in tables)
            or len({table.casefold() for table in tables}) != len(tables)
            or not isinstance(publish, bool)
        ):
            return ToolOutcome(ok=False, summary="", error="A name and 1–8 tables are required.")
        database = args.get("database") or tables[0].split(".")[0]
        schema_name = args.get("schema_name", getattr(context, "schema_name", None) or "")
        description = str(args.get("description") or "")
        goal = str(args.get("request") or "")
        if len(description) > 1_000 or len(goal) > 4_000:
            return ToolOutcome(ok=False, summary="", error="The Semantic View request is too long.")
        try:
            request = SemanticViewCreate(
                name=name,
                database=database,
                schema_name=schema_name,
                definition="pending",
            )
        except Exception:
            return ToolOutcome(ok=False, summary="", error="Invalid Semantic View scope.")
        metadata = await self._designer._fetch_metadata(tables, context)
        if len(metadata) != len(tables):
            return ToolOutcome(
                ok=False,
                summary="",
                error="A named table is unavailable under your active role.",
                error_class="AUTHORIZATION_FAILURE",
            )
        if sum(len(item["columns"]) for item in metadata) > 250:
            return ToolOutcome(
                ok=False, summary="", error="Select fewer tables or columns for this view."
            )
        try:
            spec = await self._designer._generate(
                name,
                description,
                goal,
                metadata,
                context,
            )
        except AssistantProviderError:
            return ToolOutcome(ok=False, summary="", error="Could not generate the definition.")
        except Exception as exc:
            logger.warning("Semantic View generation failed: %s", type(exc).__name__)
            return ToolOutcome(ok=False, summary="", error="Could not generate the definition.")
        if not isinstance(spec, dict) or not spec.get("datasets"):
            return ToolOutcome(
                ok=False,
                summary="",
                error="The model returned no usable Semantic View definition.",
                recoverable=True,
            )
        spec["name"] = name
        definition = build_ossie_yaml(spec)
        try:
            document, ir = semantic_view_service._parse(definition, name)
            validation = validate_semantic_model_ir(ir)
        except (HTTPException, ValueError):
            return ToolOutcome(
                ok=False,
                summary="",
                error="The generated definition is invalid.",
                recoverable=True,
            )
        if validation.errors:
            return ToolOutcome(
                ok=False,
                summary="",
                error="The generated definition failed semantic validation.",
                recoverable=True,
                repair_context={"errors": list(validation.errors)[:8]},
            )
        if len(ir.metrics) > 12:
            return ToolOutcome(
                ok=False, summary="", error="Use at most twelve metrics per generated view."
            )
        approved_sources = {item["table"].casefold() for item in metadata}
        datasets = document.get("datasets") or []
        if {
            str(dataset.get("source") or "").casefold() for dataset in datasets
        } != approved_sources:
            return ToolOutcome(
                ok=False,
                summary="",
                error="The definition changed the approved table set.",
                error_class="POLICY_VIOLATION",
            )
        columns = {
            item["table"].casefold(): {column["name"].casefold() for column in item["columns"]}
            for item in metadata
        }
        for dataset in datasets:
            allowed = columns[str(dataset["source"]).casefold()]
            if any(
                str(field.get("name") or "").casefold() not in allowed
                for field in dataset.get("fields") or []
            ):
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error="The definition invented a table column.",
                    error_class="POLICY_VIOLATION",
                )
        dataset_columns = {
            str(dataset["name"]): columns[str(dataset["source"]).casefold()]
            for dataset in datasets
        }
        for relationship in document.get("relationships") or []:
            left = dataset_columns.get(str(relationship.get("from") or ""))
            right = dataset_columns.get(str(relationship.get("to") or ""))
            if left is None or right is None or any(
                str(column).casefold() not in allowed
                for allowed, names in (
                    (left, relationship.get("from_columns") or []),
                    (right, relationship.get("to_columns") or []),
                )
                for column in names
            ):
                return ToolOutcome(
                    ok=False, summary="", error="A generated relationship uses an unknown column.",
                    error_class="POLICY_VIOLATION",
                )
        request.definition = definition
        scoped_user = {
            **user,
            "active_role": getattr(context, "active_role", None) or user.get("active_role"),
            "session_id": getattr(context, "audit_session_id", None) or user.get("session_id"),
        }
        for metric in ir.metrics:
            try:
                sql = SemanticCompiler().compile(
                    ir, SemanticPlan(metrics=(metric.name,))
                ).sql
                check = await asyncio.wait_for(
                    query_service.execute(
                        sql=f"EXPLAIN {sql}",
                        username=scoped_user["username"],
                        encrypted_password=scoped_user["encrypted_password"],
                        database=request.database,
                        role=scoped_user.get("active_role"),
                        session_id=scoped_user.get("session_id"),
                        max_rows=0,
                    ),
                    timeout=5,
                )
                if check.error:
                    raise ValueError("Metric SQL was rejected")
            except Exception as exc:
                logger.warning("Semantic View metric check failed: %s", type(exc).__name__)
                return ToolOutcome(
                    ok=False, summary="", error="A generated metric could not be verified.",
                    recoverable=True,
                )
        try:
            created = await semantic_view_service.create(request, scoped_user)
            view_id = created["id"]
            report = await semantic_view_service.validate(view_id, 1, scoped_user)
            published = bool(report.get("valid") and publish)
            if published:
                await semantic_view_service.publish(view_id, 1, scoped_user)
        except HTTPException as exc:
            logger.warning("Semantic View creation rejected: %s", exc.status_code)
            return ToolOutcome(
                ok=False,
                summary="",
                error="Semantic View creation was rejected by Nova.",
                error_class="AUTHORIZATION_FAILURE"
                if exc.status_code in {401, 403, 404}
                else "TOOL_ERROR",
            )
        except Exception as exc:
            logger.warning("Semantic View creation failed: %s", type(exc).__name__)
            return ToolOutcome(
                ok=False,
                summary="",
                error="Semantic View operation failed; verify its state before retrying.",
            )
        status = "PUBLISHED" if published else "DRAFT"
        warnings = [str(error) for error in report.get("errors") or []][:8]
        return ToolOutcome(
            ok=True,
            summary=f"Semantic View {name} {status.lower()} (version 1).",
            data={
                "view_id": view_id,
                "name": name,
                "status": status,
                "version": 1,
                "validation": {"valid": bool(report.get("valid")), "errors": warnings},
            },
            evidence={"source": "nova_semantic_view", "view_id": view_id, "version": 1},
            warnings=warnings,
        )


create_semantic_view_tool = CreateSemanticViewTool()
