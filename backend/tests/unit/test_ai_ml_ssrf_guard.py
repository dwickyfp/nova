"""Regression tests for the SSRF guard on the AI provider test-connection path.

Threat (NOVA-107): ``POST /api/v1/ai/test-connection`` accepts an arbitrary
``endpoint`` from an authenticated user and issues a server-side ``httpx`` GET,
returning the response body. Left unguarded that is an SSRF primitive against
the loopback interface, the deployment's private network, and the cloud
metadata service.

The tests are grouped by the issue's acceptance criteria so each can be checked
independently:

1. a private/loopback/link-local resolution is rejected before any request;
2. ``169.254.169.254``, ``localhost`` and ``127.0.0.1`` are refused while a
   public/mock-allowed endpoint still works;
3. redirects are subject to the same policy;
4. the surfaced error does not leak internal detail (resolved address, etc.).
"""

from __future__ import annotations

import ipaddress
import socket

import httpx
import pytest

from app.common.ssrf_guard import (
    BlockedEndpointError,
    guarded_async_client,
    resolve_and_validate_url,
)

# ── AC1 + AC2: private / loopback / link-local literals ─────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/v1",
        "http://127.1.2.3/v1",
        "http://localhost:9000/v1",
        "http://[::1]:9000/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://[fe80::1]/v1",
        "http://10.0.0.5/v1",
        "http://192.168.1.10/v1",
        "http://172.16.5.4/v1",
        "http://0.0.0.0/v1",
        "http://[fc00::1]/v1",
        "http://100.64.0.1/v1",
    ],
)
def test_non_public_literal_addresses_are_blocked(url):
    with pytest.raises(BlockedEndpointError):
        resolve_and_validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/v1",
        "file:///etc/passwd",
        "gopher://example.com/",
        "https://",
        "not-a-url",
        "",
    ],
)
def test_disallowed_schemes_and_malformed_urls_are_blocked(url):
    with pytest.raises(BlockedEndpointError):
        resolve_and_validate_url(url)


# ── AC1: block happens after DNS resolution, not by string match ────────────


def test_hostname_resolving_to_loopback_is_blocked(monkeypatch):
    """A benign-looking hostname that resolves to 127.0.0.1 must be refused."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(BlockedEndpointError):
        resolve_and_validate_url("https://evil.example.com/v1")


def test_hostname_with_one_private_answer_among_public_answers_is_blocked(monkeypatch):
    """A split-horizon answer is treated as unsafe if *any* address is private."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(BlockedEndpointError):
        resolve_and_validate_url("https://api.example.com/v1")


