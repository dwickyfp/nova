"""Authenticated pipe administration and ingestion file status."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.pipes.schemas import (
    PipeCreate,
    PipeFileListResponse,
    PipeFileResponse,
    PipeListResponse,
    PipeResponse,
)
from app.modules.pipes.service import pipe_service

router = APIRouter(default_response_class=SanitizingJSONResponse)
CurrentUser = Annotated[dict, Depends(get_current_user)]


def _caller(user: dict) -> dict:
    try:
        role = SecurityContext.from_session(user).active_role
    except SecurityContextError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "username": user["username"],
        "encrypted_password": user["encrypted_password"],
        "session_id": user.get("session_id"),
        "role": role,
        "security_context_version": int(user.get("security_context_version") or 1),
    }


@router.get("", response_model=PipeListResponse)
async def list_pipes(user: CurrentUser, database: str | None = Query(None)):
    rows = await pipe_service.list_pipes(database=database, **_caller(user))
    return PipeListResponse(pipes=[PipeResponse(**row) for row in rows], count=len(rows))


@router.get("/{name}", response_model=PipeResponse)
async def get_pipe(name: str, user: CurrentUser, database: str | None = Query(None)):
    pipe = await pipe_service.get_pipe(name, database=database, **_caller(user))
    if not pipe:
        raise HTTPException(status_code=404, detail="Pipe does not exist or is not accessible")
    return PipeResponse(**pipe)


@router.post("", response_model=PipeResponse, status_code=201)
async def create_pipe(body: PipeCreate, user: CurrentUser):
    return PipeResponse(**await pipe_service.create_pipe(body.model_dump(), **_caller(user)))


@router.patch("/{name}/suspend")
async def suspend_pipe(name: str, user: CurrentUser, database: str | None = Query(None)):
    return await pipe_service.suspend_pipe(name, database=database, **_caller(user))


@router.patch("/{name}/resume")
async def resume_pipe(name: str, user: CurrentUser, database: str | None = Query(None)):
    return await pipe_service.resume_pipe(name, database=database, **_caller(user))


@router.delete("/{name}")
async def drop_pipe(name: str, user: CurrentUser, database: str | None = Query(None)):
    return await pipe_service.drop_pipe(name, database=database, **_caller(user))


@router.get("/{name}/files", response_model=PipeFileListResponse)
async def list_pipe_files(name: str, user: CurrentUser, database: str | None = Query(None)):
    rows = await pipe_service.list_pipe_files(name, database=database, **_caller(user))
    return PipeFileListResponse(files=[PipeFileResponse(**row) for row in rows], count=len(rows))
