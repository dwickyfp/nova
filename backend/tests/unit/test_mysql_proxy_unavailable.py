from unittest.mock import AsyncMock

import asyncmy
import pytest

from app.proxy.auth import AuthenticationError
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
