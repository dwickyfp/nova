"""``derive_outcome``: one outcome per loop branch, decided from recorded facts."""

from __future__ import annotations

import pytest

from app.modules.assistant.turn_outcome import (
    KIND_REASONS,
    OutcomeKind,
    OutcomeLedger,
    TurnOutcome,
    derive_outcome,
    outcome_label,
)


def _ledger(**fields) -> OutcomeLedger:
    return OutcomeLedger(**fields)


def _pair(outcome: TurnOutcome) -> tuple[str, str]:
    return outcome.kind.value, outcome.reason.value


@pytest.mark.parametrize(("finish_reason", "ledger", "expected"), [
    ("cancelled", _ledger(), ("cancelled", "stopped")),
    ("consent_timeout", _ledger(), ("cancelled", "hitl_expired")),
    ("timeout", _ledger(), ("error", "timeout")),
    ("token_budget", _ledger(), ("error", "budget_exhausted")),
    ("iteration_cap", _ledger(), ("error", "budget_exhausted")),
    ("context_overflow", _ledger(), ("error", "context_overflow")),
    ("error", _ledger(), ("error", "internal_error")),
    ("error", _ledger(failures=[{"tool": "semantic_query", "error_class": "X"}]),
     ("error", "tool_failed")),
    ("error", _ledger(failures=[{"tool": "query_execute", "error_class": "PERMISSION_DENIED"}]),
     ("refuse", "role_not_permitted")),
    ("denied", _ledger(denied="consent"), ("refuse", "consent_declined")),
    ("denied", _ledger(denied="policy"), ("refuse", "policy_denied")),
    ("client_action_pending", _ledger(), ("action_done", "client_action_pending")),
    ("clarification", _ledger(not_answered="CATALOG_UNSUPPORTED"),
     ("limitation", "catalog_unsupported")),
    ("clarification", _ledger(not_answered="OUTSIDE_DATA_WINDOW"),
     ("limitation", "outside_data_window")),
    ("clarification", _ledger(not_answered="CLARIFICATION_REQUIRED"),
     ("clarify", "ambiguous_term")),
    ("out_of_scope", _ledger(failures=[{"tool": "semantic_query", "error_class": None}]),
     ("limitation", "tool_failed")),
    ("required_capability_incomplete",
     _ledger(actions=[{"tool_call_id": "a", "tool": "schedule_automation", "ok": False,
                       "error_class": "INVALID_TOOL_ARGUMENTS"}]),
     ("action_failed", "action_invalid_args")),
    ("required_capability_incomplete",
     _ledger(actions=[{"tool_call_id": "a", "tool": "schedule_automation", "ok": False,
                       "error_class": "AUDIT_UNAVAILABLE"}]),
     ("action_failed", "tool_failed")),
    ("required_capability_incomplete",
     _ledger(failures=[{"tool": "semantic_query", "error_class": None}]),
     ("limitation", "tool_failed")),
    ("data_evidence_incomplete", _ledger(), ("limitation", "required_capability_missing")),
    ("stop", _ledger(evidence={"evidence_1": "c1"}), ("answer", "governed_data")),
    ("stop", _ledger(evidence={"evidence_1": "c1"}, empty=["evidence_1"]),
     ("limitation", "no_rows")),
    ("stop", _ledger(actions=[{"tool_call_id": "a", "tool": "schedule_automation",
                               "ok": True, "error_class": None}]),
     ("action_done", "action_committed")),
    ("stop", _ledger(denied="guard"), ("refuse", "policy_denied")),
    ("stop", _ledger(informational=["describe_agent"]), ("answer", "agent_meta")),
    ("stop", _ledger(informational=["web_search", "load_skill"]), ("answer", "public_content")),
    ("stop", _ledger(hitl={"r1": {"kind": "form", "status": "pending"}}),
     ("clarify", "hitl_pending")),
])
def test_each_branch_has_one_outcome(finish_reason, ledger, expected):
    outcome = derive_outcome(finish_reason, ledger)
    assert _pair(outcome) == expected
    assert outcome.basis == "facts"
    assert outcome.finish_reason == finish_reason
    assert outcome.reason in KIND_REASONS[outcome.kind]


def test_a_text_only_answer_is_unverified():
    outcome = derive_outcome("stop", _ledger())
    assert _pair(outcome) == ("answer", "general")
    assert outcome.basis == "unverified"


def test_a_label_without_results_is_accepted_as_the_models():
    ledger = _ledger(label={"outcome": "refuse", "reason": "source_not_available",
                            "alternatives_shown": False})
    outcome = derive_outcome("stop", ledger)
    assert _pair(outcome) == ("refuse", "source_not_available")
    assert outcome.basis == "model_label"
    assert not outcome.alternatives_shown


def test_a_refusal_with_a_labelled_alternative_shows_it():
    """D2: refused source, then a labelled figure from the agent's own data."""
    ledger = _ledger(
        evidence={"evidence_1": "call-1"},
        label={"outcome": "refuse", "reason": "source_not_available",
               "alternatives_shown": True},
    )
    outcome = derive_outcome("stop", ledger)
    assert _pair(outcome) == ("refuse", "source_not_available")
    assert outcome.basis == "model_label"
    assert outcome.alternatives_shown
    assert outcome.evidence_ids == ["call-1"]


