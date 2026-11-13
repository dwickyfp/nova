"""Per-user News entitlement stored in the preference metadata (PostgreSQL).

News is a managerial surface, so an administrator switches it on per user. The
flag uses the reserved ``nova.`` key namespace next to the other account flags
in :mod:`app.common.user_flags`; no user-facing preference writer can set it.

The entitlement is an additional product gate layered *after* data
authorization. It never grants access to a Semantic View or to a story: those
remain decided by the caller's active role and the access proof.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Annotated

from fastapi import Depends, HTTPException

from app.common.audit import write_audit_log
from app.common.preference_store import read_value, write_values
from app.core.deps import get_current_user
from app.core.metadata_scope import actor_for, metadata_transaction
from app.modules.access_control.security_context import SecurityContext

NEWS_ENABLED = "nova.news_enabled"

#: Scheduled execution accounts publish editions; they never read the newspaper.
_SERVICE_ACCOUNT_PREFIX = "nova_task_service_"

_DENIED_DETAIL = "News is not enabled for this account"


async def is_news_enabled(username: str, actor: SecurityContext) -> bool:
    """True only when an administrator enabled News for ``username``.

    ``actor`` reads the row: the user themself, or an active administrator on
    the web login reading another user's entitlement. Unlike the login flags
    this is an access gate, so a missing row, a row RLS hides and a store error
    all mean disabled.
    """
    try:
        value = await read_value(actor, username, NEWS_ENABLED)
    except Exception:
        return False
    return str(value).lower() == "true"


async def set_news_enabled(username: str, *, enabled: bool, actor: dict) -> None:
    """Switch News on or off for ``username`` on behalf of an administrator.

    The flag and the administrator's success audit commit in one transaction.
    """
    if enabled and username.startswith(_SERVICE_ACCOUNT_PREFIX):
        raise ValueError("News cannot be enabled for a scheduled execution account")
    security = actor_for(actor)
    async with metadata_transaction(security):
        await write_values(security, username, {NEWS_ENABLED: "true" if enabled else "false"})
        await write_audit_log(
            event_type="USER_ADMIN",
            user_name=actor["username"],
            action="SET_NEWS_ENTITLEMENT",
            object_type="USER",
            object_name=username,
            status="SUCCESS",
            session_id=actor.get("session_id"),
            active_role=actor.get("active_role"),
            security_context_version=actor.get("security_context_version"),
            decision="ENABLE" if enabled else "DISABLE",
        )


async def require_news_entitlement(user: dict, *, action: str = "READ_NEWS") -> None:
    """Refuse and audit when News is not enabled for the caller."""
    try:
        security = actor_for(user)
    except HTTPException:
        security = None
    if security is not None and await is_news_enabled(user["username"], security):
        return
    # A failed audit write must not turn a refusal into an allow.
    with suppress(Exception):
        await write_audit_log(
            event_type="INTELLIGENCE",
            user_name=user["username"],
            action=action,
            object_type="NewsEntitlement",
            object_name=user["username"],
            status="DENIED",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
            security_context_version=user.get("security_context_version"),
            decision="DENY",
        )
    raise HTTPException(status_code=403, detail=_DENIED_DETAIL)


async def _news_user(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    await require_news_entitlement(user)
    return user


NewsUser = Annotated[dict, Depends(_news_user)]

__all__ = [
    "NEWS_ENABLED",
    "NewsUser",
    "is_news_enabled",
    "require_news_entitlement",
    "set_news_enabled",
]
