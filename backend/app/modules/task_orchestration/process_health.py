"""Process-level worker liveness in Redis.

Task-run heartbeats only move while a node is executing. The monitoring page
also needs to distinguish an idle worker from no worker, so each worker records
an opaque instance id in a sorted set on a short cadence. Timestamps are the
only stored values; no user, task body, or credential enters this registry.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)


class WorkerProcessHeartbeat:
    """Publish and clean up one worker process's liveness marker."""

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        instance_id: str | None = None,
        registry_key: str | None = None,
        interval_seconds: float | None = None,
        stale_seconds: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._instance_id = instance_id or f"{settings.WORKER_NAME}:{uuid.uuid4()}"
        self._registry_key = registry_key or settings.WORKER_REGISTRY_KEY
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else settings.WORKER_PROCESS_HEARTBEAT_INTERVAL_SECONDS
        )
        self._stale_seconds = (
            stale_seconds if stale_seconds is not None else settings.WORKER_PROCESS_STALE_SECONDS
        )
        self._clock = clock

    async def publish(self) -> None:
        """Record a fresh timestamp and prune long-dead registry entries."""
        now = self._clock()
        await self._client.zadd(self._registry_key, {self._instance_id: now})
        await self._client.zremrangebyscore(
            self._registry_key,
            "-inf",
            now - (self._stale_seconds * 10),
        )

    async def run(self, stop_event: asyncio.Event) -> None:
        """Publish immediately, then continue until the worker stops."""
        while not stop_event.is_set():
            try:
                await self.publish()
            except Exception:
                logger.exception("worker process heartbeat failed; continuing")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)

    async def close(self) -> None:
        """Remove the marker on a graceful shutdown."""
        try:
            await self._client.zrem(self._registry_key, self._instance_id)
        except Exception:
            logger.exception("worker process heartbeat cleanup failed")
