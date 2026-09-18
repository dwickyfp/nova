"""Tool registry for the assistant.

Stage B defines the boundary the loop uses; Stage C registers ``query_execute``
against it. Keeping the registry here (rather than in the loop) is what lets
T-C1 land without touching the loop's control flow, and what keeps the loop
testable with a fake tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.modules.assistant.schemas import ToolClassification


@dataclass(frozen=True)
class ToolInvocation:
    """A model-proposed tool call, before consent has been resolved."""

    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcome:
    """Result of running a tool, for the loop to fold back into the model.

    ``summary`` is what the panel shows (row/affected count or a status line).
    It must never carry rows or credential-shaped values.
    """

    ok: bool
    summary: str
    error: str | None = None


class AssistantTool(Protocol):
    """A tool the loop may invoke.

    ``classification`` decides whether a conversation grant may auto-approve a
    call (E2b: read-only only). It is declared as a **read-only property**
    because ``query_execute`` classifies per invocation (a payload with a denied
    statement is not read-only); a stub tool may satisfy it with a plain
    attribute, which mypy accepts as a property implementation.
    ``preview`` produces the redacted SQL shown in the approval card — Stage C
    owns making that redaction real.
    """

    name: str

    @property
    def classification(self) -> ToolClassification: ...

    def preview(self, invocation: ToolInvocation) -> str: ...

    async def run(
        self, invocation: ToolInvocation, context: Any
    ) -> ToolOutcome: ...


class ToolRegistry:
    """Name → tool map. v1 holds at most one tool (``query_execute``)."""

    def __init__(self) -> None:
        self._tools: dict[str, AssistantTool] = {}

    def register(self, tool: AssistantTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> AssistantTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def clear(self) -> None:
        """Test helper."""
        self._tools.clear()


tool_registry = ToolRegistry()
"""Deprecated alias: the canonical registry is ``app.modules.assistant.registry``,
which registers the Stage C tools. Kept so a Stage B import keeps working."""

#: Signature the loop uses to request a consent decision from the transport.
#: ``None`` means the client disconnected or the turn was cancelled.
ConsentResolver = Callable[[ToolInvocation, str], Awaitable[bool | None]]