def test_a_refusal_label_beside_results_it_does_not_call_alternatives_is_unverified():
    ledger = _ledger(
        evidence={"evidence_1": "call-1"},
        label={"outcome": "limitation", "reason": "catalog_unsupported",
               "alternatives_shown": False},
    )
    outcome = derive_outcome("stop", ledger)
    assert outcome.kind is OutcomeKind.LIMITATION
    assert outcome.basis == "unverified"
    assert not outcome.alternatives_shown


def test_an_alternatives_claim_without_results_is_not_believed():
    ledger = _ledger(label={"outcome": "refuse", "reason": "source_not_available",
                            "alternatives_shown": True})
    assert not derive_outcome("stop", ledger).alternatives_shown


def test_facts_outrank_the_label():
    ledger = _ledger(not_answered="OUTSIDE_DATA_WINDOW",
                     label={"outcome": "answer", "reason": "governed_data",
                            "alternatives_shown": False})
    outcome = derive_outcome("clarification", ledger)
    assert _pair(outcome) == ("limitation", "outside_data_window")
    assert outcome.basis == "facts"


def test_an_answer_label_with_no_result_is_unverified():
    ledger = _ledger(label={"outcome": "answer", "reason": "governed_data",
                            "alternatives_shown": False})
    outcome = derive_outcome("stop", ledger)
    assert outcome.basis == "unverified"


def test_claims_carry_the_stream_ids_and_stated_values():
    ledger = _ledger(evidence={"evidence_1": "call-1"})
    facts = {"numeric_consistency": {"claims": [
        {"text": "Rp 1,2 juta", "evidence_id": "evidence_1", "column": "revenue",
         "supported": True, "value": 1200000.0, "kind": "cell"},
        {"text": "15%", "evidence_id": None, "column": None, "supported": False,
         "kind": "derived"},
    ]}}
    outcome = derive_outcome("stop", ledger, facts)
    first, second = outcome.claims
    assert (first.evidence_id, first.value, first.kind, first.supported) == (
        "call-1", 1200000.0, "figure", True)
    assert (second.evidence_id, second.kind, second.supported) == (None, "derived", False)


def test_hitl_requests_are_read_from_the_turns_frames():
    ledger = OutcomeLedger()
    ledger.observe_frame('event: input_required\ndata: {"request_id":"r1","kind":"choice"}\n\n')
    ledger.observe_frame('event: input_resolved\ndata: {"request_id":"r1","status":"answered"}\n\n')
    ledger.observe_frame('event: input_resolved\ndata: {"request_id":"zz","status":"answered"}\n\n')
    ledger.observe_frame("event: input_required\ndata: not json\n\n")
    outcome = derive_outcome("stop", ledger)
    assert [record.model_dump() for record in outcome.hitl] == [
        {"kind": "choice", "status": "answered"}]


def test_a_role_switch_forgets_results_read_under_the_earlier_role():
    ledger = _ledger(evidence={"evidence_1": "c1"}, informational=["describe_agent"])
    ledger.reset_evidence()
    assert derive_outcome("stop", ledger).basis == "unverified"


def test_the_ledger_survives_a_checkpoint():
    ledger = _ledger(not_answered="CATALOG_UNSUPPORTED", evidence={"evidence_1": "c1"})
    assert OutcomeLedger.restore(ledger.snapshot()) == ledger
    assert OutcomeLedger.restore(None) == OutcomeLedger()
    assert OutcomeLedger.restore({"unknown": 1}) == OutcomeLedger()


@pytest.mark.parametrize(("block", "expected"), [
    ('<claims>[{"kind": "outcome", "outcome": "refuse", "reason": "write_not_allowed"}]</claims>',
     {"outcome": "refuse", "reason": "write_not_allowed", "alternatives_shown": False}),
    ('<claims>[{"text": "5", "kind": "cell"}, {"kind": "outcome", "outcome": "limitation", '
     '"reason": "general_knowledge", "alternatives_shown": true}]</claims>',
     {"outcome": "limitation", "reason": "general_knowledge", "alternatives_shown": True}),
    # A reason that does not belong to the kind, or a kind a model may not claim.
    ('<claims>[{"kind": "outcome", "outcome": "refuse", "reason": "no_rows"}]</claims>', None),
    ('<claims>[{"kind": "outcome", "outcome": "action_done", "reason": "action_committed"}]'
     "</claims>", None),
    ("<claims>not json</claims>", None),
    ("No block at all.", None),
])
def test_the_models_label_is_read_for_shape_only(block, expected):
    assert outcome_label("Answer. " + block) == expected


def test_every_outcome_serializes_with_the_old_finish_reason():
    payload = derive_outcome("stop", _ledger(evidence={"e": "c"})).model_dump(mode="json")
    assert payload["schema_version"] == 1
    assert payload["finish_reason"] == "stop"
    assert set(payload) == {
        "schema_version", "kind", "reason", "basis", "evidence_ids", "claims",
        "alternatives_shown", "hitl", "action_receipts", "finish_reason",
    }


def test_a_run_the_route_ended_says_why():
    """rebench mcp-005: the run's progress could not be saved while a call ran."""
    from app.modules.assistant.turn_outcome import ReasonCode, stopped_outcome

    interrupted = stopped_outcome("interrupted", ReasonCode.PERSISTENCE_UNAVAILABLE)
    assert (interrupted["kind"], interrupted["reason"]) == ("error", "persistence_unavailable")
    assert interrupted["finish_reason"] == "interrupted"
    cancelled = stopped_outcome("cancelled", ReasonCode.STOPPED)
    assert (cancelled["kind"], cancelled["reason"]) == ("cancelled", "stopped")
