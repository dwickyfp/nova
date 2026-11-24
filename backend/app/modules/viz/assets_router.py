"""Dashboard images over HTTP: an editor uploads, any viewer of the dashboard reads.

The picture is the request body itself (no form encoding), read in a bounded
stream; its type is decided from its bytes. Responses are served with their
recorded type, ``nosniff`` and a sandboxing content policy, and are never
stored by the browser: each view is authorized again (the page keeps its own
copy only until the signed-in role changes).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.encoders import jsonable_encoder

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user

from .assets import MAX_BYTES, ImageRefused, native_asset_service

router = APIRouter(default_response_class=SanitizingJSONResponse)
User = Annotated[dict[str, Any], Depends(get_current_user)]

_ACCEPTED = {"image/png", "image/jpeg", "image/webp", "application/octet-stream"}


async def _picture(request: Request) -> bytes:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in _ACCEPTED:
        raise ImageRefused("Use a PNG, JPEG or WebP picture. Other files, including SVG, "
                           "are not accepted.")
    chunks: list[bytes] = []
    size = 0
    try:
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ImageRefused("Pictures can be up to 2 MB", status_code=413)
                chunks.append(chunk)
    except TimeoutError:
        raise ImageRefused("The upload took too long", status_code=408) from None
    return b"".join(chunks)


@router.post("/dashboards/{identity}/images", status_code=201)
async def upload_dashboard_image(identity: UUID, request: Request, user: User) -> Any:
    """Add a picture to a dashboard the caller may edit; returns its asset ID and size."""
    response = await native_asset_service.upload(user, identity, await _picture(request))
    return SanitizingJSONResponse(jsonable_encoder(response), status_code=201,
                                  headers={"Cache-Control": "no-store"})


@router.get("/dashboards/{identity}/images/{asset_id}")
async def dashboard_image(identity: UUID, asset_id: UUID, user: User) -> Response:
    """A picture of a dashboard the caller can view, checked on every request."""
    data, media_type = await native_asset_service.read(user, identity, asset_id)
    return Response(data, media_type=media_type, headers={
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": "inline",
    })
