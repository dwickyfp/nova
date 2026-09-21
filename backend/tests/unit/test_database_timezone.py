"""Timezone pinning in the StarRocks connection factory.

Nova pins every session's ``time_zone`` to ``NOVA_TIMEZONE`` so ``NOW()`` and
naive DATETIME round-trips agree regardless of the engine's global. These tests
cover the pure helpers and the ``init_command`` those sessions are opened with;
the driver is never contacted.
"""

from __future__ import annotations

import asyncmy
import pytest

from app.core import database
from app.core.config import settings


def test_configured_timezone_defaults_to_asia_jakarta(monkeypatch):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "   ")
    assert database.configured_timezone() == "Asia/Jakarta"


def test_configured_timezone_trims_the_value(monkeypatch):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "  Europe/Berlin  ")
    assert database.configured_timezone() == "Europe/Berlin"


def test_quote_escapes_a_single_quote(monkeypatch):
    # Operator-controlled config, but a stray quote must not break the session.
    assert database._quote("Asia/Jakarta") == "'Asia/Jakarta'"
    assert database._quote("O'Brien") == "'O''Brien'"


async def test_system_pool_pins_the_session_timezone(monkeypatch):
    captured: dict = {}

    async def _create_pool(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "Asia/Jakarta")
    monkeypatch.setattr(asyncmy, "create_pool", _create_pool)

    factory = database.StarRocksConnectionFactory()
    await factory.init_system_pool()

    assert captured["init_command"] == "SET time_zone = 'Asia/Jakarta'"


async def test_user_connection_pins_the_session_timezone(monkeypatch):
    captured: dict = {}

    class _FakeConn:
        def close(self) -> None:  # pragma: no cover - never reached
            pass

    async def _connect(**kwargs):
        captured.update(kwargs)
        return _FakeConn()

    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "Europe/Berlin")
    monkeypatch.setattr(asyncmy, "connect", _connect)

    factory = database.StarRocksConnectionFactory()
    async with factory.user_conn("analyst", "pw"):
        pass

    assert captured["init_command"] == "SET time_zone = 'Europe/Berlin'"


@pytest.mark.asyncio
async def test_apply_global_time_zone_is_best_effort(monkeypatch):
    """A missing GLOBAL privilege must not raise; sessions are already pinned."""
    calls: list[str] = []

    async def _execute_system(sql: str, params=None):
        calls.append(sql)
        return {}

    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "Europe/Berlin")
    factory = database.StarRocksConnectionFactory()
    monkeypatch.setattr(factory, "execute_system", _execute_system)

    await factory.apply_global_time_zone()

    assert calls == ["SET GLOBAL time_zone = 'Europe/Berlin'"]


@pytest.mark.asyncio
async def test_apply_global_time_zone_swallows_failure(monkeypatch):
    async def _boom(sql: str, params=None):
        raise RuntimeError("Access denied")

    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "Europe/Berlin")
    factory = database.StarRocksConnectionFactory()
    monkeypatch.setattr(factory, "execute_system", _boom)

    # Must not raise: the per-session pin is the guarantee, the global advisory.
    await factory.apply_global_time_zone()
