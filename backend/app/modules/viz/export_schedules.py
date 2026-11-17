"""Schedules that save a dashboard page as an Excel workbook on a timetable.

A person with Export access asks for a schedule under their active business
role. Like News, the schedule runs on the task scheduler as that role's
scheduled-task account, never as the person and never as an administrator, so
each file holds what the role can read. Before saving, the account is checked
the way it will run: it must open the published dashboard with Export, and
edit the shared workspace that receives the files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.common.audit import write_audit_log
from app.core.exceptions import NovaException, StorageError
from app.core.metadata_scope import actor_for

from .contracts.dashboards import DashboardDefinition
from .scheduled_export import parse_record, record_for

HANDLER = "viz.dashboard_export"
#: Administration roles never run business exports (as for News).
FORBIDDEN_ROLES = frozenset({
    "ACCOUNTADMIN", "SECURITYADMIN", "ROOT", "PUBLIC", "USER_ADMIN", "SECURITY_ADMIN",
    "DB_ADMIN", "CLUSTER_ADMIN",
})
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: UUID
    cadence: Literal["daily", "weekly", "monthly"]
    time: Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
    #: Weekly: 0 is Monday.
    weekday: int | None = Field(default=None, ge=0, le=6)
    #: Monthly: days 1–28 exist in every month.
    day: int | None = Field(default=None, ge=1, le=28)
    timezone: str = Field(default="UTC", min_length=1, max_length=40)

    @field_validator("timezone")
    @classmethod
    def known(cls, value: str) -> str:
        if ":" in value:
            raise ValueError("Unknown time zone")
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Unknown time zone") from None
        return value

    @model_validator(mode="after")
    def cadence_fields(self) -> Self:
        if (self.cadence == "weekly") != (self.weekday is not None):
            raise ValueError("A weekly schedule needs a weekday, and only it")
        if (self.cadence == "monthly") != (self.day is not None):
            raise ValueError("A monthly schedule needs a day of the month, and only it")
        return self

    def cron(self) -> str:
        hour, minute = (int(part) for part in self.time.split(":"))
        if self.cadence == "weekly":
            assert self.weekday is not None
            return f"{minute} {hour} * * {(self.weekday + 1) % 7}"
        if self.cadence == "monthly":
            return f"{minute} {hour} {self.day} * *"
        return f"{minute} {hour} * * *"


_CRON = re.compile(r"^(\d{1,2}) (\d{1,2}) (\*|\d{1,2}) \* (\*|\d)$")


def describe(expression: str | None) -> dict[str, Any] | None:
    """The cadence fields back from a stored cron expression."""
    match = _CRON.match(expression or "")
    if not match:
        return None
    minute, hour, day, weekday = match.groups()
    out: dict[str, Any] = {"time": f"{int(hour):02d}:{int(minute):02d}"}
    if weekday != "*":
        out.update(cadence="weekly", weekday=(int(weekday) - 1) % 7)
    elif day != "*":
        out.update(cadence="monthly", day=int(day))
    else:
        out["cadence"] = "daily"
    return out


def _refused(detail: str, status: int = 409) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


class ExportScheduleService:
    async def _bound(self, user: Mapping[str, Any]) -> tuple[str, str]:
        from app.modules.task_orchestration.repository import task_orchestration_repository

        role = str(actor_for(user).active_role)
        if role.upper() in FORBIDDEN_ROLES:
            raise _refused("Switch to a business role to schedule exports. Administration "
                           "roles do not run scheduled work.", 422)
        bound = await task_orchestration_repository.get_role_execution_user(role)
        if not bound:
            raise _refused(f"The role {role} has no scheduled-task account yet. Ask an "
                           "administrator to set one up.")
        return role, bound

    @staticmethod
    def _runner(bound: str, role: str) -> dict[str, Any]:
        """The identity a run uses, for checking what it will be allowed to do."""
        return {"username": bound, "active_role": role, "roles": [role],
                "assigned_roles": [role], "security_context_version": 1,
                "session_id": f"schedule-check:{uuid4()}"}

    async def _tasks(self, dashboard: UUID) -> list[dict[str, Any]]:
        from app.modules.task_orchestration.repository import task_orchestration_repository

        found = []
        for task in await task_orchestration_repository.list_tasks():
            if task.get("handler") != HANDLER:
                continue
            raw = task.get("handler_config")
            try:
                config = json.loads(raw) if isinstance(raw, str) else (raw or {})
                target, timezone = parse_record(str(config.get("record_id") or ""))
            except Exception:  # noqa: BLE001 - a malformed row is not this dashboard's
                continue
            if target.dashboard_id == dashboard:
                found.append({**task, "_target": target, "_timezone": timezone})
        return found

    async def _require_export(self, user: Mapping[str, Any], dashboard: UUID) -> None:
        from .document_service import native_document_service as documents

        await documents.require_kind(user, dashboard, "dashboard")
        if "export" not in await documents.permissions(user, dashboard):
            raise _refused("Your access to this dashboard does not include downloading it.", 403)

    async def list(self, user: Mapping[str, Any], dashboard: UUID) -> list[dict[str, Any]]:
        await self._require_export(user, dashboard)
        role = str(actor_for(user).active_role)
        out = []
        for task in await self._tasks(dashboard):
            cadence = describe(task.get("schedule_expr"))
            if task.get("schedule_kind") != "cron" or cadence is None:
                continue
            out.append({
                "schedule_id": task["id"], "page_id": str(task["_target"].page_id),
                "role": task.get("owner_role"), "timezone": task["_timezone"],
                "format": "xlsx", "mine": task.get("owner_role") == role, **cadence,
            })
        return sorted(out, key=lambda item: (str(item["role"]), item["page_id"]))

    async def save(self, user: Mapping[str, Any], dashboard: UUID,
                   request: ScheduleRequest) -> dict[str, Any]:
        from app.modules.intelligence.contracts import Scope
        from app.modules.intelligence.schedules import configure_schedule
        from app.modules.task_orchestration.repository import task_orchestration_repository
        from app.modules.workspaces.spaces import space_service

        from .document_service import native_document_service as documents

        await self._require_export(user, dashboard)
        role, bound = await self._bound(user)
        try:
            published = (await documents.read(user, dashboard, draft=False))["definition"]
        except NovaException:
            raise _refused("Publish the dashboard first. Scheduled files use its published "
                           "version.") from None
        if not isinstance(published, DashboardDefinition) or not any(
                page.page_id == request.page_id for page in published.pages):
            raise _refused("That page is not in the published dashboard yet. Publish it first.")
        workspace = await documents.workspace_of(user, dashboard)
        if workspace == "personal":
            raise _refused("Scheduled files are saved in the dashboard's shared workspace. "
                           "This dashboard is in a personal workspace.")
        runner = self._runner(bound, role)
        permissions = await documents.scheduled_permissions(bound, role, dashboard)
        if not {"view", "export"} <= permissions:
            raise _refused(f"Share this dashboard with the role {role}, including Export, "
                           "before scheduling it.")
        try:
            await space_service.resolve(runner, workspace, edit=True)
        except StorageError:
            raise _refused(f"Share this dashboard's workspace with the role {role} with edit "
                           "access, so the files can be saved there.") from None

        record = record_for(dashboard, request.page_id, request.timezone)
        # One schedule per page and role: a new time zone replaces the old schedule.
        for task in await self._tasks(dashboard):
            if (task["_target"].page_id == request.page_id and task.get("owner_role") == role
                    and task["_timezone"] != request.timezone):
                await task_orchestration_repository.delete_task(task["id"])
        scheduled = await configure_schedule(
            SimpleNamespace(id=record), dict(user), handler=HANDLER, enabled=True,
            execution_scope=Scope(principal=bound, active_role=role, security_context_version=1),
            schedule_kind="cron", schedule_expr=request.cron(), timezone=request.timezone,
        )
        actor = actor_for(user)
        await write_audit_log(
            event_type="viz", action="SCHEDULE_EXPORT", object_type="viz_resource",
            object_name=f"{dashboard}:{request.page_id}", user_name=actor.principal,
            active_role=actor.active_role, session_id=actor.session_id,
            security_context_version=actor.security_context_version,
            status="SUCCESS", decision="ALLOW",
        )
        return {"schedule_id": scheduled["task_id"], "page_id": str(request.page_id),
                "role": role, "timezone": request.timezone, "format": "xlsx", "mine": True,
                **(describe(request.cron()) or {})}

    async def remove(self, user: Mapping[str, Any], dashboard: UUID, schedule: UUID) -> None:
        from app.modules.task_orchestration.access import is_task_admin
        from app.modules.task_orchestration.repository import task_orchestration_repository

        await self._require_export(user, dashboard)
        role = str(actor_for(user).active_role)
        task = next((item for item in await self._tasks(dashboard)
                     if str(item["id"]) == str(schedule)), None)
        if task is None or (task.get("owner_role") != role and not is_task_admin(dict(user))):
            raise _refused("Schedule not found", 404)
        await task_orchestration_repository.delete_task(task["id"])
        actor = actor_for(user)
        await write_audit_log(
            event_type="viz", action="UNSCHEDULE_EXPORT", object_type="viz_resource",
            object_name=f"{dashboard}:{task['_target'].page_id}", user_name=actor.principal,
            active_role=actor.active_role, session_id=actor.session_id,
            security_context_version=actor.security_context_version,
            status="SUCCESS", decision="ALLOW",
        )


native_export_schedules = ExportScheduleService()
