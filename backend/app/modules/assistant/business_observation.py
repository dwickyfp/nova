"""Explicit channels for canonical business facts at the tool boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MAX_BUSINESS_OBSERVATION_BYTES = 16 * 1024
SEMANTIC_TOOLS = frozenset({"semantic_query", "semantic_view_query"})


@dataclass(frozen=True)
class BusinessResultHookResult:
    public_event: dict[str, Any] | None = None
    provider_observation: dict[str, Any] | None = None
    trace_metadata: dict[str, Any] | None = None

    def __post_init__(self):
        if self.provider_observation is not None and len(json.dumps(
            self.provider_observation, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")) > MAX_BUSINESS_OBSERVATION_BYTES:
            raise ValueError("Canonical business observation exceeds its byte bound")


def provider_safe_payload(payload: Any, *, tool_name: str | None = None) -> Any:
    """Remove deprecated semantic pseudo-probabilities, including old snapshots."""
    semantic = tool_name in SEMANTIC_TOOLS or (
        isinstance(payload, dict) and payload.get("tool") in SEMANTIC_TOOLS
    )
    if not semantic:
        return payload

    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()
                    if not (key in {"confidence", "legacy_confidence"}
                            and isinstance(item, int | float))}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(payload)


def provider_history_content(content: str, *, tool_name: str | None = None) -> str:
    prefix, suffix = "<TOOL_RESULT_DATA>", "</TOOL_RESULT_DATA>"
    wrapped = content.startswith(prefix) and content.endswith(suffix)
    raw = content[len(prefix):-len(suffix)] if wrapped else content
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return content
    cleaned = provider_safe_payload(payload, tool_name=tool_name)
    if cleaned == payload:
        return content
    rendered = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
    return prefix + rendered + suffix if wrapped else rendered
