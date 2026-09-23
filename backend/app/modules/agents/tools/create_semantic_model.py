"""``create_semantic_model`` — let Nove build an Ossie model from a description.

This is a **write** tool: it creates a persistent ``CONFIG_SEMANTIC_MODELS`` row.
It is classified ``destructive`` so it can never be auto-approved by the
read-only conversation grant; the user must approve it explicitly, and the tool
card shows exactly what will be created.

The tool asks the LLM to produce an Ossie document from the user's plain-language
request plus the *real* column metadata for the tables named. Grounding on live
schema is what keeps the generated model valid: the model does not have to invent
column names or types, so the output usually passes the same validator the
builder UI uses.

Credential rule: the definition is metadata only. The parser screens it, so a
credential-shaped value fails closed exactly as on the create endpoint.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.common.audit import write_audit_log
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.ossie import OssieParseError, parse_ossie
from app.modules.agents.semantic.serialize import build_ossie_yaml
from app.modules.assistant.provider import (
    AssistantProviderClient,
    AssistantProviderError,
)
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import (
    ToolInvocation,
    ToolOutcome,
    record_provider_usage,
)

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Model name, lower_snake_case, e.g. sales_analytics.",
        },
        "description": {
            "type": "string",
            "description": "One line describing what the model is for.",
        },
        "request": {
            "type": "string",
            "description": (
                "What the model should cover, in the user's words, e.g. "
                "'revenue by product category and top customers'."
            ),
        },
        "tables": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Fully-qualified tables to include, e.g. ['NOVA_DEMO.orders', "
                "'NOVA_CATALOG.products']. Column metadata for these is fetched "
                "and used to ground the model."
            ),
        },
    },
    "required": ["name", "tables"],
}

_SYSTEM = """You design a semantic model from a business request and real table
metadata. Return ONLY a JSON object (no prose, no code fence) with this shape:

{
  "name": "<model name, lower_snake_case>",
  "description": "<one line>",
  "datasets": [
    {
      "name": "<dataset name, usually the table name>",
      "source": "<the exact fully-qualified table name given>",
      "primary_key": "<primary key column, or empty>",
      "description": "<one line>",
      "fields": [
        {"name": "<column>", "datatype": "<Integer|Decimal|Float|String|Date|DateTime|Time|Boolean>", "is_time": false, "description": "<short>"}
      ]
    }
  ],
  "metrics": [
    {"name": "<metric name>", "expression": "<SQL aggregate, e.g. SUM(orders.total_amount)>", "datatype": "Decimal", "description": "<short>"}
  ],
  "relationships": [
    {"name": "<from>_to_<to>", "from": "<dataset>", "to": "<dataset>", "from_columns": ["<col>"], "to_columns": ["<col>"]}
  ]
}

Rules:
- Use ONLY the columns provided in the metadata. Never invent a column.
- Include the fields that matter for analysis; each field's `name` must be an
  exact column name.
- `is_time` is true only for date/datetime columns.
- A metric expression references the dataset name and a column, e.g.
  SUM(orders.total_amount) or COUNT(DISTINCT orders.order_id).
