"""``GET /api/v1/system/info`` — connection details for external clients.

Unit-level: the route is exercised directly with a fake ``Request`` and a stub
user, so no engine, Redis, or HTTP stack is involved. The point under test is
the host resolution: ``PROXY_PUBLIC_HOST`` wins, otherwise the request host is
reported, and credentials never appear in the payload.
"""

from __future__ import annotations

from starlette.requests import Request

from app.core.config import settings
from app.modules.system import router as system_router


def _request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/system/info",
            "headers": [(b"host", host.encode())],
            "scheme": "http",
        }
    )


async def test_info_reports_proxy_port_and_request_host(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_PUBLIC_HOST", "")
    monkeypatch.setattr(settings, "PROXY_PORT", 4406)

    result = await system_router.get_info(_request("nova.example.com:8000"), user={})

    assert result["proxy"] == {"enabled": True, "host": "nova.example.com", "port": 4406}


async def test_info_public_host_overrides_request_host(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_PUBLIC_HOST", "db.nova.io")
    monkeypatch.setattr(settings, "PROXY_PORT", 4406)

    result = await system_router.get_info(_request("nova.example.com"), user={})

    assert result["proxy"]["host"] == "db.nova.io"


async def test_info_never_returns_credentials(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_PORT", 4406)

    result = await system_router.get_info(_request("localhost"), user={})

    assert set(result["proxy"]) == {"enabled", "host", "port"}
