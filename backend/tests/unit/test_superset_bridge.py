"""Superset bridge: BI handles, data tokens, service auth and the proxy login.

Redis is replaced with a small in-memory fake implementing exactly the
commands the session store and the token store use, so the tests exercise the
real key layout and expiry decisions rather than mocks of them.
"""

from __future__ import annotations

import fnmatch
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncmy
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.exceptions import InsufficientRoleError
from app.core.redis import session_store
from app.core.security import encrypt_password
from app.modules.access_control.role_activation import RoleAssignments
from app.modules.access_control.service import AccessControlError
from app.modules.superset import access, tokens
from app.proxy.executor import ProxyQueryExecutor
from app.proxy.protocol import scramble_native_password
from app.proxy.server import MySQLProxyServer
from app.proxy.session import SessionState


class FakeRedis:
    """The subset of ``redis.asyncio`` the session and token stores use."""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.expiry: dict[str, float] = {}

    def _live(self, key: str):
        if key in self.expiry and self.expiry[key] <= time.time():
            self.data.pop(key, None)
            self.expiry.pop(key, None)
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = str(value)
        if ex:
            self.expiry[key] = time.time() + ex

    async def get(self, key):
        return self._live(key)

    async def hset(self, key, mapping):
        self.data.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    async def hgetall(self, key):
        return dict(self._live(key) or {})

    async def expire(self, key, seconds):
        if key in self.data:
            self.expiry[key] = time.time() + seconds

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)
            self.expiry.pop(key, None)

    async def zadd(self, key, mapping):
        self.data.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(self, key, low, high):
        zset = self._live(key) or {}
        high = float(high)
        for member in [m for m, score in zset.items() if score <= high]:
            del zset[member]

    async def zcard(self, key):
        return len(self._live(key) or {})

    async def zpopmin(self, key, count):
        zset = self._live(key) or {}
        popped = sorted(zset.items(), key=lambda item: item[1])[:count]
        for member, _ in popped:
            del zset[member]
        return popped

    async def zrangebyscore(self, key, low, high):
        zset = self._live(key) or {}
        return [m for m, score in sorted(zset.items(), key=lambda i: i[1]) if score >= float(low)]

    async def zrem(self, key, member):
        (self._live(key) or {}).pop(member, None)

    def keys_matching(self, pattern: str) -> list[str]:
        return [key for key in self.data if fnmatch.fnmatch(key, pattern)]


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(session_store, "_redis", fake)
    return fake


async def _session(role: str = "finance", *, password: str = "S3cret!pw") -> str:
    return await session_store.create(
        "alice",
        encrypt_password(password),
        ["finance", "marketing"],
        default_role="marketing",
        active_role=role,
    )


# ── Handles ──────────────────────────────────────────────────────────────


async def test_handle_resolves_to_its_session_and_is_stored_hashed(redis):
    session_id = await _session()
    handle = await tokens.issue_handle(session_id)

    resolved = await tokens.session_for_handle(handle)

    assert (resolved.username, resolved.active_role) == ("alice", "finance")
    assert not any(handle in key for key in redis.data)


async def test_handle_dies_with_its_session(redis):
    session_id = await _session()
    handle = await tokens.issue_handle(session_id)
    await session_store.delete(session_id)

    with pytest.raises(tokens.BiCredentialError):
        await tokens.session_for_handle(handle)


async def test_revoked_or_unknown_handle_is_refused(redis):
    handle = await tokens.issue_handle(await _session())
    await tokens.revoke_handle(handle)
    with pytest.raises(tokens.BiCredentialError):
        await tokens.session_for_handle(handle)
    with pytest.raises(tokens.BiCredentialError):
        await tokens.session_for_handle("")


async def test_session_without_readable_password_is_not_usable(redis):
    session_id = await session_store.create(
        "alice", "not-a-fernet-token", ["finance"], default_role="finance", active_role="finance"
    )
    with pytest.raises(tokens.BiCredentialError):
        await tokens.resolve_session(session_id)


# ── Data tokens ──────────────────────────────────────────────────────────


async def test_data_token_is_reused_while_the_binding_holds(redis):
    bi_session = await tokens.resolve_session(await _session())

    first = await tokens.mint_data_token(bi_session)
    second = await tokens.mint_data_token(bi_session)

    assert first.token == second.token
    stored = [redis.data[k] for k in redis.keys_matching("nova:bi:token:*")]
    assert len(stored) == 1
    assert first.token not in stored[0]["token"]


