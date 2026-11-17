"""The MySQL proxy's data-token login for notebook and task sandboxes.

A sandbox presents a data token bound to a Nova session (or, for a Python
task run, a password-free delegated session) and its live runtime. The proxy
accepts it only from sandbox networks, completes the engine login as the
principal (or through the worker account's impersonation of the owner) and
pins the bound role. Anything else is relayed to the engine unchanged.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import asyncmy
import fakeredis.aioredis
import pytest

from app.core.config import settings
from app.core.redis import session_store
from app.core.security import encrypt_password
from app.modules.access_control import data_tokens
from app.modules.access_control.role_activation import RoleAssignments
from app.modules.task_orchestration import delegation
from app.proxy.protocol import scramble_native_password
from app.proxy.server import MySQLProxyServer


@pytest.fixture
async def redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(session_store, "_redis", fake)
    monkeypatch.setattr(settings, "NOTEBOOK_SANDBOX_NETWORKS", "127.0.0.0/8")
    try:
        yield fake
    finally:
        await fake.aclose()


class FakeUpstream:
    def __init__(self) -> None:
        self.scramble = bytes(range(1, 21))
        self.connection_id = 42
        self.finished: list[bytes] = []

    async def finish(self, *, username, auth_response, database=None):
        self.finished.append(auth_response)

    async def open_session(self):
        return object()

    async def close(self):
        pass


class FakeActivation:
    def __init__(self) -> None:
        self.requested: list[str | None] = []

    async def activate(self, connection, *, principal, requested_role=None, **_):
        self.requested.append(requested_role)
        role = requested_role or "marketing"
        return role, RoleAssignments(
            assigned_roles=("finance", "marketing"), default_role="marketing"
        )


async def _proxy(monkeypatch, upstream, activation):
    monkeypatch.setattr(
        "app.proxy.connection.open_starrocks_login", AsyncMock(return_value=upstream)
    )
    monkeypatch.setattr("app.proxy.connection.role_activation_service", activation)
    monkeypatch.setattr("app.proxy.connection.write_audit_log", AsyncMock())
    server = MySQLProxyServer(host="127.0.0.1", port=0)
    await server.start()
    return server


async def _connect(server, password: str) -> None:
    conn = await asyncmy.connect(
        host="127.0.0.1", port=server.bound_port, user="alice", password=password,
        autocommit=True, connect_timeout=2,
    )
    conn.close()


async def _notebook_token(redis, *, password: str = "EnginePw#1") -> tuple[str, str]:
    session_id = await session_store.create(
        "alice", encrypt_password(password), ["finance", "marketing"],
        default_role="marketing", active_role="finance",
    )
    await redis.set(f"{data_tokens.NOTEBOOK.subject_key_prefix}rt-1", "live")
    issued = await data_tokens.mint(
        await data_tokens.resolve_session(session_id), purpose=data_tokens.NOTEBOOK,
        ttl=300, max_per_principal=4, subject="rt-1",
    )
    return session_id, issued.token


async def test_a_notebook_token_completes_the_engine_login_with_the_session_credential(
    redis, monkeypatch
):
    _, token = await _notebook_token(redis)
    upstream, activation = FakeUpstream(), FakeActivation()
    server = await _proxy(monkeypatch, upstream, activation)
    try:
        await _connect(server, token)
    finally:
        await server.stop()
    assert upstream.finished == [scramble_native_password("EnginePw#1", upstream.scramble)]
    assert activation.requested == ["finance"]


async def test_a_token_from_a_changed_session_is_refused(redis, monkeypatch):
    session_id, token = await _notebook_token(redis)
    await session_store.set_active_role(session_id, "marketing")
    upstream = FakeUpstream()
    server = await _proxy(monkeypatch, upstream, FakeActivation())
    try:
        with pytest.raises(asyncmy.errors.OperationalError) as error:
            await _connect(server, token)
    finally:
        await server.stop()
    assert error.value.args[0] == 1045 and upstream.finished == []


async def test_a_token_from_outside_the_sandbox_networks_is_only_relayed(redis, monkeypatch):
    _, token = await _notebook_token(redis)
    monkeypatch.setattr(settings, "NOTEBOOK_SANDBOX_NETWORKS", "10.9.0.0/16")
    upstream, activation = FakeUpstream(), FakeActivation()
    server = await _proxy(monkeypatch, upstream, activation)
    try:
        await _connect(server, token)
    finally:
        await server.stop()
    # The engine sees the token itself as a password, which it refuses for real.
    assert upstream.finished == [scramble_native_password(token, upstream.scramble)]
    assert activation.requested == [None]


async def _task_token(redis) -> str:
    session_id = await data_tokens.create_delegated_session("alice", "marketing", 60)
    await redis.set(f"{data_tokens.TASK.subject_key_prefix}run-1", "live")
    issued = await data_tokens.mint(
        await data_tokens.resolve_session(session_id), purpose=data_tokens.TASK,
        ttl=300, max_per_principal=4, subject="run-1",
    )
    return issued.token


async def test_a_task_token_logs_in_through_owner_impersonation(redis, monkeypatch):
    opened: list[tuple[str, str]] = []

    @asynccontextmanager
    async def owner_session(username, role):
        opened.append((username, role))
        yield object()

    monkeypatch.setattr(delegation, "owner_session", owner_session)
    token = await _task_token(redis)
    upstream, activation = FakeUpstream(), FakeActivation()
    server = await _proxy(monkeypatch, upstream, activation)
    try:
        await _connect(server, token)
    finally:
        await server.stop()
    assert opened == [("alice", "marketing")]
    assert upstream.finished == [] and activation.requested == ["marketing"]


async def test_a_refused_delegated_login_is_denied(redis, monkeypatch):
    @asynccontextmanager
    async def owner_session(username, role):
        raise delegation.DelegationError("not authorized")
        yield

    monkeypatch.setattr(delegation, "owner_session", owner_session)
    token = await _task_token(redis)
    server = await _proxy(monkeypatch, FakeUpstream(), FakeActivation())
    try:
        with pytest.raises(asyncmy.errors.OperationalError) as error:
            await _connect(server, token)
    finally:
        await server.stop()
    assert error.value.args[0] == 1045


@pytest.mark.parametrize(
    "username", ["root", "nova_admin", "NOVA_ADMIN", "nova_task_worker", "a b", ""]
)
def test_privileged_and_malformed_principals_are_never_delegated(username, monkeypatch):
    monkeypatch.setattr(settings, "WORKER_IMPERSONATION_USER", "nova_task_worker")
    with pytest.raises(delegation.DelegationError):
        delegation.check_principal(username)


def test_delegation_needs_a_configured_worker_account(monkeypatch):
    monkeypatch.setattr(settings, "WORKER_IMPERSONATION_USER", "")
    with pytest.raises(delegation.DelegationError, match="worker account"):
        delegation._executor()


async def test_owner_sessions_activate_exactly_the_requested_role(monkeypatch):
    from app.modules.task_orchestration.credentials import CredentialUnavailable

    class Executor:
        def __init__(self, refuse: bool) -> None:
            self.refuse = refuse

        def owner_connection(self, owner):
            @asynccontextmanager
            async def connection():
                if self.refuse:
                    raise CredentialUnavailable("Task worker is not authorized")
                yield object()

            return connection()

    activate = AsyncMock(return_value=("marketing", RoleAssignments(("marketing",), "marketing")))
    monkeypatch.setattr(delegation.role_activation_service, "activate", activate)
    monkeypatch.setattr(delegation, "_executor", lambda: Executor(False))
    async with delegation.owner_session("alice", "marketing") as connection:
        assert connection is not None
    activate.return_value = ("finance", RoleAssignments(("finance",), "finance"))
    with pytest.raises(delegation.DelegationError, match="could not be activated"):
        async with delegation.owner_session("alice", "marketing"):
            pass
    monkeypatch.setattr(delegation, "_executor", lambda: Executor(True))
    with pytest.raises(delegation.DelegationError, match="not authorized"):
        async with delegation.owner_session("alice", "marketing"):
            pass
