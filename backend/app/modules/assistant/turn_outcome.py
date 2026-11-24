"""What a turn ended as, from the loop's own facts: the ``TurnOutcome`` contract.

``finish_reason`` says how the loop stopped. ``TurnOutcome`` says what the user
got: an answer, a refusal, a stated limit, a question back, or an action done or
failed, each with a reason code. It is filled from state the loop already holds
(tool error classes, denials, guard decisions, human-input requests, verified
evidence and the checked claims), never by reading the answer's words.

One field is the model's: a declined request or a stated limit with no tool
behind it (a write refused, a source that is not available) leaves no fact to
read. The model labels it in its claims block (``{"kind": "outcome", ...}``),
and the label is accepted only where the facts allow it. A label that disagrees
with the facts, or an answer with no fact behind it, is marked ``basis:
"unverified"`` so a reviewer decides.

The outcome rides ``run.finished`` (additive: ``finish_reason`` is unchanged)
and is stored in the turn's ``quality_observation`` facts.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class OutcomeKind(StrEnum):
    ANSWER = "answer"
    REFUSE = "refuse"
    LIMITATION = "limitation"
    CLARIFY = "clarify"
    ACTION_DONE = "action_done"
    ACTION_FAILED = "action_failed"
    ERROR = "error"
    CANCELLED = "cancelled"


class ReasonCode(StrEnum):
    # answer
    GOVERNED_DATA = "governed_data"
    PUBLIC_CONTENT = "public_content"
    AGENT_META = "agent_meta"
    GENERAL = "general"
    # refuse
    POLICY_DENIED = "policy_denied"
    CONSENT_DECLINED = "consent_declined"
    ROLE_NOT_PERMITTED = "role_not_permitted"
    WRITE_NOT_ALLOWED = "write_not_allowed"
    SOURCE_NOT_AVAILABLE = "source_not_available"
    # limitation
    CATALOG_UNSUPPORTED = "catalog_unsupported"
    OUTSIDE_DATA_WINDOW = "outside_data_window"
    NO_ROWS = "no_rows"
    TOOL_FAILED = "tool_failed"
    GENERAL_KNOWLEDGE = "general_knowledge"
    REQUIRED_CAPABILITY_MISSING = "required_capability_missing"
    # clarify
    AMBIGUOUS_TERM = "ambiguous_term"
    MISSING_INPUT = "missing_input"
    HITL_PENDING = "hitl_pending"
    # action
    ACTION_COMMITTED = "action_committed"
    ACTION_INVALID_ARGS = "action_invalid_args"
    CLIENT_ACTION_PENDING = "client_action_pending"
    # error / cancelled
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CONTEXT_OVERFLOW = "context_overflow"
    INTERNAL_ERROR = "internal_error"
    PERSISTENCE_UNAVAILABLE = "persistence_unavailable"
    STOPPED = "stopped"
    HITL_EXPIRED = "hitl_expired"


#: Which reasons each kind may carry. A model label outside this map is ignored.
KIND_REASONS: dict[OutcomeKind, frozenset[ReasonCode]] = {
    OutcomeKind.ANSWER: frozenset({
        ReasonCode.GOVERNED_DATA, ReasonCode.PUBLIC_CONTENT, ReasonCode.AGENT_META,
        ReasonCode.GENERAL,
    }),
    OutcomeKind.REFUSE: frozenset({
        ReasonCode.POLICY_DENIED, ReasonCode.CONSENT_DECLINED, ReasonCode.ROLE_NOT_PERMITTED,
        ReasonCode.WRITE_NOT_ALLOWED, ReasonCode.SOURCE_NOT_AVAILABLE,
    }),
    OutcomeKind.LIMITATION: frozenset({
        ReasonCode.CATALOG_UNSUPPORTED, ReasonCode.OUTSIDE_DATA_WINDOW, ReasonCode.NO_ROWS,
        ReasonCode.TOOL_FAILED, ReasonCode.GENERAL_KNOWLEDGE,
        ReasonCode.REQUIRED_CAPABILITY_MISSING,
    }),
    OutcomeKind.CLARIFY: frozenset({
        ReasonCode.AMBIGUOUS_TERM, ReasonCode.MISSING_INPUT, ReasonCode.HITL_PENDING,
    }),
    OutcomeKind.ACTION_DONE: frozenset({
        ReasonCode.ACTION_COMMITTED, ReasonCode.CLIENT_ACTION_PENDING,
    }),
    OutcomeKind.ACTION_FAILED: frozenset({
        ReasonCode.ACTION_INVALID_ARGS, ReasonCode.TOOL_FAILED,
        ReasonCode.REQUIRED_CAPABILITY_MISSING,
    }),
    OutcomeKind.ERROR: frozenset({
        ReasonCode.TOOL_FAILED, ReasonCode.TIMEOUT, ReasonCode.BUDGET_EXHAUSTED,
        ReasonCode.CONTEXT_OVERFLOW, ReasonCode.INTERNAL_ERROR,
        ReasonCode.PERSISTENCE_UNAVAILABLE,
    }),
    OutcomeKind.CANCELLED: frozenset({ReasonCode.STOPPED, ReasonCode.HITL_EXPIRED}),
}

#: The kinds and reasons a model may label itself (a refusal or a stated limit
#: no tool decided). Everything else comes from facts only.
LABEL_REASONS: dict[str, frozenset[str]] = {
    OutcomeKind.REFUSE.value: frozenset({
        ReasonCode.WRITE_NOT_ALLOWED.value, ReasonCode.SOURCE_NOT_AVAILABLE.value,
        ReasonCode.ROLE_NOT_PERMITTED.value, ReasonCode.POLICY_DENIED.value,
    }),
    OutcomeKind.LIMITATION.value: frozenset({
        ReasonCode.GENERAL_KNOWLEDGE.value, ReasonCode.CATALOG_UNSUPPORTED.value,
        ReasonCode.OUTSIDE_DATA_WINDOW.value, ReasonCode.NO_ROWS.value,
    }),
    OutcomeKind.ANSWER.value: frozenset({
        ReasonCode.GOVERNED_DATA.value, ReasonCode.PUBLIC_CONTENT.value,
        ReasonCode.AGENT_META.value, ReasonCode.GENERAL.value,
    }),
}

#: Tool error classes the user is answered from without data, by outcome.
NOT_ANSWERED_OUTCOMES: dict[str, tuple[OutcomeKind, ReasonCode]] = {
    "CATALOG_UNSUPPORTED": (OutcomeKind.LIMITATION, ReasonCode.CATALOG_UNSUPPORTED),
    "OUTSIDE_DATA_WINDOW": (OutcomeKind.LIMITATION, ReasonCode.OUTSIDE_DATA_WINDOW),
    "CLARIFICATION_REQUIRED": (OutcomeKind.CLARIFY, ReasonCode.AMBIGUOUS_TERM),
}

#: Error classes that mean the call's arguments were wrong, not the system.
INVALID_ARGUMENT_CLASSES = frozenset({
    "INVALID_ARGUMENTS", "INVALID_TOOL_ARGUMENTS", "VALIDATION_ERROR", "INVALID_INPUT",
    "INVALID_SCHEDULE", "INVALID_REQUEST",
})

#: Error classes that are a refusal by role or policy.
ROLE_CLASSES = frozenset({
    "PERMISSION_DENIED", "AUTHORIZATION_FAILURE", "AUTHORIZATION", "AUTHORIZATION_DENIED",
    "ACCESS_DENIED",
})

#: Governed data tools: a failure of one is a data limitation.
DATA_TOOLS = frozenset({
    "semantic_query", "semantic_view_query", "query_execute", "diagnose_change",
    "native_dataset_query", "canonical_investigation",
})

#: Tools that answer about the agent itself.
META_TOOLS = frozenset({"describe_agent"})

#: Tools that run nothing the answer is about (a skill, a question to the user).
_NEUTRAL_TOOLS = frozenset({"load_skill", "request_human_input"})

_TERMINAL_ERRORS: dict[str, ReasonCode] = {
    "timeout": ReasonCode.TIMEOUT,
    "token_budget": ReasonCode.BUDGET_EXHAUSTED,
    "iteration_cap": ReasonCode.BUDGET_EXHAUSTED,
    "context_overflow": ReasonCode.CONTEXT_OVERFLOW,
    "planning_failed": ReasonCode.INTERNAL_ERROR,
    "unexpected_tool_call": ReasonCode.INTERNAL_ERROR,
    "interrupted": ReasonCode.INTERNAL_ERROR,
}

Basis = Literal["facts", "model_label", "unverified"]


class OutcomeClaim(BaseModel):
    """A number the answer states, bound by Nova's check to its source."""

    text: str
    value: float | None = None
    evidence_id: str | None = None
    column: str | None = None
    kind: Literal["figure", "derived", "hypothesis"] = "figure"
    supported: bool


