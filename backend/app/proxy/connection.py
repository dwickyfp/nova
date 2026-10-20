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
import struct
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
from app.proxy.executor import ProxyQueryExecutor, WireResult
from app.proxy.protocol import (
    CLIENT_DEPRECATE_EOF,
    CLIENT_MULTI_RESULTS,
    COM_CHANGE_USER,
    COM_FIELD_LIST,
    COM_INIT_DB,
    COM_PING,
    COM_QUERY,
    COM_QUIT,
    COM_SET_OPTION,
    COM_STMT_CLOSE,
    COM_STMT_EXECUTE,
    COM_STMT_FETCH,
    COM_STMT_PREPARE,
    COM_STMT_RESET,
    COM_STMT_SEND_LONG_DATA,
    DEFAULT_SERVER_STATUS,
    ER_ACCESS_DENIED_ERROR,
    ER_NO_DB_ERROR,
    ER_NOT_SUPPORTED_YET,
    ER_UNKNOWN_COM_ERROR,
    ER_UNKNOWN_STMT_HANDLER,
    MAX_COMMAND_SIZE,
    MAX_FRAME_PAYLOAD,
    SERVER_CAPABILITIES,
    SERVER_MORE_RESULTS_EXISTS,
    ColumnDefinition,
    ProtocolError,
    build_binary_row,
    build_column_definition,
    build_eof_packet,
    build_error_packet,
    build_handshake_packet,
    build_ok_packet,
    build_prepare_ok,
    build_text_row,
    encode_length_encoded_int,
    encode_packet,
    frame_payload,
    parse_handshake_response,
    parse_stmt_execute,
)
from app.proxy.session import (
    SessionState,
    bind_placeholders,
    parse_prepared_statement,
    placeholder_spans,
    quote_database_target,
    split_statements,
    sql_literal,
)

logger = logging.getLogger(__name__)

