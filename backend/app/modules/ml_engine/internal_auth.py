"""Authentication for Nova's internal machine-to-machine endpoints.

Internal endpoints (``/api/v1/internal/...``) are reachable without a user
session, so they cannot use the JWT bearer dependency. Two independent gates
protect them instead:

1. **Caller address** — the peer must be loopback (``127.0.0.0/8`` or
   ``::1``) or the application explicitly trusts a proxy fronting the backend
   via ``NOVA_INTERNAL_TRUSTED_PROXY``. The check is on the TCP peer, so it
   holds even when the process binds ``0.0.0.0``; it does not rely on
   deployment documentation.
2. **Shared secret** — the caller must present the secret in
   ``X-Nova-Internal-Token``, compared in constant time.

The secret is read from ``NOVA_INTERNAL_TOKEN``. No credential is committed:
the deployment supplies the value. If the variable is unset the dependency
fails closed — every internal request is rejected rather than allowed through.
"""

from __future__ import annotations

import ipaddress
import logging
import secrets

from fastapi import HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger(__name__)

INTERNAL_TOKEN_HEADER = "X-Nova-Internal-Token"


def _is_loopback(host: str) -> bool:
    """Return True when ``host`` is a loopback address or hostname.

    A malformed or empty host is not loopback: the check is fail-closed.
    """
    if not host:
        return False
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _trusted_proxies() -> set[str]:
    raw = settings.NOVA_INTERNAL_TRUSTED_PROXY
    return {entry.strip() for entry in raw.split(",") if entry.strip()}


def require_internal_caller(request: Request) -> None:
    """FastAPI dependency enforcing the internal-channel gates.

    Raises ``401`` when the secret is missing or wrong, and ``403`` when the
    caller is neither loopback nor a configured trusted proxy.
    """
    peer = request.client.host if request.client else ""
    trusted = _trusted_proxies()
    if not _is_loopback(peer) and peer not in trusted:
        logger.warning("Rejected internal call from non-loopback peer %s", peer)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    expected = settings.NOVA_INTERNAL_TOKEN
    presented = request.headers.get(INTERNAL_TOKEN_HEADER, "")
    if not expected:
        logger.error("Rejected internal call: NOVA_INTERNAL_TOKEN is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal endpoint is not configured",
        )
    if not secrets.compare_digest(presented, expected):
        logger.warning("Rejected internal call from %s: invalid token", peer)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )
