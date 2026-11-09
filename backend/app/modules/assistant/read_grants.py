"""The "always allow read-only" grant of one Neva conversation, kept in Redis.

The grant used to live only in the process, so a restart or a second worker
asked again. It is now kept per user and thread, and stamped with the active
role and security-context version it was given under: after a role switch, or
when the session changes, it no longer applies and is removed. Redis owns this
runtime state (root contract); without Redis the grant simply is not restored,
so Neva asks again (fail closed).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: A grant lasts as long as a working day at most; the user can revoke it.
GRANT_SECONDS = 12 * 3600


def _key(user_name: str, thread_id: str) -> str:
    return f"nova:assistant:read-grant:{user_name}:{thread_id}"


def stamp(user: dict[str, Any]) -> dict[str, Any]:
    """The role and session version a grant belongs to.

    A session without a valid active role gets an empty stamp; its turns are
    refused by the loop anyway, and the stamp never matches a real role.
    """
    from app.modules.access_control.security_context import (
        SecurityContext,
        SecurityContextError,
    )

    try:
        security = SecurityContext.from_session(user)
    except SecurityContextError:
        return {"role": None, "version": None}
    return {"role": security.active_role, "version": security.security_context_version}


def _redis() -> Any:
    from app.core.redis import session_store

    return session_store._redis  # noqa: SLF001 - the shared runtime connection


async def remember(user: dict[str, Any], thread_id: str, granted: bool) -> None:
    """Keep or drop the grant for this user, thread and security context."""
    redis = _redis()
    if redis is None:
        return
    key = _key(user["username"], thread_id)
    try:
        if granted:
            await redis.set(key, json.dumps(stamp(user)), ex=GRANT_SECONDS)
        else:
            await redis.delete(key)
    except Exception as exc:  # noqa: BLE001 - the in-process grant still applies
        logger.info("Read-only grant not stored: %s", type(exc).__name__)


async def restore(user: dict[str, Any], thread_id: str) -> bool:
    """Whether a stored grant applies to the caller's current security context."""
    redis = _redis()
    if redis is None:
        return False
    key = _key(user["username"], thread_id)
    try:
        raw = await redis.get(key)
        if not raw:
            return False
        if json.loads(raw) == stamp(user):
            return True
        # Given under another role or session version: never carried over.
        await redis.delete(key)
    except Exception as exc:  # noqa: BLE001 - no grant is the safe answer
        logger.info("Read-only grant not restored: %s", type(exc).__name__)
    return False


async def apply(runtime: Any, user: dict[str, Any], thread_id: str) -> None:
    """Start a turn with the grant that belongs to the caller's current context.

    A grant given under another role or session version is dropped, in memory
    and in Redis; a stored grant for this context is restored after a restart.
    """
    current = stamp(user)
    consent = runtime.consent
    if consent.always_allow_read_only and consent.granted_under != current:
        consent.always_allow_read_only = False
        consent.granted_under = None
        await remember(user, thread_id, False)
    if not consent.always_allow_read_only and await restore(user, thread_id):
        consent.always_allow_read_only = True
        consent.granted_under = current


async def grant(runtime: Any, user: dict[str, Any], thread_id: str, granted: bool) -> None:
    """Give or take back the grant for the caller's current context."""
    runtime.consent.always_allow_read_only = granted
    runtime.consent.granted_under = stamp(user) if granted else None
    await remember(user, thread_id, granted)