class PreparedStatement:
    """A ``COM_STMT_PREPARE`` statement held by the proxy until it is closed."""

    def __init__(self, sql: str, num_params: int) -> None:
        self.sql = sql
        self.num_params = num_params
        self.types: list[tuple[int, int]] | None = None
        self.long_data: dict[int, bytes] = {}


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
        #: Sequence id the next response starts at: one past the client's
        #: last frame of the current command.
        self._response_sequence = 1
        #: Binary-protocol prepared statements of this connection.
        self._statements: dict[int, PreparedStatement] = {}
        self._next_statement_id = 0

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
        """Read one logical packet, reassembling continuation frames.

        A client splits a payload longer than 0xFFFFFF bytes into frames of
        exactly that length followed by a shorter (possibly empty) one. Each
        header is read first and the running total bounded before the body is
        read, so a peer cannot make the proxy allocate an arbitrary buffer.
        The response to this packet continues the client's sequence numbers.
        """
        payload = bytearray()
        while True:
            header = await asyncio.wait_for(
                self._reader.readexactly(4), timeout=self._read_timeout
            )
            length = int.from_bytes(header[:3], "little")
            if len(payload) + length > MAX_COMMAND_SIZE:
                total = len(payload) + length
                raise ProtocolError(f"packet of {total} bytes exceeds the proxy limit")
            if length:
                payload += await asyncio.wait_for(
                    self._reader.readexactly(length), timeout=self._read_timeout
                )
            self._response_sequence = (header[3] + 1) % 256
            if length < MAX_FRAME_PAYLOAD:
                return bytes(payload)

    async def _write_payloads(
        self, payloads: list[bytes], *, start_sequence: int | None = None
    ) -> int:
        """Write payloads as consecutive packets; returns the next sequence id."""
        sequence = self._response_sequence if start_sequence is None else start_sequence
        # One write per batch: a transport write per packet costs a syscall per
        # result row.
        buffer = bytearray()
        for payload in payloads:
            frames, sequence = frame_payload(payload, sequence)
            buffer += frames
        self._writer.write(bytes(buffer))
        await self._writer.drain()
        return sequence

    async def _write_error(self, code: int, message: str, *, sequence: int | None = None) -> None:
        """Write an ERR packet.

        ``sequence`` defaults to the command's response sequence (1 for a
        single-frame command, which was sequence 0). Handshake-phase failures are sequence
        2 instead — the server handshake was 0 and the client's response was 1
        — so those call sites pass it explicitly. A sequence the client does not
        expect makes it discard the packet and report a lost connection instead
        of the error Nova actually sent.
        """
        payload = build_error_packet(code, message, capabilities=self._ctx.capabilities)
        await self._write_payloads([payload], start_sequence=sequence)

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
                sequence=0,
            )
            return False

        if upstream.connection_id:
            self._ctx.connection_id = upstream.connection_id
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
            if command == COM_STMT_PREPARE:
                await self._on_stmt_prepare(body)
                continue
            if command == COM_STMT_EXECUTE:
                await self._on_stmt_execute(body)
                continue
            if command == COM_STMT_SEND_LONG_DATA:
                # No response, by protocol.
                self._on_stmt_long_data(body)
                continue
            if command == COM_STMT_CLOSE:
                # No response, by protocol.
                if len(body) >= 4:
                    self._statements.pop(int.from_bytes(body[:4], "little"), None)
                continue
            if command == COM_STMT_RESET:
                statement = self._statements.get(int.from_bytes(body[:4], "little"))
                if statement is None:
                    await self._write_error(ER_UNKNOWN_STMT_HANDLER, "Unknown prepared statement")
                else:
                    statement.long_data.clear()
                    await self._write_payloads(
                        [build_ok_packet(0, 0, capabilities=self._ctx.capabilities)]
                    )
                continue
            if command == COM_FIELD_LIST:
                await self._on_field_list(body)
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
        sink = WireSink(self)
        try:
            await self._executor.execute_to(
                sql,
                sink,
                username=self._ctx.user.username,
                connection=self._ctx.connection,
                session_id=self._ctx.session_id,
            )
        except (ConnectionError, asyncio.IncompleteReadError):
            raise
        except Exception:
            logger.exception("[%s] query dispatch failed", self._ctx.connection_id)
            if sink.complete:
                return
            sink.errored = True
            await sink.emit(WireResult(error="Query execution failed"), more=False)
        PROXY_QUERIES.labels(status="error" if sink.errored else "success").inc()

    async def _on_init_db(self, body: bytes) -> None:
        """``COM_INIT_DB`` — what the mysql CLI sends for ``use <db>``.

        It is answered by running ``USE`` through the same path as a ``USE``
        statement, so the engine validates the database before the session
        records it; an unknown database is refused here rather than failing
        every later statement.
        """
        database = body.decode("utf-8", errors="surrogateescape").strip()
        if not database:
            await self._write_error(ER_NO_DB_ERROR, "No database selected")
            return
        statement = f"USE {quote_database_target(database)}"
        await self._on_query(statement.encode("utf-8", errors="surrogateescape"))

    async def _on_stmt_prepare(self, body: bytes) -> None:
        """``COM_STMT_PREPARE``: keep the text; nothing reaches the engine yet.

        The statement is syntax-checked with its markers in place (the grammar
        accepts ``?``). Execution binds the parameters as literals and runs the
        text through the same pipeline as ``COM_QUERY``.
        """
        sql = body.decode("utf-8", errors="surrogateescape").strip().rstrip(";").strip()
        statements = split_statements(sql)
        if len(statements) != 1 or parse_prepared_statement(sql) is not None:
            await self._write_error(1064, "A prepared statement holds exactly one statement")
            return
        try:
            from app.sql_frontend.parser import parse_statement_async

            await parse_statement_async(sql)
        except ValueError as exc:
            await self._write_error(1064, str(exc))
            return
        self._next_statement_id += 1
        statement_id = self._next_statement_id
        num_params = len(placeholder_spans(sql))
        self._statements[statement_id] = PreparedStatement(sql, num_params)
        await self._write_payloads(
            build_prepare_ok(statement_id, num_params, capabilities=self._ctx.capabilities)
        )

    def _on_stmt_long_data(self, body: bytes) -> None:
        if len(body) < 6:
            return
        statement = self._statements.get(int.from_bytes(body[:4], "little"))
        if statement is not None:
            index = int.from_bytes(body[4:6], "little")
            statement.long_data[index] = statement.long_data.get(index, b"") + body[6:]

    async def _on_stmt_execute(self, body: bytes) -> None:
        assert self._ctx.user is not None
        statement_id = int.from_bytes(body[:4], "little") if len(body) >= 4 else -1
        statement = self._statements.get(statement_id)
        if statement is None:
            await self._write_error(ER_UNKNOWN_STMT_HANDLER, "Unknown prepared statement")
            return
        try:
            request = parse_stmt_execute(
                body,
                num_params=statement.num_params,
                previous_types=statement.types,
                long_data=statement.long_data,
            )
            sql = bind_placeholders(statement.sql, [sql_literal(value) for value in request.values])
        except (ProtocolError, ValueError, IndexError, struct.error) as exc:
            await self._write_error(1210, f"Incorrect arguments to EXECUTE: {exc}")
            return
        statement.types = request.types
        statement.long_data.clear()
        sink = WireSink(self, row_encoder=build_binary_row)
        try:
            await self._executor.execute_to(
                sql,
                sink,
                username=self._ctx.user.username,
                connection=self._ctx.connection,
                session_id=self._ctx.session_id,
            )
        except (ConnectionError, asyncio.IncompleteReadError):
            raise
        except Exception:
            logger.exception("[%s] prepared execution failed", self._ctx.connection_id)
            if not sink.complete:
                sink.errored = True
                await sink.emit(WireResult(error="Query execution failed"), more=False)
        PROXY_QUERIES.labels(status="error" if sink.errored else "success").inc()

    async def _on_field_list(self, body: bytes) -> None:
        """``COM_FIELD_LIST``: the column list the mysql CLI uses for completion."""
        assert self._ctx.user is not None
        table, _, _ = body.partition(b"\x00")
        name = table.decode("utf-8", errors="surrogateescape")
        if not name:
            await self._write_error(ER_NO_DB_ERROR, "No table name")
            return
        result = await self._executor.execute(
            "SHOW COLUMNS FROM `" + name.replace("`", "``") + "`",
            username=self._ctx.user.username,
            connection=self._ctx.connection,
            session_id=self._ctx.session_id,
        )
        if result.error is not None:
            await self._write_error(result.error_code, result.error)
            return
        payloads = [
            build_column_definition(ColumnDefinition(name=str(row[0])))
            for row in result.rows
            if row
        ]
        payloads.append(
            build_ok_packet(0, 0, capabilities=self._ctx.capabilities)
            if self._ctx.capabilities & CLIENT_DEPRECATE_EOF
            else build_eof_packet()
        )
        await self._write_payloads(payloads)

    async def _on_unsupported(self, command: int) -> None:
        """Refuse a command Nova does not implement, in MySQL's own idiom.

        ``COM_STMT_PREPARE`` and friends get ``ER_NOT_SUPPORTED_YET`` rather
        than a generic unknown-command error, because that is the code clients
        interpret as "use text protocol instead" — MySQL Connector/J and
        friends fall back cleanly on it. Anything genuinely unknown gets 1047.
        """
        if command == COM_STMT_FETCH:
            await self._write_error(
                ER_NOT_SUPPORTED_YET,
                "Server-side cursors are not supported by the Nova MySQL proxy; "
                "results are returned in full",
            )
            return
        if command == COM_CHANGE_USER:
            await self._write_error(
                ER_NOT_SUPPORTED_YET,
                "COM_CHANGE_USER is not supported by the Nova MySQL proxy; open a new connection",
            )
            return
        await self._write_error(ER_UNKNOWN_COM_ERROR, f"Unknown command {command}")


