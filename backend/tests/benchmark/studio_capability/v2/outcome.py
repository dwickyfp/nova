"""How a turn ended, from structured signals only.

Studio's ``TurnOutcome`` contract (PR-0, DESIGN §3.2) is mirrored here so the
harness reads it from ``run.finished.outcome`` once the product sends it. Until
then :func:`derive_outcome` derives the same kind/reason from typed events
(the prototype's rules, DESIGN §3.4). Both paths return :class:`Outcome`, which
names its ``source`` so a report can tell product facts from derivations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from tests.benchmark.studio_capability.v2.fold import Signals


class OutcomeKind(StrEnum):
    ANSWER = "answer"
    REFUSE = "refuse"
    LIMITATION = "limitation"
    CLARIFY = "clarify"
    ACTION_DONE = "action_done"
    ACTION_FAILED = "action_failed"


class ReasonCode(StrEnum):
    GOVERNED_DATA = "governed_data"
    PUBLIC_CONTENT = "public_content"
    AGENT_META = "agent_meta"
    POLICY_DENIED = "policy_denied"
    ROLE_NOT_PERMITTED = "role_not_permitted"
    WRITE_NOT_ALLOWED = "write_not_allowed"
    SOURCE_NOT_AVAILABLE = "source_not_available"
    CATALOG_UNSUPPORTED = "catalog_unsupported"
    OUTSIDE_DATA_WINDOW = "outside_data_window"
    NO_ROWS = "no_rows"
    TOOL_FAILED = "tool_failed"
    GENERAL_KNOWLEDGE = "general_knowledge"
    AMBIGUOUS_TERM = "ambiguous_term"
    MISSING_INPUT = "missing_input"
    HITL_PENDING = "hitl_pending"
    ACTION_COMMITTED = "action_committed"
    ACTION_INVALID_ARGS = "action_invalid_args"
    REQUIRED_CAPABILITY_MISSING = "required_capability_missing"


class Claim(BaseModel):
    model_config = ConfigDict(extra="ignore")
    value: float | None = None
    evidence_id: str | None = None
    column: str | None = None
    kind: Literal["figure", "derived", "hypothesis"] = "figure"
    supported: bool = False


class TurnOutcome(BaseModel):
    """The product contract (additive on ``run.finished``; finish_reason stays)."""

    model_config = ConfigDict(extra="ignore")
    schema_version: Literal[1] = 1
    kind: OutcomeKind
    reason: ReasonCode
    evidence_ids: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    alternatives_shown: bool = False
    hitl: list[dict[str, Any]] = Field(default_factory=list)
    action_receipts: list[str] = Field(default_factory=list)
    finish_reason: str | None = None


#: Kinds the harness adds when it cannot read a product outcome: the turn broke,
#: was stopped, showed only prose (meaning question) or nothing at all.
HARNESS_KINDS = frozenset({"error", "cancelled", "text_only", "empty", "clarify_or_limitation"})
ACTION_CLASSES = frozenset({"destructive", "write"})
INFO_TOOLS = frozenset({"describe_agent", "load_skill"})
DATA_TOOLS = frozenset({"semantic_query", "query_execute", "semantic_view_query",
                        "diagnose_change"})


@dataclass(frozen=True)
class Outcome:
    kind: str
    reason: str
    source: Literal["product", "derived"]
    claims: tuple[Claim, ...] = ()
    alternatives_shown: bool | None = None
    evidence_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())


def product_outcome(payload: Any) -> TurnOutcome | None:
    """The product's TurnOutcome from a ``run.finished`` payload, when valid."""
    if not isinstance(payload, dict):
        return None
    try:
        return TurnOutcome.model_validate(payload)
    except ValidationError:
        return None


def outcome_of(sig: Signals) -> Outcome:
    """The product outcome when Studio sent one, else the derived outcome.

    Transport errors and runner cancellations stay harness facts either way.
    """
    if sig.transport_error or sig.cancelled:
        return derive_outcome(sig)
    product = sig.product_outcome
    if product is not None:
        return Outcome(product.kind.value, product.reason.value, "product",
                       tuple(product.claims), product.alternatives_shown,
                       tuple(product.evidence_ids))
    return derive_outcome(sig)


def derive_outcome(sig: Signals) -> Outcome:
    """(kind, reason) from typed events, in the prototype's priority order."""
    kind, reason = _derive(sig)
    return Outcome(kind, reason, "derived", tuple(sig.claims))


def _derive(sig: Signals) -> tuple[str, str]:
    finish = sig.finish_reason
    if sig.transport_error or finish in {"error", "interrupted", "unexpected_tool_call"} or (
            sig.run_errors and finish is None):
        code = sig.run_errors[0].get("code") if sig.run_errors else (finish or "transport")
        return "error", str(code or "error")
    if finish == "cancelled" or sig.cancelled:
        return "cancelled", "cancelled"
    if finish == "denied":
        return "refuse", ReasonCode.POLICY_DENIED.value
    if sig.hitl_pending:
        return "clarify", ReasonCode.HITL_PENDING.value
    if finish == "required_capability_incomplete":
        return "action_failed", ReasonCode.REQUIRED_CAPABILITY_MISSING.value
    actions = [t for t in sig.tools if t.get("classification") in ACTION_CLASSES]
    if any(t.get("status") == "done" for t in actions):
        return "action_done", ReasonCode.ACTION_COMMITTED.value
    if finish == "out_of_scope":
        if any(t.get("status") == "failed" for t in sig.tools if t.get("name") in DATA_TOOLS):
            return "limitation", ReasonCode.TOOL_FAILED.value
        return "limitation", "out_of_scope"
    if finish == "clarification":
        if sig.data_shown:
            return "answer", ReasonCode.GOVERNED_DATA.value
        # One finish reason covers "the catalog cannot express it", "the period has
        # no data" and "a term is ambiguous": not decidable without TurnOutcome.
        unresolved = any("unresolved_semantic_ambiguity" in (t.get("reasons") or [])
                         for t in sig.tools)
        return "clarify_or_limitation", ("catalog_unresolved" if unresolved
                                         else "no_data_window")
    if sig.data_shown:
        return "answer", ReasonCode.GOVERNED_DATA.value
    if sig.public_content_shown:
        return "answer", ReasonCode.PUBLIC_CONTENT.value
    mcp = {str(t.get("name")) for t in sig.tools if str(t.get("name") or "").startswith("mcp")}
    if sig.tool_ok(INFO_TOOLS | mcp):
        return "answer", ReasonCode.AGENT_META.value
    attempted = [t for t in sig.tools if t.get("name") in DATA_TOOLS]
    if attempted and not sig.tool_ok(DATA_TOOLS):
        return "limitation", ReasonCode.TOOL_FAILED.value
    if sig.drafts:
        return "answer", ReasonCode.GOVERNED_DATA.value
    if sig.answer_chars or sig.page_text_only:
        return "text_only", "no_tool_no_artifact" if sig.answer_chars else "page_text_only"
    return "empty", "empty"
