"""Identical access checks that run at the same time share one run.

Some checks repeat across the parallel requests of one page load and each costs
a Ranger round trip and an engine plan: an agent's dependency check
(:func:`app.modules.agents.access.has_verified_access`) and a Semantic View's
source check. Concurrent callers with the same key wait for one run of the
check and all get its answer.

Nothing is kept once the run finishes: the next request checks again, so a
grant revoked anywhere (in Nova or directly in the policy store) denies at
once. A key covers the namespace and what was checked, plus the caller's
principal, active role, session and security-context version, so callers in
different security contexts never share a run.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence


async def key(namespace: str, user: dict, parts: Sequence[str]) -> str | None:
    """The key concurrent identical checks share, or ``None`` to always run alone."""
    if not user.get("session_id") or not user.get("username"):
        return None
    raw = "\x1f".join(
        [
            namespace,
            *parts,
            str(user["username"]),
            str(user.get("active_role") or ""),
            str(user.get("session_id")),
            str(int(user.get("security_context_version") or 1)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


#: Checks running in this process, by key.
_inflight: dict[str, asyncio.Future[bool]] = {}


async def once(entry: str | None, check: Callable[[], Awaitable[bool]]) -> bool:
    """``check()``, run once for callers of the same key while it is running.

    Without a key every caller runs its own check. A check that raises is
    re-raised to every waiter.
    """
    if entry is None:
        return await check()
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
        future.set_result(result)
        return result
    finally:
        _inflight.pop(entry, None)
