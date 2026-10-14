import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncmy
import pytest

from app.proxy.auth import AuthenticationError, _build_handshake_response
from app.proxy.protocol import encode_packet
from app.proxy.server import MySQLProxyServer


async def test_unavailable_engine_login_is_a_valid_initial_error_packet(monkeypatch):
    monkeypatch.setattr(
        "app.proxy.connection.open_starrocks_login",
        AsyncMock(side_effect=AuthenticationError("Engine unavailable")),
    )
    server = MySQLProxyServer(host="127.0.0.1", port=0)
    await server.start()
    try:
        with pytest.raises(asyncmy.errors.OperationalError) as error:
            await asyncmy.connect(
                host="127.0.0.1",
                port=server.bound_port,
                user="fixture",
                password="fixture",
                autocommit=True,
                connect_timeout=2,
            )
        assert error.value.args[0] == 1045
        assert "Authentication service unavailable" in str(error.value)
    finally:
        await server.stop()


@pytest.mark.parametrize("failure", ["disconnect", "malformed", "denied"])
async def test_failed_handshake_closes_the_upstream_login(monkeypatch, failure):
    upstream = SimpleNamespace(
        connection_id=1,
        scramble=bytes(range(20)),
        finish=AsyncMock(side_effect=AuthenticationError("Access denied")),
        open_session=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        "app.proxy.connection.open_starrocks_login", AsyncMock(return_value=upstream)
    )
    server = MySQLProxyServer(host="127.0.0.1", port=0)
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
    try:
        header = await reader.readexactly(4)
        await reader.readexactly(int.from_bytes(header[:3], "little"))
        if failure != "disconnect":
            payload = (
                b"\x00"
                if failure == "malformed"
                else _build_handshake_response("fixture", b"x" * 20)
            )
            writer.write(encode_packet(payload, 1))
            await writer.drain()
            reply = await reader.read()
            assert reply[4] == 0xFF
            assert b"Access denied" in reply
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()

    upstream.close.assert_awaited_once()
    upstream.open_session.assert_not_awaited()
    if failure == "denied":
        upstream.finish.assert_awaited_once()
    else:
        upstream.finish.assert_not_awaited()
