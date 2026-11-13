"""Live health probes for Nova's local runtime services."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

import redis.asyncio as aioredis
from pydantic import BaseModel

from app.common.metadata_actors import metadata_scope
from app.core.config import settings
from app.modules.access_control.security_context import SecurityContext
from app.modules.task_orchestration.leadership import SCHEDULER_LEASE_NAME

HealthStatus = Literal["healthy", "unhealthy", "unknown"]


class RuntimeServiceHealth(BaseModel):
    status: HealthStatus
    message: str
    instances: int | None = None


class RuntimeHealthResponse(BaseModel):
    checked_at: datetime
    redis: RuntimeServiceHealth
    scheduler: RuntimeServiceHealth
    worker: RuntimeServiceHealth


async def scheduler_lease_active(actor: SecurityContext) -> bool:
    async with metadata_scope(actor, read_only=True) as connection:
        row = await (await connection.execute(
            "SELECT EXISTS (SELECT 1 FROM nova_meta.scheduler_leases "
            "WHERE name=%s AND expires_at>clock_timestamp()) AS active",
            (SCHEDULER_LEASE_NAME,),
        )).fetchone()
    return bool(row and row["active"])


class RuntimeHealthProbe:
    """Probe Redis plus the liveness markers owned by scheduler and workers."""

    def __init__(
        self,
        *,
        client_factory: Callable[..., Any] = aioredis.from_url,
        clock: Callable[[], float] = time.time,
        scheduler_probe: Callable[[SecurityContext], Awaitable[bool]] = scheduler_lease_active,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._client_factory = client_factory
        self._clock = clock
        self._scheduler_probe = scheduler_probe
        self._timeout_seconds = timeout_seconds

    async def collect(self, actor: SecurityContext) -> RuntimeHealthResponse:
        checked_at = datetime.fromtimestamp(self._clock(), tz=UTC)
        scheduler = await self._scheduler_health(actor)
        client = self._client_factory(settings.REDIS_URL, decode_responses=True)
        try:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    redis_ok = bool(await client.ping())
            except Exception:
                redis_ok = False

            if not redis_ok:
                return RuntimeHealthResponse(
                    checked_at=checked_at,
                    redis=RuntimeServiceHealth(
                        status="unhealthy", message="Redis did not respond to PING."
                    ),
                    scheduler=scheduler,
                    worker=RuntimeServiceHealth(
                        status="unknown",
                        message="Worker state cannot be read while Redis is unavailable.",
                    ),
                )

            worker = await self._worker_health(client)
            return RuntimeHealthResponse(
                checked_at=checked_at,
                redis=RuntimeServiceHealth(
                    status="healthy", message="Redis responded to PING."
                ),
                scheduler=scheduler,
                worker=worker,
            )
        finally:
            await client.aclose()

    async def _scheduler_health(self, actor: SecurityContext) -> RuntimeServiceHealth:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                active = await self._scheduler_probe(actor)
        except Exception:
            return RuntimeServiceHealth(
                status="unknown", message="Scheduler leader lease could not be read."
            )
        if active:
            return RuntimeServiceHealth(
                status="healthy", message="An active scheduler holds the leader lease."
            )
        return RuntimeServiceHealth(
            status="unhealthy", message="No scheduler currently holds the leader lease."
        )

    async def _worker_health(self, client: Any) -> RuntimeServiceHealth:
        cutoff = self._clock() - settings.WORKER_PROCESS_STALE_SECONDS
        try:
            async with asyncio.timeout(self._timeout_seconds):
                instances = int(
                    await client.zcount(settings.WORKER_REGISTRY_KEY, cutoff, "+inf")
                )
        except Exception:
            return RuntimeServiceHealth(
                status="unknown", message="Worker heartbeats could not be read."
            )
        if instances > 0:
            noun = "worker" if instances == 1 else "workers"
            return RuntimeServiceHealth(
                status="healthy",
                message=f"{instances} active {noun} reporting heartbeats.",
                instances=instances,
            )
        return RuntimeServiceHealth(
            status="unhealthy",
            message="No worker heartbeat was received within the expected window.",
            instances=0,
        )


runtime_health_probe = RuntimeHealthProbe()
