"""Stage Manager API router — stage CRUD + file operation endpoints.

Endpoints:
  GET    /stages                          → list all stages
  GET    /stages/{id}                     → get stage detail
  POST   /stages                          → create stage
  DELETE /stages/{id}                     → delete stage
  GET    /stages/{id}/files               → list files in stage
  POST   /stages/{id}/files               → upload file
  GET    /stages/{id}/files/{filename}    → download file
  DELETE /stages/{id}/files/{filename}    → delete file
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user
from app.modules.stages.schemas import (
    StageCreate,
    StageListResponse,
    StageResponse,
)
from app.modules.stages.service import StagePathError, stage_service

router = APIRouter()

# Built once so routes can use a module-level dependency instead of calling
# `Depends(...)` in argument defaults (ruff B008). Same pattern as
# `modules/ml_engine/router.py` and `modules/users/router.py`.
require_user = Depends(get_current_user)


# ── Stage CRUD ──────────────────────────────────────────────────


@router.get("", response_model=StageListResponse)
async def list_stages(
    user: dict = require_user,
):
    """List all registered stages."""
    rows = await stage_service.list_stages()
    stages = [StageResponse(**row) for row in rows]
    return StageListResponse(stages=stages, count=len(stages))


@router.get("/{stage_id}", response_model=StageResponse)
async def get_stage(
    stage_id: str,
    user: dict = require_user,
):
    """Get stage detail by ID."""
    stage = await stage_service.get_stage(stage_id)
    if not stage:
        raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' not found")
    return StageResponse(**stage)


@router.post("", response_model=StageResponse, status_code=201)
async def create_stage(
    body: StageCreate,
    user: dict = require_user,
):
    """Create a new stage."""
    stage = await stage_service.create_stage(body.model_dump(), user["username"])
    if not stage:
        raise HTTPException(status_code=500, detail="Failed to create stage")
    return StageResponse(**stage)


@router.delete("/{stage_id}")
async def delete_stage(
    stage_id: str,
    user: dict = require_user,
):
    """Delete a stage by ID."""
    deleted = await stage_service.delete_stage(stage_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' not found")
    return {"success": True, "message": f"Stage '{stage_id}' deleted"}


# ── Stage Files ─────────────────────────────────────────────────


@router.get("/{stage_id}/files")
async def list_files(
    stage_id: str,
    prefix: str = "",
    user: dict = require_user,
):
    """List files in a stage's storage path."""
    try:
        files = await stage_service.list_files(stage_id, prefix=prefix)
    except StagePathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"files": files, "prefix": prefix, "count": len(files)}


@router.post("/{stage_id}/files")
async def upload_file(
    stage_id: str,
    file: UploadFile,
    user: dict = require_user,
):
    """Upload a file to the stage's storage path."""
    content = await file.read()
    try:
        result = await stage_service.upload_file(stage_id, file.filename or "unknown", content)
    except StagePathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"success": True, "file": result}


@router.get("/{stage_id}/files/{filename:path}")
async def download_file(
    stage_id: str,
    filename: str,
    user: dict = require_user,
):
    """Download a file from the stage's storage path."""
    try:
        content = await stage_service.download_file(stage_id, filename)
    except StagePathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found") from e

    return StreamingResponse(
        iter([content]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename.split("/")[-1]}"'},
    )


@router.delete("/{stage_id}/files/{filename:path}")
async def delete_file(
    stage_id: str,
    filename: str,
    user: dict = require_user,
):
    """Delete a file from the stage's storage path."""
    try:
        await stage_service.delete_file(stage_id, filename)
    except StagePathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"success": True, "message": f"File '{filename}' deleted"}
