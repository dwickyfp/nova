"""Keeps the catalog store in step with the engine in the background.

Runs in every control-plane process; a Redis leader lock lets one instance do
each pass. A pass reconciles the catalog list and syncs every live catalog
(:func:`app.modules.catalog.sync.reconcile_catalogs`), then harvests lineage
from ``AUDIT_LOG`` and re-links Semantic Views and entities to securable ids.
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


async def metadata_pass() -> None:
    """Harvest lineage and re-link Semantic Views and entities to securables."""
    from .lineage import harvest_lineage
    from .links import reconcile_links

    for step in (harvest_lineage, reconcile_links):
        try:
            await step()
        except Exception:  # noqa: BLE001 - one step never stops the other
            logger.warning("Catalog %s pass failed", step.__name__, exc_info=True)


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
        await metadata_pass()
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
