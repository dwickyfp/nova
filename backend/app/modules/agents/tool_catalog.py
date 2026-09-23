"""The builtin tool catalog.

Nova's own tools (the ones the loop can call in-process) are the rows the Tools
Registry shows under ``source = "builtin"``. They are defined here rather than
discovered: they are code, not a remote catalog.

A builtin row records the tool's name, a description for the registry UI, and
the JSON Schema the model is given. It deliberately does **not** include the
callable: the registry is a catalog, and the actual tool objects live in the
assistant/agents registries. Keeping the two apart means the Tools page can list
everything the platform offers without importing every tool module.
"""

from __future__ import annotations

from typing import Any

#: name -> (description, input_schema)
BUILTIN_TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    "search_knowledge": (
        "Search packaged Nova product guidance and playbooks with source revisions; "
        "documentation does not establish runtime database facts.",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "query_execute": (
        "Run one read-only StarRocks SELECT/SHOW/DESCRIBE/EXPLAIN on the user's "
        "connection for explicit SQL, schema inspection, or data outside a semantic "
        "model. Do not use it for a governed business metric that semantic_query defines. "
        "Returns columns, redacted rows, row count, and execution metadata.",
        {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single read-only SQL statement."}
            },
            "required": ["sql"],
        },
    ),
    "semantic_query": (
        "Answer a governed business-metric question. Nova selects a semantic model, "
        "validates a SemanticPlan, resolves joins and grain, compiles StarRocks SQL, "
        "and returns verified rows. Do not use for explicit SQL or schema inspection.",
        {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The business question in natural language.",
                }
            },
            "required": ["question"],
        },
    ),
    "semantic_search": (
        "Resolve entity literals or discover semantic metadata through indexed text. "
        "Use it for names such as products or customers, not for aggregating metrics. "
        "Returns matching canonical values and citations; results are not metric totals.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords to search for."},
                "column": {"type": "string", "description": "The indexed text column."},
                "mode": {"type": "string", "enum": ["any", "all"]},
            },
            "required": ["query", "column"],
        },
    ),
    "data_to_chart": (
        "Build a sanitized Vega-Lite chart from the latest verified table. Use only when "
        "the user asks for a chart or a trend/comparison materially benefits from one. "
        "Do not call it before a data result exists.",
        {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "description": "What the chart should show."}
            },
        },
    ),
    "diagnose_change": (
        "Reconcile a two-period authorized result into arithmetic volume, unit-value, "
        "interaction, and returns contributions. Unavailable drivers stay unassigned; "
        "the tool does not prove causes.",
        {
            "type": "object",
            "properties": {
                "prior_period": {"type": "string"},
                "current_period": {"type": "string"},
                "revenue_column": {"type": "string"},
                "units_column": {"type": "string"},
                "returns_column": {"type": "string"},
            },
            "required": ["prior_period", "current_period", "revenue_column"],
            "additionalProperties": False,
        },
    ),
    "ml_execute": (
        "Run Nova's bounded ML runtime for forecast, classification, regression, anomaly "
        "detection, or clustering over caller-authorized SQL features. Do not approximate "
        "these tasks with prose or arbitrary SQL. Returns a verified result/artifact.",
        {
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
                "horizon": {"type": "integer", "minimum": 1},
                "mode": {"type": "string", "enum": ["interactive", "balanced", "best"]},
                "persist": {"type": "boolean"},
                "model_name": {"type": "string"},
                "parameters": {"type": "object"},
            },
            "required": ["task", "input_sql"],
        },
    ),
    "load_skill": (
        "Load the full playbook for one Nova SQL skill before answering a task it covers.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name from the catalog."}
            },
            "required": ["name"],
        },
    ),
    "create_semantic_model": (
        "Create a semantic model from real tables, grounded on their columns.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "request": {"type": "string"},
                "tables": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "tables"],
        },
    ),
    "create_agent": (
        "Create an Agent Studio agent with instructions and tools.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "instructions_response": {"type": "string"},
                "instructions_orchestration": {"type": "string"},
                "semantic_model_name": {"type": "string"},
                "tools": {"type": "array", "items": {"type": "string"}},
                "sample_questions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name"],
        },
    ),
    "find_ui_operation": (
        "Find a Nova UI API operation by task, including its exact path and input fields.",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "call_ui_operation": (
        "Call one discovered Nova UI API operation as the current user, with consent and audit.",
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string"},
                "path_params": {"type": "object", "additionalProperties": True},
                "query": {"type": "object", "additionalProperties": True},
                "body": {"type": "object", "additionalProperties": True},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    ),
}


#: Names the agent builder may bundle. Distinct from the builtin catalog: the
#: authoring tools (create_*) are Nove-only and are not offered to a user agent.
AGENT_BUNDLEABLE_TOOLS = (
    "load_skill",
    "query_execute",
    "semantic_query",
    "semantic_search",
    "data_to_chart",
    "diagnose_change",
    "ml_execute",
)


def builtin_rows() -> list[dict[str, Any]]:
    """The builtin catalog as registry rows (``source='builtin'``)."""
    return [
        {
            "name": name,
            "description": description,
            "source": "builtin",
            "input_schema": schema,
            "is_enabled": True,
        }
        for name, (description, schema) in BUILTIN_TOOLS.items()
    ]