class WireSink:
    """Writes the responses of one command to the client, in order.

    Sequence ids run on across every packet of the command. A client that did
    not negotiate ``CLIENT_MULTI_RESULTS`` can read one response per command,
    so responses flagged ``more`` are dropped for it and it receives the final
    one, as a single-result server would answer. ``row_encoder`` switches the
    result rows to the binary protocol for prepared statements.
    """

    def __init__(self, connection: ProxyConnection, *, row_encoder=None):
        self._connection = connection
        self._capabilities = connection._ctx.capabilities
        self._multi = bool(self._capabilities & CLIENT_MULTI_RESULTS)
        self._sequence = connection._response_sequence
        self._row_encoder = row_encoder or build_text_row
        self._suppressed = False
        self.errored = False
        self.complete = False

    def _status(self, more: bool) -> int:
        return DEFAULT_SERVER_STATUS | (SERVER_MORE_RESULTS_EXISTS if more else 0)

    async def _write(self, payloads: list[bytes]) -> None:
        self._sequence = await self._connection._write_payloads(
            payloads, start_sequence=self._sequence
        )

    def _terminator(self, more: bool) -> bytes:
        if self._capabilities & CLIENT_DEPRECATE_EOF:
            return build_ok_packet(
                0, 0, status_flags=self._status(more), capabilities=self._capabilities
            )
        return build_eof_packet(status_flags=self._status(more))

    async def emit(self, part: WireResult, *, more: bool) -> None:
        if part.error is not None:
            self.errored = True
            self.complete = True
            await self._write(
                [build_error_packet(part.error_code, part.error, capabilities=self._capabilities)]
            )
            return
        if not more:
            self.complete = True
        elif not self._multi:
            return
        if part.is_resultset:
            await self.columns(part.columns, more=more)
            await self.rows(part.rows, part.columns)
            await self.end_rows(more=more)
            return
        await self._write(
            [
                build_ok_packet(
                    part.ok_affected,
                    0,
                    status_flags=self._status(more),
                    capabilities=self._capabilities,
                )
            ]
        )

    async def columns(self, columns: list[ColumnDefinition], *, more: bool) -> None:
        self._suppressed = more and not self._multi
        if self._suppressed:
            return
        payloads = [encode_length_encoded_int(len(columns))]
        payloads.extend(build_column_definition(column) for column in columns)
        if not self._capabilities & CLIENT_DEPRECATE_EOF:
            payloads.append(build_eof_packet())
        await self._write(payloads)

    async def rows(self, rows: list[list], columns: list[ColumnDefinition]) -> None:
        if self._suppressed or not rows:
            return
        await self._write([self._row_encoder(list(row), columns) for row in rows])

    async def end_rows(self, *, more: bool) -> None:
        if self._suppressed:
            self._suppressed = False
            return
        if not more:
            self.complete = True
        await self._write([self._terminator(more)])


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
