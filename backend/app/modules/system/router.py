"""System router — deployment info for clients.

Endpoints under /api/v1/system:
  GET /info → how to reach Nova (MySQL proxy host/port, version)

Credentials are intentionally never returned: users are StarRocks users, and
each client supplies its own username/password.
"""

from fastapi import APIRouter, Depends, Request

from app.core.config import settings
from app.core.deps import get_current_user

router = APIRouter()

require_user = Depends(get_current_user)


@router.get("/info")
async def get_info(request: Request, user: dict = require_user):
    """Connection details for external MySQL clients.

    The proxy host defaults to the host the request arrived on, so a single-host
    deployment reports the address the user actually typed. ``PROXY_PUBLIC_HOST``
    overrides it for split web/proxy topologies.
    """
    host = settings.PROXY_PUBLIC_HOST or (request.url.hostname or "")
    return {
        "proxy": {
            "enabled": settings.PROXY_ENABLED,
            "host": host,
            "port": settings.PROXY_PORT,
        },
    }
