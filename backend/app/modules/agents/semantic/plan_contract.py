"""Shared provider and runtime contract for generated semantic plans."""

from __future__ import annotations

from typing import Any

from app.modules.agents.semantic.planning import (
    HAVING_OPERATORS,
    TRANSFORM_KINDS,
    SemanticPlanError,
    TimeComparison,
)
from app.modules.assistant.intelligence import validate_json_arguments


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def semantic_plan_schema() -> dict[str, Any]:
    scalar = {"type": ["string", "number", "boolean", "null"]}
    strings = {"type": "array", "items": {"type": "string"}}
    time = _object(
        {
            "dimension": {"type": "string"},
            "grain": {
                "type": ["string", "null"],
                "enum": [None, "day", "week", "month", "quarter", "year"],
            },
            "range": {"type": ["string", "null"]},
            "compare": {
                "type": ["string", "null"],
                "enum": [None, *(item.value for item in TimeComparison)],
            },
        }
    )
    time["type"] = ["object", "null"]
    top_n = _object(
        {
            "n": {"type": "integer", "minimum": 1, "maximum": 100},
            "partition_by": {"type": "array", "items": {"type": "string"}},
            "metric": {"type": "string"},
        }
    )
    top_n["type"] = ["object", "null"]
    return _object(
        {
            "metrics": strings,
            "dimensions": strings,
            "filters": {
                "type": "array",
                "items": _object(
                    {
                        "field": {"type": "string"},
                        "operator": {
                            "type": "string",
                            "enum": ["=", "!=", ">", ">=", "<", "<=", "IN", "LIKE"],
                        },
                        "value": {
                            "type": ["string", "number", "boolean", "null", "array"],
                            "items": scalar,
                        },
                    }
                ),
            },
            "named_filters": strings,
            "time": time,
            "order_by": {
                "type": "array",
                "items": _object(
                    {
                        "field": {"type": "string"},
                        "direction": {"type": "string", "enum": ["asc", "desc"]},
                    }
                ),
            },
            "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 1000},
            "unresolved_concepts": {
                "type": "array",
                "items": _object(
                    {
                        "text": {"type": "string"},
                        "type_hint": {"type": "string"},
                        "material": {"type": "boolean"},
                    }
                ),
            },
            "having": {
                "type": "array",
                "items": _object(
                    {
                        "metric": {"type": "string"},
                        "operator": {"type": "string", "enum": list(HAVING_OPERATORS)},
                        "value": {"type": "number"},
                    }
                ),
            },
            "transforms": {
                "type": "array",
                "items": _object(
                    {
                        "metric": {"type": "string"},
                        "kind": {"type": "string", "enum": list(TRANSFORM_KINDS)},
                    }
                ),
            },
            "top_n_per_group": top_n,
        }
    )


#: Keys added after the first contract. A plan stored or generated before them
#: is still valid; the defaults mean "not used".
_OPTIONAL_DEFAULTS = {"having": [], "transforms": [], "top_n_per_group": None}


def validate_generated_plan(value: dict[str, Any]) -> None:
    for key, default in _OPTIONAL_DEFAULTS.items():
        value.setdefault(key, default)
    errors = validate_json_arguments(semantic_plan_schema(), value)
    if errors:
        paths = ", ".join(error.path for error in errors[:8])
        raise SemanticPlanError(f"Generated plan violates its schema at: {paths}.")
    for item in value["filters"]:
        if item["operator"] == "IN":
            if not isinstance(item["value"], list) or not item["value"]:
                raise SemanticPlanError("IN requires a non-empty list of literal values.")
        elif isinstance(item["value"], list):
            raise SemanticPlanError("A list of filter values requires IN.")
    time = value.get("time")
    if isinstance(time, dict) and time.get("range"):
        from app.modules.agents.semantic.time_ranges import (
            RANGE_GRAMMAR_HELP,
            is_supported_time_range,
        )

        if not is_supported_time_range(str(time["range"])):
            raise SemanticPlanError(
                f"Unsupported time.range {time['range']!r}. {RANGE_GRAMMAR_HELP}"
            )
    if any(not item["text"].strip() for item in value["unresolved_concepts"]):
        raise SemanticPlanError("Unresolved concepts must identify the missing constraint.")
