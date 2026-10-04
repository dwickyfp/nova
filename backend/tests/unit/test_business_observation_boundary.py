"""Canonical facts remain verifiable through the existing evidence and replay owners."""

import json

import pytest

from app.modules.assistant.answer_contract import check_numeric_answer
from app.modules.assistant.business_observation import (
    BusinessResultHookResult,
    provider_history_content,
)
from app.modules.assistant.intelligence import EvidenceTracker
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolOutcome
from app.modules.assistant.workflow_provenance import workflow_provenance


def observation():
    return {"kind": "investigation", "id": "canonical", "revision": 3,
            "target_metric": "net_revenue", "baseline_value": 10000,
            "current_value": 8700, "delta": -1300, "delta_pct": -.13, "residual": -120,
            "hypotheses": [{"label": "Enterprise", "contribution": -480,
                            "contribution_pct": 480 / 1300, "causal_status": "arithmetic"}]}


def test_canonical_contributions_are_numbers_for_final_verification_and_replay():
    evidence = EvidenceTracker()
    facts = evidence.add_business_result(observation(), workflow={"mission_id": "revenue"})
    assert check_numeric_answer("Enterprise contributes -480, about 36.9%.", question="",
                         tables=evidence.tables).accepted
    assert not check_numeric_answer("Enterprise contributes 999.", question="",
                             tables=evidence.tables).accepted
    snapshot = evidence.snapshot()
    restored = EvidenceTracker()
    restored.restore(snapshot)
    assert restored.add_business_result(observation(), workflow={"mission_id": "revenue"}) == facts
    assert restored.snapshot() == snapshot


def test_authorized_specialist_facts_keep_provenance_and_remap_numeric_references():
    child, root = EvidenceTracker(), EvidenceTracker()
    child.add_business_result(observation(), workflow={"mission_id": "revenue",
                              "run_id": "child-turn", "root_run_id": "root"})
    root.add("search_knowledge", "Packaged documentation")
    participant = {"status": "completed", "depth": 1, "agent_id": "finance",
                   "current_turn_id": "child-turn", "evidence": child.snapshot()}
    root.import_results(participant)
    canonical = next(item for item in root.items if item.tool == "canonical_investigation")
    assert canonical.metadata["workflow"]["run_id"] == "child-turn"
    assert canonical.metadata["canonical_business_result"]["numeric_evidence_refs"] == {
        "comparison": "evidence_2", "hypotheses": "evidence_3",
    }
    snapshot = root.snapshot()
    root.import_results(participant)
    assert root.snapshot() == snapshot
    untrusted = {**participant, "current_turn_id": "untrusted", "status": "running"}
    root.import_results(untrusted)
    assert root.snapshot() == snapshot


def test_provider_channels_exclude_internal_trace_and_legacy_confidence():
    result = BusinessResultHookResult(
        public_event={"mission_id": "mission"}, provider_observation=observation(),
        trace_metadata={"private_reasoning": "never sent"},
    )
    tool = ToolOutcome(ok=True, summary="Executed", data={"confidence": .8},
                       trace_detail={"session_id": "private"}, business_hook_result=result)
    envelope = tool.envelope(tool_name="semantic_query")
    assert "confidence" not in envelope["data"]
    assert envelope["canonical_business_result"] == observation()
    assert "private" not in json.dumps(envelope)
    historical = json.dumps({"tool": "semantic_query", "data": {"confidence": .8},
                             "evidence": {"health": {"label": "moderate"}}})
    assert "confidence" not in provider_history_content(historical)
    assert "moderate" in provider_history_content(historical)


def test_byte_bound_counts_utf8_and_workflow_is_owned_by_the_server_context():
    with pytest.raises(ValueError, match="byte bound"):
        BusinessResultHookResult(provider_observation={"label": "語" * 6000})
    context = LoopContext(user_name="alice", mission_id="actual", run_id="actual-run",
                          user={"mission_id": "forged", "run_id": "forged"})
    assert workflow_provenance(context) == {
        "mission_id": "actual", "run_id": "actual-run", "root_run_id": "actual-run",
    }
