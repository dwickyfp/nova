"""Shared helpers for the L3 integration suite.

The stack gate lives here rather than in each module: ``tests/conftest.py``
never raises when the Docker stack cannot start (a missing stack must be a
skip, not a failure), so a fixture that wants the stack has to read the status
and skip.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from _pytest.fixtures import FixtureRequest

    from tests.conftest import StackStatus


def shared_stack_host_port(env: str, default: int) -> int:
    """The host port of a shared-stack service, for a module's own connection.

    Several modules connect directly (a raw ``asyncmy`` connection, a MinIO
    client) instead of going through a fixture. When the run uses the shared
    compose stack, those connections must resolve the *same* host port the
    stack publishes, including any ``NOVA_TEST_*`` override — otherwise a
    checkout that shifted its ports collides with another stack again. An
    explicit per-module env (``NOVA_ORCH_*``) still wins.
    """
    override = os.getenv(env)
    return int(override) if override else default


def require_shared_stack(request: FixtureRequest, *, enabled: bool = True) -> None:
    """Bring up the shared stack and skip when it is unavailable.

    ``enabled`` mirrors ``_USE_SHARED_STACK``: a module pointed at an
    already-running engine (``NOVA_ORCH_SR_PORT``) manages its own stack and
    must not start or require the compose one. ``docker_services`` is only
    resolved when the fixture is actually in play, matching the previous
    behaviour of calling ``request.getfixturevalue("docker_services")``.
    """
    if not enabled or "docker_services" not in request.fixturenames:
        return
    stack: StackStatus = request.getfixturevalue("docker_services")
    if stack.unavailable:
        pytest.skip(f"real engine stack unavailable: {stack.reason}")
