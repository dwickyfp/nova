"""Credential-free action requests, receipts, and durable dispatch state."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.intelligence.contracts import (
    Contract,
    MonitorConfiguration,
    PolicyResult,
    Record,
    SemanticRef,
)

BUSINESS_ACTION_TOOL_NAME = "execute_business_action"
BUSINESS_ACTION_TOOL_DESCRIPTION = (
    "Execute or compensate a reviewed business action. Each call requires explicit consent."
)
BUSINESS_ACTION_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "action_id": {"type": "string", "minLength": 1, "maxLength": 64},
        "operation_id": {"type": "string", "minLength": 8, "maxLength": 128},
        "expected_revision": {"type": "integer", "minimum": 1},
        "thread_id": {"type": "string", "minLength": 1, "maxLength": 64},
        "compensate": {"type": "boolean"},
    },
    "required": ["action_id", "operation_id", "expected_revision", "thread_id"],
    "additionalProperties": False,
}

ActionStatus = Literal[
    "awaiting_approval",
    "approved",
    "awaiting_consent",
    "dispatch_ready",
    "executing",
    "verification_required",
    "verified",
    "failed",
    "compensating",
    "compensation_required",
    "compensated",
    "cancelled",
    "denied",
]


class ActionPreview(Contract):
    idempotency_key: str = Field(min_length=8, max_length=128)
    decision_id: str = Field(min_length=1, max_length=64)
    expected_decision_revision: int = Field(ge=1)
    option_id: str = Field(min_length=1, max_length=128)
    adapter_id: Literal["monitor-v1"] = "monitor-v1"
    configuration: MonitorConfiguration


class ActionOperation(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    expected_revision: int = Field(ge=1)
    thread_id: str = Field(min_length=1, max_length=64)


class ActionReview(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    expected_revision: int = Field(ge=1)
    operation: Literal["approve", "deny"]


class ActionApproval(Contract):
    actor: str
    active_role: str
    security_context_version: int = Field(ge=1)
    session_id: str
    policy_digest: str
    operation_id: str
    approved_at: datetime


class ActionReceipt(Contract):
    monitor_id: str
    monitor_revision: int = Field(ge=1)
    task_id: str | None = None
    schedule_enabled: bool


class ActionVerification(Contract):
    checked_at: datetime
    complete: bool
    reason: Literal[
        "monitor_and_schedule_match",
        "monitor_missing",
        "monitor_changed",
        "schedule_missing",
        "schedule_changed",
        "monitor_and_schedule_disabled",
        "readback_failed",
    ]


class Action(Record):
    decision_id: str
    decision_revision: int = Field(ge=1)
    decision_digest: str
    option_id: str
    semantic: SemanticRef
    adapter_id: str
    tool_name: Literal["execute_business_action"] = "execute_business_action"
    action_type: Literal["monitor"] = "monitor"
    idempotency_key: str
    request_digest: str
    configuration: MonitorConfiguration
    side_effect_class: Literal["destructive"] = "destructive"
    idempotency_mode: Literal["nova_guarded"] = "nova_guarded"
    expected_effect: str = "Create an authorized metric monitor and its schedule"
    policy: PolicyResult
    approval: ActionApproval | None = None
    status: ActionStatus
    dispatch_attempts: int = Field(default=0, ge=0, le=1)
    compensation_attempts: int = Field(default=0, ge=0, le=1)
    dispatch_fence: str | None = None
    consent_call_id: str | None = None
    consent_previous_status: ActionStatus | None = None
    consent_compensate: bool = False
    last_operation_id: str | None = None
    last_operation_digest: str | None = None
    last_actor: str | None = None
    receipt: ActionReceipt | None = None
    compensation_receipt: ActionReceipt | None = None
    verification: ActionVerification | None = None
    compensation: ActionVerification | None = None
    error_code: str | None = None


class ActionEvent(Record):
    action_id: str
    decision_id: str
    action_revision: int = Field(ge=1)
    event: ActionStatus
    actor: str
    context_digest: str
    dispatch_fence: str | None = None


class MonitorPolicyInput(Contract):
    action_type: Literal["monitor"] = "monitor"
    cost: float = 0
    risk: Literal["low"] = "low"
    feasible: Literal[True] = True
