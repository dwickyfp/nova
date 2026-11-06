"""Who may see, steward, and release ontology metadata.

Ontology permission is separate from data permission: seeing a concept never
grants a read of the data behind it. Visibility and stewardship are decided by
the caller's single active role, which StarRocks assigns and the session
validates; Nova keeps no second role store.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.common.audit import write_audit_log
from app.modules.ontology.contracts import Actor, Domain

ADMIN_ROLE = "ACCOUNTADMIN"
NOT_FOUND = "Ontology record not found"


def active_role(user: dict) -> str:
    return Actor.from_user(user).active_role


def is_admin(user: dict) -> bool:
    return active_role(user).upper() == ADMIN_ROLE


def chain(domain: Domain, domains: dict[str, Domain]) -> list[Domain] | None:
    """The domain and its ancestors, or ``None`` if the chain is broken."""
    path = [domain]
    seen = {domain.id}
    while path[-1].parent_id:
        parent = domains.get(path[-1].parent_id)
        if parent is None or parent.id in seen or len(path) >= 16:
            return None
        seen.add(parent.id)
        path.append(parent)
    return path


def qualified_path(domain: Domain, domains: dict[str, Domain]) -> str:
    path = chain(domain, domains) or [domain]
    return ".".join(item.name for item in reversed(path))


def can_see(domain: Domain | None, domains: dict[str, Domain], user: dict) -> bool:
    """A domain is visible when every domain on its path is visible."""
    if domain is None:
        return False
    path = chain(domain, domains)
    if path is None:
        return False
    if is_admin(user):
        return True
    role = active_role(user)
    return all(
        item.status == "ACTIVE"
        and (
            not item.visibility_roles
            or role in item.visibility_roles
            or role in item.steward_roles
        )
        for item in path
    )


def stewards(domain: Domain, user: dict) -> bool:
    return domain.status == "ACTIVE" and active_role(user) in domain.steward_roles


async def audit(
    action: str,
    object_type: str,
    name: str,
    user: dict,
    *,
    status: str = "SUCCESS",
    decision: str | None = None,
    error: str | None = None,
) -> None:
    await write_audit_log(
        event_type="ONTOLOGY",
        user_name=user.get("username") or "unknown",
        action=action,
        object_type=object_type,
        object_name=name,
        status=status,
        error_message=error,
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
        security_context_version=user.get("security_context_version"),
        decision=decision,
    )


async def deny(action: str, object_type: str, name: str, user: dict, detail: str) -> HTTPException:
    """Audit a refused governed action and return the error to raise."""
    await audit(action, object_type, name, user, status="DENIED", decision="DENY", error=detail)
    return HTTPException(status_code=403, detail=detail)


async def require_admin(action: str, object_type: str, name: str, user: dict) -> None:
    if not is_admin(user):
        raise await deny(action, object_type, name, user, f"{ADMIN_ROLE} must be the active role")
