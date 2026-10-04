"""Monitor action adapter using governed Intelligence and task owners."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from fastapi import HTTPException

from app.modules.intelligence.action_contracts import Action, ActionReceipt, ActionVerification
from app.modules.intelligence.contracts import Monitor, Scope, fingerprint, utc_now
from app.modules.intelligence.schedules import configure_schedule, require_execution_binding
from app.modules.task_orchestration.internal_handlers import InternalTaskConfiguration
from app.modules.task_orchestration.repository import task_orchestration_repository


class MonitorActionAdapter:
    id = "monitor-v1"
    idempotency_mode = "nova_guarded"

    def __init__(self, service=None, tasks=task_orchestration_repository):
        self._service, self.tasks = service, tasks

    @property
    def service(self):
        if self._service is None:
            from app.modules.intelligence.engine import intelligence_service

            self._service = intelligence_service
        return self._service

    @staticmethod
    def monitor(action: Action, user: dict) -> Monitor:
        if action.scope != Scope.from_user(user):
            raise HTTPException(status_code=404, detail="Action unavailable")
        return Monitor(
            id=fingerprint([action.id, "monitor-v1"]),
            scope=Scope.from_user(user),
            **action.configuration.model_dump(),
        )

    async def schedule(self, monitor: Monitor):
        name = "intelligence_" + fingerprint(["intelligence.monitor", monitor.id])[:32]
        return await self.tasks.find_task(name, None, None)

    @staticmethod
    def schedule_matches(action: Action, task: dict, *, enabled: bool) -> bool:
        raw = task.get("handler_config")
        try:
            config = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            return False
        expected_config = InternalTaskConfiguration(
            scope=action.scope.model_copy(update={"session_id": None}),
            record_id=fingerprint([action.id, "monitor-v1"]),
        ).model_dump(mode="json")
        return (
            task.get("created_by") == action.scope.principal
            and task.get("owner_role") == action.scope.active_role
            and task.get("handler") == "intelligence.monitor"
            and config == expected_config
            and task.get("database_name") is None
            and task.get("schema_name") is None
            and task.get("definition") == ""
            and task.get("when_expr") is None
            and task.get("overlap_policy") == "skip"
            and task.get("timezone") == "UTC"
            and task.get("schedule_kind") == ("interval" if enabled else "manual")
            and task.get("schedule_expr")
            == (f"{action.configuration.cadence_minutes} minutes" if enabled else None)
            and (
                not action.receipt
                or action.receipt.task_id is None
                or task.get("id") == action.receipt.task_id
            )
        )

    async def preview(self, configuration, user: dict) -> None:
        from app.modules.agents.router import _require_agent

        if not configuration.enabled:
            raise HTTPException(
                status_code=422, detail="Monitor actions require an enabled schedule"
            )
        await _require_agent(configuration.agent_id, user)
        await require_execution_binding(user)
        monitor = Monitor(id="preview", scope=Scope.from_user(user), **configuration.model_dump())
        await self.service.validate_monitor(monitor, user)

    async def execute(
        self, action: Action, user: dict, *, guard: Callable[[], Awaitable[None]] | None = None
    ) -> ActionReceipt:
        monitor = self.monitor(action, user)
        if guard:
            await guard()
        saved = await self.service.register_monitor(monitor, user)
        if guard:
            await guard()
        schedule = await configure_schedule(
            saved,
            user,
            handler="intelligence.monitor",
            enabled=True,
            cadence=saved.cadence_minutes,
        )
        return ActionReceipt(
            monitor_id=saved.id,
            monitor_revision=saved.revision,
            task_id=schedule["task_id"],
            schedule_enabled=True,
        )

    async def verify(self, action: Action, user: dict, *, compensated=False) -> ActionVerification:
        expected = self.monitor(action, user)
        monitor = await self.service.repository.get(
            "monitors", expected.id, expected.scope, Monitor
        )
        if monitor is None:
            return ActionVerification(
                checked_at=utc_now(), complete=False, reason="monitor_missing"
            )
        monitor = await self.service.get("monitors", monitor.id, user)
        receipt = action.compensation_receipt if compensated else action.receipt
        if monitor.scope != action.scope or (
            receipt
            and (
                receipt.monitor_id != monitor.id
                or receipt.monitor_revision != monitor.revision
                or receipt.schedule_enabled == compensated
            )
        ):
            return ActionVerification(
                checked_at=utc_now(), complete=False, reason="monitor_changed"
            )
        configuration = action.configuration.model_dump()
        if compensated:
            configuration["enabled"] = False
        actual = monitor.model_dump(include=set(configuration))
        if actual != configuration:
            return ActionVerification(
                checked_at=utc_now(), complete=False, reason="monitor_changed"
            )
        task = await self.schedule(monitor)
        if task is None:
            return ActionVerification(
                checked_at=utc_now(),
                complete=compensated,
                reason="monitor_and_schedule_disabled" if compensated else "schedule_missing",
            )
        match = self.schedule_matches(action, task, enabled=not compensated)
        if match and not compensated:
            match = (
                await self.tasks.get_role_execution_user(action.scope.active_role)
                == action.scope.principal
            )
        return ActionVerification(
            checked_at=utc_now(),
            complete=match,
            reason=(
                "monitor_and_schedule_disabled" if compensated else "monitor_and_schedule_match"
            )
            if match
            else "schedule_changed",
        )

    async def compensate(
        self, action: Action, user: dict, *, guard: Callable[[], Awaitable[None]] | None = None
    ) -> ActionReceipt:
        monitor = await self.service.get("monitors", self.monitor(action, user).id, user)
        if monitor.scope != action.scope or (
            action.receipt
            and (
                action.receipt.monitor_id != monitor.id
                or action.receipt.monitor_revision != monitor.revision
            )
        ):
            raise HTTPException(status_code=409, detail="Monitor revision changed")
        expected = action.configuration.model_dump()
        expected["enabled"] = monitor.enabled
        if monitor.model_dump(include=set(expected)) != expected:
            raise HTTPException(
                status_code=409, detail="Monitor changed; review before compensation"
            )
        task = await self.schedule(monitor)
        if task is not None and not self.schedule_matches(
            action, task, enabled=monitor.enabled
        ):
            raise HTTPException(status_code=409, detail="Schedule changed; review compensation")
        if guard:
            await guard()
        saved = await self.service.register_monitor(
            monitor.model_copy(update={"enabled": False}),
            user,
            expected_revision=monitor.revision,
        )
        if guard:
            await guard()
        schedule = await configure_schedule(
            saved,
            user,
            handler="intelligence.monitor",
            enabled=False,
        )
        return ActionReceipt(
            monitor_id=saved.id,
            monitor_revision=saved.revision,
            task_id=schedule["task_id"],
            schedule_enabled=False,
        )
