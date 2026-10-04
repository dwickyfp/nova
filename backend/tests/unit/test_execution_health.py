"""Generic tool outcomes expose unknown evidence facts without trusting narrative claims."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.assistant.evidence_health import (
    EvidenceFacts,
    assess_evidence,
    attach_evidence_health,
    attach_execution_health,
    replay_evidence_health,
)
from app.modules.assistant.tools import ToolOutcome

NOW = datetime(2026, 10, 3, 10, tzinfo=UTC)


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)


def _strong():
    return assess_evidence(EvidenceFacts(
        semantic_grounding="published", semantic_view_id="sales", semantic_version=2,
        semantic_fingerprint="sales-v2", verified_query_hit=True, verified_query_id="verified-1",
        execution_status="success", coverage="complete", semantic_ambiguity="none",
        source_agreement="consistent", unsupported_numeric_claims=False,
        data_as_of=NOW - timedelta(minutes=1), max_age_seconds=600,
    ), assessed_at=NOW)


@pytest.mark.parametrize("tool", ["query_execute", "feature_lookup", "wait_agent", "list_agents"])
def test_success_does_not_promote_confidence_or_dates_to_verified_evidence(tool):
    outcome = ToolOutcome(
        ok=True, summary="Strong, fresh evidence; confidence 1.0",
        data={"confidence": 1.0, "as_of": NOW.isoformat(), "values": {"revenue": 42}},
        metadata={"semantic_grounding": "published", "data_freshness": "fresh"},
        table={"columns": ["revenue"], "rows": [[42]]},
    )
    result = attach_execution_health(outcome, tool_name=tool)
    health = replay_evidence_health(result.metadata["evidence_health"])
    assert health.label == "limited"
    assert health.facts.execution_status == "success"
    assert health.facts.semantic_grounding == ("none" if tool == "query_execute" else "unknown")
    assert health.facts.coverage == "unknown"
    assert health.data_freshness.status == "unknown"
    assert {
        "verified_query_hit", "source_agreement", "causal_strength", "data_freshness",
        "unsupported_numeric_claims", "semantic_ambiguity",
    }.issubset(health.unknown_signals)
    assert result.data["confidence"] == 1.0
    assert result.table == outcome.table
    assert "evidence_health" not in outcome.data


@pytest.mark.parametrize("fields, execution, coverage, ambiguity, label", [
    ({"ok": False, "error": "Engine denied the query"},
     "failed", "unknown", "unknown", "insufficient"),
    ({"ok": False, "error_class": "CLARIFICATION_REQUIRED"},
     "not_run", "unknown", "unresolved", "insufficient"),
    ({"ok": True, "metadata": {"partial": True}},
     "partial", "partial", "unknown", "limited"),
    ({"ok": True, "data": {"execution_status": "partial"}},
     "partial", "partial", "unknown", "limited"),
    ({"ok": True, "table": {"truncated": True}},
     "success", "truncated", "unknown", "limited"),
    ({"ok": True, "data": {"fields_truncated": True}},
     "success", "truncated", "unknown", "limited"),
    ({"ok": True, "trace_detail": {"preview_truncated": True}},
     "success", "truncated", "unknown", "limited"),
    ({"ok": True, "metadata": {"truncated": False}},
     "success", "complete", "unknown", "limited"),
    ({"ok": True, "metadata": {"truncated": "false", "partial": "true"}},
     "success", "unknown", "unknown", "limited"),
    ({"ok": False, "metadata": {"partial": True, "truncated": True}},
     "failed", "truncated", "unknown", "insufficient"),
])
def test_execution_flags_classify_failures_clarification_and_bounds(
    fields, execution, coverage, ambiguity, label,
):
    outcome = attach_execution_health(ToolOutcome(summary="", **fields), "feature_lookup")
    health = replay_evidence_health(outcome.evidence["health"])
    assert (
        health.facts.execution_status, health.facts.coverage,
        health.facts.semantic_ambiguity, health.label,
    ) == (execution, coverage, ambiguity, label)
    assert outcome.metadata["evidence_health"] == outcome.trace_detail["evidence_health"]
    assert outcome.metadata["evidence_health"] == outcome.evidence["evidence_health"]


@pytest.mark.parametrize("location", ["metadata", "evidence", "trace_detail", "data"])
def test_validated_assessment_survives_mapping_without_changing_its_clock(monkeypatch, location):
    import app.modules.assistant.evidence_health as module

    health = _strong()
    payload = health.model_dump(mode="json")
    before = deepcopy(payload)
    outcome = ToolOutcome(ok=True, summary="Executed", **{location: {"evidence_health": payload}})

    class NoClock(datetime):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError("Existing assessments must retain their persisted clock")

    monkeypatch.setattr(module, "datetime", NoClock)
    result = attach_execution_health(outcome, "semantic_view_query")
    assert result.metadata["evidence_health"] == before
    assert result.evidence["health"] == before
    assert payload == before


@pytest.mark.parametrize("change", [
    {"label": "strong"}, {"rule_version": "unsupported"}, {"credential": "private-secret"},
])
def test_invalid_assessments_are_replaced_and_never_expose_extra_fields(change):
    payload = assess_evidence(EvidenceFacts(), assessed_at=NOW).model_dump(mode="json")
    payload.update(change)
    result = attach_execution_health(
        ToolOutcome(ok=True, summary="Executed", metadata={"evidence_health": payload}),
        "wait_agent",
    )
    health = replay_evidence_health(result.metadata["evidence_health"])
    assert health.label == "limited" and health.facts.execution_status == "success"
    assert "private-secret" not in str(health.model_dump())
    assert "credential" not in result.metadata["evidence_health"]


@pytest.mark.parametrize("fields, execution, coverage", [
    ({"ok": False, "error": "private engine detail"}, "failed", "unknown"),
    ({"ok": False, "error_class": "CLARIFICATION_REQUIRED"}, "not_run", "unknown"),
    ({"ok": True, "table": {"truncated": True}}, "success", "truncated"),
    ({"ok": True, "metadata": {"partial": True}}, "partial", "partial"),
])
def test_execution_failure_and_bounds_override_an_incompatible_strong_assessment(
    fields, execution, coverage,
):
    outcome = attach_evidence_health(ToolOutcome(summary="", **fields), _strong())
    result = attach_execution_health(outcome, "semantic_query")
    health = replay_evidence_health(result.metadata["evidence_health"])
    assert health.label != "strong"
    assert health.facts.execution_status == execution
    assert health.facts.coverage == coverage
    assert "private engine detail" not in str(health.model_dump())


def test_conflicting_valid_assessments_do_not_select_the_more_favorable_claim():
    result = attach_execution_health(ToolOutcome(
        ok=True, summary="Executed",
        metadata={"evidence_health": _strong().model_dump(mode="json")},
        evidence={"health": assess_evidence(
            EvidenceFacts(execution_status="success"), assessed_at=NOW,
        ).model_dump(mode="json")},
    ), "semantic_query")
    assert result.metadata["evidence_health"]["label"] == "limited"


def test_mapping_preserves_non_object_data_and_other_envelope_fields():
    outcome = ToolOutcome(
        ok=True, summary="Executed", data=[{"amount": 42}],
        artifacts=[{"id": "artifact"}], warnings=["bounded"], state_patch={"metric": "amount"},
    )
    result = attach_execution_health(outcome, "configured_tool")
    assert result.data == outcome.data
    assert result.artifacts == outcome.artifacts
    assert result.warnings == outcome.warnings and result.state_patch == outcome.state_patch
    assert "health" in result.envelope(tool_name="configured_tool")["evidence"]


def test_disabled_feature_retains_original_outcome(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False)
    outcome = ToolOutcome(ok=True, summary="Executed")
    assert attach_execution_health(outcome, "query_execute") is outcome
