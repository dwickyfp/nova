"""Reviewed automation actions use the existing Studio automation owner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException

from app.modules.agents.automations import (
    AutomationCreate,
    AutomationError,
    AutomationExecutionBinding,
    AutomationUpdate,
    automation_configuration,
    automation_creation_id,
    automation_repository,
    next_run,
)
from app.modules.intelligence.action_contracts import (
    Action,
    ActionVerification,
    AutomationActionConfiguration,
    AutomationActionReceipt,
    action_adapter_contract,
)
from app.modules.intelligence.contracts import Scope, fingerprint, utc_now
from app.modules.intelligence.schedules import require_execution_binding


class AutomationActionAdapter:
    id = "automation-v1"
    idempotency_mode = "nova_guarded"
    contract = action_adapter_contract(id)

    def __init__(self, service=None, repository=automation_repository):
        self._service, self.repository = service, repository

    @property
    def service(self):
        if self._service is None:
            from app.modules.intelligence.engine import intelligence_service

            self._service = intelligence_service
        return self._service

    @staticmethod
    def configuration(action: Action) -> AutomationActionConfiguration:
        if not isinstance(action.configuration, AutomationActionConfiguration):
            raise HTTPException(status_code=409, detail="Automation action configuration changed")
        return action.configuration

    @staticmethod
    def action_receipt(
        action: Action, *, compensated: bool = False
    ) -> AutomationActionReceipt | None:
        receipt = action.compensation_receipt if compensated else action.receipt
        if receipt is not None and not isinstance(receipt, AutomationActionReceipt):
            raise HTTPException(status_code=409, detail="Automation action receipt changed")
        return receipt

    @staticmethod
    def body(configuration: AutomationActionConfiguration) -> AutomationCreate:
        return AutomationCreate(
            **configuration.model_dump(exclude={"agent_id", "semantic", "delivery"})
        )

    @staticmethod
    def binding(action: Action) -> AutomationExecutionBinding:
        return AutomationExecutionBinding(
            action_id=action.id,
            scope=action.scope.model_copy(update={"session_id": None}),
            semantic=action.semantic,
        )

    @staticmethod
    def identity(action: Action) -> str:
        return automation_creation_id(
            action.scope.principal,
            action.scope.active_role,
            AutomationActionAdapter.configuration(action).agent_id,
            action.id,
        )

    @classmethod
    def expected(cls, action: Action, *, enabled: bool) -> dict:
        body = cls.body(AutomationActionAdapter.configuration(action)).model_dump(mode="json")
        body["delivery"]["execution_binding"] = cls.binding(action).model_dump(mode="json")
        return {
            **body,
            "enabled": enabled,
            "agent_id": AutomationActionAdapter.configuration(action).agent_id,
            "owner_name": action.scope.principal,
            "role_name": action.scope.active_role,
        }

    async def authorize(self, configuration: AutomationActionConfiguration, user: dict) -> None:
        from app.modules.agents.router import _require_owned_agent

        await _require_owned_agent(configuration.agent_id, user)
        await require_execution_binding(user)
        await self.service.authorize_semantic(configuration.semantic, user, active=True)

    async def preview(self, configuration: AutomationActionConfiguration, user: dict) -> None:
        await self.authorize(configuration, user)
        try:
            next_run(
                configuration.schedule_kind,
                configuration.schedule_expr,
                configuration.timezone,
                utc_now(),
            )
        except AutomationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def read(self, action: Action, user: dict) -> dict | None:
        if action.scope != Scope.from_user(user):
            raise HTTPException(status_code=404, detail="Action unavailable")
        await self.authorize(AutomationActionAdapter.configuration(action), user)
        return await self.repository.get(self.identity(action), owner_name=action.scope.principal)

    @staticmethod
    def receipt(record: dict) -> AutomationActionReceipt:
        return AutomationActionReceipt(
            automation_id=record["automation_id"],
            configuration_digest=fingerprint(automation_configuration(record)),
            schedule_enabled=record["enabled"],
        )

    async def execute(
        self, action: Action, user: dict, *, guard: Callable[[], Awaitable[None]] | None = None
    ) -> AutomationActionReceipt:
        if action.scope != Scope.from_user(user):
            raise HTTPException(status_code=404, detail="Action unavailable")
        await self.preview(AutomationActionAdapter.configuration(action), user)
        if guard:
            await guard()
        try:
            saved = await self.repository.create(
                agent_id=AutomationActionAdapter.configuration(action).agent_id,
                owner_name=action.scope.principal,
                role_name=action.scope.active_role,
                body=self.body(AutomationActionAdapter.configuration(action)),
                creation_identity=action.id,
                execution_binding=self.binding(action),
            )
        except AutomationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if guard:
            await guard()
        return self.receipt(saved)

    async def verify(self, action: Action, user: dict, *, compensated=False) -> ActionVerification:
        record = await self.read(action, user)
        if record is None:
            return ActionVerification(
                checked_at=utc_now(), complete=False, reason="automation_missing"
            )
        expected = automation_configuration(self.expected(action, enabled=not compensated))
        actual = automation_configuration(record)
        receipt = self.action_receipt(action, compensated=compensated)
        match = actual == expected and record["automation_id"] == self.identity(action)
        if receipt is not None:
            match = match and (
                receipt.automation_id == record["automation_id"]
                and receipt.configuration_digest == fingerprint(actual)
                and receipt.schedule_enabled == (not compensated)
            )
        return ActionVerification(
            checked_at=utc_now(),
            complete=match,
            reason=("automation_disabled" if compensated else "automation_matches")
            if match
            else "automation_changed",
        )

    async def compensate(
        self, action: Action, user: dict, *, guard: Callable[[], Awaitable[None]] | None = None
    ) -> AutomationActionReceipt:
        record = await self.read(action, user)
        if record is None:
            raise HTTPException(
                status_code=409, detail="Automation unavailable; reconcile before compensation"
            )
        expected = automation_configuration(self.expected(action, enabled=True))
        actual = automation_configuration(record)
        receipt = self.action_receipt(action)
        if actual != expected or (
            receipt is not None
            and (
                receipt.automation_id != record["automation_id"]
                or receipt.configuration_digest != fingerprint(actual)
            )
        ):
            raise HTTPException(
                status_code=409, detail="Automation changed; review before compensation"
            )
        if guard:
            await guard()
        try:
            saved = await self.repository.update(
                record,
                AutomationUpdate(enabled=False),
                expected_configuration_digest=fingerprint(actual),
            )
        except AutomationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if guard:
            await guard()
        return self.receipt(saved)
