"""``nova-scheduler`` — the standalone process entrypoint.

The scheduler is a **separate process**, never embedded in the FastAPI lifespan:
unlike the MySQL proxy, it does not share the web process's lifecycle, and a
singleton is easier to reason about when it is the only thing in its process.

    uv run python -m app.scheduler

It initialises the system connection pool (the repository reads and writes
``NOVA_SYSTEM`` through it), connects to Redis, then ticks until ``SIGINT`` or
``SIGTERM``. It never executes SQL against StarRocks — only ``NOVA_SYSTEM``
metadata reads/writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import redis.asyncio as aioredis

from app.common.nova_system import init_task_orchestration
from app.core.config import settings
from app.core.database import db
from app.modules.task_orchestration.repository import task_orchestration_repository
from app.modules.task_orchestration.schedule import engine_timezone_matches
from app.modules.task_orchestration.service import build_scheduler_service
from app.modules.task_orchestration.transport import (
    LeaderLock,
    RedisGraphRunTransport,
)

logger = logging.getLogger(__name__)


class EngineTimezoneMismatchError(RuntimeError):
    """Raised when an explicit SCHEDULER_ENGINE_TIMEZONE disagrees with the engine."""


async def _assert_engine_timezone() -> str | None:
    """Fail fast when an explicit zone override contradicts the engine.

    By default the zone is auto-detected from ``SELECT @@time_zone`` and cannot be
    wrong. When an operator pins ``SCHEDULER_ENGINE_TIMEZONE``, startup verifies it
    against the engine: a mismatch shifts every interval anchor by the offset
    difference — the 7-hour bug that made interval tasks never due — so refusing
    to run is the safe failure. Returns the effective zone, or ``None`` when the
    engine stays silent and there is nothing to verify against.
    """
    engine_zone = await task_orchestration_repository.get_engine_timezone()
    if engine_zone is None:
        logger.warning(
            "engine reported no session timezone; skipping the timezone guard"
        )
        return None
    configured = (settings.SCHEDULER_ENGINE_TIMEZONE or "").strip()
    if not configured:
        logger.info("engine timezone auto-detected: %s", engine_zone)
        return engine_zone
    if not engine_timezone_matches(configured, engine_zone):
        raise EngineTimezoneMismatchError(
            f"SCHEDULER_ENGINE_TIMEZONE={configured!r} does not match the engine "
            f"session timezone {engine_zone!r}. It is an override for unusual "
            "deployments only; unset it to use the engine's zone, or set it to "
            f"{engine_zone!r} — a mismatch silently shifts every interval anchor "
            "and tasks never become due."
        )
    logger.info(
        "engine timezone verified: %s (SCHEDULER_ENGINE_TIMEZONE=%s)",
        engine_zone,
        configured,
    )
    return engine_zone


async def _run() -> None:
    await db.init_system_pool()
    try:
        await _assert_engine_timezone()
        # The scheduler reads CONFIG_TASK_GRAPH_RUNS (including heartbeat_at),
        # so it must ensure the schema exists and is up to date. Idempotent:
        # creates the tables when absent and adds late columns to a table an
        # older release (or init-nova.sql) created without them.
        await init_task_orchestration()
    except Exception:
        await db.close_system_pool()
        raise
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    stop_event = asyncio.Event()

    def _request_stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        signal_number = getattr(signal, signal_name, None)
        if signal_number is None:
            continue
        try:
            loop.add_signal_handler(signal_number, _request_stop)
        except NotImplementedError:  # pragma: no cover - Windows
            signal.signal(signal_number, lambda *_: _request_stop())

    service = build_scheduler_service(
        RedisGraphRunTransport(client),
        LeaderLock(client),
    )

    try:
        await service.run_forever(stop_event)
    finally:
        await client.aclose()
        await db.close_system_pool()


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


if __name__ == "__main__":
    main()
