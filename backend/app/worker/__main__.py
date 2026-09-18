"""``nova-worker`` — the standalone graph-execution process.

The worker is a **separate process**, never embedded in the FastAPI lifespan:
it must scale horizontally and survive independently of the web service. It
consumes graph runs the scheduler published, executes each ready node as its
owner (delegate-first), and advances the graph's state in ``NOVA_SYSTEM``.

    uv run python -m app.worker

It initialises the system connection pool (the repository reads and writes
``NOVA_SYSTEM`` through it), connects to Redis, then consumes until ``SIGINT``
or ``SIGTERM``. Credentials are resolved per execution from the live session
path and discarded when the connection closes; nothing is written to
``NOVA_SYSTEM`` except ids, states and timings.
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
from app.modules.task_orchestration.credentials import SessionCredentialProvider
from app.modules.task_orchestration.execution import DelegateExecutor
from app.modules.task_orchestration.reconciler import Reconciler
from app.modules.task_orchestration.repository import (
    task_orchestration_repository as repository,
)
from app.modules.task_orchestration.worker_service import WorkerService

logger = logging.getLogger(__name__)


def build_worker_service(client: aioredis.Redis) -> WorkerService:
    """Wire the default worker from a Redis client."""
    executor = DelegateExecutor(SessionCredentialProvider(client))
    return WorkerService(
        repository,
        executor,
        GraphRunConsumer(client),
        Reconciler(repository),
    )


async def _run() -> None:
    # Fail closed before the first connection: the worker decrypts stored
    # credentials (SessionCredentialProvider), so a blank/invalid FERNET_KEY
    # must abort boot rather than fail every task at runtime (NOVA-108).
    require_configured_secrets()
    await db.init_system_pool()
    await init_task_orchestration()
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
