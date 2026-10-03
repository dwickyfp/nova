from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Mode(StrEnum):
    OBSERVE = "OBSERVE"
    GOVERNED = "GOVERNED"
    AUTONOMOUS = "AUTONOMOUS"


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    EXPIRED = "expired"
    UNSUPPORTED = "unsupported"
    UNAUTHORIZED = "unauthorized"


class Risk(StrEnum):
    AUTO = "AUTO"
    APPROVAL = "APPROVAL"
    RECOMMEND_ONLY = "RECOMMEND_ONLY"
    PROHIBITED = "PROHIBITED"


class Scope(Model):
    principal: str = Field(min_length=1, max_length=128)
    active_role: str | None = Field(default=None, min_length=1, max_length=128)
    security_context_version: int = Field(ge=1)
    catalog: str = "default_catalog"
    database: str = ""
    policy_revision: str | None = None
    settings_hash: str = ""

    @model_validator(mode="after")
    def valid_identity(self):
        from app.modules.access_control.security_context import SecurityContext

        if self.active_role is None:
            return self
        context = SecurityContext(
            principal=self.principal,
            active_role=self.active_role,
            security_context_version=self.security_context_version,
        )
        if context.principal != self.principal or context.active_role != self.active_role:
            raise ValueError("Identity and active role must not contain surrounding whitespace")
        return self

    @property
    def cohort_id(self) -> str:
        return digest(self.model_dump())


class Observation(Model):
    id: str
    family_id: str
    scope: Scope
    observed_at: datetime = Field(default_factory=utcnow)
    source: str
    status: str
    total_ms: float = Field(ge=0)
    engine_ms: float | None = Field(default=None, ge=0)
    fetch_ms: float | None = Field(default=None, ge=0)
    nova_ms: float | None = Field(default=None, ge=0)
    engine_roundtrip_ms: float | None = Field(default=None, ge=0)
    profile_requested: bool = False
    returned_rows: int = Field(default=0, ge=0)
    truncated: bool = False
    engine_query_ids: tuple[str, ...] = ()
    audit_ids: tuple[str, ...] = ()
    correlation: Availability = Availability.UNAVAILABLE
    classified: bool = False
    canonical: str | None = None
    tables: tuple[str, ...] = ()
    sample_ref: str | None = None


class Evidence(Model):
    id: str
    family_id: str
    cohort_id: str
    kind: str
    availability: Availability
    collected_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    source: str
    summary: dict[str, Any] = Field(default_factory=dict)
    payload_ref: str | None = None
    query_ids: tuple[str, ...] = ()
    reason: str | None = None

    def effective_availability(self, now: datetime) -> Availability:
        if self.expires_at and self.expires_at <= now:
            return Availability.EXPIRED
        return self.availability


class Budget(Model):
    timeout_seconds: int = Field(default=900, ge=30, le=3600)
    max_result_rows: int = Field(default=200000, ge=1, le=1000000)
    max_result_bytes: int = Field(default=33554432, ge=1024, le=268435456)
    max_memory_bytes: int = Field(default=536870912, ge=1048576, le=2147483648)
    repetitions: int = Field(default=30, ge=30, le=100)
    warmups: int = Field(default=3, ge=3, le=10)
    resource_group: str = Field(min_length=1, max_length=128)


class Enrollment(Model):
    id: str
    version: int = Field(default=1, ge=1)
    scope: Scope
    sandbox_database: str = Field(min_length=1, max_length=128)
    table_mapping: dict[str, str]
    snapshot_id: str = Field(min_length=1, max_length=256)
    snapshot_created_at: datetime
    snapshot_frozen: bool
    execution_principal: str = Field(min_length=1, max_length=128)
    execution_role: str = Field(min_length=1, max_length=128)
    budget: Budget
    replay_opt_in: bool = False
    statistics_auto: bool = False
    native_feedback_auto: bool = False
    ranger_acceptance_ref: str | None = None
    production_resource_group: str | None = None
    control_families: tuple[str, ...] = ()
    enabled: bool = True

    @model_validator(mode="after")
    def isolated(self):
        if not self.scope.active_role:
            raise ValueError("Enrollment requires an explicit named active role")
        if self.sandbox_database == self.scope.database or not self.table_mapping:
            raise ValueError(
                "An enrolled, separate snapshot database and table mapping are required"
            )
        if self.execution_principal.lower() == "root":
            raise ValueError("Root cannot execute experiments")
        if self.execution_role.upper() in {"ALL", "DEFAULT", "NONE", "ACCOUNTADMIN"}:
            raise ValueError("Experiments require a dedicated bounded role")
        return self


class Policy(Model):
    id: str = "default"
    version: int = Field(default=1, ge=1)
    mode: Mode = Mode.GOVERNED
    collection_enabled: bool = True
    profile_sample_rate: float = Field(default=0.01, ge=0, le=0.1)
    regression_ratio: float = Field(default=1.5, ge=1.05, le=10)
    regression_absolute_ms: float = Field(default=100, ge=1)
    absolute_slow_ms: float = Field(default=5000, ge=1)
    high_frequency: int = Field(default=100, ge=20)
    minimum_gain: float = Field(default=0.1, ge=0.01, le=0.9)
    observations_days: int = Field(default=7, ge=1, le=30)
    payload_hours: int = Field(default=24, ge=1, le=24)
    history_days: int = Field(default=90, ge=28, le=365)
    max_statistics_tables: int = Field(default=1, ge=1, le=5)


class ActionKind(StrEnum):
    STATISTICS = "STATISTICS"
    HISTOGRAM = "HISTOGRAM"
    MATERIALIZED_VIEW = "MATERIALIZED_VIEW"
    REFRESH_POLICY = "REFRESH_POLICY"
    PLAN_BASELINE = "PLAN_BASELINE"
    RESOURCE_GROUP = "RESOURCE_GROUP"
    ADD_INDEX = "ADD_INDEX"
    NATIVE_FEEDBACK = "NATIVE_FEEDBACK"
    PARTITION = "PARTITION"
    SORT_KEY = "SORT_KEY"
    BUCKETING = "BUCKETING"
    APPLICATION_SQL = "APPLICATION_SQL"
    DESTRUCTIVE = "DESTRUCTIVE"
    SECURITY_POLICY = "SECURITY_POLICY"


class State(StrEnum):
    PROPOSED = "PROPOSED"
    VALIDATING = "VALIDATING"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"
    READY_AUTO = "READY_AUTO"
    READY_APPROVAL = "READY_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    VERIFYING = "VERIFYING"
    SUCCESS = "SUCCESS"
    NO_IMPROVEMENT = "NO_IMPROVEMENT"
    REGRESSED = "REGRESSED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"


class Candidate(Model):
    id: str
    version: int = Field(default=1, ge=1)
    family_id: str
    scope: Scope
    kind: ActionKind
    targets: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    evidence_digest: str | None = None
    enrollment_id: str
    enrollment_version: int
    policy_version: int
    experiment_id: str | None = None
    experiment_digest: str | None = None
    state: State = State.PROPOSED
    reason: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    proposed_at: datetime = Field(default_factory=utcnow)
    approved_by: str | None = None
    approval_digest: str | None = None
    approval_expires_at: datetime | None = None
    owned_object: str | None = None

    @property
    def proposal_binding(self) -> str:
        return self.model_copy(update={"experiment_id": None, "experiment_digest": None}).binding

    @property
    def binding(self) -> str:
        return digest(
            self.model_dump(
                exclude={
                    "state",
                    "reason",
                    "approved_by",
                    "approval_digest",
                    "approval_expires_at",
                    "owned_object",
                }
            )
        )
