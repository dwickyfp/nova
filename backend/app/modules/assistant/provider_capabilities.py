"""Provider transport capabilities, separate from Nova business logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.assistant.intelligence import HarnessMode


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_tools: bool = True
    supports_tool_choice: bool = False
    supports_required_tool: bool = False
    supports_strict_tool_schema: bool = False
    supports_json_schema: bool = False
    supports_structured_output: bool = False
    supports_parallel_tool_calls: bool = False
    supports_tool_role_messages: bool = True
    context_window: int = 32_000
    preferred_temperature_controls: bool = False

    @classmethod
    def from_mapping(
        cls,
        value: dict[str, Any] | None,
        *,
        base: ProviderCapabilities | None = None,
    ) -> ProviderCapabilities:
        """Read an explicit provider/model capability profile, never a vendor guess."""
        current = base or cls()
        if not isinstance(value, dict):
            return current
        fields = cls.__dataclass_fields__
        updates: dict[str, Any] = {
            name: getattr(current, name) for name in fields
        }
        for name in fields:
            candidate = value.get(name)
            if name == "context_window":
                if isinstance(candidate, int) and candidate > 0:
                    updates[name] = candidate
            elif isinstance(candidate, bool):
                updates[name] = candidate
        return cls(**updates)

    def recommended_mode(self) -> HarnessMode:
        if not self.supports_tools:
            return HarnessMode.STRICT
        if self.supports_required_tool and self.supports_strict_tool_schema:
            return HarnessMode.FAST
        return HarnessMode.GUIDED


CONSERVATIVE_OPENAI_COMPATIBLE = ProviderCapabilities()


@dataclass(frozen=True)
class AssistantDecision:
    """Provider-neutral response consumed by Nova business logic."""

    content: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    structured_output: dict[str, Any] | None = None
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None

    @classmethod
    def from_openai_message(
        cls,
        message: dict[str, Any],
        *,
        usage: dict[str, int] | None = None,
        finish_reason: str | None = None,
    ) -> AssistantDecision:
        structured = message.get("structured_output")
        return cls(
            content=str(message.get("content") or ""),
            tool_calls=tuple(message.get("tool_calls") or ()),
            structured_output=structured if isinstance(structured, dict) else None,
            usage=usage or {},
            finish_reason=finish_reason,
        )
