"""TCP listener for the Nova MySQL proxy.

The server is a thin shell: it owns the accept loop, the per-connection task
set and the bounded-concurrency limit, and hands each accepted socket to
``ProxyConnection``. Everything protocol-shaped lives there and in
``protocol.py``.

Lifecycle is designed for two callers with different needs:

* ``main.py`` embeds it in the FastAPI lifespan (``start`` / ``stop``), so the
  web service can expose port 4406 without a second process; and
* ``python -m app.proxy`` runs it standalone for deployments that split the
  proxy out (``docs/arch-07-mysql-proxy.md`` shows both).

Both go through :func:`serve` / :class:`MySQLProxyServer` so there is one
implementation of the accept loop, not two.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from app.core.config import settings
from app.observability.metrics import (
    PROXY_CONNECTIONS_ACTIVE,
    PROXY_CONNECTIONS_REJECTED,
    PROXY_LISTENER_UP,
)
from app.proxy.connection import ProxyConnection

logger = logging.getLogger(__name__)


class MySQLProxyServer:
    """An ``asyncio`` server for the MySQL wire protocol."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        max_connections: int | None = None,
        read_timeout: float | None = None,
    ) -> None:
        self._host = host if host is not None else settings.PROXY_HOST
        self._port = port if port is not None else settings.PROXY_PORT
        self._max_connections = (
            max_connections if max_connections is not None else settings.PROXY_MAX_CONNECTIONS
        )
        self._read_timeout = (
            read_timeout if read_timeout is not None else float(settings.PROXY_READ_TIMEOUT)
        )
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.Task] = set()
        self._next_connection_id = 1000

    @property
    def bound_port(self) -> int:
        """The port actually bound. Meaningful after ``start``.

        Differs from ``self._port`` when port 0 was requested, which is how the
        tests get an ephemeral port instead of racing for 4406.
        """
        if self._server is None:
            return self._port
        for socket in self._server.sockets or ():
            address = socket.getsockname()
            if isinstance(address, tuple) and len(address) >= 2:
                return int(address[1])
        return self._port

    async def start(self) -> None:
        """Bind and begin accepting. Returns once the socket is listening."""
        self._server = await asyncio.start_server(
            self._on_client,
            host=self._host,
            port=self._port,
        )
        PROXY_LISTENER_UP.set(1)
        logger.info(
            "Nova MySQL proxy listening on %s:%s (max_connections=%d)",
            self._host,
            self.bound_port,
            self._max_connections,
        )

    async def stop(self) -> None:
        """Stop accepting and wait for in-flight connections to finish.

        In-flight connections are *awaited*, not cancelled: a client mid-query
        gets its result and a clean disconnect instead of a truncated packet,
        which is the difference between a restart and a visible error.
        """
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        PROXY_LISTENER_UP.set(0)

        if self._connections:
            await asyncio.gather(*tuple(self._connections), return_exceptions=True)
        self._connections.clear()
        logger.info("Nova MySQL proxy stopped")

    async def serve_forever(self) -> None:
        await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._connections) >= self._max_connections:
            # Refusing at accept time keeps the limit honest: the socket is
            # closed before a handshake is written, so the client sees a
            # connection error rather than a login that hangs.
            logger.warning("Connection limit (%d) reached; refusing client", self._max_connections)
            PROXY_CONNECTIONS_REJECTED.inc()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return

        self._next_connection_id += 1
        connection = ProxyConnection(
            reader,
            writer,
            connection_id=self._next_connection_id,
            read_timeout=self._read_timeout,
        )
        task = asyncio.current_task()
        if task is not None:
            self._connections.add(task)
        PROXY_CONNECTIONS_ACTIVE.inc()
        try:
            await connection.run()
        finally:
            PROXY_CONNECTIONS_ACTIVE.dec()
            if task is not None:
                self._connections.discard(task)


async def serve(
    *,
    host: str | None = None,
    port: int | None = None,
    ready: Callable[[int], None] | None = None,
) -> None:
    """Run the proxy until cancelled. ``ready`` receives the bound port."""
    server = MySQLProxyServer(host=host, port=port)
    await server.start()
    try:
        if ready is not None:
            ready(server.bound_port)
        assert server._server is not None
        async with server._server:
            await server._server.serve_forever()
    finally:
        await server.stop()


__all__ = ["MySQLProxyServer", "serve"]
