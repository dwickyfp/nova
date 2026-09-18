"""``python -m app.proxy`` — run the MySQL proxy as its own process.

The embedded path (FastAPI's lifespan) and this one share
``MySQLProxyServer``; the only thing this module adds is process lifecycle:
signal handling that turns ``SIGINT``/``SIGTERM`` into a graceful shutdown so
in-flight queries finish and the port is released before the process exits.

The system connection pool is initialised here because ``QueryService`` writes
audit rows through it and reads ``@stage`` configs from it. Without it every
statement would fail on the first audit write.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from app.common.secret_keys import guard_process_startup
from app.core.config import settings
from app.core.database import db
from app.proxy.server import MySQLProxyServer

logger = logging.getLogger(__name__)


async def _run() -> None:
    # Fail fast on missing/placeholder signing and encryption keys (NOVA-108),
    # before the pool opens a connection.
    guard_process_startup()
    await db.init_system_pool()
    server = MySQLProxyServer()

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

    await server.start()
    try:
        await stop_event.wait()
    finally:
        await server.stop()
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
