"""Sign a benchmark client in to Nova and BI, exactly as a browser does.

Credentials come from ``docker/.env`` (``NOVA_ADMIN_TEST_PASSWORD`` and the
optional ``RETAIL_STEWARD_TEST_*`` pair) and are never printed. The BI handle
is the HttpOnly ``nova_bi`` cookie Nova issues for the signed-in session, so
every Superset request the benchmark makes runs under that session's active
role, through the proxy and Ranger, like a real viewer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / "docker" / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class Principal:
    username: str
    password: str
    role: str | None = None


def principal(name: str = "admin") -> Principal:
    env = env_file()
    if name == "steward":
        return Principal(
            env.get("RETAIL_STEWARD_TEST_USER", "retail_steward_test"),
            env["RETAIL_STEWARD_TEST_PASSWORD"],
        )
    return Principal("nova_admin", env["NOVA_ADMIN_TEST_PASSWORD"], "ACCOUNTADMIN")


@dataclass
class BiSession:
    """A signed-in Nova session and its BI cookie."""

    api: str
    superset: str
    token: str
    handle: str

    def nova(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.api, headers={"Authorization": f"Bearer {self.token}"}, timeout=120
        )

    def bi(self, *, limits: httpx.Limits | None = None) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            base_url=self.superset,
            timeout=120,
            follow_redirects=False,
            headers={"Referer": f"{self.superset}/"},
            limits=limits or httpx.Limits(max_connections=200),
        )
        client.cookies.set("nova_bi", self.handle)
        return client


async def sign_in(
    who: Principal,
    *,
    api: str = os.environ.get("NOVA_API", "http://127.0.0.1:8000/api/v1"),
    superset: str = os.environ.get("NOVA_SUPERSET", "http://127.0.0.1:8088/bi"),
) -> BiSession:
    async with httpx.AsyncClient(base_url=api, timeout=60) as client:
        login = await client.post("/auth/login", json={"username": who.username, "password": who.password})
        login.raise_for_status()
        body = login.json()
        if body.get("status") != "AUTHENTICATED" or not body.get("access_token"):
            raise RuntimeError(f"{who.username} could not sign in ({body.get('status')})")
        token = body["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        role = who.role or body.get("default_role")
        if role and body.get("active_role") != role:
            switched = await client.post("/auth/switch-role", json={"role": role}, headers=headers)
            switched.raise_for_status()
        opened = await client.post("/superset/session", headers=headers)
        opened.raise_for_status()
        handle = opened.cookies.get("nova_bi")
        if not handle:
            raise RuntimeError("Nova did not issue a BI session")
    return BiSession(api=api, superset=superset, token=token, handle=handle)


async def csrf(bi: httpx.AsyncClient) -> str:
    response = await bi.get("/api/v1/security/csrf_token/")
    response.raise_for_status()
    return str(response.json()["result"])
