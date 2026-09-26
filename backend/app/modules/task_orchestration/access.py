"""Task ownership follows the active role; creators remain audit metadata."""

from typing import Any

from fastapi import HTTPException

from app.core.database import db
from app.core.security import decrypt_password
from app.modules.access_control.role_activation import role_activation_service

ADMIN_ROLES = frozenset({"ACCOUNTADMIN", "user_admin", "security_admin"})


def is_task_admin(user: dict[str, Any]) -> bool:
    return user.get("active_role") in ADMIN_ROLES


def owns_task(task: dict[str, Any], user: dict[str, Any]) -> bool:
    role = task.get("owner_role")
    if role:
        return role == user.get("active_role")
    # Legacy definitions stay private until an administrator assigns a role.
    return bool(task.get("created_by")) and task["created_by"] == user.get("username")


def graph_role(tasks: list[dict[str, Any]]) -> str | None:
    roles = {task.get("owner_role") for task in tasks}
    if len(roles) != 1 or None in roles or "" in roles:
        return None
    return str(next(iter(roles)))


async def verify_active_role(user: dict[str, Any]) -> None:
    """Check the engine again so a cached login cannot retain revoked access."""
    role = user.get("active_role")
    if not role:
        return
    try:
        password = decrypt_password(user["encrypted_password"])
        async with db.user_conn(user["username"], password) as conn:
            await role_activation_service.activate(
                conn, principal=user["username"], requested_role=role
            )
    except Exception:
        raise HTTPException(
            403, "Your active role is no longer available. Select a role again."
        ) from None
