"""Business response contracts. Call only after the owning service authorizes a read."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_serializer,
)

from app.modules.assistant.evidence_health import EvidenceHealth
from app.modules.intelligence.contracts import Contract


class PublicContract(Contract):
    # Every nested object has its own allowlist; unknown persisted fields stay private.
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


PRIVATE_WORKFLOW_FIELDS = frozenset(
    {
        "scope",
        "owner_scope",
        "current_binding",
        "run_bindings",
        "object_bindings",
        "historical_bindings",
        "session_id",
        "auth_session_id",
        "security_context_version",
        "security_version",
        "security_context",
        "authorization_binding",
        "binding",
        "bindings",
        "generation",
        "projection_cursor",
        "cursor",
        "cursors",
        "fence",
        "fences",
        "leases",
        "lease",
        "lease_id",
        "worker_id",
        "worker_name",
        "redis_key",
        "redis_keys",
        "operation_id",
        "idempotency_key",
        "request_digest",
        "digest",
        "dispatch_fence",
        "consent_previous_status",
        "consent_compensate",
        "last_operation_id",
        "last_operation_digest",
        "last_actor",
        "resume_operations",
        "turn_operations",
        "execution_contexts",
        "release_pins",
        "historical_object_refs",
        "provider_observation",
        "trace_metadata",
        "private_reasoning",
        "hidden_reasoning",
        "chain_of_thought",
        "credentials",
        "password",
        "encrypted_password",
        "api_key",
        "secret_key",
        "access_token",
        "refresh_token",
        "raw_attachment_bytes",
    }
)


def private_workflow_field(key: str) -> bool:
    key = key.lower()
    return key in PRIVATE_WORKFLOW_FIELDS or key.endswith(
        (
            "_digest",
            "_digests",
            "_fence",
            "_fences",
            "_lease",
            "_leases",
            "_lease_id",
            "_binding",
            "_bindings",
            "_scope",
            "_cursor",
            "_cursors",
            "_session_id",
            "_password",
            "_secret",
            "_token",
            "_api_key",
            "_credentials",
        )
    )


def _business_scalars(value):
    if isinstance(value, Mapping):
        return {key: item for key, item in value.items() if not private_workflow_field(str(key))}
    return value


def projection_input(value: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)


class PublicSemanticRef(PublicContract):
    view_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    fingerprint: str = Field(min_length=1, max_length=128)


class PublicWindow(PublicContract):
    start: datetime
    end: datetime


class PublicEvidenceRef(PublicContract):
    id: str = Field(min_length=1, max_length=128)
    source_type: Literal["query", "tool", "user_statement", "semantic", "outcome", "model"]
    source_id: str = Field(min_length=1, max_length=128)
    semantic: PublicSemanticRef | None = None
    method: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None
    evidence_health: EvidenceHealth | None = None


class PublicCanonicalEvidenceRef(PublicContract):
    id: str
    source_type: str
    source_id: str
    method: str
    semantic: PublicSemanticRef | None = None
    observed_at: str | None = None
    window_start: str | None = None
    window_end: str | None = None


class PublicCanonicalHypothesis(PublicContract):
    id: str | None = None
    label: str = Field(max_length=512)
    dimension: str | None = None
    contribution: float | None = None
    contribution_pct: float | None = None
    causal_status: Literal["arithmetic", "association", "supported_effect", "unknown"]
    confidence_label: str | None = None
    method: str | None = None
    next_test: str | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class PublicCanonicalDecomposition(PublicContract):
    dimension: str
    residual: str
    reconciled: bool
    baseline: str | None = None


class PublicRevisionRef(PublicContract):
    id: str
    revision: int = Field(ge=1)


class PublicObservationTruncation(PublicContract):
    truncated: bool
    omitted_hypotheses: int = Field(ge=0)
    omitted_evidence_refs: int = Field(ge=0)
    omitted_hypothesis_refs: int = Field(default=0, ge=0)


class PublicCanonicalObservation(PublicContract):
    kind: Literal["investigation"]
    id: str
    revision: int = Field(ge=1)
    status: str | None = None
    method: str | None = None
    semantic: PublicSemanticRef | None = None
    news: PublicRevisionRef | None = None
    comparison: PublicRevisionRef | None = None
    target_metric: str
    current_window: PublicWindow | None = None
    baseline_window: PublicWindow | None = None
    timezone: str | None = None
    baseline_value: float | None = None
    current_value: float | None = None
    delta: float | None = None
    delta_pct: float | None = None
    hypotheses: list[PublicCanonicalHypothesis] = Field(default_factory=list, max_length=10)
    residual: float | None = None
    decompositions: list[PublicCanonicalDecomposition] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list)
    evidence_refs: list[PublicCanonicalEvidenceRef] = Field(default_factory=list, max_length=20)
    truncation: PublicObservationTruncation | None = None
    numeric_evidence_refs: dict[Literal["comparison", "hypotheses"], str] = Field(
        default_factory=dict
    )

    @field_validator("numeric_evidence_refs", mode="before")
    @classmethod
    def numeric_references(cls, value):
        if isinstance(value, Mapping):
            return {key: item for key, item in value.items() if key in {"comparison", "hypotheses"}}
        return value


class PublicConfidence(PublicContract):
    dimension: str
    method: str
    label: Literal["low", "medium", "high", "insufficient"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class PublicHypothesis(PublicContract):
    id: str
    label: str = Field(max_length=512)
    contribution: float | None = None
    dimension: str | None = None
    causal_status: Literal["arithmetic", "association", "supported_effect", "unknown"]
    confidence: PublicConfidence
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    contradicting_ids: list[str] = Field(default_factory=list, max_length=100)
    next_test: str = Field(default="", max_length=1000)


class PublicTimelineEvent(PublicContract):
    at: datetime
    kind: str
    reference: str | None = None
    label: str | None = None
    before: float | None = None
    after: float | None = None
    evidence_id: str | None = None
    causal_status: Literal["arithmetic", "association", "supported_effect", "unknown"] | None = None


class PublicContribution(PublicContract):
    segment: str
    contribution: str


class PublicDecomposition(PublicContract):
    dimension: str
    change: str
    residual: str
    reconciled: bool
    baseline: str | None = None
    components: list[PublicContribution] = Field(default_factory=list, max_length=10)


class PublicRecord(PublicContract):
    id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class PublicInvestigation(PublicRecord):
    news_id: str
    news_revision: int = Field(default=1, ge=1)
    semantic: PublicSemanticRef
    hypotheses: list[PublicHypothesis] = Field(default_factory=list, max_length=50)
    evidence: list[PublicEvidenceRef] = Field(default_factory=list, max_length=100)
    timeline: list[PublicTimelineEvent] = Field(default_factory=list, max_length=100)
    residual: float
    decompositions: list[PublicDecomposition] = Field(default_factory=list, max_length=3)
    method: str
    status: Literal["complete", "insufficient", "pending"]


class PublicPolicyResult(PublicContract):
    decision: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]
    reason: str
    policy_id: str
    policy_revision: int = Field(ge=1)


class PublicDecisionOption(PublicContract):
    id: str
    scenario_kind: str = "unit-economics"
    scenario_version: int = Field(default=1, ge=1)
    description: str
    action_type: str
    assumptions: dict[str, float | str | bool] = Field(max_length=32)
    prediction: float
    lower_bound: float | None = None
    upper_bound: float | None = None
    cost: float
    effects: dict[str, float | str | bool] = Field(default_factory=dict, max_length=32)
    incremental_gross_profit: float | None = None
    risk: Literal["low", "medium", "high"]
    feasible: bool
    method: str
    run_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("assumptions", "effects", mode="before")
    @classmethod
    def business_values(cls, value):
        return _business_scalars(value)


class PublicDecision(PublicRecord):
    title: str
    agent_id: str
    thread_id: str | None = None
    investigation_id: str
    investigation_revision: int | None = None
    mission_id: str | None = None
    semantic: PublicSemanticRef
    target_metric: str
    baseline: float
    currency: str | None
    outcome_window: PublicWindow
    options: list[PublicDecisionOption] = Field(max_length=30)
    selected_option_id: str | None = None
    policy: PublicPolicyResult | None = None
    status: str
    evidence: list[PublicEvidenceRef] = Field(max_length=100)


class PublicLearningRef(PublicContract):
    kind: Literal["knowledge"]
    id: str
    revision: int = Field(ge=1)


class PublicOutcome(PublicRecord):
    action_ids: list[str] = Field(default_factory=list, max_length=100)
    learning_refs: list[PublicLearningRef] = Field(default_factory=list, max_length=100)
    decision_id: str
    decision_revision: int
    semantic: PublicSemanticRef
    target_metric: str | None = None
    currency: str | None = None
    window: PublicWindow
    predicted: float
    actual: float | None
    completeness: float = Field(ge=0, le=1)
    attribution: Literal["observed_after", "association", "supported_effect", "unknown"]
    status: Literal["pending", "missing_data", "complete", "superseded"]
    overlapping_decisions: list[str] = Field(default_factory=list)
    dimensions: dict[str, float | str | bool | None]
    evidence: list[PublicEvidenceRef] = Field(default_factory=list, max_length=100)

    @field_validator("dimensions", mode="before")
    @classmethod
    def business_values(cls, value):
        return _business_scalars(value)


class PublicFilter(PublicContract):
    field: str
    operator: str | None = "="
    value: str | int | float | bool | list[str | int | float | bool] | None


class PublicHaving(PublicContract):
    metric: str
    operator: str | None = ">"
    value: str | int | float | bool | None


class PublicTime(PublicContract):
    dimension: str
    grain: str | None = None
    range: str | None = None
    compare: str | None = None


class PublicOrder(PublicContract):
    field: str
    direction: str | None = "desc"


class PublicTransform(PublicContract):
    metric: str
    kind: str


class PublicTopN(PublicContract):
    n: int
    partition_by: list[str] | None = Field(default_factory=list)
    metric: str


class PublicUnresolvedConcept(PublicContract):
    text: str
    type_hint: str | None = "unknown"
    material: bool | None = True


class PublicSemanticPlan(PublicContract):
    metrics: list[str] | None = Field(default_factory=list)
    dimensions: list[str] | None = Field(default_factory=list)
    filters: list[PublicFilter] | None = Field(default_factory=list)
    named_filters: list[str] | None = Field(default_factory=list)
    time: PublicTime | None = None
    order_by: list[PublicOrder] | None = Field(default_factory=list)
    limit: int | None = None
    unresolved_concepts: list[PublicUnresolvedConcept] | None = Field(default_factory=list)
    having: list[PublicHaving] | None = Field(default_factory=list)
    transforms: list[PublicTransform] | None = Field(default_factory=list)
    top_n_per_group: PublicTopN | None = None

    @model_serializer(mode="wrap")
    def preserve_plan_shape(self, handler):
        # Adding default plan fields would change reviewed configuration/request digests.
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}


class PublicTimelineSource(PublicContract):
    kind: str
    plan: PublicSemanticPlan
    time_dimension: str
    time_column: str
    identity_column: str
    label_columns: list[str] = Field(default_factory=list, max_length=3)
    preceding_hours: int

    @field_serializer("plan")
    def reviewed_plan(self, value):
        return value.model_dump(mode="json", exclude_unset=True)


class PublicMonitorConfiguration(PublicContract):
    name: str
    agent_id: str
    semantic: PublicSemanticRef
    plan: PublicSemanticPlan
    value_column: str
    count_column: str | None = None
    completeness_column: str | None = None
    time_dimension: str
    driver_dimensions: list[str] = Field(default_factory=list, max_length=3)
    related_monitor_ids: list[str] = Field(default_factory=list, max_length=3)
    timeline_sources: list[PublicTimelineSource] = Field(default_factory=list, max_length=3)
    relative_threshold: float
    absolute_threshold: float
    minimum_samples: int
    baseline_weeks: int
    window_hours: int
    cooldown_hours: int
    cadence_minutes: int
    enabled: bool
    timezone: str

    @field_serializer("plan")
    def reviewed_plan(self, value):
        return value.model_dump(mode="json", exclude_unset=True)


class PublicMonitor(PublicRecord, PublicMonitorConfiguration):
    pass


class PublicNews(PublicRecord):
    monitor_id: str
    monitor_revision: int = Field(default=1, ge=1)
    title: str
    summary: str
    semantic: PublicSemanticRef
    window: PublicWindow
    before: float
    after: float
    change: float
    relative_change: float | None = None
    severity: str
    status: str
    confidence: PublicConfidence
    evidence: list[PublicEvidenceRef] = Field(max_length=100)
    investigation_id: str | None = None
    baseline_windows: list[PublicWindow] = Field(default_factory=list, max_length=2)
    status_note: str = ""


class PublicInvestigationContext(PublicContract):
    investigation: PublicInvestigation
    news: PublicNews
    monitor: PublicMonitor


class PublicAutomationCondition(PublicContract):
    metric: str
    operator: str
    value: float


class PublicAutomationConfiguration(PublicContract):
    agent_id: str
    semantic: PublicSemanticRef
    title: str
    prompt: str
    schedule_kind: Literal["cron", "interval"]
    schedule_expr: str
    timezone: str
    condition: PublicAutomationCondition | None = None
    delivery: Literal["studio"]
    enabled: bool


class PublicActionApproval(PublicContract):
    actor: str
    active_role: str
    approved_at: datetime


class PublicMonitorReceipt(PublicContract):
    monitor_id: str
    monitor_revision: int
    task_id: str | None = None
    schedule_enabled: bool


class PublicAutomationReceipt(PublicContract):
    automation_id: str
    schedule_enabled: bool
    delivery: Literal["studio"]


class PublicActionVerification(PublicContract):
    checked_at: datetime
    complete: bool
    reason: str


class PublicAction(PublicRecord):
    decision_id: str
    decision_revision: int
    option_id: str
    semantic: PublicSemanticRef
    adapter_id: Literal["monitor-v1", "automation-v1"]
    action_type: Literal["monitor", "automation"]
    configuration: PublicMonitorConfiguration | PublicAutomationConfiguration
    mission_id: str | None = None
    expected_effect: str
    policy: PublicPolicyResult
    approval: PublicActionApproval | None = None
    status: str
    dispatch_attempts: int
    compensation_attempts: int
    receipt: PublicMonitorReceipt | PublicAutomationReceipt | None = None
    compensation_receipt: PublicMonitorReceipt | PublicAutomationReceipt | None = None
    verification: PublicActionVerification | None = None
    compensation: PublicActionVerification | None = None
    error_code: str | None = None
    execution_current: bool = False
    consent_call_id: str | None = None


class PublicActionSummary(PublicContract):
    id: str
    revision: int
    status: str


class PublicDecisionEvent(PublicRecord):
    decision_id: str
    decision_revision: int
    event: str
    actor: str
    references: list[str] = Field(default_factory=list, max_length=100)


class PublicActionEvent(PublicRecord):
    action_id: str
    decision_id: str
    action_revision: int
    event: str
    actor: str


class PublicObservation(PublicRecord):
    monitor_id: str
    monitor_revision: int
    semantic: PublicSemanticRef
    window: PublicWindow
    value: float | None
    sample_count: int | None
    completeness: float | None
    evidence: list[PublicEvidenceRef] = Field(max_length=100)


class PublicComparison(PublicRecord):
    semantic: PublicSemanticRef
    monitor_id: str
    monitor_revision: int | None = None
    configuration: PublicMonitorConfiguration | None = None
    investigation_revision: int | None = None
    current_window: PublicWindow
    baseline_window: PublicWindow
    status: str
    reason: str | None = None
    observation_ids: list[str] = Field(default_factory=list, max_length=2)
    news_id: str | None = None
    investigation_id: str | None = None
    calendar_timezone: str | None = None


class PublicContextNode(PublicRecord):
    kind: str
    name: str
    reference_id: str
    reference_revision: int | None = None
    reference_agent_id: str | None = None
    reference_parent_id: str | None = None
    reference_run_id: str | None = None
    reference_fingerprint: str | None = None
    semantic: PublicSemanticRef | None = None
    state: str
    authority: str | None = None
    evidence: list[PublicEvidenceRef] = Field(default_factory=list, max_length=100)
    source_kind: str
    authority_basis: dict[str, str | int | bool] = Field(default_factory=dict, max_length=8)
    validity: str
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    freshness: str
    usage_count: int | None = None
    aliases: list[str] = Field(default_factory=list, max_length=32)
    contradictions: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("authority_basis", mode="before")
    @classmethod
    def business_values(cls, value):
        return _business_scalars(value)


class PublicContextEdge(PublicRecord):
    source: str
    target: str
    relationship: str
    state: str
    evidence: list[PublicEvidenceRef] = Field(default_factory=list, max_length=100)


def public_investigation(value: BaseModel | Mapping[str, Any]) -> PublicInvestigation:
    return PublicInvestigation.model_validate(projection_input(value))


def public_action(
    value: BaseModel | Mapping[str, Any],
    *,
    execution_current: bool | None = None,
) -> PublicAction:
    data = projection_input(value)
    if execution_current is not None:
        data["execution_current"] = execution_current
    # Raw historical records cannot establish a live consent binding.
    if not data.get("execution_current") or data.get("status") != "awaiting_consent":
        data["consent_call_id"] = None
    return PublicAction.model_validate(data)


PUBLIC_RECORD_MODELS = {
    "investigations": PublicInvestigation,
    "decisions": PublicDecision,
    "outcomes": PublicOutcome,
    "news": PublicNews,
    "monitors": PublicMonitor,
    "actions": PublicAction,
    "events": PublicDecisionEvent,
    "action_events": PublicActionEvent,
    "observations": PublicObservation,
    "comparisons": PublicComparison,
    "nodes": PublicContextNode,
    "edges": PublicContextEdge,
}


def public_canonical_record(kind: str, value: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    aliases = {
        "investigation": "investigations",
        "decision": "decisions",
        "action": "actions",
        "outcome": "outcomes",
        "monitor": "monitors",
    }
    kind = aliases.get(kind, kind)
    if kind == "actions":
        result = public_action(value)
    else:
        result = PUBLIC_RECORD_MODELS[kind].model_validate(projection_input(value))
    return result.model_dump(mode="json")


def public_investigation_context(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        kind: public_canonical_record(kind, value[kind])
        for kind in ("investigation", "news", "monitor")
    }
