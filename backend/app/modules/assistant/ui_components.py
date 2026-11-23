"""Studio registry components: the server side of the one component contract.

The schemas live in the frontend registry
(``frontend/src/features/studio/runtime/components/schemas.ts``) and are
exported to ``ui_components.json`` by ``pnpm components:bundle``; CI fails when
the two differ. This module validates every component the loop emits and
every registry tool argument against that file, and gives the model the same
parameter schemas as tool definitions. There is no second hand-written schema.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

CONTRACT_PATH = Path(__file__).with_name("ui_components.json")
#: The registry component event: ``{component, version, props}`` plus position.
EVENT_COMPONENT = "component"


class ComponentError(ValueError):
    """A component or tool argument does not match the registry contract."""


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    value = json.loads(CONTRACT_PATH.read_text())
    if not isinstance(value, dict) or not value.get("components"):
        raise ComponentError("The Studio component contract is unreadable")
    return value


@lru_cache(maxsize=16)
def _validator(kind: str, name: str) -> Draft202012Validator:
    section = contract()["components" if kind == "component" else "tools"]
    entry = section.get(name)
    if entry is None:
        raise ComponentError(f"Unknown Studio {kind} {name!r}")
    schema = entry["props"] if kind == "component" else entry["parameters"]
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _message(error: ValidationError) -> str:
    where = ".".join(str(part) for part in error.absolute_path) or "the value"
    return f"{where}: {error.message}"[:300]


def component_version(name: str) -> int:
    entry = contract()["components"].get(name)
    if entry is None:
        raise ComponentError(f"Unknown Studio component {name!r}")
    return int(entry["version"])


def validate_props(name: str, props: Any) -> dict[str, Any]:
    """The props, unchanged, when they match the component's schema."""
    errors = sorted(_validator("component", name).iter_errors(props), key=lambda e: e.path)
    if errors:
        raise ComponentError(f"{name} is invalid: {_message(errors[0])}")
    return props


def validate_tool_arguments(name: str, arguments: Any) -> dict[str, Any]:
    errors = sorted(_validator("tool", name).iter_errors(arguments), key=lambda e: e.path)
    if errors:
        raise ComponentError(f"The {name} arguments are invalid: {_message(errors[0])}")
    return arguments


def tool_parameters(name: str) -> dict[str, Any]:
    """The JSON Schema the model receives for a registry tool."""
    return json.loads(json.dumps(contract()["tools"][name]["parameters"]))


def tool_description(name: str) -> str:
    return str(contract()["tools"][name]["description"])


def component(name: str, props: dict[str, Any]) -> dict[str, Any]:
    """A validated registry component payload."""
    return {"component": name, "version": component_version(name),
            "props": validate_props(name, props)}


#: Aggregate prefixes an engine column name may carry, read as words.
_AGGREGATE_WORDS = {"sum": "Total", "total": "Total", "avg": "Average", "average": "Average",
                    "mean": "Average", "min": "Minimum", "max": "Maximum",
                    "cnt": "Number of", "count": "Number of"}
_ACRONYMS = frozenset({"id", "ids", "sku", "url", "kpi", "gmv", "aov", "roi", "vat", "nik", "npwp"})


def field_label(name: str) -> str:
    """A result column as words: ``sum_revenue`` is "Total revenue", ``customer_id``
    "Customer ID". A name that is not snake_case is returned as written."""
    raw = str(name).rsplit(".", 1)[-1].strip()
    words = [word for word in raw.split("_") if word]
    if not words or raw != raw.lower():
        return raw or str(name)
    lead = _AGGREGATE_WORDS.get(words[0]) if len(words) > 1 else None
    rest = [word.upper() if word in _ACRONYMS else word for word in words[1 if lead else 0:]]
    text = " ".join([lead, *rest] if lead else rest)
    return text[:1].upper() + text[1:]

