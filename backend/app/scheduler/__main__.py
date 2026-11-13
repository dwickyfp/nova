"""Standalone scheduler with PG authority, Redis wakeups and engine observations."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import redis.asyncio as aioredis

from app.common.nova_system import init_task_orchestration
from app.common.secret_keys import require_configured_secrets
from app.core.config import settings
from app.core.database import db
from app.core.metadata_db import MetadataDatabase
from app.core.metadata_process import (
    close_process_metadata,
    load_runtime_settings,
    open_process_metadata,
    watch_metadata,
)
from app.modules.task_orchestration.repository import task_orchestration_repository
from app.modules.task_orchestration.schedule import engine_timezone_matches
from app.modules.task_orchestration.service import build_scheduler_service
from app.modules.task_orchestration.transport import RedisGraphRunTransport
from app.observability.metrics import start_metrics_server

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
    # Fail closed before the first connection: the scheduler signs session
    # tokens with SECRET_KEY, so a missing key must abort boot (NOVA-108).
    require_configured_secrets()
    metadata_settings = load_runtime_settings()
    # Fail closed on schema/epoch/login problems; wait only for an outage.
    metadata = await open_process_metadata("scheduler", settings=metadata_settings)
    if metadata is None:  # only possible when a stop event is supplied
        return
    try:
        await _run_admitted(metadata, metadata_settings.readiness_interval_seconds)
    finally:
        await close_process_metadata(metadata, "scheduler")


async def _run_admitted(metadata: MetadataDatabase, readiness_interval: float) -> None:
    await db.init_system_pool()
    try:
        await _assert_engine_timezone()
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

    service = build_scheduler_service(RedisGraphRunTransport(client))
    start_metrics_server("scheduler")
    readiness = asyncio.create_task(watch_metadata(
        metadata, "scheduler", stop_event, interval=readiness_interval,
    ))

    try:
        await service.run_forever(stop_event)
    finally:
        stop_event.set()
        await readiness
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
