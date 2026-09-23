"""Tool registry for the assistant.

Stage B defines the boundary the loop uses; Stage C registers ``query_execute``
against it. Keeping the registry here (rather than in the loop) is what lets
T-C1 land without touching the loop's control flow, and what keeps the loop
testable with a fake tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.modules.assistant.consent import ConsentApproval
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

    ``table`` / ``chart`` / ``citations`` are optional **structured** payloads
    the loop turns into ``table`` / ``chart`` / ``citation`` SSE frames, so the
    chat can render data instead of only prose. They are redacted by the tool
    before they are set; the loop does not inspect them. ``table`` is
    ``{"title", "columns", "rows"}``; ``chart`` is ``{"chart_spec"}`` where
    ``chart_spec`` is a Vega-Lite v5 JSON string.
    """

    ok: bool
    summary: str
    error: str | None = None
    table: dict[str, Any] | None = None
    chart: dict[str, Any] | None = None
    citations: list[dict[str, Any]] | None = None
    #: Bounded, credential-free observability metadata owned by the tool. The
    #: loop stores it on the matching tool step but never sends it to the model.
    #: Semantic query uses this for the model/dataset/SQL snapshot shown in the
    #: trace inspector. It must not contain result rows or credentials.
    trace_detail: dict[str, Any] | None = None
    #: Canonical provider-independent envelope fields. Existing tools may omit
    #: them; the loop fills ``tool`` and evidence metadata at the boundary.
    tool: str | None = None
    data: Any = None
    evidence: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    state_patch: dict[str, Any] = field(default_factory=dict)
    error_class: str | None = None
    recoverable: bool = False
    safe_detail: str | None = None
    repair_context: dict[str, Any] | None = None

    def envelope(self, *, tool_name: str, evidence_id: str | None = None) -> dict[str, Any]:
        """Return the normalized result sent through a provider adapter."""
        if self.ok:
            evidence = dict(self.evidence or {})
            if evidence_id:
                evidence["evidence_id"] = evidence_id
            return {
                "ok": True,
                "tool": self.tool or tool_name,
                "data": self.data if self.data is not None else {"summary": self.summary},
                "evidence": evidence,
                "artifacts": self.artifacts,
                "warnings": self.warnings,
                "metadata": self.metadata,
                "state_patch": self.state_patch,
            }
        return {
            "ok": False,
            "tool": self.tool or tool_name,
            "error_class": self.error_class or "TOOL_ERROR",
            "recoverable": self.recoverable,
            "safe_detail": self.safe_detail or self.error or "The tool failed.",
            "repair_context": self.repair_context or {},
        }


def report_tool_progress(
    context: Any,
    *,
    stage: str,
    text: str,
    sql_preview: str | None = None,
) -> None:
    """Publish one observable tool lifecycle update to the running loop.

    The context owns a request-local queue installed by ``AssistantLoop``.  A
    tool that runs outside that loop simply has no sink, which keeps direct unit
    tests and non-streaming callers compatible.  Values crossing this boundary
    must already be redacted; in particular, raw credential-bearing SQL must
    never be passed as ``sql_preview``.
    """
    sink = getattr(context, "tool_progress_sink", None)
    if not callable(sink):
        return
    sink(
        {
            "stage": stage,
            "text": text,
            "sql_preview": sql_preview,
        }
    )


def record_provider_usage(context: Any, message: dict[str, Any]) -> None:
    """Add usage from a nested tool model call to the turn total.

    SQL generation and chart composition are model calls too. Counting only the
    outer orchestration loop would under-report the real cost of an agent turn.
    Missing or malformed provider usage remains an honest unknown rather than a
    fabricated zero.
    """
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return
    current = getattr(context, "usage", None) or {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            current[key] = current.get(key, 0) + value
    context.usage = current


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

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome: ...


def invocation_classification(
    tool: AssistantTool, invocation: ToolInvocation
) -> ToolClassification:
    """Resolve security classification without shared invocation state."""
    resolver = getattr(tool, "classification_for", None)
    if callable(resolver):
        return resolver(invocation)
    return tool.classification


def requires_consent(tool: AssistantTool) -> bool:
    """Whether ``tool`` must be approved by the user before it runs.

    Default ``True``: any tool that touches the engine or the user's data must be
    approved. A pure tool — one that only reads packaged, credential-free
    reference data, such as ``load_skill`` — sets ``requires_consent = False`` so
    it never prompts. Read with ``getattr`` so a tool that omits the attribute is
    treated as consent-requiring (fail closed).
    """
    return bool(getattr(tool, "requires_consent", True))


class ToolRegistry:
    """Name → tool map. v1 holds at most one tool (``query_execute``)."""

    def __init__(self) -> None:
        self._tools: dict[str, AssistantTool] = {}
        self.default_skills: tuple[str, ...] = ()
        self.discoverable_skills: tuple[str, ...] = ()
        self.skill_definitions: dict[str, Any] = {}

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
ConsentResolver = Callable[
    [ToolInvocation, str], Awaitable[bool | None | ConsentApproval]
]