async def test_role_switch_issues_a_new_token_and_invalidates_the_old_binding(redis):
    session_id = await _session()
    old = await tokens.mint_data_token(await tokens.resolve_session(session_id))
    scramble = bytes(range(20))
    binding = await tokens.match_data_token(
        "alice", scramble, scramble_native_password(old.token, scramble)
    )
    assert binding is not None and binding.active_role == "finance"

    await session_store.set_active_role(session_id, "marketing")

    with pytest.raises(tokens.BiCredentialError):
        await tokens.verify_binding(binding)
    new = await tokens.mint_data_token(await tokens.resolve_session(session_id))
    assert new.token != old.token
    new_binding = await tokens.match_data_token(
        "alice", scramble, scramble_native_password(new.token, scramble)
    )
    assert new_binding is not None and new_binding.active_role == "marketing"
    assert (await tokens.verify_binding(new_binding)).active_role == "marketing"


async def test_wrong_response_or_principal_matches_nothing(redis):
    issued = await tokens.mint_data_token(await tokens.resolve_session(await _session()))
    scramble = bytes(20)
    response = scramble_native_password(issued.token, scramble)

    assert await tokens.match_data_token("alice", scramble, bytes(20)) is None
    assert await tokens.match_data_token("bob", scramble, response) is None
    assert await tokens.match_data_token("alice", bytes(range(20)), response) is None
    assert await tokens.match_data_token("alice", scramble, b"short") is None


async def test_logout_invalidates_a_token_binding(redis):
    session_id = await _session()
    issued = await tokens.mint_data_token(await tokens.resolve_session(session_id))
    scramble = bytes(20)
    binding = await tokens.match_data_token(
        "alice", scramble, scramble_native_password(issued.token, scramble)
    )
    await session_store.delete(session_id)

    with pytest.raises(tokens.BiCredentialError):
        await tokens.verify_binding(binding)


async def test_live_tokens_per_principal_are_bounded(redis, monkeypatch):
    monkeypatch.setattr(settings, "SUPERSET_MAX_TOKENS_PER_PRINCIPAL", 2)
    for _ in range(4):
        await tokens.mint_data_token(await tokens.resolve_session(await _session()))

    assert await redis.zcard("nova:bi:tokens:alice") == 2
    assert len(redis.keys_matching("nova:bi:token:*")) == 2


# ── Service access and role mapping ──────────────────────────────────────


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1", True),
        ("172.18.0.5", True),
        ("::ffff:10.1.2.3", True),
        ("8.8.8.8", False),
        ("not-an-ip", False),
        ("", False),
    ],
)
def test_service_networks(host, allowed):
    assert access.peer_allowed(host) is allowed


def _request(host: str, token: str | None):
    headers = {} if token is None else {access.SERVICE_TOKEN_HEADER: token}
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)


def test_service_token_gates(monkeypatch):
    monkeypatch.setattr(settings, "SUPERSET_SERVICE_TOKEN", "")
    with pytest.raises(HTTPException) as unset:
        access.require_superset_service(_request("172.18.0.5", "x"))
    assert unset.value.status_code == 503

    monkeypatch.setattr(settings, "SUPERSET_SERVICE_TOKEN", "service-secret")
    with pytest.raises(HTTPException) as wrong:
        access.require_superset_service(_request("172.18.0.5", "nope"))
    assert wrong.value.status_code == 401
    with pytest.raises(HTTPException) as public:
        access.require_superset_service(_request("8.8.8.8", "service-secret"))
    assert public.value.status_code == 403
    access.require_superset_service(_request("172.18.0.5", "service-secret"))


async def test_superset_role_follows_the_active_role(monkeypatch):
    monkeypatch.setattr(access, "author_roles", AsyncMock(return_value=frozenset({"finance"})))
    assert await access.superset_role_for("ACCOUNTADMIN") == access.SUPERSET_ADMIN
    assert await access.superset_role_for("finance") == access.SUPERSET_AUTHOR
    assert await access.superset_role_for("marketing") == access.SUPERSET_VIEWER
    assert await access.superset_role_for("") == access.SUPERSET_VIEWER


async def test_author_roles_are_validated_before_any_write(monkeypatch):
    execute = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(access.db, "execute_system", execute)
    with pytest.raises(ValueError):
        await access.replace_author_roles(["finance", "x; DROP TABLE t"], changed_by="admin")
    execute.assert_not_called()


# ── Proxy ────────────────────────────────────────────────────────────────


async def test_role_statements_are_refused_on_a_bi_connection():
    session = SessionState(
        principal="alice",
        assigned_roles=("finance", "marketing"),
        default_role="marketing",
        active_role="finance",
        role_locked=True,
    )
    result = await ProxyQueryExecutor(session).execute(
        "SET ROLE marketing", username="alice", connection=object()
    )
    assert result.error_code == 1235
    assert session.active_role == "finance"


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


