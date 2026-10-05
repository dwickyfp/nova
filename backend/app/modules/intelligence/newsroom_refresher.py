"""Keeps recent News readers' access proofs warm in the background.

Each pass re-proves the newest edition for sessions that read News recently and
still exist, using that session exactly as a request from the reader would. No
service or impersonated identity is involved, and a session that expired,
logged out, lost its News entitlement or went idle is simply dropped.
"""

from __future__ import annotations

import asyncio
import logging

from app.common.news_entitlement import is_news_enabled
from app.core.config import settings
from app.core.redis import session_store
from app.core.security import decrypt_password
from app.modules.intelligence.newsroom_cache import proof_cache
from app.modules.task_orchestration.transport import LeaderLock

logger = logging.getLogger(__name__)

_LOCK = "nova:news:refresher"
_READERS_PER_PASS = 20
_CONCURRENCY = 2


async def session_user(session_id: str) -> dict | None:
    """The caller a request with this session would be, or ``None`` if it is gone."""
    session = await session_store.get(session_id)
    if not session:
        return None
    try:
        decrypt_password(session["encrypted_password"])
    except Exception:
        return None
    return {
        "username": session["username"],
        "session_id": session_id,
        "roles": session["roles"],
        "assigned_roles": session["assigned_roles"],
        "default_role": session.get("default_role"),
        "active_role": session.get("active_role"),
        "security_context_version": session.get("security_context_version", 1),
        "encrypted_password": session["encrypted_password"],
    }


async def refresh_once(service=None, cache=proof_cache) -> int:
    """One pass over recent readers; returns how many were refreshed."""
    if service is None:
        from app.modules.intelligence.newsroom import newsroom_service as service

    gate = asyncio.Semaphore(_CONCURRENCY)

    async def refresh(session_id: str) -> bool:
        async with gate:
            try:
                user = await session_user(session_id)
                if user is None or not await is_news_enabled(user["username"]):
                    await cache.forget(session_id)
                    return False
                await service.refresh_reader(user)
                return True
            except Exception:
                logger.warning("News proof refresh failed for one reader", exc_info=True)
                return False

    done = await asyncio.gather(
        *(refresh(session_id) for session_id in await cache.readers(_READERS_PER_PASS))
    )
    return sum(done)


async def run(stop: asyncio.Event) -> None:
    """Refresh on an interval until ``stop`` is set; one instance works per pass."""
    while not stop.is_set():
        try:
            if proof_cache.enabled:
                lock = LeaderLock(
                    proof_cache.client,
                    key=_LOCK,
                    ttl_seconds=max(60, settings.NEWS_PROOF_REFRESH_INTERVAL_SECONDS * 4),
                )
                if await lock.acquire():
                    try:
                        await refresh_once()
                    finally:
                        await lock.release()
        except Exception:
            logger.warning("News proof refresher pass failed", exc_info=True)
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.NEWS_PROOF_REFRESH_INTERVAL_SECONDS
            )
        except TimeoutError:
            continue
