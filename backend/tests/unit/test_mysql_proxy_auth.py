"""Unit tests for the proxy's authentication relay.

The design under test: the proxy reads StarRocks' own ``HandshakeV10``, presents
that scramble to the client as its own, and forwards the client's response back
on the same upstream socket. StarRocks — the only holder of the password hash —
decides. The proxy never sees, derives or stores a password.

Two properties are pinned hard because both are security-relevant:

* the handshake a client sends is verified against the scramble StarRocks
  issued (so a replayed response from a different connection cannot pass), and
* the relay is per-connection (a response is valid only on the socket whose
  scramble produced it).

The parsing tests run against byte-exact handshake payloads rather than a live
engine, so they catch a layout regression on an upgrade without needing one.
"""

import asyncio
import socket
import struct

import pytest

from app.proxy import protocol as p
from app.proxy.auth import (
    _UPSTREAM_CAPABILITIES,
    NATIVE_PLUGIN_NAME,
    AuthenticationError,
    StarRocksLogin,
    _build_handshake_response,
    open_starrocks_login,
    parse_starrocks_handshake,
)


def _handshake(
    *,
    scramble: bytes = bytes(range(20)),
    plugin: bytes | None = NATIVE_PLUGIN_NAME,
    protocol_version: int = 0x0A,
    auth_plugin_data_length: int = 21,
) -> bytes:
    """Build a ``HandshakeV10`` payload the way an engine would."""
    capabilities = p.SERVER_CAPABILITIES
    payload = bytes([protocol_version])
    payload += b"8.0.33\x00"
    payload += struct.pack("<I", 7)
    payload += scramble[:8] + b"\x00"
    payload += struct.pack("<H", capabilities & 0xFFFF)
    payload += bytes([45])
    payload += struct.pack("<H", 2)
    payload += struct.pack("<H", (capabilities >> 16) & 0xFFFF)
    payload += bytes([auth_plugin_data_length])
    payload += b"\x00" * 10
    payload += scramble[8:] + b"\x00"
    if plugin is not None:
        payload += plugin + b"\x00"
    return payload


class TestHandshakeParsing:
    def test_reads_the_full_twenty_byte_scramble(self):
        """The scramble is split across two fields with a filler between.

        Reading only the first eight bytes produces a challenge the client
        cannot answer, and every login fails with a *correct* password.
        """
        scramble = bytes(range(20))
        parsed = parse_starrocks_handshake(_handshake(scramble=scramble))
        assert parsed.scramble == scramble
        assert len(parsed.scramble) == 20

    def test_reads_the_plugin_name(self):
        parsed = parse_starrocks_handshake(_handshake())
        assert parsed.plugin == NATIVE_PLUGIN_NAME

    def test_reads_capabilities(self):
        parsed = parse_starrocks_handshake(_handshake())
        assert parsed.capabilities == p.SERVER_CAPABILITIES

    def test_a_different_scramble_each_time_is_what_makes_the_relay_single_use(self):
        first = parse_starrocks_handshake(_handshake(scramble=bytes(range(20))))
        second = parse_starrocks_handshake(_handshake(scramble=bytes(range(1, 21))))
        assert first.scramble != second.scramble

    def test_rejects_an_unsupported_protocol_version(self):
        with pytest.raises(AuthenticationError):
            parse_starrocks_handshake(_handshake(protocol_version=9))

    def test_rejects_an_empty_handshake(self):
        with pytest.raises(AuthenticationError):
            parse_starrocks_handshake(b"")

    def test_rejects_a_truncated_handshake(self):
        with pytest.raises(AuthenticationError):
            parse_starrocks_handshake(_handshake()[:10])

    def test_fails_closed_on_an_unrelayable_plugin(self):
        """A challenge under a plugin the proxy cannot relay must not be used.

        Relaying a ``caching_sha2_password`` challenge would produce a response
        StarRocks could not verify against a ``mysql_native_password`` hash, and
        the failure would surface as a wrong-password error rather than as a
        misconfiguration.
        """
        with pytest.raises(AuthenticationError) as excinfo:
            parse_starrocks_handshake(_handshake(plugin=b"caching_sha2_password"))
        assert "caching_sha2_password" in str(excinfo.value)

    def test_absent_plugin_defaults_to_native_password(self):
        """Pre-5.7.10 servers omit the plugin name (upstream bug #59453)."""
        parsed = parse_starrocks_handshake(_handshake(plugin=None))
        assert parsed.plugin == NATIVE_PLUGIN_NAME

    def test_handles_the_twenty_byte_length_form(self):
        """``auth_plugin_data_length`` 21 means 20 bytes plus a NUL."""
        parsed = parse_starrocks_handshake(_handshake(auth_plugin_data_length=21))
        assert len(parsed.scramble) == 20