- A relationship joins two datasets on columns they actually share.
- `from_columns` and `to_columns` must be arrays of the same length.
"""  # noqa: E501 - the JSON example is clearer unbroken


class CreateSemanticModelTool:
    """Generates and stores an Ossie model, grounded on live schema."""

    name = "create_semantic_model"
    description = (
        "Create a semantic model from a plain-language request and real tables. "
        "It reads the table columns, generates an Ossie model, and saves it. "
        "Requires your approval before it writes."
    )
    parameters = _PARAMETERS
    #: A write to persistent state: never auto-approved, always per-call consent.
    classification: ToolClassification = "destructive"
    requires_consent = True

    def __init__(self, *, provider: AssistantProviderClient | None = None) -> None:
        self._provider = provider or AssistantProviderClient()

    def preview(self, invocation: ToolInvocation) -> str:
        name = str(invocation.arguments.get("name") or "").strip()
        tables = invocation.arguments.get("tables") or []
        table_list = ", ".join(str(t) for t in tables) if isinstance(tables, list) else ""
        return f"create semantic model `{name}` from: {table_list}"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        name = str(invocation.arguments.get("name") or "").strip()
        description = str(invocation.arguments.get("description") or "").strip()
        request = str(invocation.arguments.get("request") or "").strip()
        tables = invocation.arguments.get("tables") or []
        if not name:
            return ToolOutcome(ok=False, summary="", error="A model name is required.")
        if not isinstance(tables, list) or not tables:
            return ToolOutcome(ok=False, summary="", error="At least one table is required.")

        owner = getattr(context, "user_name", None)
        user = getattr(context, "user", None) or {}
        if not owner or user.get("username") != owner or not user.get("encrypted_password"):
            return ToolOutcome(ok=False, summary="", error="No user connection is available.")
        if len(tables) > 8:
            return ToolOutcome(ok=False, summary="", error="Use at most eight tables per model.")
        existing = next(
            (
                model
                for model in await agent_repository.list_semantic_models(owner_name=owner)
                if model["name"] == name
            ),
            None,
        )
        if existing is not None:
            return ToolOutcome(
                ok=False,
                summary="",
                error=f"Semantic model {name!r} already exists. Review it before an update.",
            )

        metadata = await self._fetch_metadata(tables, context)
        if len(metadata) != len(tables):
            return ToolOutcome(
                ok=False,
                summary="",
                error="One or more named tables could not be read under your active role.",
            )

        try:
            spec = await self._generate(name, description, request, metadata, context)
        except AssistantProviderError as exc:
            return ToolOutcome(ok=False, summary="", error=str(exc))

        if not isinstance(spec, dict) or not spec.get("datasets"):
            return ToolOutcome(
                ok=False,
                summary="",
                error="The model could not design a semantic model from those tables.",
            )

        # Nova owns the serialization: the model returned a flat spec, and this
        # turns it into a valid Ossie document. The parser is still the gate.
        spec["name"] = name
        if not spec.get("description"):
            spec["description"] = description
        yaml_text = build_ossie_yaml(spec)

        try:
            parsed = parse_ossie(yaml_text)
        except OssieParseError as exc:
            return ToolOutcome(
                ok=False,
                summary="",
                error=f"The generated model is not valid: {exc}",
            )

        allowed_fields = {
            item["table"].casefold(): {column["name"].casefold() for column in item["columns"]}
            for item in metadata
        }
        datasets = parsed.model.get("datasets") or []
        sources = {str(dataset.get("source") or "").casefold() for dataset in datasets}
        if sources != set(allowed_fields):
            return ToolOutcome(
                ok=False, summary="", error="The generated model changed the approved table set."
            )
        for dataset in datasets:
            columns = allowed_fields[str(dataset["source"]).casefold()]
            if any(
                str(field.get("name") or "").casefold() not in columns
                for field in dataset.get("fields") or []
            ):
                return ToolOutcome(
                    ok=False, summary="", error="The generated model invented a table column."
                )

        first_source = (parsed.model.get("datasets") or [{}])[0].get("source") or ""
        database = first_source.split(".")[0] if first_source else None
        model_name = parsed.model.get("name") or name

        fields = {
            "name": model_name,
            "description": parsed.model.get("description") or description,
            "database_name": database,
            "schema_name": None,
            "ossie_version": parsed.version,
            "definition": parsed.as_dict(),
            "source_file_id": None,
        }
        audit_fields = {
            "event_type": "assistant_semantic_model_create",
            "user_name": owner,
            "action": "CREATE",
            "object_type": "SEMANTIC_MODEL",
            "object_name": model_name,
            "session_id": getattr(context, "audit_session_id", None),
        }
        await write_audit_log(**audit_fields, status="PENDING")
        try:
            created = await agent_repository.create_semantic_model(
                owner_name=owner, fields=fields
            )
        except Exception:
            await write_audit_log(**audit_fields, status="FAILED")
            return ToolOutcome(
                ok=False, summary="", error="The semantic model could not be created."
            )
        await write_audit_log(**audit_fields, status="SUCCESS")
        assert created is not None
        return ToolOutcome(
            ok=True,
            summary=(
                f"Created semantic model `{created['name']}` "
                f"({parsed.dataset_count} datasets, {parsed.metric_count} metrics, "
                f"{parsed.relationship_count} relationships). "
                "Open AI & ML > Semantic to review it. Do not create it again."
            ),
        )

    async def _fetch_metadata(self, tables: list, context: Any) -> list[dict[str, Any]]:
        """Read columns for each table on the user's connection."""
        from app.modules.query.service import query_service

        user = context.user
        out: list[dict[str, Any]] = []
        for raw in tables:
            full = str(raw).strip()
            parts = full.split(".")
            if len(parts) != 2 or any(_IDENTIFIER.fullmatch(part) is None for part in parts):
                return []
            database, table = parts[0], parts[1]
            query_context = {
                "username": user["username"],
                "encrypted_password": user["encrypted_password"],
                "database": database,
                "role": user.get("active_role"),
                "session_id": getattr(context, "audit_session_id", None),
                "tenant": user.get("tenant", "default"),
                "security_context_version": user.get("security_context_version", 1),
            }
            try:
                allowed = await query_service.execute_statements(
                    sql=f"SELECT * FROM `{database}`.`{table}` LIMIT 0",
                    max_rows=1,
                    **query_context,
                )
                if not allowed or allowed[0].error:
                    return []
                results = await query_service.execute_statements(
                    sql=f"DESCRIBE `{database}`.`{table}`",
                    max_rows=201,
                    **query_context,
                )
            except Exception:
                return []
            if not results or results[0].error or not results[0].rows:
                return []
            rows = results[0].rows
            if len(rows) > 200:
                return []
            out.append(
                {
                    "table": full,
                    "primary_key": next(
                        (str(row[0]) for row in rows if len(row) > 3 and row[3] == "PRI"),
                        None,
                    ),
                    "columns": [
                        {"name": str(row[0]), "type": str(row[1])}
                        for row in rows if len(row) > 1
                    ],
                }
            )
        return out

    async def _generate(
        self,
        name: str,
        description: str,
        request: str,
        metadata: list[dict[str, Any]],
        context: Any,
    ) -> dict[str, Any]:
        user = (
            f"name: {name}\n"
            f"description: {description or '(none)'}\n"
            f"request: {request or 'model the given tables'}\n\n"
            "Tables and columns:\n" + json.dumps(metadata, indent=2)
        )
        provider_id = getattr(context, "model_provider_id", None)
        model = getattr(context, "model_name", None)
        config = await self._provider.resolve(provider_id=provider_id, model=model)
        message = await self._provider.complete(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            provider=config,
        )
        record_provider_usage(context, message)
        content = (message.get("content") or "").strip()
        if content.startswith("```"):
            content = content.split("```", 2)[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


create_semantic_model_tool = CreateSemanticModelTool()
