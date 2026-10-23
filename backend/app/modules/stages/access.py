"""Authorize access to a stage before its storage connection is resolved."""

from __future__ import annotations

import asyncmy


class StageAccessDenied(ValueError):
    pass


async def check_stage_access(
    stage: dict,
    *,
    action: str,
    username: str,
    password: str = "",
    active_role: str | None = None,
    connection: asyncmy.Connection | None = None,
) -> None:
    """Fail closed unless the active StarRocks principal can access the stage scope."""
    if action not in {"read", "write", "delete"}:
        raise ValueError("Unknown stage action")
    database = str(stage["database_name"])
    schema = str(stage["schema_name"])
    del password, connection
    if not active_role:
        raise StageAccessDenied("Stage access requires an active role")
    from app.modules.access_control.service import access_control_service

    effective = await access_control_service.effective_access(
        principal=username, active_role=active_role, resource=f"{database}.{schema}"
    )
    allowed = {str(item).upper() for item in effective["object_access"]}
    required = {"read": {"SELECT", "INSERT", "ALL"},
                "write": {"INSERT", "ALL"}, "delete": {"DELETE", "ALL"}}[action]
    if not allowed & required:
        raise StageAccessDenied("Stage access denied")
