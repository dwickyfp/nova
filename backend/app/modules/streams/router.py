"""Read-only Streams API: what the caller's active role may see, never journal rows."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.modules.streams.namespace import StreamName
from app.modules.streams.schemas import StreamError
from app.modules.streams.service import StreamCaller, stream_service

router = APIRouter()
current_user = Depends(get_current_user)

_NOT_FOUND = {"STREAM_NOT_FOUND"}
_GONE = {"STREAM_STALE"}


def _caller(user: dict[str, Any]) -> StreamCaller:
    return StreamCaller(
        user["username"], user.get("active_role"), int(user.get("security_context_version") or 1)
    )


def _http(exc: StreamError) -> HTTPException:
    status = (
        404 if exc.code in _NOT_FOUND
        else 410 if exc.code in _GONE
        else 403 if exc.code == "STREAM_ACCESS_DENIED"
        else 409
    )
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


@router.get("")
async def list_streams(database: str, user: dict = current_user) -> dict[str, Any]:
    try:
        streams = await stream_service.list_streams(database, _caller(user))
    except StreamError as exc:
        raise _http(exc) from None
    return {"streams": streams, "count": len(streams)}


@router.get("/{database}/{name}")
async def stream_status(database: str, name: str, user: dict = current_user) -> dict[str, Any]:
    try:
        return await stream_service.status(StreamName(database, name), _caller(user))
    except StreamError as exc:
        raise _http(exc) from None


@router.get("/{database}/{name}/backlog")
async def stream_backlog(database: str, name: str, user: dict = current_user) -> dict[str, Any]:
    try:
        return await stream_service.backlog(StreamName(database, name), _caller(user))
    except StreamError as exc:
        raise _http(exc) from None


@router.get("/{database}/{name}/consumptions")
async def stream_consumptions(
    database: str, name: str, limit: int = 50, user: dict = current_user
) -> dict[str, Any]:
    """Recent consumptions: offsets moved, target, state. Never SQL or rows."""
    try:
        items = await stream_service.consumptions(StreamName(database, name), _caller(user), limit)
    except StreamError as exc:
        raise _http(exc) from None
    return {"consumptions": items, "count": len(items)}
