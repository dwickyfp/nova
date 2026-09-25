"""Legacy tool alias and shared schema-grounded Semantic View designer."""

from __future__ import annotations

import json
import re
from typing import Any

from app.modules.assistant.provider import AssistantProviderClient
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
    """Provides schema-grounded design for canonical Semantic View creation."""

    name = "create_semantic_model"
    description = (
        "Legacy alias for creating a published Semantic View from authorized tables. "
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
        return f"create Semantic View `{name}` from: {table_list}"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.assistant.tools.create_semantic_view import CreateSemanticViewTool

        return await CreateSemanticViewTool(designer=self).run(invocation, context)

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