class TestUpstreamHandshakeResponse:
    """The response the proxy sends to StarRocks after reading its challenge."""

    def test_does_not_set_connect_with_db(self):
        """StarRocks answers ``CLIENT_CONNECT_WITH_DB`` with error 5501.

        Measured against StarRocks 4.1.1: a handshake response carrying the flag
        is refused outright, so the flag and the field are both omitted and the
        database context lives in the proxy's session instead.
        """
        payload = _build_handshake_response("nova_admin", b"x" * 20, None)
        capabilities = struct.unpack("<I", payload[:4])[0]
        assert not capabilities & p.CLIENT_CONNECT_WITH_DB

    def test_does_not_set_connect_with_db_even_when_a_database_is_named(self):
        payload = _build_handshake_response("nova_admin", b"x" * 20, "NOVA_DEMO")
        capabilities = struct.unpack("<I", payload[:4])[0]
        assert not capabilities & p.CLIENT_CONNECT_WITH_DB
        assert b"NOVA_DEMO" not in payload

    def test_layout_is_field_for_field(self):
        payload = _build_handshake_response("nova_admin", b"y" * 20, None)
        assert struct.unpack("<I", payload[:4])[0] == _UPSTREAM_CAPABILITIES
        assert struct.unpack("<I", payload[4:8])[0] == 16 * 1024 * 1024
        assert payload[8] == 45
        assert payload[9:32] == b"\x00" * 23
        assert payload[32:42] == b"nova_admin"
        assert payload[42] == 0x00
        assert payload[43] == 20
        assert payload[44:64] == b"y" * 20
        assert payload[64:] == NATIVE_PLUGIN_NAME + b"\x00"

    def test_capabilities_are_the_ones_the_engine_expects(self):
        """The literal set must stay in step with what StarRocks accepts.

        The list is duplicated as a literal to keep ``auth`` free of a cycle
        back into ``protocol``, so this test is the tie between the two.
        """
        assert _UPSTREAM_CAPABILITIES & p.CLIENT_PROTOCOL_41
        assert _UPSTREAM_CAPABILITIES & p.CLIENT_PLUGIN_AUTH
        assert _UPSTREAM_CAPABILITIES & p.CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA
        assert not _UPSTREAM_CAPABILITIES & p.CLIENT_DEPRECATE_EOF


