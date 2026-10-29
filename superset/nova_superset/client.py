"""Calls from Superset to Nova's internal Superset endpoints."""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

SERVICE_TOKEN_HEADER = "X-Nova-Superset-Token"
_TIMEOUT_SECONDS = 5.0


class NovaUnauthorized(Exception):
    """Nova does not recognise the BI handle (signed out, expired, revoked)."""


class NovaUnavailable(Exception):
    """Nova could not answer; callers fail closed."""


@dataclass(frozen=True)
class Identity:
    username: str
    active_role: str
    superset_role: str
    security_context_version: int


@dataclass(frozen=True)
class DataToken:
    username: str
    token: str
    expires_in: int


def _base_url() -> str:
    return os.environ.get("NOVA_API_URL", "http://nova-backend:8000").rstrip("/")


def _post(path: str, handle: str) -> dict:
    secret = os.environ.get("NOVA_SUPERSET_SERVICE_TOKEN", "")
    if not secret:
        raise NovaUnavailable("NOVA_SUPERSET_SERVICE_TOKEN is not configured")
    try:
        response = requests.post(
            f"{_base_url()}/api/v1/internal/superset/{path}",
            json={"handle": handle},
            headers={SERVICE_TOKEN_HEADER: secret},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise NovaUnavailable(type(exc).__name__) from None
    if response.status_code in (401, 422):
        raise NovaUnauthorized()
    if response.status_code != 200:
        raise NovaUnavailable(f"Nova answered {response.status_code}")
    return response.json()


def identity(handle: str) -> Identity:
    body = _post("identity", handle)
    return Identity(
        username=body["username"],
        active_role=body["active_role"],
        superset_role=body["superset_role"],
        security_context_version=int(body["security_context_version"]),
    )


def data_token(handle: str) -> DataToken:
    body = _post("token", handle)
    return DataToken(
        username=body["username"], token=body["token"], expires_in=int(body["expires_in"])
    )
