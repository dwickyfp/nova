"""Regression tests for the assistant SSRF path and the provider write gate.

Threat (NOVA-119, follow-up to NOVA-107): the assistant called the stored
provider ``endpoint`` with a raw ``httpx.AsyncClient`` and no ``ssrf_guard``,
and ``POST/PUT /api/v1/ai/providers`` accepted an endpoint from **any**
authenticated user. Together those are the same SSRF primitive PR #110 closed
on ``test_connection``, reached through a second egress path.

These tests pin the two fixes:

1. ``assistant.provider.AssistantProviderClient.complete`` validates the stored
   endpoint through the shared guard *and* routes the call through the guarded
   transport, so a private/loopback/link-local target (including the metadata
   service) is refused before any socket is opened.
2. The provider write routes require an admin role, and an invalid/private
   endpoint is refused at store time.

The tests drive the real router through ``TestClient`` (dependency override
only for ``get_current_user``) so the authorization chain is the real thing,
and they stub the transport/service at the module boundary — no engine, Redis,
network, or MinIO is required.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.ssrf_guard import (
    BlockedEndpointError,
    _GuardedTransport,
    resolve_and_validate_url,
)
from app.core import deps as deps_module
from app.modules.assistant.provider import (
    AssistantProviderClient,
    AssistantProviderError,
    ProviderConfig,
)

PUBLIC_IP = "93.184.216.34"
PRIVATE_ENDPOINTS = [
    "http://127.0.0.1:8000/v1/chat/completions",
    "http://localhost:11434/v1/chat/completions",
    "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/v1/chat/completions",
    "http://192.168.1.10/v1/chat/completions",
    "http://[::1]:9000/v1/chat/completions",
]


def _public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))
        ],
    )


def _config(endpoint: str) -> ProviderConfig:
    return ProviderConfig(
        provider_id="p1", model="gpt-4o-mini", endpoint=endpoint, api_key="sk-test"
    )


def _mock_guarded_client(handler) -> Any:
    """A ``guarded_async_client`` replacement backed by a mock transport.

    The real guard transport is layered over the mock handler so redirect hops
    are still re-validated, while the actual socket is replaced.
    """

    def factory(*args, **kwargs):
        return httpx.AsyncClient(
            transport=_GuardedMock(handler),
            timeout=kwargs.get("timeout"),
            follow_redirects=True,
        )

    return factory


class _GuardedMock(_GuardedTransport):
    """Guard transport whose underlying send is a mock handler."""

    def __init__(self, handler) -> None:
        super().__init__()
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resolve_and_validate_url(str(request.url))
        return await self._handler(request)


# ── 1. assistant path: private targets are refused before any socket ────────


@pytest.mark.parametrize("endpoint", PRIVATE_ENDPOINTS)
async def test_assistant_refuses_private_endpoint_without_calling_out(endpoint, monkeypatch):
    called = False

    def tracking_client(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("a guarded client was constructed for a blocked endpoint")

    monkeypatch.setattr("app.modules.assistant.provider.guarded_async_client", tracking_client)

    client = AssistantProviderClient(timeout_seconds=1.0)
    with pytest.raises(AssistantProviderError) as exc:
        await client.complete(
            messages=[{"role": "user", "content": "hi"}], provider=_config(endpoint)
        )

    assert called is False, "egress was attempted to a blocked endpoint"
    assert "not allowed" in str(exc.value)


async def test_assistant_blocked_message_does_not_leak_internal_detail():
    client = AssistantProviderClient(timeout_seconds=1.0)
    with pytest.raises(AssistantProviderError) as exc:
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            provider=_config("http://169.254.169.254/latest/meta-data"),
        )
    message = str(exc.value)
    assert "169.254.169.254" not in message
    assert "non-public" not in message
    assert "sk-test" not in message


async def test_assistant_refuses_redirect_to_private_address(monkeypatch):
    """A public endpoint that 302s to the metadata service must not be followed."""
    _public_dns(monkeypatch)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    monkeypatch.setattr(
        "app.modules.assistant.provider.guarded_async_client", _mock_guarded_client(handler)
    )

    client = AssistantProviderClient(timeout_seconds=1.0)
    with pytest.raises(AssistantProviderError) as exc:
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            provider=_config("https://public.example.com/v1/chat/completions"),
        )
    assert "not allowed" in str(exc.value)


async def test_assistant_public_endpoint_still_succeeds(monkeypatch):
    """The over-block control: a public endpoint is still callable."""
    _public_dns(monkeypatch)
    seen: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        )

    monkeypatch.setattr(
        "app.modules.assistant.provider.guarded_async_client", _mock_guarded_client(handler)
    )

    client = AssistantProviderClient(timeout_seconds=1.0)
    message = await client.complete(
        messages=[{"role": "user", "content": "hi"}],
        provider=_config("https://api.example.com/v1/chat/completions"),
    )

    assert message["content"] == "ok"
    assert message["usage"]["total_tokens"] == 5
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"


async def test_assistant_retries_a_transient_dns_failure(monkeypatch):
    """A public provider is not mislabeled private after one DNS miss."""
    _public_dns(monkeypatch)
    real_validate = resolve_and_validate_url
    validations = 0

    def flaky_validate(url: str):
        nonlocal validations
        validations += 1
        if validations == 1:
            raise BlockedEndpointError("Endpoint host could not be resolved", retryable=True)
        return real_validate(url)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        )

    monkeypatch.setattr("app.modules.assistant.provider.resolve_and_validate_url", flaky_validate)
    monkeypatch.setattr(
        "app.modules.assistant.provider.guarded_async_client", _mock_guarded_client(handler)
    )

    client = AssistantProviderClient(max_attempts=3, retry_base_seconds=0)
    message = await client.complete(
        messages=[{"role": "user", "content": "hi"}],
        provider=_config("https://api.example.com/v1/chat/completions"),
    )

    assert message["content"] == "ok"
    assert validations == 2


async def test_assistant_reports_persistent_dns_failure_accurately(monkeypatch):
    validations = 0
    client_constructed = False

    def unresolved(_url: str):
        nonlocal validations
        validations += 1
        raise BlockedEndpointError("Endpoint host could not be resolved", retryable=True)

    def tracking_client(*args, **kwargs):
        nonlocal client_constructed
        client_constructed = True
        raise AssertionError("request transport constructed after unresolved DNS")

    monkeypatch.setattr("app.modules.assistant.provider.resolve_and_validate_url", unresolved)
    monkeypatch.setattr("app.modules.assistant.provider.guarded_async_client", tracking_client)

    client = AssistantProviderClient(max_attempts=3, retry_base_seconds=0)
    with pytest.raises(AssistantProviderError) as exc:
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            provider=_config("https://api.example.com/v1/chat/completions"),
        )

    assert validations == 3
    assert client_constructed is False
    assert "could not be resolved" in str(exc.value)
    assert "public http" not in str(exc.value)


@pytest.mark.parametrize("status_code", [503, 520])
async def test_assistant_retries_transient_provider_status(monkeypatch, status_code):
    _public_dns(monkeypatch)
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code, headers={"retry-after": "0"})
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        )

    monkeypatch.setattr(
        "app.modules.assistant.provider.guarded_async_client", _mock_guarded_client(handler)
    )

    client = AssistantProviderClient(timeout_seconds=1.0, max_attempts=2, retry_base_seconds=0)
    message = await client.complete(
        messages=[{"role": "user", "content": "hi"}],
        provider=_config("https://api.example.com/v1/chat/completions"),
    )

    assert message["content"] == "ok"
    assert attempts == 2


async def test_assistant_stream_retries_only_before_visible_delta(monkeypatch):
    _public_dns(monkeypatch)
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(
            200,
            content=(
                'data: {"choices":[{"delta":{"content":"ok"},'
                '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    monkeypatch.setattr(
        "app.modules.assistant.provider.guarded_async_client", _mock_guarded_client(handler)
    )

    client = AssistantProviderClient(timeout_seconds=1.0, max_attempts=2, retry_base_seconds=0)
    events = [
        event
        async for event in client.stream(
            messages=[{"role": "user", "content": "hi"}],
            provider=_config("https://api.example.com/v1/chat/completions"),
        )
    ]

    assert events[0] == ("delta", "ok")
    assert events[-1][0] == "message"
    assert attempts == 2


async def test_assistant_provider_now_uses_the_shared_guard():
    """Structural backstop: the module must reference the guard, not raw httpx."""
    from pathlib import Path

    source = Path("app/modules/assistant/provider.py").read_text(encoding="utf-8")
    assert "guarded_async_client" in source
    assert "resolve_and_validate_url" in source
    assert "httpx.AsyncClient(" not in source


# ── 2. provider write routes: admin gate + store-time endpoint validation ────

ADMIN_USER = {
    "username": "admin",
    "session_id": "s",
    "roles": ["ACCOUNTADMIN"],
    "active_role": None,
    "encrypted_password": "enc",
}
ANALYST_USER = {
    "username": "analyst",
    "session_id": "s",
    "roles": ["test_analyst"],
    "active_role": None,
    "encrypted_password": "enc",
}
ROLELESS_USER: dict[str, Any] = {
    "username": "nobody",
    "session_id": "s",
    "roles": [],
    "active_role": None,
    "encrypted_password": "enc",
}

PREFIX = "/api/v1/ai"

CREATE_BODY = {"name": "openai", "type": "openai", "endpoint": "https://api.openai.com/v1"}


class _SpyAIService:
    """Records writes so a denied request can be proven not to have reached memory."""

    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[str] = []

    async def create_provider(self, data: dict, username: str) -> dict:
        self.create_calls.append({"data": data, "username": username})
        return {
            "id": "p1",
            "name": data["name"],
            "type": data["type"],
            "endpoint": data["endpoint"],
            "has_api_key": False,
            "api_key_masked": None,
        }

    async def update_provider(self, provider_id: str, data: dict) -> dict:
        self.update_calls.append((provider_id, data))
        return {
            "id": provider_id,
            "name": data.get("name", "openai"),
            "type": data.get("type", "openai"),
            "endpoint": data.get("endpoint", "https://api.openai.com/v1"),
            "has_api_key": False,
            "api_key_masked": None,
        }

    async def delete_provider(self, provider_id: str) -> bool:
        self.delete_calls.append(provider_id)
        return True


@pytest.fixture
def spy(monkeypatch) -> _SpyAIService:
    from app.modules.ai_ml import router as ai_router

    service = _SpyAIService()
    for name in ("create_provider", "update_provider", "delete_provider"):
        monkeypatch.setattr(ai_router.ai_service, name, getattr(service, name))
    return service


@pytest.fixture
def app(monkeypatch) -> FastAPI:
    from app.core.exceptions import register_exception_handlers
    from app.modules.ai_ml.router import router

    # Keep the public-control cases deterministic. Some local DNS resolvers
    # rewrite reserved example domains to a private sink address, which would
    # make the SSRF guard correctly reject what this test intends as public.
    original_getaddrinfo = socket.getaddrinfo

    def stable_getaddrinfo(host, port, *args, **kwargs):
        if host in {"api.example.com", "api.openai.com"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]
        return original_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", stable_getaddrinfo)

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(router, prefix=PREFIX)
    return application


def _as(client_app: FastAPI, user: dict | None) -> TestClient:
    async def fake_current_user():
        if user is None:
            raise deps_module.HTTPException(status_code=401, detail="Invalid or expired token")
        return user

    client_app.dependency_overrides[deps_module.get_current_user] = fake_current_user
    return TestClient(client_app, raise_server_exceptions=False)


class TestProviderWriteRoutesRequireAdmin:
    @pytest.mark.parametrize("user", [ANALYST_USER, ROLELESS_USER])
    def test_create_provider_non_admin_gets_403(self, app, spy, user):
        with _as(app, user) as client:
            resp = client.post(f"{PREFIX}/providers", json=CREATE_BODY)
        assert resp.status_code == 403, resp.text
        assert spy.create_calls == [], "provider write reached the service for a non-admin"

    @pytest.mark.parametrize("user", [ANALYST_USER, ROLELESS_USER])
    def test_update_provider_non_admin_gets_403(self, app, spy, user):
        with _as(app, user) as client:
            resp = client.put(f"{PREFIX}/providers/p1", json={"name": "x"})
        assert resp.status_code == 403, resp.text
        assert spy.update_calls == []

    @pytest.mark.parametrize("user", [ANALYST_USER, ROLELESS_USER])
    def test_delete_provider_non_admin_gets_403(self, app, spy, user):
        with _as(app, user) as client:
            resp = client.delete(f"{PREFIX}/providers/p1")
        assert resp.status_code == 403, resp.text
        assert spy.delete_calls == []

    def test_create_provider_unauthenticated_is_401(self, app, spy):
        with _as(app, None) as client:
            resp = client.post(f"{PREFIX}/providers", json=CREATE_BODY)
        assert resp.status_code == 401, resp.text
        assert spy.create_calls == []

    def test_admin_can_create_provider(self, app, spy):
        with _as(app, ADMIN_USER) as client:
            resp = client.post(f"{PREFIX}/providers", json=CREATE_BODY)
        assert resp.status_code == 201, resp.text
        assert spy.create_calls and spy.create_calls[0]["username"] == "admin"

    def test_admin_can_update_provider(self, app, spy):
        with _as(app, ADMIN_USER) as client:
            resp = client.put(f"{PREFIX}/providers/p1", json={"name": "x"})
        assert resp.status_code == 200, resp.text
        assert spy.update_calls and spy.update_calls[0][0] == "p1"


class TestProviderEndpointValidatedAtStoreTime:
    @pytest.mark.parametrize("endpoint", PRIVATE_ENDPOINTS)
    def test_admin_private_endpoint_on_create_is_400(self, app, spy, endpoint):
        with _as(app, ADMIN_USER) as client:
            resp = client.post(f"{PREFIX}/providers", json={**CREATE_BODY, "endpoint": endpoint})
        assert resp.status_code == 400, resp.text
        assert "not allowed" in resp.json()["detail"]
        assert spy.create_calls == [], "a private endpoint was persisted"

    @pytest.mark.parametrize("endpoint", PRIVATE_ENDPOINTS)
    def test_admin_private_endpoint_on_update_is_400(self, app, spy, endpoint):
        with _as(app, ADMIN_USER) as client:
            resp = client.put(f"{PREFIX}/providers/p1", json={"endpoint": endpoint})
        assert resp.status_code == 400, resp.text
        assert "not allowed" in resp.json()["detail"]
        assert spy.update_calls == [], "a private endpoint was persisted"

    def test_error_does_not_leak_resolved_address(self, app, spy):
        with _as(app, ADMIN_USER) as client:
            resp = client.post(
                f"{PREFIX}/providers",
                json={**CREATE_BODY, "endpoint": "http://169.254.169.254"},
            )
        detail = resp.json()["detail"]
        assert "169.254.169.254" not in detail

    def test_public_endpoint_is_still_accepted(self, app, spy):
        with _as(app, ADMIN_USER) as client:
            resp = client.post(f"{PREFIX}/providers", json=CREATE_BODY)
        assert resp.status_code == 201, resp.text
        assert len(spy.create_calls) == 1


class TestProviderWriteRoutesUseTheSharedRoleSet:
    def test_admin_roles_match_the_repo_admin_set(self):
        from app.modules.ai_ml.router import ADMIN_ROLES
        from app.modules.users.router import ADMIN_ROLES as USER_ADMIN_ROLES

        assert ADMIN_ROLES == USER_ADMIN_ROLES

    @pytest.mark.parametrize(
        "method,path",
        [("post", "/providers"), ("put", "/providers/{provider_id}")],
    )
    def test_write_routes_are_guarded_by_require_role(self, method, path):
        from app.modules.ai_ml.router import router

        route = next(
            r for r in router.routes if r.path == path and method.upper() in (r.methods or set())
        )
        assert "require_role.<locals>._check" in _dependency_names(route.dependant)


def _dependency_names(dependant) -> set[str]:
    names: set[str] = set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        call = getattr(node, "call", None)
        if call is not None:
            names.add(f"{getattr(call, '__qualname__', getattr(call, '__name__', ''))}")
        stack.extend(getattr(node, "dependencies", []) or [])
    return names


# ── guard library sanity (kept here so the path tests have an oracle) ───────


def test_guard_still_classifies_the_expected_ranges():
    for blocked in PRIVATE_ENDPOINTS:
        with pytest.raises(BlockedEndpointError):
            resolve_and_validate_url(blocked)
    assert ipaddress.ip_address(PUBLIC_IP).is_global
