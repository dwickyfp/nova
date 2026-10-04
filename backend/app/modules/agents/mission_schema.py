"""Public contracts and StarRocks metadata for Studio business work."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from app.modules.intelligence.contracts import Contract, Scope, SemanticRef, Window
from app.modules.intelligence.evidence import EvidenceEnvelope


class WorkIntent(StrEnum):
    ANSWER = "ANSWER"
    ANALYZE = "ANALYZE"
    INVESTIGATE = "INVESTIGATE"
    PLAN = "PLAN"
    RESEARCH = "RESEARCH"
    ACT = "ACT"


class StageKind(StrEnum):
    INVESTIGATE = "investigate"
    EVIDENCE = "evidence"
    SCENARIOS = "scenarios"
    DECIDE = "decide"
    APPROVE = "approve"
    EXECUTE = "execute"
    VERIFY = "verify"
    OBSERVE = "observe"
    IMPROVE = "improve"


STAGE_LABELS = {
    StageKind.INVESTIGATE: "Investigate",
    StageKind.EVIDENCE: "Inspect evidence",
    StageKind.SCENARIOS: "Compare scenarios",
    StageKind.DECIDE: "Decide",
    StageKind.APPROVE: "Approve",
    StageKind.EXECUTE: "Execute",
    StageKind.VERIFY: "Verify",
    StageKind.OBSERVE: "Observe outcome",
    StageKind.IMPROVE: "Propose improvements",
}


class MissionStage(Contract):
    kind: StageKind
    label: str
    status: Literal["planned", "running", "completed", "blocked", "cancelled"] = "planned"
    source_refs: list[str] = Field(default_factory=list, max_length=100)


class ObjectRef(Contract):
    kind: Literal["investigation", "decision", "action", "outcome"]
    id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)


class MissionOwnerScope(Contract):
    principal: str = Field(min_length=1, max_length=128)
    active_role: str = Field(min_length=1, max_length=128)


class MissionBinding(Contract):
    scope: Scope
    generation: int = Field(default=1, ge=1)
    bound_at: datetime


class ContinuationDecision(Contract):
    mode: Literal["continue", "new", "none"]
    reason: Literal[
        "replay",
        "explicit_continue",
        "explicit_new",
        "semantic_anchor",
        "canonical_reference",
        "screen_follow_up",
        "different_semantic_target",
        "unrelated_or_ambiguous",
        "lightweight_answer",
    ]
    mission_id: str | None = None


class SemanticAnchor(Contract):
    semantic: SemanticRef
    metrics: list[str] = Field(min_length=1, max_length=32)
    filter_population_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def bounded_metrics(self):
        if any(not name or len(name) > 128 for name in self.metrics):
            raise ValueError("Invalid metric name")
        self.metrics = sorted(set(self.metrics))
        return self


class ExecutionTimeContext(Contract):
    execution_now: datetime
    timezone: str = Field(min_length=1, max_length=64)
    semantic: SemanticRef
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    current_window: Window | None = None
    baseline_window: Window | None = None
    warnings: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def concrete_context(self):
        if self.execution_now.tzinfo is None:
            raise ValueError("Execution time must be timezone-aware")
        ZoneInfo(self.timezone)
        if any(len(warning) > 256 for warning in self.warnings):
            raise ValueError("Execution warning exceeds the bound")
        if self.baseline_window and not self.current_window:
            raise ValueError("A baseline requires a current window")
        return self


class MissionExecutionContext(ExecutionTimeContext):
    run_id: str = Field(min_length=1, max_length=64)
    tool_call_id: str = Field(min_length=1, max_length=128)
    context_digest: str = Field(min_length=64, max_length=64)


class MissionReleasePin(Contract):
    agent_id: str = Field(min_length=1, max_length=128)
    manifest_id: str = Field(min_length=1, max_length=128)
    version_id: str = Field(min_length=1, max_length=128)
    fingerprint: str = Field(min_length=64, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)


class ResumeReceipt(Contract):
    request_digest: str
    binding: MissionBinding
    mission_revision: int = Field(ge=1)


class InvestigationRequirements(Contract):
    required_inputs: list[str] = Field(min_length=1, max_length=16)
    established: EvidenceEnvelope

    @model_validator(mode="after")
    def bounded_requirements(self):
        if any(not name or len(name) > 128 for name in self.required_inputs):
            raise ValueError("Invalid investigation requirement")
        self.required_inputs = sorted(set(self.required_inputs))
        return self


class Mission(Contract):
    mission_id: str
    thread_id: str
    agent_id: str | None = Field(default=None, min_length=1, max_length=128)
    scope: Scope
    objective: str = Field(min_length=1, max_length=4000)
    work_intent: WorkIntent
    status: Literal["planned", "running", "completed", "blocked", "cancelling", "cancelled"]
    revision: int = Field(ge=1)
    operation_id: str
    request_digest: str = ""
    run_ids: list[str] = Field(default_factory=list, max_length=100)
    stages: list[MissionStage] = Field(default_factory=list, max_length=9)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    object_refs: list[ObjectRef] = Field(default_factory=list, max_length=100)
    projection_cursor: dict[str, int] = Field(default_factory=dict, max_length=100)
    cancel_requested: bool = False
    cancellation_complete: bool = False
    created_at: datetime
    updated_at: datetime
    owner_scope: MissionOwnerScope | None = None
    current_binding: MissionBinding | None = None
    run_bindings: dict[str, Scope] = Field(default_factory=dict, max_length=100)
    object_bindings: dict[str, Scope] = Field(default_factory=dict, max_length=100)
    historical_object_refs: list[ObjectRef] = Field(default_factory=list, max_length=100)
    historical_bindings: list[Scope] = Field(default_factory=list, max_length=32)
    semantic_anchors: list[SemanticAnchor] = Field(default_factory=list, max_length=32)
    execution_contexts: list[MissionExecutionContext] = Field(default_factory=list, max_length=100)
    release_pins: list[MissionReleasePin] = Field(default_factory=list, max_length=100)
    continuation: ContinuationDecision | None = None
    resume_operations: dict[str, ResumeReceipt] = Field(default_factory=dict, max_length=32)
    turn_operations: dict[str, str] = Field(default_factory=dict, max_length=100)
    investigation_requirements: InvestigationRequirements | None = None

    @model_validator(mode="before")
    @classmethod
    def legacy_bindings(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        scope = value.get("scope")
        if scope is None:
            return value
        scope_value = scope.model_dump() if isinstance(scope, Scope) else scope
        value.setdefault(
            "owner_scope",
            {
                "principal": scope_value["principal"],
                "active_role": scope_value["active_role"],
            },
        )
        value.setdefault(
            "current_binding",
            {
                "scope": scope_value,
                "bound_at": value["created_at"],
                "generation": 1,
            },
        )
        value.setdefault("run_bindings", {run: scope_value for run in value.get("run_ids", [])})
        value.setdefault(
            "object_bindings",
            {
                object_binding_key(ObjectRef.model_validate(ref)): scope_value
                for ref in value.get("object_refs", [])
            },
        )
        return value

    @model_validator(mode="after")
    def bindings_match_owner(self):
        owner = MissionOwnerScope(
            principal=self.scope.principal, active_role=self.scope.active_role
        )
        if (
            self.owner_scope != owner
            or not self.current_binding
            or self.current_binding.scope != self.scope
        ):
            raise ValueError("Mission execution binding does not match its owner")
        for bound in [
            *self.run_bindings.values(),
            *self.object_bindings.values(),
            *self.historical_bindings,
        ]:
            if (bound.principal, bound.active_role) != (owner.principal, owner.active_role):
                raise ValueError("Historical binding does not match Mission ownership")
        if len({pin.run_id for pin in self.release_pins}) != len(self.release_pins):
            raise ValueError("Mission run has conflicting release pins")
        if any(pin.run_id not in self.run_bindings for pin in self.release_pins):
            raise ValueError("Release pin requires its recorded run binding")
        return self

    @property
    def pinned_objects(self) -> list[ObjectRef]:
        return list(
            {
                object_binding_key(ref): ref
                for ref in [*self.object_refs, *self.historical_object_refs]
            }.values()
        )


def object_binding_key(ref: ObjectRef) -> str:
    return f"{ref.kind}:{ref.id}:{ref.revision}"


class MissionCreate(Contract):
    objective: str = Field(min_length=1, max_length=4000)
    work_intent: WorkIntent = WorkIntent.INVESTIGATE
    public_work_steps: list[StageKind] = Field(default_factory=list, max_length=9)
    operation_id: str = Field(min_length=1, max_length=128)
    new_mission: bool = False
    continue_mission_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def explicit_controls(self):
        if self.new_mission and self.continue_mission_id:
            raise ValueError("New and continue Mission controls conflict")
        return self


class MissionOperation(Contract):
    expected_revision: int = Field(ge=1)


class MissionLink(MissionOperation):
    object_ref: ObjectRef


class MissionResume(MissionOperation):
    operation_id: str = Field(min_length=1, max_length=128)


class ResumableMission(Contract):
    mission_id: str
    thread_id: str
    objective: str
    status: str
    revision: int
    resume_required: bool


DeliverableKind = Literal[
    "decision_memo",
    "action_plan",
    "analysis_summary",
    "investigation_report",
    "scenario_comparison",
    "outcome_report",
]


class DeliverableSource(Contract):
    kind: Literal["mission", "investigation", "decision", "action", "outcome", "execution"]
    id: str = Field(min_length=1, max_length=256)
    revision: int = Field(ge=1)
    fingerprint: str = Field(min_length=64, max_length=64)
    semantic: SemanticRef | None = None
    facts: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def bounded_facts(self):
        if len(self.model_dump_json().encode()) > 24000:
            raise ValueError("Source snapshot exceeds the bound")
        return self


class DeliverableCreate(MissionOperation):
    kind: DeliverableKind
    operation_id: str = Field(min_length=1, max_length=128)


class MissionDeliverable(Contract):
    deliverable_id: str
    mission_id: str
    mission_revision: int
    kind: DeliverableKind
    title: str
    markdown: str = Field(max_length=32000)
    evidence_refs: list[str] = Field(max_length=100)
    object_refs: list[ObjectRef] = Field(max_length=100)
    created_at: datetime
    sources: list[DeliverableSource] = Field(default_factory=list, max_length=201)


MISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS (
    mission_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    security_version INT NOT NULL,
    revision BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(mission_id)
DISTRIBUTED BY HASH(mission_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

DELIVERABLES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_DELIVERABLES (
    deliverable_id VARCHAR(64) NOT NULL,
    mission_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    security_version INT NOT NULL,
    request_digest VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(deliverable_id)
DISTRIBUTED BY HASH(deliverable_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

MISSION_DDLS = (MISSIONS_DDL, DELIVERABLES_DDL)