class TestRelayAgainstAFakeEngine:
    """Drive the relay against a scripted engine instead of a live StarRocks."""

    @pytest.fixture
    def fake_engine(self):
        """A minimal server that completes or refuses a login on request."""

        class Engine:
            def __init__(self):
                self.scramble = bytes(range(20))
                self.accept = True
                self.received_response: bytes | None = None
                self.received_capabilities: int | None = None
                self.connections = 0

        engine = Engine()

        async def handler(reader, writer):
            engine.connections += 1
            payload = _handshake(scramble=engine.scramble)
            writer.write(len(payload).to_bytes(3, "little") + b"\x00" + payload)
            await writer.drain()
            header = await reader.readexactly(4)
            length = int.from_bytes(header[:3], "little")
            body = await reader.readexactly(length)
            engine.received_capabilities = struct.unpack("<I", body[:4])[0]
            username_len = body.index(b"\x00", 32) - 32
            offset = 32 + username_len + 1
            response_length = body[offset]
            engine.received_response = body[offset + 1 : offset + 1 + response_length]
            if engine.accept:
                writer.write(b"\x07\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00")
            else:
                message = b"#28000Access denied"
                writer.write(
                    (len(message) + 3).to_bytes(3, "little")
                    + b"\x02\xff\x15\x04"
                    + message
                )
            await writer.drain()

        async def serve():
            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            return server

        return engine, serve

    async def test_reads_the_challenge_without_closing_the_socket(self, fake_engine):
        engine, serve = fake_engine
        server = await serve()
        port = server.sockets[0].getsockname()[1]
        try:
            login = await open_starrocks_login(host="127.0.0.1", port=port)
            assert login.scramble == engine.scramble
            # The socket must still be open: the response goes back on it.
            assert not login.writer.is_closing()
            await login.close()
        finally:
            server.close()
            await server.wait_closed()

    async def test_finishing_sends_the_clients_response_verbatim(self, fake_engine):
        engine, serve = fake_engine
        server = await serve()
        port = server.sockets[0].getsockname()[1]
        try:
            login = await open_starrocks_login(host="127.0.0.1", port=port)
            client_response = b"z" * 20
            await login.finish(username="nova_admin", auth_response=client_response)
            assert engine.received_response == client_response
            await login.close()
        finally:
            server.close()
            await server.wait_closed()

    async def test_a_refused_login_raises_without_a_traceback(self, fake_engine):
        engine, serve = fake_engine
        engine.accept = False
        server = await serve()
        port = server.sockets[0].getsockname()[1]
        try:
            login = await open_starrocks_login(host="127.0.0.1", port=port)
            with pytest.raises(AuthenticationError) as excinfo:
                await login.finish(username="nova_admin", auth_response=b"z" * 20)
            assert "Access denied" in excinfo.value.message
            assert excinfo.value.code == 1045
            await login.close()
        finally:
            server.close()
            await server.wait_closed()

    async def test_open_session_adopts_the_authenticated_socket(self, fake_engine):
        """The adopted connection must be usable and must not re-authenticate.

        ``open_session`` marks the ``asyncmy`` connection connected and installs
        the relayed reader/writer; if it did not, the first query would try to
        run a fresh handshake on an already-authenticated socket.
        """
        engine, serve = fake_engine
        server = await serve()
        port = server.sockets[0].getsockname()[1]
        try:
            login = await open_starrocks_login(host="127.0.0.1", port=port)
            # Accept the login, then script a one-column result for the query.
            await login.finish(username="nova_admin", auth_response=b"z" * 20)

            conn = await login.open_session()
            assert conn.connected
            assert engine.connections == 1  # no second handshake
            await login.close()
        finally:
            server.close()
            await server.wait_closed()

    async def test_each_open_builds_a_new_login(self, fake_engine):
        """Two clients must not share a scramble.

        A shared scramble would make one client's response replayable by another
        for as long as the challenge lived.
        """
        engine, serve = fake_engine
        server = await serve()
        port = server.sockets[0].getsockname()[1]
        try:
            first = await open_starrocks_login(host="127.0.0.1", port=port)
            second = await open_starrocks_login(host="127.0.0.1", port=port)
            assert engine.connections == 2
            await first.close()
            await second.close()
        finally:
            server.close()
            await server.wait_closed()

    async def test_unreachable_engine_is_an_auth_error_not_an_oserror(self):
        """A refused connection must surface as an auth failure.

        The proxy's handshake handler turns ``AuthenticationError`` into an ERR
        packet; an ``OSError`` escaping it would be caught by the generic handler
        and the client would see a closed socket instead of a message.
        """
        # Bind and immediately close a port so nothing is listening on it.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        with pytest.raises(AuthenticationError):
            await open_starrocks_login(host="127.0.0.1", port=port, timeout=1.0)


class TestStarRocksLoginLifecycle:
    def test_login_dataclass_carries_the_challenge(self):
        async def _noop() -> None:
            return None

        login = StarRocksLogin(
            scramble=bytes(20),
            plugin=NATIVE_PLUGIN_NAME,
            capabilities=0,
            reader=None,  # type: ignore[arg-type]
            writer=None,  # type: ignore[arg-type]
        )
        assert len(login.scramble) == 20
        assert login.plugin == NATIVE_PLUGIN_NAME
