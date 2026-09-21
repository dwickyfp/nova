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
    "query_execute": (
        "Run one read-only SQL statement (SELECT, WITH … SELECT, SHOW, DESCRIBE, "
        "EXPLAIN) on the user's connection.",
        {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single read-only SQL statement."}
            },
            "required": ["sql"],
        },
    ),
    "semantic_query": (
        "Answer a business question from the agent's semantic model, translating "
        "it into SQL over defined metrics and dimensions.",
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
        "Full-text search over indexed text in the semantic model's datasets. "
        "Results are unranked.",
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
        "Build a Vega-Lite chart from the latest data already fetched in this conversation.",
        {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "description": "What the chart should show."}
            },
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
}


#: Names the agent builder may bundle. Distinct from the builtin catalog: the
#: authoring tools (create_*) are Nove-only and are not offered to a user agent.
AGENT_BUNDLEABLE_TOOLS = (
    "load_skill",
    "query_execute",
    "semantic_query",
    "semantic_search",
    "data_to_chart",
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
