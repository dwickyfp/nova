"""Sign a benchmark client in to Nova, exactly as a browser does.

Credentials come from ``docker/.env`` (``NOVA_ADMIN_TEST_PASSWORD`` and the
optional ``RETAIL_STEWARD_TEST_*`` pair) and are never printed. Every request
the benchmark makes runs under the signed-in session's active role.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]


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
class NovaSession:
    """A signed-in Nova session."""

    api: str
    token: str

    def nova(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.api, headers={"Authorization": f"Bearer {self.token}"}, timeout=120
        )


async def sign_in(
    who: Principal,
    *,
    api: str = os.environ.get("NOVA_API", "http://127.0.0.1:8000/api/v1"),
) -> NovaSession:
    async with httpx.AsyncClient(base_url=api, timeout=60) as client:
        login = await client.post(
            "/auth/login", json={"username": who.username, "password": who.password}
        )
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
    return NovaSession(api=api, token=token)
