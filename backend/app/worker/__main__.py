"""``nova-worker`` — the standalone graph-execution process.

The worker is a **separate process**, never embedded in the FastAPI lifespan:
it must scale horizontally and survive independently of the web service. It
consumes graph runs the scheduler published, executes each ready node as its
owner (delegate-first), and advances the graph's state in ``NOVA_SYSTEM``.

    uv run python -m app.worker

It initialises the system connection pool (the repository reads and writes
``NOVA_SYSTEM`` through it), connects to Redis, then consumes until ``SIGINT``
or ``SIGTERM``. A dedicated worker account impersonates each task owner on a
fresh connection; its credential comes from the worker environment and is never
written to ``NOVA_SYSTEM``.
"""

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
from app.modules.task_orchestration.consumer import GraphRunConsumer
from app.modules.task_orchestration.execution import DelegateExecutor
from app.modules.task_orchestration.process_health import WorkerProcessHeartbeat
from app.modules.task_orchestration.reconciler import Reconciler
from app.modules.task_orchestration.repository import (
    task_orchestration_repository as repository,
)
from app.modules.task_orchestration.worker_service import WorkerService

logger = logging.getLogger(__name__)


def build_worker_service(client: aioredis.Redis) -> WorkerService:
    """Wire the default worker from a Redis client."""
    executor = DelegateExecutor(
        None,
        impersonation_user=settings.WORKER_IMPERSONATION_USER,
        impersonation_password=settings.WORKER_IMPERSONATION_PASSWORD,
        impersonation_role=settings.WORKER_IMPERSONATION_ROLE,
    )
    return WorkerService(
        repository,
        executor,
        GraphRunConsumer(client),
        Reconciler(repository),
    )


async def _run() -> None:
    # Validate configured secrets and the worker account before connecting.
    require_configured_secrets()
    # Validate before opening the system pool or consuming any graph runs.
    if not settings.WORKER_IMPERSONATION_USER or not settings.WORKER_IMPERSONATION_PASSWORD:
        raise RuntimeError("worker impersonation credentials must be configured")
    if settings.WORKER_IMPERSONATION_USER.lower() in {"root", "nova_admin"}:
        raise RuntimeError("the task worker requires a dedicated unprivileged account")
    if settings.RANGER_ENABLED and not settings.WORKER_IMPERSONATION_ROLE:
        raise RuntimeError("Ranger mode requires a dedicated task worker role")
    await db.init_system_pool()
    try:
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

    service = build_worker_service(client)
    heartbeat = WorkerProcessHeartbeat(client)
    heartbeat_task = asyncio.create_task(
        heartbeat.run(stop_event), name="nova-worker-process-heartbeat"
    )
    try:
        await service.run_forever(stop_event)
    finally:
        stop_event.set()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        await heartbeat.close()
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
