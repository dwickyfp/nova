"""Versioned, credential-free contracts shared by the intelligence lifecycle."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.assistant.security import session_security

_CREDENTIAL_TOKEN = re.compile(
    r"\b(?:sk-|gh[pousr]_)[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|://[^/\s:@]+:[^/\s@]+@"
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def reject_credentials(self):
        from app.modules.assistant.skills import contains_credential_shape

        text = self.model_dump_json()
        if contains_credential_shape(text.replace('":', '"=')) or _CREDENTIAL_TOKEN.search(text):
            raise ValueError("Intelligence records cannot contain credentials")
        return self


class KnowledgeState(StrEnum):
    HYPOTHESIS = "HYPOTHESIS"
    INFERRED = "INFERRED"
    VERIFIED = "VERIFIED"
    CONFLICTED = "CONFLICTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class Scope(Contract):
    principal: str = Field(min_length=1, max_length=128)
    active_role: str = Field(min_length=1, max_length=128)
    security_context_version: int = Field(ge=1)
    session_id: str | None = Field(default=None, max_length=128)

    @classmethod
    def from_user(cls, user: dict) -> Scope:
        context = session_security(user)
        return cls(
            principal=context.principal,
            active_role=context.active_role,
            security_context_version=context.security_context_version,
            session_id=context.session_id,
        )


class SemanticRef(Contract):
    view_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    fingerprint: str = Field(min_length=1, max_length=128)


class EvidenceRef(Contract):
    id: str = Field(min_length=1, max_length=128)
    source_type: Literal["query", "tool", "user_statement", "semantic", "outcome", "model"]
    source_id: str = Field(min_length=1, max_length=128)
    scope: Scope
    semantic: SemanticRef | None = None
    relations: list[str] = Field(default_factory=list, max_length=32)
    method: str = Field(min_length=1, max_length=128)
    digest: str = Field(min_length=64, max_length=64)
    observed_at: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None
    semantic_plan: dict[str, Any] | None = None


class Confidence(Contract):
    dimension: Literal[
        "semantic",
        "detection",
        "statistical",
        "causal",
        "knowledge",
        "recommendation",
        "outcome_attribution",
    ]
    method: str = Field(min_length=1, max_length=128)
    value: float | None = Field(default=None, ge=0, le=1)
    label: Literal["low", "medium", "high", "insufficient"] = "insufficient"
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class Window(Contract):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def ordered(self):
        if self.start.tzinfo is None or self.end.tzinfo is None or self.end <= self.start:
            raise ValueError("Windows require ordered timezone-aware timestamps")
        self.start = self.start.astimezone(UTC)
        self.end = self.end.astimezone(UTC)
        return self


class Record(Contract):
    id: str = Field(min_length=1, max_length=64)
    revision: int = Field(default=1, ge=1)
    scope: Scope
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ContextNode(Record):
    kind: Literal[
        "entity",
        "dataset",
        "field",
        "metric",
        "rule",
        "semantic_view",
        "system",
        "deployment",
        "incident",
        "agent",
        "decision",
        "outcome",
        "news",
        "investigation",
        "policy",
        "domain",
    ]
    name: str = Field(min_length=1, max_length=256)
    reference_id: str = Field(min_length=1, max_length=128)
    reference_revision: int | None = Field(default=None, ge=1)
    reference_agent_id: str | None = Field(default=None, max_length=64)
    semantic: SemanticRef | None = None
    state: KnowledgeState = KnowledgeState.HYPOTHESIS
    authority: str | None = Field(default=None, max_length=128)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=100)


class ContextEdge(Record):
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    relationship: str = Field(min_length=1, max_length=64, pattern=r"^[a-z_]+$")
    state: KnowledgeState = KnowledgeState.HYPOTHESIS
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=100)


class MetricObservation(Record):
    monitor_id: str
    monitor_revision: int = Field(default=1, ge=1)
    semantic: SemanticRef
    window: Window
    value: float | None
    sample_count: int = Field(ge=0)
    completeness: float | None = Field(default=None, ge=0, le=1)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=100)


class TimelineSource(Contract):
    kind: Literal["deployment", "incident", "inventory", "support", "business_event"]
    plan: dict[str, Any]
    time_dimension: str = Field(min_length=1, max_length=128)
    time_column: str = Field(min_length=1, max_length=128)
    identity_column: str = Field(min_length=1, max_length=128)
    label_columns: list[str] = Field(default_factory=list, max_length=3)
    preceding_hours: int = Field(default=24, ge=0, le=168)


class MonitorConfiguration(Contract):
    name: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=64)
    semantic: SemanticRef
    plan: dict[str, Any]
    value_column: str = Field(min_length=1, max_length=128)
    count_column: str = Field(min_length=1, max_length=128)
    completeness_column: str | None = Field(default=None, max_length=128)
    time_dimension: str = Field(min_length=1, max_length=128)
    driver_dimensions: list[str] = Field(default_factory=list, max_length=3)
    related_monitor_ids: list[str] = Field(default_factory=list, max_length=3)
    timeline_sources: list[TimelineSource] = Field(default_factory=list, max_length=3)
    relative_threshold: float = Field(default=0.1, gt=0)
    absolute_threshold: float = Field(default=0, ge=0)
    minimum_samples: int = Field(default=30, ge=2)
    baseline_weeks: int = Field(default=4, ge=2, le=12)
    window_hours: int = Field(default=24, ge=1, le=168)
    cooldown_hours: int = Field(default=24, ge=1, le=720)
    cadence_minutes: int = Field(default=15, ge=15)
    enabled: bool = False
    timezone: str = Field(default="Asia/Jakarta", max_length=128)

    @model_validator(mode="after")
    def valid_timezone(self):
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("Use a named IANA timezone") from exc
        return self


class Monitor(Record, MonitorConfiguration):
    pass


class NewsItem(Record):
    monitor_id: str
    monitor_revision: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=256)
    summary: str = Field(max_length=4000)
    semantic: SemanticRef
    window: Window
    before: float
    after: float
    change: float
    relative_change: float | None = None
    severity: Literal["info", "warning", "critical"]
    status: Literal["open", "investigating", "resolved", "dismissed"] = "open"
    dedup_key: str
    confidence: Confidence
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=100)
    investigation_id: str | None = None
    baseline_windows: list[Window] = Field(default_factory=list, max_length=2)
    status_operation_id: str | None = None
    status_request_digest: str | None = None
    status_note: str = Field(default="", max_length=2000)


class Hypothesis(Contract):
    id: str
    label: str = Field(max_length=512)
    contribution: float | None = None
    dimension: str | None = None
    causal_status: Literal["arithmetic", "association", "supported_effect", "unknown"]
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    contradicting_ids: list[str] = Field(default_factory=list, max_length=100)
    next_test: str = Field(default="", max_length=1000)


class Investigation(Record):
    news_id: str
    news_revision: int = Field(default=1, ge=1)
    semantic: SemanticRef
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=50)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    timeline: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    residual: float
    decompositions: list[dict[str, Any]] = Field(default_factory=list, max_length=3)
    method: str
    status: Literal["complete", "insufficient", "pending"]


class DecisionOption(Contract):
    id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    action_type: Literal[
        "recommendation", "inventory_transfer", "campaign_budget", "rollback", "discount", "spend"
    ]
    assumptions: dict[str, float | str | bool]
    prediction: float
    lower_bound: float | None = None
    upper_bound: float | None = None
    cost: float = Field(ge=0)
    incremental_gross_profit: float
    risk: Literal["low", "medium", "high"]
    feasible: bool
    method: str
    run_id: str | None = None
    evidence_ids: list[str] = Field(min_length=1, max_length=100)


class PolicyResult(Contract):
    decision: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]
    reason: str
    policy_id: str
    policy_revision: int = Field(ge=1)
    context_digest: str


class Decision(Record):
    title: str = Field(min_length=1, max_length=256)
    agent_id: str
    thread_id: str | None = None
    learning_enabled: bool = True
    investigation_id: str
    semantic: SemanticRef
    target_metric: str
    baseline: float
    currency: str = Field(default="IDR", min_length=3, max_length=3)
    outcome_window: Window
    options: list[DecisionOption] = Field(min_length=1, max_length=30)
    selected_option_id: str | None = None
    last_operation_id: str | None = None
    request_digest: str
    last_operation_digest: str
    policy: PolicyResult | None = None
    status: Literal[
        "proposed",
        "awaiting_approval",
        "approved",
        "denied",
        "selected",
        "observing",
        "evaluated",
        "cancelled",
        "superseded",
    ] = "proposed"
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=100)


class DecisionEvent(Record):
    decision_id: str
    decision_revision: int = Field(ge=1)
    event: Literal[
        "created",
        "selected",
        "approved",
        "denied",
        "observed",
        "cancelled",
        "superseded",
        "evaluated",
    ]
    actor: str
    context_digest: str
    references: list[str] = Field(default_factory=list, max_length=100)


class Outcome(Record):
    decision_id: str
    decision_revision: int
    semantic: SemanticRef
    target_metric: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    window: Window
    predicted: float
    actual: float | None
    completeness: float = Field(ge=0, le=1)
    attribution: Literal["observed_after", "association", "supported_effect", "unknown"]
    status: Literal["pending", "missing_data", "complete", "superseded"]
    overlapping_decisions: list[str] = Field(default_factory=list)
    dimensions: dict[str, float | str | bool | None]
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=100)
