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

from app.core.config import settings
from app.core.database import db
from app.modules.task_orchestration.schedule import engine_timezone_matches
from app.modules.task_orchestration.service import build_scheduler_service
from app.modules.task_orchestration.transport import (
    LeaderLock,
    RedisGraphRunTransport,
)

logger = logging.getLogger(__name__)


class EngineTimezoneMismatchError(RuntimeError):
    """Raised when SCHEDULER_ENGINE_TIMEZONE disagrees with the engine."""


async def _assert_engine_timezone() -> str:
    """Fail fast unless the configured zone matches the engine's session zone.

    ``created_at`` is written by ``NOW()`` as a naive ``DATETIME`` in the engine's
    session timezone. If the configured zone disagrees, every interval anchor is
    silently shifted — the 7-hour bug that made interval tasks never due. The
    engine is the source of truth, so startup reads ``SELECT @@time_zone`` and
    refuses to run rather than scheduling wrong.
    """
    engine_zone = await db.probe_engine_timezone()
    configured = settings.SCHEDULER_ENGINE_TIMEZONE
    if not engine_timezone_matches(configured, engine_zone):
        raise EngineTimezoneMismatchError(
            f"SCHEDULER_ENGINE_TIMEZONE={configured!r} does not match the engine "
            f"session timezone {engine_zone!r}. Set SCHEDULER_ENGINE_TIMEZONE to "
            f"{engine_zone!r} (or change the engine's time_zone) — a mismatch "
            "silently shifts every interval anchor and tasks never become due."
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
