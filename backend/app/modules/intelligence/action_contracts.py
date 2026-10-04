"""Credential-free action requests, receipts, and durable dispatch state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Protocol

from pydantic import ConfigDict, Field, model_serializer, model_validator

from app.modules.agents.automations import AutomationCondition
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


class AutomationActionCondition(AutomationCondition):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AutomationActionConfiguration(Contract):
    agent_id: str = Field(min_length=1, max_length=64)
    semantic: SemanticRef
    title: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=3, max_length=4000)
    schedule_kind: Literal["cron", "interval"]
    schedule_expr: str = Field(min_length=1, max_length=128)
    timezone: str = Field(default="Asia/Jakarta", min_length=1, max_length=64)
    condition: AutomationActionCondition | None = None
    delivery: Literal["studio"] = "studio"
    enabled: Literal[True] = True


ActionConfiguration = MonitorConfiguration | AutomationActionConfiguration


class ActionPreview(Contract):
    idempotency_key: str = Field(min_length=8, max_length=128)
    decision_id: str = Field(min_length=1, max_length=64)
    expected_decision_revision: int = Field(ge=1)
    option_id: str = Field(min_length=1, max_length=128)
    adapter_id: Literal["monitor-v1", "automation-v1"] = "monitor-v1"
    configuration: ActionConfiguration
    mission_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_serializer(mode="wrap")
    def legacy_payload(self, handler):
        payload = handler(self)
        if self.mission_id is None:
            payload.pop("mission_id", None)
        return payload

    @model_validator(mode="after")
    def adapter_configuration(self):
        contract = action_adapter_contract(self.adapter_id)
        if not isinstance(self.configuration, contract.configuration_type):
            raise ValueError("Configuration does not match the selected Action adapter")
        return self


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


class AutomationActionReceipt(Contract):
    automation_id: str = Field(min_length=1, max_length=64)
    configuration_digest: str = Field(min_length=64, max_length=64)
    schedule_enabled: bool
    delivery: Literal["studio"] = "studio"


ActionReceiptValue = ActionReceipt | AutomationActionReceipt


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
        "automation_matches",
        "automation_missing",
        "automation_changed",
        "automation_disabled",
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
    action_type: Literal["monitor", "automation"] = "monitor"
    idempotency_key: str
    request_digest: str
    configuration: ActionConfiguration
    mission_id: str | None = Field(default=None, min_length=1, max_length=64)
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
    receipt: ActionReceiptValue | None = None
    compensation_receipt: ActionReceiptValue | None = None
    verification: ActionVerification | None = None
    compensation: ActionVerification | None = None
    error_code: str | None = None

    @model_serializer(mode="wrap")
    def legacy_payload(self, handler):
        payload = handler(self)
        if self.mission_id is None:
            payload.pop("mission_id", None)
        return payload

    @model_validator(mode="after")
    def adapter_contract(self):
        contract = action_adapter_contract(self.adapter_id)
        if self.action_type != contract.action_type or not isinstance(
            self.configuration, contract.configuration_type
        ):
            raise ValueError("Action configuration does not match its registered adapter")
        for receipt in (self.receipt, self.compensation_receipt):
            if receipt is not None and not isinstance(receipt, contract.receipt_type):
                raise ValueError("Action receipt does not match its registered adapter")
        return self


class ActionEvent(Record):
    action_id: str
    decision_id: str
    action_revision: int = Field(ge=1)
    event: ActionStatus
    actor: str
    context_digest: str
    dispatch_fence: str | None = None


class ActionRead(Action):
    execution_current: bool = True


class MonitorPolicyInput(Contract):
    action_type: Literal["monitor"] = "monitor"
    cost: float = 0
    risk: Literal["low"] = "low"
    feasible: Literal[True] = True


class AutomationPolicyInput(Contract):
    action_type: Literal["automation"] = "automation"
    cost: float = 0
    risk: Literal["low"] = "low"
    feasible: Literal[True] = True


@dataclass(frozen=True)
class ActionAdapterContract:
    id: str
    action_type: Literal["monitor", "automation"]
    configuration_type: type[Contract]
    receipt_type: type[Contract]
    policy_type: type[Contract]
    expected_effect: str


ACTION_ADAPTER_CONTRACTS = MappingProxyType(
    {
        "monitor-v1": ActionAdapterContract(
            "monitor-v1",
            "monitor",
            MonitorConfiguration,
            ActionReceipt,
            MonitorPolicyInput,
            "Create an authorized metric monitor and its schedule",
        ),
        "automation-v1": ActionAdapterContract(
            "automation-v1",
            "automation",
            AutomationActionConfiguration,
            AutomationActionReceipt,
            AutomationPolicyInput,
            "Create an authorized scheduled agent report in Studio",
        ),
    }
)


def action_adapter_contract(adapter_id: str) -> ActionAdapterContract:
    contract = ACTION_ADAPTER_CONTRACTS.get(adapter_id)
    if contract is None:
        raise ValueError("Action adapter is not registered")
    return contract


class ActionAdapter(Protocol):
    id: str
    contract: ActionAdapterContract

    async def preview(self, configuration: ActionConfiguration, user: dict) -> None: ...

    async def execute(
        self, action: Action, user: dict, *, guard: Callable[[], Awaitable[None]] | None = None
    ) -> ActionReceiptValue: ...

    async def verify(
        self, action: Action, user: dict, *, compensated: bool = False
    ) -> ActionVerification: ...

    async def compensate(
        self, action: Action, user: dict, *, guard: Callable[[], Awaitable[None]] | None = None
    ) -> ActionReceiptValue: ...
