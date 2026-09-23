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

import hashlib
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.common.audit import write_audit_log
from app.core.deps import get_current_user
from app.core.security import decrypt_password
from app.modules.stages.access import StageAccessDenied, check_stage_access
from app.modules.stages.schemas import (
    StageCreate,
    StageListResponse,
    StageResponse,
)
from app.modules.stages.service import StagePathError, stage_service

router = APIRouter()
logger = logging.getLogger(__name__)

# Built once so routes can use a module-level dependency instead of calling
# `Depends(...)` in argument defaults (ruff B008). Same pattern as
# `modules/ml_engine/router.py` and `modules/users/router.py`.
require_user = Depends(get_current_user)


async def _authorize(stage: dict, user: dict, action: str) -> None:
    try:
        await check_stage_access(
            stage,
            action=action,
            username=user["username"],
            password=decrypt_password(user["encrypted_password"]),
            active_role=user.get("active_role"),
        )
    except StageAccessDenied as exc:
        raise HTTPException(status_code=403, detail="Stage access denied") from exc


async def _authorized_stage(stage_id: str, user: dict, action: str) -> dict:
    stage = await stage_service.get_stage(stage_id)
    if stage is None:
        raise HTTPException(status_code=404, detail="Stage not found")
    await _authorize(stage, user, action)
    return stage


@asynccontextmanager
async def _stage_mutation_audit(
    stage: dict, user: dict, action: str, object_name: str
) -> AsyncIterator[None]:
    encoded_name = object_name.encode("utf-8")
    if len(encoded_name) > 500:
        prefix = encoded_name[:400].decode("utf-8", errors="ignore")
        object_name = f"{prefix}#{hashlib.sha256(encoded_name).hexdigest()}"
    fields = {
        "event_type": "stage",
        "user_name": user["username"],
        "action": action,
        "object_type": "STAGE",
        "object_name": object_name,
        "session_id": user.get("session_id"),
        "database_name": stage.get("database_name"),
        "schema_name": stage.get("schema_name"),
        "active_role": user.get("active_role"),
        "security_context_version": user.get("security_context_version"),
    }
    await write_audit_log(**fields, status="ATTEMPTED")
    status = "ERROR"
    error: str | None = None
    try:
        yield
    except Exception as exc:
        error = type(exc).__name__
        raise
    else:
        status = "SUCCESS"
    finally:
        try:
            await write_audit_log(**fields, status=status, error_message=error)
        except Exception as exc:
            logger.error("Stage audit outcome failed: %s", type(exc).__name__)


# ── Stage CRUD ──────────────────────────────────────────────────


@router.get("", response_model=StageListResponse)
async def list_stages(
    user: dict = require_user,
):
    """List all registered stages."""
    rows = await stage_service.list_stages()
    stages = []
    allowed_scopes: dict[tuple[str, str], bool] = {}
    for row in rows:
        scope = (str(row["database_name"]), str(row["schema_name"]))
        if scope not in allowed_scopes:
            try:
                await _authorize(row, user, "read")
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
                allowed_scopes[scope] = False
            else:
                allowed_scopes[scope] = True
        if allowed_scopes[scope]:
            stages.append(StageResponse(**row))
    return StageListResponse(stages=stages, count=len(stages))


@router.get("/{stage_id}", response_model=StageResponse)
async def get_stage(
    stage_id: str,
    user: dict = require_user,
):
    """Get stage detail by ID."""
    stage = await _authorized_stage(stage_id, user, "read")
    return StageResponse(**stage)


@router.post("", response_model=StageResponse, status_code=201)
async def create_stage(
    body: StageCreate,
    user: dict = require_user,
):
    """Create a new stage."""
    data = body.model_dump()
    await _authorize(data, user, "delete")
    async with _stage_mutation_audit(data, user, "CREATE_STAGE", data["name"]):
        stage = await stage_service.create_stage(data, user["username"])
        if not stage:
            raise HTTPException(status_code=500, detail="Failed to create stage")
    return StageResponse(**stage)


@router.delete("/{stage_id}")
async def delete_stage(
    stage_id: str,
    user: dict = require_user,
):
    """Delete a stage by ID."""
    stage = await _authorized_stage(stage_id, user, "delete")
    async with _stage_mutation_audit(stage, user, "DELETE_STAGE", stage_id):
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
        stage_service._safe_relative_path(prefix, field="prefix")
    except StagePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        await _authorized_stage(stage_id, user, "read")
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
    try:
        stage_service._safe_relative_path(file.filename or "unknown")
    except StagePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        stage = await _authorized_stage(stage_id, user, "write")
        filename = file.filename or "unknown"
        async with _stage_mutation_audit(
            stage, user, "UPLOAD_STAGE_FILE", f"{stage_id}/{filename}"
        ):
            result = await stage_service.upload_stream(stage_id, filename, file.file)
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
        stage_service._safe_relative_path(filename)
    except StagePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _authorized_stage(stage_id, user, "read")
    try:
        body = await stage_service.open_file(stage_id, filename)
    except StagePathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found") from e

    def chunks() -> Iterator[bytes]:
        try:
            yield from body.iter_chunks(chunk_size=64 * 1024)
        finally:
            body.close()

    download_name = quote(filename.split("/")[-1], safe="")
    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{download_name}"},
    )


@router.delete("/{stage_id}/files/{filename:path}")
async def delete_file(
    stage_id: str,
    filename: str,
    user: dict = require_user,
):
    """Delete a file from the stage's storage path."""
    try:
        stage_service._safe_relative_path(filename)
    except StagePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        stage = await _authorized_stage(stage_id, user, "delete")
        async with _stage_mutation_audit(
            stage, user, "DELETE_STAGE_FILE", f"{stage_id}/{filename}"
        ):
            await stage_service.delete_file(stage_id, filename)
    except StagePathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"success": True, "message": f"File '{filename}' deleted"}