async def test_token_login_completes_the_engine_login_with_the_session_credential(
    redis, monkeypatch
):
    session_id = await _session(password="EnginePw#1")
    issued = await tokens.mint_data_token(await tokens.resolve_session(session_id))
    upstream, activation = FakeUpstream(), FakeActivation()
    server = await _proxy(monkeypatch, upstream, activation)
    try:
        conn = await asyncmy.connect(
            host="127.0.0.1",
            port=server.bound_port,
            user="alice",
            password=issued.token,
            autocommit=True,
            connect_timeout=2,
        )
        conn.close()
    finally:
        await server.stop()

    assert upstream.finished == [scramble_native_password("EnginePw#1", upstream.scramble)]
    assert activation.requested == ["finance"]


async def test_token_from_a_changed_session_is_refused(redis, monkeypatch):
    session_id = await _session()
    issued = await tokens.mint_data_token(await tokens.resolve_session(session_id))
    await session_store.set_active_role(session_id, "marketing")
    upstream = FakeUpstream()
    server = await _proxy(monkeypatch, upstream, FakeActivation())
    try:
        with pytest.raises(asyncmy.errors.OperationalError) as error:
            await asyncmy.connect(
                host="127.0.0.1",
                port=server.bound_port,
                user="alice",
                password=issued.token,
                autocommit=True,
                connect_timeout=2,
            )
    finally:
        await server.stop()

    assert error.value.args[0] == 1045
    assert upstream.finished == []


async def test_ordinary_password_is_still_relayed_unchanged(redis, monkeypatch):
    upstream, activation = FakeUpstream(), FakeActivation()
    server = await _proxy(monkeypatch, upstream, activation)
    try:
        conn = await asyncmy.connect(
            host="127.0.0.1",
            port=server.bound_port,
            user="alice",
            password="typed-password",
            autocommit=True,
            connect_timeout=2,
        )
        conn.close()
    finally:
        await server.stop()

    assert upstream.finished == [scramble_native_password("typed-password", upstream.scramble)]
    assert activation.requested == [None]


async def test_a_bound_connection_ends_when_the_session_changes(redis, monkeypatch):
    session_id = await _session()
    issued = await tokens.mint_data_token(await tokens.resolve_session(session_id))
    server = await _proxy(monkeypatch, FakeUpstream(), FakeActivation())
    try:
        conn = await asyncmy.connect(
            host="127.0.0.1",
            port=server.bound_port,
            user="alice",
            password=issued.token,
            autocommit=True,
            connect_timeout=2,
        )
        await session_store.set_active_role(session_id, "marketing")
        with pytest.raises(asyncmy.errors.OperationalError) as error:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
        assert error.value.args[0] == 1045
        conn.close()
    finally:
        await server.stop()


# ── HTTP surface ─────────────────────────────────────────────────────────


@pytest.fixture
def bridge_app(redis, monkeypatch):
    from fastapi import FastAPI

    from app.core.deps import get_current_user
    from app.modules.superset import router as bridge

    monkeypatch.setattr(settings, "SUPERSET_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(bridge, "write_audit_log", AsyncMock())
    monkeypatch.setattr(access, "author_roles", AsyncMock(return_value=frozenset({"finance"})))
    application = FastAPI()
    application.include_router(bridge.router, prefix="/api/v1/superset")
    application.include_router(bridge.internal_router, prefix="/api/v1/internal/superset")
    state: dict = {}

    async def current_user():
        return state["user"]

    application.dependency_overrides[get_current_user] = current_user
    return application, state


async def _client(application):
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://nova.test"
    )


async def test_bi_session_cookie_is_scoped_and_resolves_to_identity(bridge_app):
    application, state = bridge_app
    session_id = await _session()
    state["user"] = {
        "username": "alice",
        "session_id": session_id,
        "active_role": "finance",
        "security_context_version": 1,
    }
    async with await _client(application) as http:
        opened = await http.post("/api/v1/superset/session")
        assert opened.status_code == 200
        assert opened.json() == {"path": "/bi/", "superset_role": access.SUPERSET_AUTHOR}
        cookie = opened.headers["set-cookie"].lower()
        assert "httponly" in cookie and "path=/bi" in cookie and "samesite=strict" in cookie
        handle = opened.cookies["nova_bi"]

        service = {access.SERVICE_TOKEN_HEADER: "service-secret"}
        who = await http.post(
            "/api/v1/internal/superset/identity", json={"handle": handle}, headers=service
        )
        assert who.json() == {
            "username": "alice",
            "active_role": "finance",
            "superset_role": access.SUPERSET_AUTHOR,
            "security_context_version": 1,
        }
        token = await http.post(
            "/api/v1/internal/superset/token", json={"handle": handle}, headers=service
        )
        assert token.json()["username"] == "alice" and token.json()["token"]

        unauthenticated = await http.post(
            "/api/v1/internal/superset/identity", json={"handle": handle}
        )
        assert unauthenticated.status_code == 401

        await session_store.delete(session_id)
        ended = await http.post(
            "/api/v1/internal/superset/identity", json={"handle": handle}, headers=service
        )
        assert ended.status_code == 401


