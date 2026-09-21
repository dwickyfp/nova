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
import logging
from typing import Any

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

logger = logging.getLogger(__name__)

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
        if not owner:
            return ToolOutcome(ok=False, summary="", error="No user context is available.")

        metadata = await self._fetch_metadata(tables, context)
        if not metadata:
            return ToolOutcome(
                ok=False,
                summary="",
                error="None of the named tables could be read. Check the names.",
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
        spec.setdefault("name", name)
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

        first_source = (parsed.model.get("datasets") or [{}])[0].get("source") or ""
        database = first_source.split(".")[0] if first_source else None
        model_name = parsed.model.get("name") or name

        # Idempotent by name: a model with this name already owned by the caller is
        # updated rather than duplicated. A model that keeps its name should not
        # accumulate copies when a model calls the tool more than once, which is a
        # real behaviour when a request is split across steps.
        existing = next(
            (
                m
                for m in await agent_repository.list_semantic_models(owner_name=owner)
                if m["name"] == model_name
            ),
            None,
        )
        fields = {
            "name": model_name,
            "description": parsed.model.get("description") or description,
            "database_name": database,
            "schema_name": None,
            "ossie_version": parsed.version,
            "definition": parsed.as_dict(),
            "source_file_id": None,
        }
        if existing is not None:
            created = await agent_repository.update_semantic_model(
                existing["semantic_model_id"], owner_name=owner, fields=fields
            )
            verb = "Updated"
        else:
            created = await agent_repository.create_semantic_model(owner_name=owner, fields=fields)
            verb = "Created"
        assert created is not None
        return ToolOutcome(
            ok=True,
            summary=(
                f"{verb} semantic model `{created['name']}` "
                f"({parsed.dataset_count} datasets, {parsed.metric_count} metrics, "
                f"{parsed.relationship_count} relationships). "
                "Open AI & ML > Semantic to review it. Do not create it again."
            ),
        )

    async def _fetch_metadata(self, tables: list, context: Any) -> list[dict[str, Any]]:
        """Read columns for each table on the user's connection."""
        from app.modules.explorer.service import explorer_service

        out: list[dict[str, Any]] = []
        for raw in tables:
            full = str(raw)
            parts = full.split(".")
            if len(parts) < 2:
                continue
            database, table = parts[0], parts[1]
            try:
                detail = await explorer_service.get_table_detail(database, table)
            except Exception:  # noqa: BLE001 - a missing table is reported, not fatal
                logger.warning("create_semantic_model: cannot read %s", full)
                continue
            out.append(
                {
                    "table": full,
                    "primary_key": next(
                        (c.name for c in detail.columns if c.column_key == "PRI"), None
                    ),
                    "columns": [{"name": c.name, "type": c.data_type} for c in detail.columns],
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
