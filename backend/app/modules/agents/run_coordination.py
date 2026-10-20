"""Shared Redis coordination for durable run rows and harness event writers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

from app.core.database import db
from app.core.redis import session_store

logger = logging.getLogger(__name__)

@asynccontextmanager
async def coordination_lock(
    client: Any, key: str, *, ttl_seconds: int = 600, renew_seconds: float = 30,
):
    lock = client.lock(
        key,
        timeout=ttl_seconds,
        blocking_timeout=30,
    )
    async with lock:
        stopped = asyncio.Event()
        lost = asyncio.Event()

        async def renew() -> None:
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(
                        stopped.wait(), timeout=renew_seconds
                    )
                    return
                except TimeoutError:
                    pass
                try:
                    if not await lock.extend(ttl_seconds, replace_ttl=True):
                        lost.set()
                        return
                except Exception as exc:
                    logger.warning("Auto event lock renewal failed: %s", type(exc).__name__)
                    lost.set()
                    return

        renewal = asyncio.create_task(renew())

        async def assert_owned() -> None:
            if lost.is_set() or not await lock.owned():
                raise RuntimeError("Auto event lock was lost")

        try:
            yield assert_owned
        finally:
            stopped.set()
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal


async def update_run(run_id: str, sql: str, params: list[Any]) -> dict:
    # StarRocks UPDATE rewrites a row; overlapping processes can restore stale status.
    client = session_store._redis
    if client is None:
        raise RuntimeError("Agent run write coordinator is unavailable")
    async with coordination_lock(client, f"nova:agent:run-write:{run_id}") as owned:
        await owned()
        result = await db.execute_system(sql, params)
        await owned()
        return result
