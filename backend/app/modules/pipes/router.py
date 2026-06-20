"""Pipe Manager API router — continuous ingestion pipe CRUD + file tracking.

Endpoints:
  GET    /pipes                  → list all pipes (optional ?database= filter)
  GET    /pipes/{name}           → get pipe detail
  POST   /pipes                  → create pipe
  PATCH  /pipes/{name}/suspend   → suspend pipe
  PATCH  /pipes/{name}/resume    → resume pipe
  DELETE /pipes/{name}            → drop pipe
  GET    /pipes/{name}/files     → list files ingested by pipe
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import get_current_user
from app.modules.pipes.schemas import (
    PipeCreate,
    PipeFileListResponse,
    PipeFileResponse,
    PipeListResponse,
    PipeResponse,
)
from app.modules.pipes.service import pipe_service

router = APIRouter()


# ── Pipe CRUD ──────────────────────────────────────────────────


@router.get("", response_model=PipeListResponse)
async def list_pipes(
    database: str | None = Query(None, description="Filter pipes by database"),
    user: dict = Depends(get_current_user),
):
    """List all pipes, optionally scoped to a database."""
    rows = await pipe_service.list_pipes(database=database)
    pipes = [PipeResponse(**row) for row in rows]
    return PipeListResponse(pipes=pipes, count=len(pipes))


@router.get("/{name}", response_model=PipeResponse)
async def get_pipe(
    name: str,
    database: str | None = Query(None),
    user: dict = Depends(get_current_user),
):
    """Get detail for a single pipe by name."""
    pipe = await pipe_service.get_pipe(name, database=database)
    if not pipe:
        raise HTTPException(status_code=404, detail=f"Pipe '{name}' not found")
    return PipeResponse(**pipe)


@router.post("", response_model=PipeResponse, status_code=201)
async def create_pipe(
    body: PipeCreate,
    user: dict = Depends(get_current_user),
):
    """Create a new continuous ingestion pipe."""
    try:
        pipe = await pipe_service.create_pipe(body.model_dump())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PipeResponse(**pipe)


@router.patch("/{name}/suspend")
async def suspend_pipe(
    name: str,
    database: str | None = Query(None),
    user: dict = Depends(get_current_user),
):
    """Suspend a running pipe."""
    try:
        result = await pipe_service.suspend_pipe(name, database=database)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.patch("/{name}/resume")
async def resume_pipe(
    name: str,
    database: str | None = Query(None),
    user: dict = Depends(get_current_user),
):
    """Resume a suspended pipe."""
    try:
        result = await pipe_service.resume_pipe(name, database=database)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.delete("/{name}")
async def drop_pipe(
    name: str,
    database: str | None = Query(None),
    user: dict = Depends(get_current_user),
):
    """Drop (delete) a pipe."""
    try:
        result = await pipe_service.drop_pipe(name, database=database)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ── Pipe Files ─────────────────────────────────────────────────


@router.get("/{name}/files", response_model=PipeFileListResponse)
async def list_pipe_files(
    name: str,
    database: str | None = Query(None),
    user: dict = Depends(get_current_user),
):
    """List files tracked by a pipe (ingestion status)."""
    try:
        rows = await pipe_service.list_pipe_files(name, database=database)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    files = [PipeFileResponse(**row) for row in rows]
    return PipeFileListResponse(files=files, count=len(files))
