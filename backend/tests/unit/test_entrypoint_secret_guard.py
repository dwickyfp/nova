"""NOVA-108 AC 2 — every standalone process refuses to boot on blank keys.

The FastAPI lifespan validated the secrets, but ``app.worker``, ``app.proxy``
and ``app.scheduler`` opened ``db.init_system_pool()`` as their first statement
and never checked. With a blank ``FERNET_KEY`` they "started" and failed later
at first use — the exact boot-time failure NOVA-108 exists to provide.

These are unit-level: the pool is stubbed, so reaching it is the failure signal
and no engine/Redis is required.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
from cryptography.fernet import Fernet

from app.common.secret_keys import MissingSecretError
from app.core.config import settings
from app.proxy import __main__ as proxy_main
from app.scheduler import __main__ as scheduler_main
from app.worker import __main__ as worker_main

ENTRYPOINTS = [
    pytest.param(worker_main, id="app.worker"),
    pytest.param(proxy_main, id="app.proxy"),
    pytest.param(scheduler_main, id="app.scheduler"),
]

VALID_FERNET_KEY = Fernet.generate_key().decode()

INVALID_KEYS = [
    pytest.param("", VALID_FERNET_KEY, "SECRET_KEY", id="blank-secret-key"),
    pytest.param("real-secret", "", "FERNET_KEY", id="blank-fernet-key"),
    pytest.param("change-me-in-production", VALID_FERNET_KEY, "SECRET_KEY", id="placeholder"),
    pytest.param("real-secret", "not-a-fernet-key", "FERNET_KEY", id="malformed"),
]


class TestEntrypointsRefuseToBoot:
    @pytest.mark.parametrize("module", ENTRYPOINTS)
    @pytest.mark.parametrize("secret_key,fernet_key,expected", INVALID_KEYS)
    def test_run_raises_before_db_init(self, module, monkeypatch, secret_key, fernet_key, expected):
        monkeypatch.setattr(settings, "SECRET_KEY", secret_key)
        monkeypatch.setattr(settings, "FERNET_KEY", fernet_key)
        reached_db = _tripwire_db_init(monkeypatch)

        with pytest.raises(MissingSecretError) as excinfo:
            asyncio.run(module._run())

        assert not reached_db(), "process reached DB init with an invalid key"
        assert expected in str(excinfo.value)
        if secret_key.strip():
            assert secret_key not in str(excinfo.value)


class TestEntrypointsShareOneRule:
    @pytest.mark.parametrize("module", ENTRYPOINTS)
    def test_run_calls_the_shared_guard(self, module):
        """Guard is the first statement in ``_run`` — not merely imported."""
        source = inspect.getsource(module._run)
        guard_line = source.index("guard_process_startup(")
        assert guard_line < source.index("db.init_system_pool(")

    def test_lifespan_uses_the_same_guard(self):
        from app.main import lifespan

        source = inspect.getsource(lifespan)
        assert "guard_process_startup(" in source
        assert source.index("guard_process_startup(") < source.index("db.init_system_pool(")


def _tripwire_db_init(monkeypatch):
    """Replace ``db.init_system_pool`` with a flag; returns the flag getter."""
    called = False

    async def _init() -> None:
        nonlocal called
        called = True
        raise AssertionError("init_system_pool must not run with an invalid key")

    monkeypatch.setattr("app.core.database.db.init_system_pool", _init)
    return lambda: called
