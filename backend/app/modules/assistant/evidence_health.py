"""Explainable evidence assessments from validated execution facts, without inference."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from app.modules.assistant.tools import ToolOutcome

RULE_VERSION: Literal["evidence-health-v1"] = "evidence-health-v1"
Grounding = Literal["published", "draft", "none", "unknown"]
Execution = Literal["success", "partial", "failed", "not_run", "unknown"]
Coverage = Literal["complete", "truncated", "partial", "unknown"]
Ambiguity = Literal["none", "resolved", "unresolved", "unknown"]
Agreement = Literal["consistent", "conflicting", "unknown"]
CausalStrength = Literal["arithmetic", "association", "supported_effect", "unknown"]
Label = Literal["strong", "moderate", "limited", "insufficient"]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Evidence timestamps must include a timezone")
    return value.astimezone(UTC)


class EvidenceFacts(BaseModel):
    """Internal input. Only execution/provenance owners may establish these facts."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    semantic_grounding: Grounding = "unknown"
    semantic_view_id: str | None = Field(default=None, max_length=128)
    semantic_version: int | None = Field(default=None, ge=1, strict=True)
    semantic_fingerprint: str | None = Field(default=None, max_length=128)
    plan_source: Literal[
        "verified_query", "turn_planner", "model_planner", "compiled", "unknown"
    ] = "unknown"
    verified_query_hit: bool | None = Field(default=None, strict=True)
    verified_query_id: str | None = Field(default=None, max_length=128)
    execution_status: Execution = "unknown"
    coverage: Coverage = "unknown"
    semantic_ambiguity: Ambiguity = "unknown"
    source_agreement: Agreement = "unknown"
    causal_strength: CausalStrength = "unknown"
    unsupported_numeric_claims: bool | None = Field(default=None, strict=True)
    data_as_of: datetime | None = None
    max_age_seconds: int | None = Field(default=None, ge=0, strict=True)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("data_as_of")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            return _aware_utc(value)
        return value

    @field_validator("semantic_view_id", "semantic_fingerprint", "verified_query_id")
    @classmethod
    def safe_reference(cls, value: str | None) -> str | None:
        from app.modules.assistant.skills import contains_credential_shape

        if value is not None and (
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value)
            or contains_credential_shape(value)
        ):
            raise ValueError("Evidence references must be nonempty and credential-free")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def safe_references(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if len(value) > 128:
                raise ValueError("Evidence references exceed their bound")
            cls.safe_reference(value)
        return tuple(dict.fromkeys(values))


class DataFreshness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    status: Literal["fresh", "stale", "unknown"] = "unknown"
    data_as_of: datetime | None = None
    age_seconds: float | None = Field(default=None, ge=0)
    max_age_seconds: int | None = Field(default=None, ge=0)


class EvidenceHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1
    rule_version: Literal["evidence-health-v1"] = RULE_VERSION
    assessed_at: datetime
    label: Label
    facts: EvidenceFacts
    data_freshness: DataFreshness
    reasons: tuple[str, ...] = ()
    unknown_signals: tuple[str, ...] = ()

    @field_validator("assessed_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return _aware_utc(value)

    @model_validator(mode="after")
    def validate_assessment(self):
        derived = _assessment(self.facts, assessed_at=self.assessed_at)
        if any(getattr(self, key) != value for key, value in derived.items()):
            raise ValueError("Persisted Evidence Health disagrees with its execution facts")
        return self


def _assessment(facts: EvidenceFacts, *, assessed_at: datetime) -> dict:
    assessed_at = _aware_utc(assessed_at)
    freshness = DataFreshness(data_as_of=facts.data_as_of, max_age_seconds=facts.max_age_seconds)
    if facts.data_as_of is not None and facts.data_as_of <= assessed_at:
        age = (assessed_at - facts.data_as_of).total_seconds()
        freshness = DataFreshness(
            status=(
                "unknown" if facts.max_age_seconds is None
                else "fresh" if age <= facts.max_age_seconds else "stale"
            ),
            data_as_of=facts.data_as_of,
            age_seconds=age,
            max_age_seconds=facts.max_age_seconds,
        )
    unknown = [
        name for name in (
            "semantic_grounding", "execution_status", "coverage", "semantic_ambiguity",
            "source_agreement", "causal_strength",
        ) if getattr(facts, name) == "unknown"
    ]
    if facts.verified_query_hit is None:
        unknown.append("verified_query_hit")
    if facts.unsupported_numeric_claims is None:
        unknown.append("unsupported_numeric_claims")
    if freshness.status == "unknown":
        unknown.append("data_freshness")
    reasons = []
    if facts.execution_status in {"failed", "not_run", "unknown"}:
        reasons.append(f"execution_{facts.execution_status}")
    if facts.semantic_ambiguity == "unresolved":
        reasons.append("unresolved_semantic_ambiguity")
    if facts.unsupported_numeric_claims is True:
        reasons.append("unsupported_numeric_claims")
    insufficient = bool(reasons)
    if facts.execution_status == "partial":
        reasons.append("partial_execution")
    if facts.coverage != "complete":
        reasons.append(f"coverage_{facts.coverage}")
    if facts.source_agreement == "conflicting":
        reasons.append("conflicting_sources")
    if freshness.status != "fresh":
        reasons.append(f"freshness_{freshness.status}")
    if facts.semantic_grounding != "published":
        reasons.append(f"semantic_grounding_{facts.semantic_grounding}")
    if facts.semantic_ambiguity == "unknown":
        reasons.append("semantic_ambiguity_unknown")
    if facts.unsupported_numeric_claims is None:
        reasons.append("numeric_support_unknown")
    published_identity = (
        facts.semantic_grounding == "published" and facts.semantic_view_id is not None
        and facts.semantic_version is not None and facts.semantic_fingerprint is not None
    )
    if facts.semantic_grounding == "published" and not published_identity:
        reasons.append("incomplete_semantic_identity")
    verified = facts.verified_query_hit is True and facts.verified_query_id is not None
    if not verified:
        reasons.append(
            "verified_query_unknown" if facts.verified_query_hit is None
            else "missing_verified_query_reference" if facts.verified_query_hit
            else "no_verified_query_match"
        )
    strong = (
        published_identity and verified and facts.execution_status == "success"
        and facts.coverage == "complete" and freshness.status == "fresh"
        and facts.semantic_ambiguity in {"none", "resolved"}
        and facts.source_agreement != "conflicting" and facts.unsupported_numeric_claims is False
    )
    limited = (
        facts.execution_status == "partial" or facts.coverage in {"truncated", "partial", "unknown"}
        or facts.source_agreement == "conflicting" or freshness.status == "stale"
        or facts.semantic_grounding != "published" or not published_identity
        or facts.semantic_ambiguity == "unknown"
    )
    label: Label = (
        "insufficient" if insufficient else "strong" if strong else "limited" if limited
        else "moderate"
    )
    return {
        "assessed_at": assessed_at, "label": label, "facts": facts, "data_freshness": freshness,
        "reasons": tuple(reasons), "unknown_signals": tuple(unknown),
    }


def assess_evidence(facts: EvidenceFacts, *, assessed_at: datetime) -> EvidenceHealth:
    """The assessment clock is explicit and persisted; replay never consults now()."""
    return EvidenceHealth(**_assessment(facts, assessed_at=assessed_at))


def replay_evidence_health(payload: dict) -> EvidenceHealth:
    stored = EvidenceHealth.model_validate(payload)
    recomputed = assess_evidence(stored.facts, assessed_at=stored.assessed_at)
    if stored != recomputed:
        raise ValueError("Persisted Evidence Health disagrees with its execution facts")
    return stored


def attach_evidence_health(outcome: ToolOutcome, health: EvidenceHealth) -> ToolOutcome:
    """Keep the same assessment in the provider, durable evidence, and trace projections."""
    from dataclasses import replace

    payload = health.model_dump(mode="json")
    data = outcome.data
    if isinstance(data, dict):
        data = {**data, "evidence_health": payload}
    elif data is None:
        data = {"summary": outcome.summary, "evidence_health": payload}
    return replace(
        outcome, data=data,
        evidence={**(outcome.evidence or {}), "health": payload, "evidence_health": payload},
        metadata={**outcome.metadata, "evidence_health": payload},
        trace_detail={**(outcome.trace_detail or {}), "evidence_health": payload},
    )


def attach_execution_health(outcome: ToolOutcome, tool_name: str) -> ToolOutcome:
    """Map execution flags without promoting tool prose or confidence into evidence.

    The loop calls this before processing successes, failures, or clarification.
    Detailed assessments supplied by execution owners retain their original clock.
    """
    from app.core.config import settings

    if not settings.STUDIO_BUSINESS_WORKFLOW_ENABLED:
        return outcome

    sources = [
        value for value in (
            outcome.metadata, outcome.evidence, outcome.trace_detail, outcome.data, outcome.table,
        ) if isinstance(value, dict)
    ]
    clarification = outcome.error_class == "CLARIFICATION_REQUIRED"
    failed = not outcome.ok or outcome.error is not None
    partial = any(
        source.get("partial") is True or source.get("execution_status") == "partial"
        for source in sources
    )
    truncated = any(
        source.get(key) is True for source in sources
        for key in ("truncated", "preview_truncated", "rows_truncated", "fields_truncated")
    )
    execution: Execution = (
        "not_run" if clarification else "failed" if failed else "partial" if partial else "success"
    )
    coverage: Coverage = (
        "truncated" if truncated else "partial" if partial else "complete"
        if any(source.get("truncated") is False for source in sources) else "unknown"
    )

    # External/custom tool names are prefixed by their registry adapters.
    # Only Nova execution owners may supply semantic assessment facts.
    assessment_sources = (
        sources if tool_name in {
            "semantic_query", "semantic_view_query", "feature_lookup",
            "context_graph", "decision_lab", "wait_agent", "list_agents",
        } else []
    )
    assessments = []
    for source in assessment_sources:
        for key in ("evidence_health", "health"):
            candidate = source.get(key)
            if isinstance(candidate, EvidenceHealth):
                candidate = candidate.model_dump(mode="json")
            if not isinstance(candidate, dict):
                continue
            try:
                health = replay_evidence_health(candidate)
            except (ValueError, TypeError):
                continue
            assessments.append(health)
    if assessments and all(health == assessments[0] for health in assessments):
        health = assessments[0]
        compatible = (
            (not failed or health.facts.execution_status in {"failed", "not_run"})
            and (failed or health.facts.execution_status in {"success", "partial", "unknown"})
            and (not clarification or (
                health.facts.execution_status == "not_run"
                and health.facts.semantic_ambiguity == "unresolved"
            ))
            and (not truncated or health.facts.coverage == "truncated")
            and (not partial or health.facts.execution_status == "partial")
        )
        if compatible:
            return attach_evidence_health(outcome, health)

    facts = EvidenceFacts(
        execution_status=execution,
        coverage=coverage,
        semantic_grounding="none" if tool_name == "query_execute" else "unknown",
        semantic_ambiguity="unresolved" if clarification else "unknown",
    )
    return attach_evidence_health(outcome, assess_evidence(facts, assessed_at=datetime.now(UTC)))
