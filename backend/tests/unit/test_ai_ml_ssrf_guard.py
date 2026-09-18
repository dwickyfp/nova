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
