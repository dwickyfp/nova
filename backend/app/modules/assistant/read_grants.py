"""Read-only conversation grants bound to one login and security context.

Redis retains the grant across process restarts until expiry or revocation.
Every new turn reloads it; unavailable storage cannot restore approval. The
approved control-plane migration will replace this runtime persistence with
durable consent, without changing the shared inference loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.core.exceptions import InvalidCredentialsError

logger = logging.getLogger(__name__)

#: A grant lasts as long as a working day at most; the user can revoke it.
GRANT_SECONDS = 12 * 3600

_DELETE_MATCHING_GRANT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def _key(user_name: str, thread_id: str, session_id: str) -> str:
    identity = json.dumps([user_name, thread_id, session_id], separators=(",", ":"))
    return "nova:assistant:read-grant:v2:" + hashlib.sha256(identity.encode()).hexdigest()


def stamp(user: dict[str, Any]) -> dict[str, Any] | None:
    """An approval cannot outlive its login or active-role boundary."""
    from app.modules.access_control.security_context import SecurityContext

    try:
        security = SecurityContext.from_session(user)
    except (TypeError, ValueError):
        return None
    session_id = security.session_id
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 64:
        return None
    return {
        "principal": security.principal,
        "role": security.active_role,
        "version": security.security_context_version,
        "session_id": session_id,
    }


def _redis() -> Any:
    from app.core.redis import session_store

    return session_store._redis  # noqa: SLF001 - the shared runtime connection


async def remember(user: dict[str, Any], thread_id: str, granted: bool) -> None:
    """Keep or drop the grant for this user, thread and security context."""
    redis = _redis()
    current = stamp(user)
    if redis is None or current is None:
        return
    key = _key(current["principal"], thread_id, current["session_id"])
    try:
        if granted:
            await redis.set(key, json.dumps(current), ex=GRANT_SECONDS)
        else:
            await redis.delete(key)
    except Exception as exc:  # noqa: BLE001 - the in-process grant still applies
        logger.info("Read-only grant not stored: %s", type(exc).__name__)


async def restore(user: dict[str, Any], thread_id: str) -> bool:
    """Whether a stored grant applies to the caller's current security context."""
    redis = _redis()
    current = stamp(user)
    if redis is None or current is None:
        return False
    key = _key(current["principal"], thread_id, current["session_id"])
    try:
        raw = await redis.get(key)
        if not raw:
            return False
        if json.loads(raw) == current:
            return True
        # Do not erase a newer grant written while this stale value was read.
        await redis.eval(_DELETE_MATCHING_GRANT, 1, key, raw)
    except Exception as exc:  # noqa: BLE001 - no grant is the safe answer
        logger.info("Read-only grant not restored: %s", type(exc).__name__)
    return False


async def apply(runtime: Any, user: dict[str, Any], thread_id: str) -> None:
    """Start a turn with the grant that belongs to the caller's current context.

    Reload even when this process remembers approval, so expiry and revocation
    by another process take effect before the next turn.
    """
    current = stamp(user)
    consent = runtime.consent
    granted = current is not None and await restore(user, thread_id) and stamp(user) == current
    consent.always_allow_read_only = granted
    consent.granted_under = current if granted else None


async def grant(runtime: Any, user: dict[str, Any], thread_id: str, granted: bool) -> None:
    """Give or take back the grant for the caller's current context."""
    current = stamp(user)
    if granted and current is None:
        raise InvalidCredentialsError("Sign in again before approving read-only tools")
    runtime.consent.always_allow_read_only = granted
    runtime.consent.granted_under = current if granted else None
    await remember(user, thread_id, granted)
