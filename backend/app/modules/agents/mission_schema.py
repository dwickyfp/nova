"""Public contracts and StarRocks metadata for Studio business work."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.modules.intelligence.contracts import Contract, Scope


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


class Mission(Contract):
    mission_id: str
    thread_id: str
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


class MissionCreate(Contract):
    objective: str = Field(min_length=1, max_length=4000)
    work_intent: WorkIntent = WorkIntent.INVESTIGATE
    public_work_steps: list[StageKind] = Field(default_factory=list, max_length=9)
    operation_id: str = Field(min_length=1, max_length=128)
    new_mission: bool = False


class MissionOperation(Contract):
    expected_revision: int = Field(ge=1)


class MissionLink(MissionOperation):
    object_ref: ObjectRef


class DeliverableCreate(MissionOperation):
    kind: Literal["decision_memo", "action_plan"]
    operation_id: str = Field(min_length=1, max_length=128)


class MissionDeliverable(Contract):
    deliverable_id: str
    mission_id: str
    mission_revision: int
    kind: Literal["decision_memo", "action_plan"]
    title: str
    markdown: str = Field(max_length=32000)
    evidence_refs: list[str] = Field(max_length=100)
    object_refs: list[ObjectRef] = Field(max_length=100)
    created_at: datetime


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
