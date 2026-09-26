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

from app.modules.agents.collaboration_tools import COLLABORATION_TOOLS
from app.modules.agents.tools.describe_agent import DESCRIPTION, PARAMETERS

#: name -> (description, input_schema)
BUILTIN_TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    **COLLABORATION_TOOLS,
    "describe_agent": (DESCRIPTION, PARAMETERS),
    "send_agent_message": (
        "Send a finding or question to Auto during a specialist run.",
        {
            "type": "object",
            "properties": {
                "message_type": {"type": "string", "enum": ["finding", "question"]},
                "content": {"type": "string"},
            },
            "required": ["message_type", "content"],
            "additionalProperties": False,
        },
    ),
    "request_specialist": (
        "Ask Auto to discover an additional specialist by capability.",
        {
            "type": "object",
            "properties": {
                "capability": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["capability", "reason"],
            "additionalProperties": False,
        },
    ),
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
    "ai_search": (
        "Retrieve governed text from a Nova AI Search index with caller permissions.",
        {
            "type": "object",
            "properties": {
                "index": {"type": "string"},
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["LEXICAL", "SEMANTIC", "HYBRID"]},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["index", "query"],
            "additionalProperties": False,
        },
    ),
    "semantic_view_query": (
        "Query a published Nova Semantic View using governed metrics and dimensions.",
        {
            "type": "object",
            "properties": {
                "view_id": {"type": "string"},
                "metrics": {"type": "array", "items": {"type": "string"}},
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["view_id", "metrics"],
            "additionalProperties": False,
        },
    ),
    "feature_lookup": (
        "Look up a governed Nova Feature Group by entity key.",
        {
            "type": "object",
            "properties": {
                "group": {"type": "string"},
                "entity_key": {"type": "object"},
                "version": {"type": "integer", "minimum": 1},
            },
            "required": ["group", "entity_key"],
            "additionalProperties": False,
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
    "create_semantic_view": (
        "Create, validate, and optionally publish a Nova Semantic View from authorized tables.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "database": {"type": "string"},
                "schema_name": {"type": "string"},
                "description": {"type": "string"},
                "request": {"type": "string"},
                "tables": {"type": "array", "items": {"type": "string"}},
                "publish": {"type": "boolean"},
            },
            "required": ["name", "tables"],
            "additionalProperties": False,
        },
    ),
    "validate_sql": (
        "Check SQL syntax locally without execution, object resolution or privilege verification.",
        {"type": "object", "properties": {"sql": {"type": "string"}},
         "required": ["sql"], "additionalProperties": False},
    ),
    "provision_user": (
        "Create a user with protected temporary password input and mandatory password change.",
        {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "role": {"type": "string"},
            },
            "required": ["username", "role"],
            "additionalProperties": False,
        },
    ),
    "query_mutate": (
        "Execute approved SQL writes through the caller's query pipeline; "
        "no credentials or HTTP routes.",
        {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
            },
            "required": ["sql"],
            "additionalProperties": False,
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
    "inspect_role_access": (
        "Check an existing role's effective Ranger access on named resources.",
        {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "grants": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["role", "grants"],
            "additionalProperties": False,
        },
    ),
    "grant_role_access": (
        "Grant approved Ranger access to the exact existing role.",
        {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "grants": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["role", "grants"],
            "additionalProperties": False,
        },
    ),
    "inspect_agent_configuration": (
        "Inspect an Agent Studio agent's owner-scoped configuration and chart tool setting.",
        {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            "additionalProperties": False,
        },
    ),
    "inspect_query_error": (
        "Inspect the current failed SQL execution and matching redacted query event.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "verify_query_repair": (
        "Verify a Nove editor patch against a correlated successful SQL rerun.",
        {
            "type": "object",
            "properties": {
                "correlation_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            "additionalProperties": False,
        },
    ),
}


#: Names the agent builder may bundle. Distinct from the builtin catalog: the
#: authoring tools (create_*) are Nove-only and are not offered to a user agent.
AGENT_BUNDLEABLE_TOOLS = (
    "load_skill",
    "semantic_query",
    "semantic_search",
    "ai_search",
    "semantic_view_query",
    "feature_lookup",
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
