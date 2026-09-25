"""Run the durable Studio Auto worker: ``uv run python -m app.agent_worker``."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress
from uuid import uuid4

from app.common.secret_keys import require_configured_secrets
from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.modules.agents.capabilities import capability_repository
from app.modules.agents.harness_repository import harness_repository
from app.modules.agents.harness_worker import agent_harness_worker
from app.observability.metrics import start_metrics_server


async def _run() -> None:
    require_configured_secrets()
    await db.init_system_pool()
    await session_store.init()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover
            signal.signal(sig, lambda *_: stop.set())
    try:
        await capability_repository.ensure_schema()
        await harness_repository.ensure_schema()
        start_metrics_server("agent-worker")
        await agent_harness_worker.run_forever(stop, str(uuid4()))
    finally:
        stop.set()
        await session_store.close()
        await db.close_system_pool()


def main() -> None:
    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    with suppress(KeyboardInterrupt):
        asyncio.run(_run())


if __name__ == "__main__":
    main()
