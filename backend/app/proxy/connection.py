"""One client connection, from handshake to ``COM_QUIT``.

The loop here is intentionally sequential: MySQL's text protocol is
request/response, a connection carries one in-flight command, and the proxy has
no state that needs interleaving to stay consistent. Concurrency lives one
level up, in the server's per-connection tasks (``app/proxy/server.py``).

Three ordering rules are load-bearing, and each is a bug the MySQL handshake
protocol will punish immediately:

* The handshake is written **before** anything is read from the client.
* A failed login is an ``ERR`` packet **then** a close — never an ``OK`` and
  never a silent drop. ``auth.authenticate`` raises ``AuthenticationError``
  with a client-safe message, and no traceback ever reaches the wire.
* Sequence ids restart at 1 for every command response. A client counts them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass

import asyncmy

from app.core.config import settings
from app.modules.access_control.role_activation import (
    RoleActivationError,
    role_activation_service,
)
from app.observability.metrics import PROXY_QUERIES
from app.proxy.auth import (
    AuthenticatedUser,
    AuthenticationError,
    StarRocksLogin,
    open_starrocks_login,
)
from app.proxy.executor import ProxyQueryExecutor
from app.proxy.protocol import (
    CLIENT_DEPRECATE_EOF,
    COM_CHANGE_USER,
    COM_FIELD_LIST,
    COM_INIT_DB,
    COM_PING,
    COM_QUERY,
    COM_QUIT,
    COM_SET_OPTION,
    COM_STMT_CLOSE,
    COM_STMT_EXECUTE,
    COM_STMT_PREPARE,
    COM_STMT_RESET,
    ER_ACCESS_DENIED_ERROR,
    ER_NO_DB_ERROR,
    ER_NOT_SUPPORTED_YET,
    ER_UNKNOWN_COM_ERROR,
    MAX_PACKET_SIZE,
    SERVER_CAPABILITIES,
    ProtocolError,
    build_error_packet,
    build_handshake_packet,
    build_ok_packet,
    build_resultset,
    encode_packet,
    parse_handshake_response,
)
from app.proxy.session import SessionState

logger = logging.getLogger(__name__)

#: Advertised thread id. StarRocks' own connection ids are not visible here, so
#: the proxy mints its own; clients only use it for ``KILL``.
_START_CONNECTION_ID = 1000


@dataclass
class ConnectionContext:
    """Everything one connection needs for the length of its life."""

    connection_id: int
    peer: str
    session: SessionState
    capabilities: int = SERVER_CAPABILITIES
    user: AuthenticatedUser | None = None
    session_id: str | None = None
    connection: asyncmy.Connection | None = None
    upstream: StarRocksLogin | None = None


class ProxyConnection:
    """Handles one accepted socket."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        connection_id: int,
        read_timeout: float = 300.0,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._read_timeout = read_timeout
        self._ctx = ConnectionContext(
            connection_id=connection_id,
            peer=self._peer_name(writer),
            session=SessionState(),
        )
        self._executor = ProxyQueryExecutor(self._ctx.session)
        self._closing = False

    @staticmethod
    def _peer_name(writer: asyncio.StreamWriter) -> str:
        try:
            peer = writer.get_extra_info("peername")
        except Exception:
            peer = None
        if isinstance(peer, tuple) and peer:
            return f"{peer[0]}:{peer[1]}"
        return str(peer or "unknown")

    # ── Socket helpers ────────────────────────────────────────────────────

    async def _read_packet(self) -> bytes:
        """Read one framed packet payload.

        Reads the 4-byte header first, then exactly the advertised body. The
        length is bounded before the body is read so a peer cannot make the
        proxy allocate an arbitrary buffer by lying in the header.
        """
        header = await asyncio.wait_for(self._reader.readexactly(4), timeout=self._read_timeout)
        length = int.from_bytes(header[:3], "little")
        if length > MAX_PACKET_SIZE:
            raise ProtocolError(f"packet of {length} bytes exceeds the proxy limit")
        if length == 0:
            return b""
        return await asyncio.wait_for(self._reader.readexactly(length), timeout=self._read_timeout)

    async def _write_payloads(self, payloads: list[bytes], *, start_sequence: int = 1) -> None:
        sequence = start_sequence
        for payload in payloads:
            self._writer.write(encode_packet(payload, sequence))
            sequence = (sequence + 1) % 256
        await self._writer.drain()

    async def _write_error(self, code: int, message: str, *, sequence: int = 1) -> None:
        """Write an ERR packet.

        ``sequence`` defaults to 1, which is right for a command response: the
        client's command was sequence 0. Handshake-phase failures are sequence
        2 instead — the server handshake was 0 and the client's response was 1
        — so those call sites pass it explicitly. A sequence the client does not
        expect makes it discard the packet and report a lost connection instead
        of the error Nova actually sent.
        """
        payload = build_error_packet(code, message, capabilities=self._ctx.capabilities)
        self._writer.write(encode_packet(payload, sequence))
        await self._writer.drain()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def run(self) -> None:
        try:
            if not await self._handshake():
                return
            await self._command_loop()
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            logger.debug("[%s] client %s disconnected", self._ctx.connection_id, self._ctx.peer)
        except TimeoutError:
            logger.info("[%s] client %s timed out", self._ctx.connection_id, self._ctx.peer)
        except ProtocolError as exc:
            logger.info(
                "[%s] protocol error from %s: %s", self._ctx.connection_id, self._ctx.peer, exc
            )
        except Exception:
            # Never let an unhandled error take the listener down with it.
            logger.exception(
                "[%s] unexpected error for %s", self._ctx.connection_id, self._ctx.peer
            )
        finally:
            await self._close()

    async def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        # The upstream StarRocks session is closed first: it owns the socket the
        # relay adopted, and leaving it open would leak a StarRocks connection
        # per client connection.
        if self._ctx.upstream is not None:
            with contextlib.suppress(Exception):
                await self._ctx.upstream.close()
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass

    # ── Handshake ─────────────────────────────────────────────────────────

    async def _handshake(self) -> bool:
        """Negotiate, authenticate, and answer ``OK`` or ``ERR``.

        The negotiated capability set is the intersection of what Nova offers
        and what the client asks for. The intersection is what every later
        packet shape is computed from — in particular whether result sets are
        ``EOF``-delimited or ``OK``-delimited — so it is stored on the context
        rather than recomputed at each write site.
        """
        # StarRocks' own challenge, relayed. The proxy never sees a password:
        # the client derives its response from this scramble, the proxy passes
        # the response back on the same upstream socket, and StarRocks — which
        # holds the hash — decides. A fresh upstream login per client gives a
        # fresh scramble, which is what makes the relayed response single-use.
        # See app/proxy/auth.py for why the other designs fail.
        try:
            upstream = await open_starrocks_login()
        except AuthenticationError as exc:
            logger.error(
                "[%s] cannot open a StarRocks login: %s",
                self._ctx.connection_id,
                exc.message,
            )
            await self._write_error(
                ER_ACCESS_DENIED_ERROR,
                "Authentication service unavailable",
                sequence=2,
            )
            return False

        try:
            handshake = build_handshake_packet(self._ctx.connection_id, upstream.scramble)
            self._writer.write(encode_packet(handshake, 0))
            await self._writer.drain()

            try:
                response_payload = await self._read_packet()
            except asyncio.IncompleteReadError:
                logger.debug(
                    "[%s] client %s left before the handshake response",
                    self._ctx.connection_id,
                    self._ctx.peer,
                )
                return False

            try:
                response = parse_handshake_response(response_payload)
            except ProtocolError as exc:
                await self._write_error(ER_ACCESS_DENIED_ERROR, f"Access denied: {exc}", sequence=2)
                return False

            self._ctx.capabilities = response.capabilities & SERVER_CAPABILITIES

            try:
                await upstream.finish(
                    username=response.username,
                    auth_response=response.auth_response,
                    database=response.database,
                )
            except AuthenticationError as exc:
                logger.info(
                    "[%s] authentication failed for %r from %s",
                    self._ctx.connection_id,
                    response.username,
                    self._ctx.peer,
                )
                # ERR then close, in that order. No OK packet, no traceback.
                await self._write_error(exc.code, exc.message, sequence=2)
                return False
        except BaseException:
            await upstream.close()
            raise

        # The relay leaves a live, authenticated StarRocks session. Adopting it
        # as an asyncmy connection is what lets the query pipeline run under the
        # client's own RBAC without a password the proxy does not have.
        self._ctx.connection = await upstream.open_session()
        self._ctx.upstream = upstream

        requested_role = response.connect_attrs.get("nova_role") or None
        if settings.RANGER_ENABLED or requested_role is not None:
            try:
                active_role, assignments = await role_activation_service.activate(
                    self._ctx.connection,
                    principal=response.username,
                    requested_role=requested_role,
                )
            except RoleActivationError as exc:
                logger.info(
                    "[%s] role activation failed for %r from %s",
                    self._ctx.connection_id,
                    response.username,
                    self._ctx.peer,
                )
                await self._write_error(ER_ACCESS_DENIED_ERROR, str(exc), sequence=2)
                await upstream.close()
                return False

            self._ctx.session.establish_security(
                principal=response.username,
                assigned_roles=assignments.assigned_roles,
                default_role=assignments.default_role or "",
                active_role=active_role,
            )
        else:
            # Native-RBAC compatibility mode predates mandatory role markers.
            # Keep the authenticated principal, but never manufacture a role.
            self._ctx.session.principal = response.username

        self._ctx.user = AuthenticatedUser(
            username=response.username,
            database=response.database,
        )

        if response.database:
            self._ctx.session.set_database(response.database)

        self._ctx.session_id = str(uuid.uuid4())
        logger.info(
            "[%s] user %r connected from %s (database=%s)",
            self._ctx.connection_id,
            self._ctx.user.username,
            self._ctx.peer,
            self._ctx.session.database or "-",
        )
        # The handshake used sequence 0 and the client's response used 1, so the
        # login verdict continues at 2. Starting at 1 makes the client see a
        # replayed sequence number and drop the connection before it ever sends
        # a command.
        await self._write_payloads(
            [build_ok_packet(0, 0, capabilities=self._ctx.capabilities)],
            start_sequence=2,
        )
        return True

    # ── Command loop ──────────────────────────────────────────────────────

    async def _command_loop(self) -> None:
        while True:
            payload = await self._read_packet()
            if not payload:
                # Zero-length packet outside a terminator position: the client
                # is gone or confused. Closing is the only safe answer.
                return

            command = payload[0]
            body = payload[1:]

            if command == COM_QUIT:
                logger.debug("[%s] COM_QUIT", self._ctx.connection_id)
                return
            if command == COM_PING:
                await self._write_payloads(
                    [build_ok_packet(0, 0, capabilities=self._ctx.capabilities)]
                )
                continue
            if command == COM_QUERY:
                await self._on_query(body)
                continue
            if command == COM_INIT_DB:
                await self._on_init_db(body)
                continue
            if command == COM_SET_OPTION:
                # Connection-level option changes (multi-statement toggle).
                # Acknowledged, not applied: the proxy already handles multi
                # statements through its own splitter and one response shape.
                await self._write_payloads([build_eof_packet_payload(self._ctx.capabilities)])
                continue

            await self._on_unsupported(command)

    async def _on_query(self, body: bytes) -> None:
        assert self._ctx.user is not None
        assert self._ctx.connection is not None
        sql = body.decode("utf-8", errors="surrogateescape")
        try:
            result = await self._executor.execute(
                sql,
                username=self._ctx.user.username,
                connection=self._ctx.connection,
                session_id=self._ctx.session_id,
            )
        except Exception:
            logger.exception("[%s] query dispatch failed", self._ctx.connection_id)
            PROXY_QUERIES.labels(status="error").inc()
            await self._write_error(1064, "Query execution failed")
            return

        if result.error is not None:
            PROXY_QUERIES.labels(status="error").inc()
            await self._write_error(result.error_code, result.error)
            return

        PROXY_QUERIES.labels(status="success").inc()
        if result.is_resultset:
            payloads = build_resultset(
                result.columns,
                result.rows,
                capabilities=self._ctx.capabilities,
            )
            await self._write_payloads(payloads)
            return

        # No column metadata: a single OK packet, with the affected row count
        # written as a length-encoded integer (see protocol.build_ok_packet).
        await self._write_payloads(
            [build_ok_packet(result.ok_affected, 0, capabilities=self._ctx.capabilities)]
        )

    async def _on_init_db(self, body: bytes) -> None:
        database = body.decode("utf-8", errors="surrogateescape").strip()
        if not database:
            await self._write_error(ER_NO_DB_ERROR, "No database selected")
            return
        self._ctx.session.set_database(database)
        await self._write_payloads([build_ok_packet(0, 0, capabilities=self._ctx.capabilities)])

    async def _on_unsupported(self, command: int) -> None:
        """Refuse a command Nova does not implement, in MySQL's own idiom.

        ``COM_STMT_PREPARE`` and friends get ``ER_NOT_SUPPORTED_YET`` rather
        than a generic unknown-command error, because that is the code clients
        interpret as "use text protocol instead" — MySQL Connector/J and
        friends fall back cleanly on it. Anything genuinely unknown gets 1047.
        """
        if command in (COM_STMT_PREPARE, COM_STMT_EXECUTE, COM_STMT_CLOSE, COM_STMT_RESET):
            await self._write_error(
                ER_NOT_SUPPORTED_YET,
                "Prepared statements are not supported by the Nova MySQL proxy; "
                "use the text protocol",
            )
            return
        if command == COM_FIELD_LIST:
            await self._write_error(
                ER_NOT_SUPPORTED_YET,
                "COM_FIELD_LIST is not supported by the Nova MySQL proxy",
            )
            return
        if command == COM_CHANGE_USER:
            await self._write_error(
                ER_NOT_SUPPORTED_YET,
                "COM_CHANGE_USER is not supported by the Nova MySQL proxy; open a new connection",
            )
            return
        await self._write_error(ER_UNKNOWN_COM_ERROR, f"Unknown command {command}")


def build_eof_packet_payload(capabilities: int) -> bytes:
    """``COM_SET_OPTION``'s acknowledgement.

    With ``CLIENT_DEPRECATE_EOF`` negotiated the acknowledgement is an OK
    packet; otherwise it is the classic EOF packet.
    """
    if capabilities & CLIENT_DEPRECATE_EOF:
        return build_ok_packet(0, 0, capabilities=capabilities)
    from app.proxy.protocol import build_eof_packet

    return build_eof_packet()


__all__ = ["ConnectionContext", "ProxyConnection"]
