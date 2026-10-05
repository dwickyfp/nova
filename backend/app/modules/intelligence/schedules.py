"""Opt-in Intelligence schedules on the existing durable task orchestrator."""

import json

from fastapi import HTTPException

from app.modules.intelligence.contracts import Scope, fingerprint
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.task_orchestration.internal_handlers import InternalTaskConfiguration
from app.modules.task_orchestration.repository import task_orchestration_repository


async def require_execution_binding(user: dict) -> None:
    scope = Scope.from_user(user)
    bound = await task_orchestration_repository.get_role_execution_user(scope.active_role)
    if bound != scope.principal:
        raise HTTPException(
            status_code=403,
            detail="Scheduled work requires this principal's authorized role execution binding",
        )


async def configure_schedule(
    record,
    user: dict,
    *,
    handler: str,
    enabled: bool,
    cadence: int = 15,
    execution_scope: Scope | None = None,
) -> dict:
    """Create or update the schedule that runs ``handler`` for ``record``.

    By default the caller must be the role's bound execution account. A caller
    that has already authorized a manager to publish on behalf of that account
    passes ``execution_scope``; the binding is still verified, and the schedule
    is owned by the bound account so the worker's identity checks hold.
    """
    repository = task_orchestration_repository
    if execution_scope is None:
        if enabled:
            await require_execution_binding(user)
        scope = Scope.from_user(user)
        name = "intelligence_" + fingerprint([handler, record.id])[:32]
    else:
        scope = execution_scope
        bound = await repository.get_role_execution_user(scope.active_role)
        if enabled and bound != scope.principal:
            raise HTTPException(
                status_code=409, detail="Scheduled execution account binding changed"
            )
        name = "intelligence_" + fingerprint(
            [handler, record.id, scope.principal, scope.active_role]
        )[:32]
    schedule = {
        "schedule_kind": "interval" if enabled else "manual",
        "schedule_expr": f"{cadence} minutes" if enabled else None,
        "timezone": "UTC",
    }
    config = InternalTaskConfiguration(
        scope=scope.model_copy(update={"session_id": None}), record_id=record.id
    )
    async with metadata_lock(f"intelligence-schedule:{name}"):
        existing = await repository.find_task(name, None, None)
        if existing:
            if (
                existing["created_by"] != scope.principal
                or existing["owner_role"] != scope.active_role
            ):
                raise HTTPException(status_code=404, detail="Schedule unavailable")
            raw = existing.get("handler_config")
            old_config = json.loads(raw) if isinstance(raw, str) else raw
            if existing.get("handler") != handler:
                raise HTTPException(status_code=409, detail="Schedule handler changed")
            if any(
                existing.get(key) != value for key, value in schedule.items()
            ) or old_config != config.model_dump(mode="json"):
                existing = await repository.update_task(
                    existing["id"],
                    {
                        **schedule,
                        "handler_config": config.model_dump_json(),
                    },
                )
            return {"task_id": existing["id"], "enabled": enabled}
        if not enabled:
            return {"task_id": None, "enabled": False}
        task = await repository.create_task(
            {
                "name": name,
                "definition": "",
                **schedule,
                "owner_role": scope.active_role,
                "overlap_policy": "skip",
                "handler": handler,
                "handler_config": config.model_dump(mode="json"),
            },
            scope.principal,
        )
    return {"task_id": task["id"], "enabled": enabled}
