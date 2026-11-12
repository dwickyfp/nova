"""Who may act as Superset, and which Superset role a Nova session maps to."""

from __future__ import annotations

import ipaddress
import logging
import re
import secrets
from functools import lru_cache

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.metadata_db import MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext
from app.modules.superset.metadata_repository import superset_metadata

logger = logging.getLogger(__name__)

SERVICE_TOKEN_HEADER = "X-Nova-Superset-Token"

#: Superset roles Nova assigns. Superset roles gate product features only;
#: Ranger alone decides which rows and columns a principal sees.
SUPERSET_VIEWER = "NovaViewer"
SUPERSET_AUTHOR = "NovaAuthor"
SUPERSET_ADMIN = "NovaAdmin"
ADMIN_ROLE = "ACCOUNTADMIN"

AUTHOR_ROLES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_BI_AUTHOR_ROLES (
    role_name  VARCHAR(128) NOT NULL,
    created_by VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(role_name)
DISTRIBUTED BY HASH(role_name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


@lru_cache(maxsize=8)
def _networks(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    parsed = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            parsed.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.error("Ignoring invalid network entry %r", entry)
    return tuple(parsed)


def peer_in(host: str, networks: str) -> bool:
    """True when ``host`` is inside the comma-separated CIDRs. Fail closed."""
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in _networks(networks))


def peer_allowed(host: str) -> bool:
    """True when ``host`` is inside ``SUPERSET_SERVICE_NETWORKS``. Fail closed."""
    return peer_in(host, settings.SUPERSET_SERVICE_NETWORKS)


def notebook_peer_allowed(host: str) -> bool:
    """True when ``host`` is inside ``NOTEBOOK_SANDBOX_NETWORKS``. Fail closed."""
    return peer_in(host, settings.NOTEBOOK_SANDBOX_NETWORKS)


def require_superset_service(request: Request) -> None:
    """FastAPI dependency for ``/api/v1/internal/superset/*``.

    The caller must come from a Superset service network and present the shared
    secret. An unset secret rejects every call rather than allowing any.
    """
    peer = request.client.host if request.client else ""
    if not peer_allowed(peer):
        logger.warning("Rejected Superset service call from %s", peer)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    expected = settings.SUPERSET_SERVICE_TOKEN
    if not expected:
        logger.error("Rejected Superset service call: SUPERSET_SERVICE_TOKEN is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Superset integration is not configured",
        )
    presented = request.headers.get(SERVICE_TOKEN_HEADER, "")
    if not secrets.compare_digest(presented.encode(), expected.encode()):
        logger.warning("Rejected Superset service call from %s: invalid token", peer)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def validate_role_name(role: str) -> str:
    if not _ROLE_NAME.fullmatch(role or ""):
        raise ValueError(f"Invalid role name: {role!r}")
    return role


async def author_role_set(
    *,
    actor: SecurityContext | None = None,
) -> tuple[frozenset[str], int]:
    async with superset_metadata(actor, read_only=True) as repository:
        return await repository.author_roles()


async def author_roles(
    *,
    fresh: bool = False,
    actor: SecurityContext | None = None,
) -> frozenset[str]:
    """Read the current set in the caller's transaction; revocation is immediate."""
    del fresh
    roles, _ = await author_role_set(actor=actor)
    return roles


async def replace_author_roles(
    roles: list[str],
    *,
    changed_by: str,
    expected_revision: int | None = None,
) -> list[str]:
    wanted = sorted({validate_role_name(role) for role in roles})
    if expected_revision is None:
        _, expected_revision = await author_role_set()
    async with superset_metadata() as repository:
        if repository.actor.principal != changed_by:
            raise MetadataPermissionDenied()
        result, _ = await repository.replace_author_roles(
            wanted, expected_revision=expected_revision
        )
        return result


async def superset_role_for(
    active_role: str,
    *,
    actor: SecurityContext | None = None,
) -> str:
    """Map the session's active Nova role to exactly one Superset role."""
    if actor is not None and actor.active_role != active_role:
        raise MetadataPermissionDenied()
    if active_role == ADMIN_ROLE:
        return SUPERSET_ADMIN
    if active_role in await author_roles(actor=actor):
        return SUPERSET_AUTHOR
    return SUPERSET_VIEWER
