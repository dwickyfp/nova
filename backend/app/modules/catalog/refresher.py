"""Keeps the catalog store in step with the engine in the background.

Runs in every control-plane process; a Redis leader lock lets one instance do
each pass. A pass reconciles the catalog list and syncs every live catalog
(:func:`app.modules.catalog.sync.reconcile_catalogs`).
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.core.redis import session_store
from app.modules.task_orchestration.transport import LeaderLock

from .sync import reconcile_catalogs

logger = logging.getLogger(__name__)

_LOCK = "nova:catalog:refresher"


def _interval() -> int:
    return max(30, settings.CATALOG_SYNC_INTERVAL_SECONDS)


async def refresh_once() -> bool:
    """One pass if this instance wins the lock; returns whether it ran."""
    client = session_store._redis
    if client is None:
        return False
    lock = LeaderLock(client, key=_LOCK, ttl_seconds=max(120, _interval() * 4))
    if not await lock.acquire():
        return False
    try:
        await reconcile_catalogs(trigger="schedule")
    finally:
        await lock.release()
    return True


async def run(stop: asyncio.Event) -> None:
    """Sync on an interval until ``stop`` is set; the first pass runs at once."""
    while not stop.is_set():
        try:
            await refresh_once()
        except Exception:
            logger.warning("Catalog refresher pass failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_interval())
        except TimeoutError:
            continue
