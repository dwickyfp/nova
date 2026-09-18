"""NOVA-108 acceptance 2 — every process entrypoint must fail closed.

The API lifespan validated ``SECRET_KEY``/``FERNET_KEY``, but the three
standalone entrypoints opened the system connection pool first, so a
misconfigured deployment booted them happily and only failed later — at the
first task (the worker decrypts stored credentials) rather than at boot.

These tests drive each ``_run()`` with blank/placeholder keys and assert it
raises ``MissingSecretError`` *before* touching the database. Only the shared
helper is exercised for real; the entrypoints' database handle is a spy.

Unit-level: no engine, no Redis, no HTTP.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

from app.common.secret_keys import MissingSecretError
from app.core.config import settings

VALID_SECRET_KEY = "unit-test-signing-key-not-for-deployment"
VALID_FERNET_KEY = "8f3Q1sVx0m2pR7tY5uW9zB4cD6eF1gH3jK5lM7nO9pQ="

ENTRYPOINTS = [
    "app.worker.__main__",
    "app.proxy.__main__",
    "app.scheduler.__main__",
]


class _DbInitSpy:
    """Stands in for ``db`` and records whether a connection was opened."""

    def __init__(self) -> None:
        self.init_calls = 0

    async def init_system_pool(self) -> None:
        self.init_calls += 1
        raise AssertionError("db.init_system_pool() reached despite a bad key")

    async def close_system_pool(self) -> None:  # pragma: no cover - not reached
        pass


@pytest.fixture
def entrypoint_module(monkeypatch):
    """Yield a loader that wires each entrypoint to the DB spy."""

    def _load(module_name: str):
        module = importlib.import_module(module_name)
        spy = _DbInitSpy()
        monkeypatch.setattr(module, "db", spy)
        return module, spy

    return _load


@pytest.mark.parametrize("module_name", ENTRYPOINTS)
def test_entrypoint_refuses_to_boot_without_a_secret_key(
    module_name, entrypoint_module, monkeypatch
):
    monkeypatch.setattr(settings, "SECRET_KEY", "")
    monkeypatch.setattr(settings, "FERNET_KEY", VALID_FERNET_KEY)
    module, spy = entrypoint_module(module_name)

    with pytest.raises(MissingSecretError):
        asyncio.run(module._run())

    assert spy.init_calls == 0


@pytest.mark.parametrize("module_name", ENTRYPOINTS)
def test_entrypoint_refuses_to_boot_without_a_fernet_key(
    module_name, entrypoint_module, monkeypatch
):
    monkeypatch.setattr(settings, "SECRET_KEY", VALID_SECRET_KEY)
    monkeypatch.setattr(settings, "FERNET_KEY", "")
    module, spy = entrypoint_module(module_name)

    with pytest.raises(MissingSecretError):
        asyncio.run(module._run())

    assert spy.init_calls == 0


@pytest.mark.parametrize("module_name", ENTRYPOINTS)
def test_entrypoint_rejects_the_published_placeholder(
    module_name, entrypoint_module, monkeypatch
):
    monkeypatch.setattr(
        settings, "SECRET_KEY", "change-me-in-production-use-openssl-rand-hex-32"
    )
    monkeypatch.setattr(settings, "FERNET_KEY", VALID_FERNET_KEY)
    module, spy = entrypoint_module(module_name)

    with pytest.raises(MissingSecretError):
        asyncio.run(module._run())

    assert spy.init_calls == 0


def test_api_lifespan_uses_the_same_helper():
    """The API must share the rule with the standalone entrypoints."""
    import inspect

    from app import main

    source = inspect.getsource(main.lifespan)
    assert "require_configured_secrets" in source


def test_every_entrypoint_calls_the_shared_helper():
    """Guard against a new process entrypoint skipping the check."""
    import inspect

    for module_name in ENTRYPOINTS:
        module = importlib.import_module(module_name)
        source = inspect.getsource(module._run)
        assert "require_configured_secrets" in source, module_name
        assert source.index("require_configured_secrets") < source.index(
            "init_system_pool"
        ), module_name