class HitlRecord(BaseModel):
    kind: str
    status: str


class TurnOutcome(BaseModel):
    schema_version: Literal[1] = 1
    kind: OutcomeKind
    reason: ReasonCode
    #: ``facts``: decided by the loop's state; ``model_label``: the model's own
    #: label, consistent with the facts; ``unverified``: nothing decides it.
    basis: Basis = "facts"
    #: Tool calls whose verified results the turn showed.
    evidence_ids: list[str] = Field(default_factory=list)
    claims: list[OutcomeClaim] = Field(default_factory=list)
    #: A refusal or stated limit that also showed a labelled figure from the
    #: turn's own data.
    alternatives_shown: bool = False
    hitl: list[HitlRecord] = Field(default_factory=list)
    #: Tool calls whose action committed (a schedule, a dashboard change).
    action_receipts: list[str] = Field(default_factory=list)
    finish_reason: str


@dataclass
class OutcomeLedger:
    """The facts the loop records as it goes; read once when the turn ends.

    Per request, never sent upstream. Saved with a checkpoint so a resumed turn
    keeps what it already showed.
    """

    #: The last tool error class the user was answered from without data.
    not_answered: str | None = None
    #: ``{tool, error_class}`` for each failed call.
    failures: list[dict[str, str | None]] = field(default_factory=list)
    #: ``{tool_call_id, tool, ok, error_class}`` for each action (an effect) call.
    actions: list[dict[str, Any]] = field(default_factory=list)
    #: Informational tools that succeeded without a governed result.
    informational: list[str] = field(default_factory=list)
    #: Evidence id -> tool call id, for each verified result shown.
    evidence: dict[str, str] = field(default_factory=dict)
    #: Evidence ids whose result had no rows.
    empty: list[str] = field(default_factory=list)
    #: ``consent`` (the user declined) or ``policy``/``guard`` (a rule refused).
    denied: str | None = None
    #: request id -> ``{kind, status}``.
    hitl: dict[str, dict[str, str]] = field(default_factory=dict)
    #: The model's ``{"kind": "outcome"}`` entry from its claims block.
    label: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def restore(cls, state: Any) -> OutcomeLedger:
        if not isinstance(state, dict):
            return cls()
        known = {key: value for key, value in state.items() if key in cls.__dataclass_fields__}
        try:
            return cls(**known)
        except TypeError:
            return cls()

    def failed(self, tool: str, error_class: str | None) -> None:
        self.failures.append({"tool": tool, "error_class": error_class})

    def shown(self, evidence_id: str, tool_call_id: str, *, rows: bool) -> None:
        self.evidence[evidence_id] = tool_call_id
        if not rows:
            self.empty.append(evidence_id)

    def reset_evidence(self) -> None:
        """A role switch discards the results read under the earlier role."""
        self.evidence.clear()
        self.empty.clear()
        self.informational.clear()

    def observe_frame(self, frame: str) -> None:
        """Human-input requests and their answers, from the turn's own frames."""
        if frame.startswith("event: input_required\n"):
            payload = _frame_payload(frame)
            request_id = str(payload.get("request_id") or "")
            if request_id:
                self.hitl[request_id] = {
                    "kind": str(payload.get("kind") or "question"), "status": "pending",
                }
        elif frame.startswith("event: input_resolved\n"):
            payload = _frame_payload(frame)
            request_id = str(payload.get("request_id") or "")
            if request_id in self.hitl:
                self.hitl[request_id]["status"] = str(payload.get("status") or "resolved")