async def test_closing_the_bi_session_revokes_the_handle(bridge_app):
    application, state = bridge_app
    session_id = await _session()
    state["user"] = {"username": "alice", "session_id": session_id, "active_role": "finance"}
    async with await _client(application) as http:
        handle = (await http.post("/api/v1/superset/session")).cookies["nova_bi"]
        http.cookies.set("nova_bi", handle)
        closed = await http.delete("/api/v1/superset/session")
        assert closed.status_code == 204
    with pytest.raises(tokens.BiCredentialError):
        await tokens.session_for_handle(handle)


async def test_author_roles_need_an_active_accountadmin(bridge_app, monkeypatch):
    application, state = bridge_app
    replace = AsyncMock(return_value=["finance"])
    from app.modules.superset import router as bridge

    monkeypatch.setattr(bridge, "replace_author_roles", replace)
    put_metadata = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(bridge.access_control_service, "put_bi_metadata_access", put_metadata)
    async with await _client(application) as http:
        state["user"] = {"username": "bob", "session_id": "s", "active_role": "finance"}
        # Nova's app maps this error to 403; this bare app surfaces it as raised.
        with pytest.raises(InsufficientRoleError):
            await http.put("/api/v1/superset/author-roles", json={"roles": ["finance"]})
        replace.assert_not_called()

        state["user"] = {
            "username": "admin",
            "session_id": "s",
            "active_role": "ACCOUNTADMIN",
            "assigned_roles": ["ACCOUNTADMIN"],
        }
        invalid = await http.put("/api/v1/superset/author-roles", json={"roles": ["x y"]})
        assert invalid.status_code == 422
        put_metadata.assert_not_called()

        put_metadata.side_effect = AccessControlError("Ranger refused")
        refused = await http.put("/api/v1/superset/author-roles", json={"roles": ["finance"]})
        assert refused.status_code == 403
        replace.assert_not_called()

        put_metadata.side_effect = None
        ok = await http.put("/api/v1/superset/author-roles", json={"roles": ["finance"]})
        assert ok.json() == {"roles": ["finance"]}
        assert put_metadata.await_args.args[1] == ["finance"]


def test_bi_metadata_policy_grants_reflection_only():
    from app.integrations.ranger.compiler import compile_bi_metadata_policy

    policy = compile_bi_metadata_policy(roles=["finance", "ACCOUNTADMIN", "finance"]).to_api()
    assert policy["resources"]["database"]["values"] == ["information_schema"]
    assert "tables_config" in policy["resources"]["table"]["values"]
    [item] = policy["policyItems"]
    assert item["roles"] == ["ACCOUNTADMIN", "finance"]
    assert [access["type"] for access in item["accesses"]] == ["select"]


async def test_superset_actions_are_audited_for_the_handle_principal(bridge_app):
    from app.modules.superset import router as bridge

    application, _state = bridge_app
    handle = await tokens.issue_handle(await _session())
    service = {access.SERVICE_TOKEN_HEADER: "service-secret"}
    async with await _client(application) as http:
        recorded = await http.post(
            "/api/v1/internal/superset/audit",
            json={
                "handle": handle,
                "kind": "BI_EXPORT",
                "action": "ChartRestApi.export",
                "objects": ["chart:21"],
            },
            headers=service,
        )
        assert recorded.status_code == 200
        entry = bridge.write_audit_log.await_args.kwargs
        assert (entry["user_name"], entry["action"], entry["active_role"]) == (
            "alice",
            "BI_EXPORT",
            "finance",
        )
        assert entry["object_name"] == "ChartRestApi.export:chart:21"

        injected = await http.post(
            "/api/v1/internal/superset/audit",
            json={"handle": handle, "kind": "BI_EXPORT", "action": "x; DROP", "objects": []},
            headers=service,
        )
        assert injected.status_code == 422
