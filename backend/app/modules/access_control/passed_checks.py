"""Briefly reused *passed* access checks for one caller.

Some checks repeat on every request yet cost a Ranger round trip and an engine
plan: an agent's dependency check (:func:`app.modules.agents.access.has_verified_access`)
and a Semantic View's source check. A check that **passed** is kept here for
``ACCESS_CHECK_CACHE_SECONDS``; a failed check is never kept, so a fix to a
grant is seen on the next request.

A key covers the namespace and what was checked, plus the caller's principal,
active role, session and security-context version, and an epoch every access
change made through Nova advances. A role switch, a new session or a managed
access change therefore never reads an older answer, and while such a change
may still be reaching the engine nothing is reused. A change made directly in
the policy store is bounded by the entry's lifetime, as with the News proof and
catalog visibility caches. Without Redis, or without a session, every check
runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from app.core.config import settings
from app.core.redis import session_store

logger = logging.getLogger(__name__)

_PREFIX = "nova:access:passed:"
_EPOCH = "nova:access:passed:epoch"
_SETTLING = "nova:access:passed:settling"


def _client() -> Any:
    return session_store._redis if settings.ACCESS_CHECK_CACHE_SECONDS > 0 else None


async def key(namespace: str, user: dict, parts: Sequence[str]) -> str | None:
    """Where a passed check is kept, or ``None`` when it must not be reused now."""
    client = _client()
    if client is None or not user.get("session_id") or not user.get("username"):
        return None
    try:
        if await client.get(_SETTLING):
            return None
        epoch = str(await client.get(_EPOCH) or "0")
    except Exception:  # noqa: BLE001 - a cache fault only means "check now"
        return None
    raw = "\x1f".join(
        [
            namespace,
            *parts,
            str(user["username"]),
            str(user.get("active_role") or ""),
            str(user.get("session_id")),
            str(int(user.get("security_context_version") or 1)),
            epoch,
        ]
    )
    return _PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def passed(entry: str | None) -> bool:
    if entry is None:
        return False
    try:
        return bool(await _client().get(entry))
    except Exception:  # noqa: BLE001 - a cache fault never decides access
        return False


async def remember(entry: str | None) -> None:
    if entry is None:
        return
    try:
        await _client().set(entry, "1", ex=settings.ACCESS_CHECK_CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        logger.debug("A passed access check could not be kept", exc_info=True)


async def retire() -> None:
    """Forget every kept check after an access change made through Nova."""
    client = session_store._redis
    if client is None:
        return
    try:
        await client.incr(_EPOCH)
        await client.set(
            _SETTLING, "1", ex=max(1, int(2 * settings.RANGER_POLICY_PROPAGATION_SECONDS))
        )
    except Exception:  # noqa: BLE001 - entries still expire on their own
        logger.warning("Kept access checks could not be retired; they expire on their own")


#: Checks running in this process, by key: concurrent requests with the same
#: key (the same caller, role, session and subject) wait for one check.
_inflight: dict[str, asyncio.Future[bool]] = {}


async def once(entry: str | None, check: Callable[[], Awaitable[bool]]) -> bool:
    """``check()``, run once for concurrent callers of the same key, kept if it passed.

    Without a key every caller runs its own check. A check that raises is
    re-raised to every waiter and is not kept.
    """
    if entry is None:
        return await check()
    if await passed(entry):
        return True
    running = _inflight.get(entry)
    if running is not None:
        return await asyncio.shield(running)
    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    _inflight[entry] = future
    try:
        result = await check()
    except BaseException as exc:
        if not future.done():
            future.set_exception(exc)
            future.exception()  # retrieved here; waiters re-raise it
        raise
    else:
        if result:
            await remember(entry)
        future.set_result(result)
        return result
    finally:
        _inflight.pop(entry, None)