def _frame_payload(frame: str) -> dict[str, Any]:
    try:
        payload = json.loads(frame.split("data: ", 1)[1])
    except (IndexError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


_CLAIMS_BLOCK = re.compile(r"<claims>(.*?)</claims>\s*$", re.S)


def outcome_label(answer: str) -> dict[str, Any] | None:
    """The model's ``{"kind": "outcome", ...}`` entry in its claims block, checked
    for shape only (the facts decide whether it stands)."""
    match = _CLAIMS_BLOCK.search(answer or "")
    if match is None:
        return None
    try:
        raw = json.loads(match.group(1))
    except ValueError:
        return None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("kind") != "outcome":
            continue
        kind = item.get("outcome")
        reason = item.get("reason")
        if kind not in LABEL_REASONS or reason not in LABEL_REASONS[kind]:
            return None
        return {
            "outcome": kind, "reason": reason,
            "alternatives_shown": item.get("alternatives_shown") is True,
        }
    return None


def _claims(facts: dict[str, Any], ledger: OutcomeLedger) -> list[OutcomeClaim]:
    numeric = facts.get("numeric_consistency")
    if not isinstance(numeric, dict):
        return []
    claims: list[OutcomeClaim] = []
    for item in numeric.get("claims") or []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        evidence_id = item.get("evidence_id")
        value = item.get("value")
        kind = item.get("kind")
        claims.append(OutcomeClaim(
            text=item["text"][:200],
            value=float(value) if isinstance(value, int | float) and not isinstance(
                value, bool) else None,
            # The id the stream uses for that result (its tool call).
            evidence_id=ledger.evidence.get(evidence_id, evidence_id)
            if isinstance(evidence_id, str) else None,
            column=item.get("column") if isinstance(item.get("column"), str) else None,
            kind=kind if kind in {"derived", "hypothesis"} else "figure",
            supported=bool(item.get("supported")),
        ))
    return claims


def derive_outcome(
    finish_reason: str, ledger: OutcomeLedger, facts: dict[str, Any] | None = None,
) -> TurnOutcome:
    """The turn's outcome from its facts, for the finish reason the loop chose."""
    facts = facts or {}
    kind, reason, basis = _decide(finish_reason, ledger)
    shown = [ledger.evidence[key] for key in ledger.evidence if key not in ledger.empty]
    label = ledger.label or {}
    return TurnOutcome(
        kind=kind,
        reason=reason,
        basis=basis,
        evidence_ids=list(dict.fromkeys(ledger.evidence.values())),
        claims=_claims(facts, ledger),
        alternatives_shown=bool(
            kind in {OutcomeKind.REFUSE, OutcomeKind.LIMITATION}
            and label.get("alternatives_shown") and shown
        ),
        hitl=[HitlRecord(**record) for record in ledger.hitl.values()],
        action_receipts=[
            str(action["tool_call_id"]) for action in ledger.actions
            if action.get("ok") and action.get("tool_call_id")
        ],
        finish_reason=finish_reason,
    )


def stopped_outcome(finish_reason: str, reason: ReasonCode) -> dict[str, Any]:
    """The outcome of a run the route ended itself (its storage failed, or it was
    stopped), where the loop's facts are not at hand."""
    kind = OutcomeKind.CANCELLED if reason is ReasonCode.STOPPED else OutcomeKind.ERROR
    return TurnOutcome(kind=kind, reason=reason, finish_reason=finish_reason).model_dump(
        mode="json")


def _decide(
    finish_reason: str, ledger: OutcomeLedger
) -> tuple[OutcomeKind, ReasonCode, Basis]:
    failed_classes = {str(item.get("error_class") or "").upper() for item in ledger.failures}
    data_failed = any(item.get("tool") in DATA_TOOLS for item in ledger.failures)
    failed_action = next((item for item in reversed(ledger.actions) if not item.get("ok")), None)
    committed = any(item.get("ok") for item in ledger.actions)
    pending = any(record.get("status") == "pending" for record in ledger.hitl.values())

    if finish_reason == "cancelled":
        return OutcomeKind.CANCELLED, ReasonCode.STOPPED, "facts"
    if finish_reason == "consent_timeout":
        return OutcomeKind.CANCELLED, ReasonCode.HITL_EXPIRED, "facts"
    if pending:
        return OutcomeKind.CLARIFY, ReasonCode.HITL_PENDING, "facts"
    if finish_reason == "denied":
        return OutcomeKind.REFUSE, _refusal_reason(ledger, failed_classes), "facts"
    if finish_reason == "client_action_pending":
        return OutcomeKind.ACTION_DONE, ReasonCode.CLIENT_ACTION_PENDING, "facts"
    if finish_reason in _TERMINAL_ERRORS:
        return OutcomeKind.ERROR, _TERMINAL_ERRORS[finish_reason], "facts"
    if finish_reason == "error":
        if failed_action is not None:
            return OutcomeKind.ACTION_FAILED, _action_reason(failed_action), "facts"
        if failed_classes & ROLE_CLASSES:
            return OutcomeKind.REFUSE, ReasonCode.ROLE_NOT_PERMITTED, "facts"
        if ledger.failures:
            return OutcomeKind.ERROR, ReasonCode.TOOL_FAILED, "facts"
        return OutcomeKind.ERROR, ReasonCode.INTERNAL_ERROR, "facts"
    if finish_reason in {
        "required_capability_incomplete", "required_capability_unavailable",
        "data_evidence_incomplete",
    }:
        if failed_action is not None:
            return OutcomeKind.ACTION_FAILED, _action_reason(failed_action), "facts"
        if data_failed:
            return OutcomeKind.LIMITATION, ReasonCode.TOOL_FAILED, "facts"
        return OutcomeKind.LIMITATION, ReasonCode.REQUIRED_CAPABILITY_MISSING, "facts"
    if finish_reason in {"clarification", "out_of_scope"}:
        if ledger.not_answered in NOT_ANSWERED_OUTCOMES:
            kind, reason = NOT_ANSWERED_OUTCOMES[ledger.not_answered]
            return kind, reason, "facts"
        if data_failed:
            return OutcomeKind.LIMITATION, ReasonCode.TOOL_FAILED, "facts"
        return OutcomeKind.LIMITATION, ReasonCode.CATALOG_UNSUPPORTED, "unverified"
    # A finished answer ("stop") or a finish reason this map does not know.
    if committed:
        return OutcomeKind.ACTION_DONE, ReasonCode.ACTION_COMMITTED, "facts"
    if ledger.denied and not ledger.evidence:
        return OutcomeKind.REFUSE, _refusal_reason(ledger, failed_classes), "facts"
    label = ledger.label
    shown = [key for key in ledger.evidence if key not in ledger.empty]
    if label and label["outcome"] in {OutcomeKind.REFUSE.value, OutcomeKind.LIMITATION.value}:
        # A refusal or a stated limit the model labelled: it stands when the turn
        # showed no result, or showed one only as a labelled alternative.
        consistent = not shown or bool(label.get("alternatives_shown"))
        return (
            OutcomeKind(label["outcome"]), ReasonCode(label["reason"]),
            "model_label" if consistent else "unverified",
        )
    if shown:
        return OutcomeKind.ANSWER, ReasonCode.GOVERNED_DATA, "facts"
    if ledger.evidence:
        return OutcomeKind.LIMITATION, ReasonCode.NO_ROWS, "facts"
    informational = [name for name in ledger.informational if name not in _NEUTRAL_TOOLS]
    if informational:
        reason = (
            ReasonCode.AGENT_META if all(name in META_TOOLS for name in informational)
            else ReasonCode.PUBLIC_CONTENT
        )
        return OutcomeKind.ANSWER, reason, "facts"
    if data_failed:
        return OutcomeKind.LIMITATION, ReasonCode.TOOL_FAILED, "facts"
    if label:
        # The model says it answered, and no tool result is behind it.
        return OutcomeKind(label["outcome"]), ReasonCode(label["reason"]), "unverified"
    return OutcomeKind.ANSWER, ReasonCode.GENERAL, "unverified"


def _refusal_reason(ledger: OutcomeLedger, failed_classes: set[str]) -> ReasonCode:
    if ledger.denied == "consent":
        return ReasonCode.CONSENT_DECLINED
    if failed_classes & ROLE_CLASSES:
        return ReasonCode.ROLE_NOT_PERMITTED
    return ReasonCode.POLICY_DENIED


def _action_reason(action: dict[str, Any]) -> ReasonCode:
    if str(action.get("error_class") or "").upper() in INVALID_ARGUMENT_CLASSES:
        return ReasonCode.ACTION_INVALID_ARGS
    return ReasonCode.TOOL_FAILED