def test_dns_failure_fails_closed(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(BlockedEndpointError):
        resolve_and_validate_url("https://does-not-exist.example/v1")


def test_public_hostname_passes_and_returns_pinned_answers(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    resolved = resolve_and_validate_url("https://api.openai.com/v1")
    assert resolved.addresses == (ipaddress.ip_address("93.184.216.34"),)
    assert resolved.port == 443


# ── AC3: redirects are re-validated ─────────────────────────────────────────


async def test_redirect_to_private_address_is_rejected(monkeypatch):
    """A public host that 302s to the metadata service must not be followed.

    The transport re-validates every hop, so the second request never leaves
    the process. ``getaddrinfo`` is patched to make the first host public; the
    redirect target is a literal private address, which needs no resolution.
    """

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data"}
        )

    transport = httpx.MockTransport(handler)
    # The guard transport wraps the mock so the redirect location is checked;
    # the mock is never reached for the blocked hop.
    async with httpx.AsyncClient(
        transport=_mock_guarded_transport(transport), follow_redirects=True
    ) as client:
        with pytest.raises(BlockedEndpointError):
            await client.get("https://public.example.com/v1")


def _mock_guarded_transport(inner: httpx.AsyncBaseTransport) -> httpx.AsyncBaseTransport:
    """Wrap a mock transport with the guard's per-request validation."""
    from app.common.ssrf_guard import _GuardedTransport

    class _MockGuarded(_GuardedTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            resolve_and_validate_url(str(request.url))
            return await inner.handle_async_request(request)

    return _MockGuarded()


async def test_guarded_client_refuses_a_private_target(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ],
    )
    async with guarded_async_client(timeout=1.0) as client:
        with pytest.raises(BlockedEndpointError):
            await client.get("https://localhost.localdomain/v1")


# ── AC2 + AC4: the service boundary ─────────────────────────────────────────


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:8000",
        "http://localhost:11434",
        "http://169.254.169.254",
        "http://10.0.0.1:8080",
    ],
)
async def test_service_rejects_private_endpoints_without_calling_out(
    endpoint, monkeypatch
):
    from app.modules.ai_ml.service import AIService

    called = False

    def tracking_client(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("a request was issued to a blocked endpoint")

    monkeypatch.setattr("app.modules.ai_ml.service.guarded_async_client", tracking_client)

    result = await AIService().test_connection(
        provider_type="openai", endpoint=endpoint, api_key="sk-test"
    )
    assert result["success"] is False
    assert "not allowed" in result["message"]
    assert called is False, "the HTTP client was constructed for a blocked endpoint"


def test_blocked_message_does_not_leak_internal_detail():
    try:
        resolve_and_validate_url("http://169.254.169.254/v1")
    except BlockedEndpointError as exc:
        message = str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected BlockedEndpointError")
    assert "169.254.169.254" not in message
    assert "non-public" not in message


async def test_service_reports_a_generic_error_for_unexpected_failures(monkeypatch):
    """An internal exception must not be echoed verbatim to the caller."""

    class ExplodingClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("secret internal detail")

    monkeypatch.setattr(
        "app.modules.ai_ml.service.guarded_async_client", ExplodingClient
    )

    from app.modules.ai_ml.service import AIService

    result = await AIService().test_connection(
        provider_type="openai", endpoint="https://api.example.com", api_key=None
    )
    assert result["success"] is False
    assert "secret internal detail" not in result["message"]


async def test_a_public_mock_endpoint_still_succeeds(monkeypatch):
    """The over-block control: an allowed public endpoint is still callable."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]})

    transport = httpx.MockTransport(handler)

    def client_factory(*args, **kwargs):
        return httpx.AsyncClient(transport=transport, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(
        "app.modules.ai_ml.service.guarded_async_client", client_factory
    )

    from app.modules.ai_ml.service import AIService

    result = await AIService().test_connection(
        provider_type="openai", endpoint="https://api.example.com", api_key="sk-test"
    )
    assert result["success"] is True
    assert result["models"] == ["gpt-4o", "gpt-4o-mini"]


# ── Local and private-network model servers ─────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:1234/v1",
        "http://[::1]:9000/v1",
        "http://10.0.0.5/v1",
        "http://192.168.1.10:8000/v1",
        "http://172.16.5.4/v1",
        "http://[fc00::1]/v1",
    ],
)
def test_private_network_mode_allows_local_model_servers(url):
    assert resolve_and_validate_url(url, allow_private_network=True).addresses


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://[::ffff:169.254.169.254]/latest/meta-data",
        "http://[fe80::1]/v1",
        "http://0.0.0.0/v1",
        "http://[::]/v1",
        "http://224.0.0.1/v1",
        "http://100.64.0.1/v1",
        "http://240.0.0.1/v1",
        "file:///etc/passwd",
    ],
)
def test_private_network_mode_still_blocks_metadata_and_reserved(url):
    with pytest.raises(BlockedEndpointError):
        resolve_and_validate_url(url, allow_private_network=True)


def test_private_network_mode_checks_every_resolved_address(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.65.254", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(BlockedEndpointError):
        resolve_and_validate_url("http://host.docker.internal:11434/v1", allow_private_network=True)


async def test_private_network_client_refuses_redirect_to_metadata(monkeypatch):
    from app.common.ssrf_guard import _GuardedTransport

    class MockGuarded(_GuardedTransport):
        async def handle_async_request(self, request):
            resolve_and_validate_url(
                str(request.url), allow_private_network=self._allow_private_network
            )
            if request.url.host == "localhost":
                return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
            return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=MockGuarded(allow_private_network=True), follow_redirects=True
    ) as client:
        with pytest.raises(BlockedEndpointError):
            await client.get("http://localhost:11434/v1/models")


async def test_service_allows_a_local_endpoint_only_when_asked(monkeypatch):
    from app.modules.ai_ml.service import AIService

    seen: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "llama3"}]})

    def client_factory(*args, **kwargs):
        seen.append(kwargs.get("allow_private_network", False))
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.modules.ai_ml.service.guarded_async_client", client_factory)

    refused = await AIService().test_connection(
        provider_type="openai_compatible", endpoint="http://localhost:11434/v1", api_key=None
    )
    allowed = await AIService().test_connection(
        provider_type="openai_compatible",
        endpoint="http://localhost:11434/v1",
        api_key=None,
        allow_private_network=True,
    )

    assert refused["success"] is False and "not allowed" in refused["message"]
    assert allowed["success"] is True and allowed["models"] == ["llama3"]
    assert seen == [True]


@pytest.mark.parametrize(
    "roles,expected", [(["ACCOUNTADMIN"], True), (["ANALYST"], False), ([], False)]
)
def test_test_connection_route_allows_local_network_only_for_admins(
    monkeypatch, roles, expected
):
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core import deps
    from app.modules.ai_ml import router as ai_router

    probe = AsyncMock(return_value={"success": True, "message": "ok", "models": []})
    monkeypatch.setattr(ai_router.ai_service, "test_connection", probe)
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/v1/ai")

    async def current_user():
        return {"username": "u", "roles": roles}

    app.dependency_overrides[deps.get_current_user] = current_user
    resp = TestClient(app).post(
        "/api/v1/ai/test-connection",
        json={"type": "openai_compatible", "endpoint": "http://localhost:11434/v1"},
    )

    assert resp.status_code == 200, resp.text
    assert probe.await_args.kwargs["allow_private_network"] is expected


# ── localhost inside a container means the host machine ─────────────────────


def _capture_sent(monkeypatch) -> list[httpx.Request]:
    sent: list[httpx.Request] = []

    async def fake_send(self, request):
        sent.append(request)
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_send)
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "host.docker.internal":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.65.254", port))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    return sent


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:20128/v1/models",
        "http://127.0.0.1:20128/v1/models",
        "http://[::1]:20128/v1/models",
    ],
)
async def test_loopback_host_sends_localhost_to_the_host_machine(monkeypatch, url):
    sent = _capture_sent(monkeypatch)

    async with guarded_async_client(
        allow_private_network=True, loopback_host="host.docker.internal"
    ) as client:
        response = await client.get(url)

    assert response.status_code == 200
    assert str(sent[0].url) == "http://host.docker.internal:20128/v1/models"
    assert sent[0].headers["host"] == httpx.URL(url).netloc.decode()


async def test_loopback_host_leaves_other_hosts_alone(monkeypatch):
    sent = _capture_sent(monkeypatch)

    async with guarded_async_client(
        allow_private_network=True, loopback_host="host.docker.internal"
    ) as client:
        await client.get("http://192.168.1.10:8000/v1/models")

    assert str(sent[0].url) == "http://192.168.1.10:8000/v1/models"


async def test_loopback_host_needs_the_private_network_mode(monkeypatch):
    sent = _capture_sent(monkeypatch)

    async with guarded_async_client(loopback_host="host.docker.internal") as client:
        with pytest.raises(BlockedEndpointError):
            await client.get("http://localhost:20128/v1/models")

    assert sent == []


async def test_loopback_host_target_is_still_validated(monkeypatch):
    sent = _capture_sent(monkeypatch)

    async with guarded_async_client(
        allow_private_network=True, loopback_host="169.254.169.254"
    ) as client:
        with pytest.raises(BlockedEndpointError):
            await client.get("http://localhost:20128/v1/models")

    assert sent == []


async def test_test_connection_uses_the_configured_loopback_host(monkeypatch):
    from app.core.config import settings
    from app.modules.ai_ml.service import AIService

    sent = _capture_sent(monkeypatch)
    monkeypatch.setattr(settings, "AI_PROVIDER_LOOPBACK_HOST", "host.docker.internal")

    result = await AIService().test_connection(
        provider_type="openai_compatible",
        endpoint="http://localhost:20128/v1",
        api_key=None,
        allow_private_network=True,
    )

    assert result["success"] is True
    assert str(sent[0].url) == "http://host.docker.internal:20128/v1/models"


async def test_without_a_loopback_host_localhost_stays_local(monkeypatch):
    from app.core.config import settings
    from app.modules.ai_ml.service import AIService

    sent = _capture_sent(monkeypatch)
    monkeypatch.setattr(settings, "AI_PROVIDER_LOOPBACK_HOST", "")

    await AIService().test_connection(
        provider_type="openai_compatible",
        endpoint="http://localhost:20128/v1",
        api_key=None,
        allow_private_network=True,
    )

    assert str(sent[0].url) == "http://localhost:20128/v1/models"
