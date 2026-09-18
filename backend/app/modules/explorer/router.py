"""Database Explorer API router — real-time StarRocks metadata browsing.

Endpoints:
  GET /catalogs                          → list catalogs with databases
  GET /databases/{db}                    → all objects in a database
  GET /databases/{db}/tables/{table}     → table detail (columns, partitions, properties)
  GET /databases/{db}/views/{view}       → view detail (definition, definer)
  GET /databases/{db}/mvs/{mv}           → materialized view detail (refresh state)
  GET /databases/{db}/functions/{fn}     → function detail (definition)
  GET /databases/{db}/pipes/{pipe}       → pipe detail (state, target table)
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from app.core.deps import get_current_user

from .service import explorer_service

router = APIRouter()
log = logging.getLogger(__name__)

CurrentUser = Annotated[dict, Depends(get_current_user)]


# ── Catalogs ───────────────────────────────────────────────────


@router.get("/catalogs")
async def list_catalogs(
    _user: CurrentUser,
):
    """List all catalogs with their databases."""
    return await explorer_service.get_catalogs()


# ── Database objects ───────────────────────────────────────────


@router.get("/databases/{database}")
async def list_database_objects(
    database: str,
    _user: CurrentUser,
    catalog: str | None = None,
):
    """List all objects in a database (tables, views, MVs, functions, pipes, stages).

    ``catalog`` selects an external catalog so its external tables surface in the
    tree; it defaults to ``default_catalog`` for every self-managed database.
    """
    result = await explorer_service.get_database_objects(database, catalog=catalog)
    return result


# ── Table detail ───────────────────────────────────────────────


@router.get("/databases/{database}/tables/{table}")
async def get_table_detail(
    database: str,
    table: str,
    _user: CurrentUser,
):
    """Get table detail with columns, partitions, and properties."""
    try:
        result = await explorer_service.get_table_detail(database, table)
    except Exception as e:
        log.error("Table detail error for %s.%s: %s", database, table, e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not result:
        raise HTTPException(status_code=404, detail=f"Table {database}.{table} not found")
    return result


# ── View detail ────────────────────────────────────────────────


@router.get("/databases/{database}/views/{view}")
async def get_view_detail(
    database: str,
    view: str,
    _user: CurrentUser,
):
    """Get view detail with definition."""
    result = await explorer_service.get_view_detail(database, view)
    if not result:
        raise HTTPException(status_code=404, detail=f"View {database}.{view} not found")
    return result


# ── Materialized View detail ───────────────────────────────────


@router.get("/databases/{database}/mvs/{mv}")
async def get_mv_detail(
    database: str,
    mv: str,
    _user: CurrentUser,
):
    """Get materialized view detail with refresh state."""
    result = await explorer_service.get_mv_detail(database, mv)
    if not result:
        raise HTTPException(status_code=404, detail=f"Materialized view {database}.{mv} not found")
    return result


# ── Function detail ────────────────────────────────────────────


@router.get("/databases/{database}/functions/{fn}")
async def get_function_detail(
    database: str,
    fn: str,
    _user: CurrentUser,
):
    """Get function detail with definition."""
    result = await explorer_service.get_function_detail(database, fn)
    if not result:
        raise HTTPException(status_code=404, detail=f"Function {database}.{fn} not found")
    return result


# ── Pipe detail ────────────────────────────────────────────────


@router.get("/databases/{database}/pipes/{pipe}")
async def get_pipe_detail(
    database: str,
    pipe: str,
    _user: CurrentUser,
):
    """Get pipe detail with state and load status."""
    result = await explorer_service.get_pipe_detail(database, pipe)
    if not result:
        raise HTTPException(status_code=404, detail=f"Pipe {database}.{pipe} not found")
    return result


# ── Stage files ───────────────────────────────────────────────


@router.get("/databases/{database}/stages/{stage}/files")
async def get_stage_files(
    database: str,
    stage: str,
    _user: CurrentUser,
    prefix: str = "",
):
    """List files in a stage by name + database."""
    from app.modules.stages.service import stage_service

    # Lookup stage ID by name + database
    stage_id = await explorer_service.get_stage_id(database, stage)
    if not stage_id:
        raise HTTPException(
            status_code=404,
            detail=f"Stage '{stage}' not found in {database}",
        )
    try:
        files = await stage_service.list_files(stage_id, prefix=prefix)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"files": files, "prefix": prefix, "count": len(files)}


@router.post("/databases/{database}/stages/{stage}/files")
async def upload_stage_file(
    database: str,
    stage: str,
    file: UploadFile,
    _user: CurrentUser,
    filename: str | None = Form(None),
):
    """Upload a file to a stage by name + database."""
    from app.modules.stages.service import stage_service

    stage_id = await explorer_service.get_stage_id(database, stage)
    if not stage_id:
        raise HTTPException(
            status_code=404,
            detail=f"Stage '{stage}' not found in {database}",
        )
    # Use custom filename (with folder prefix) if provided, else original filename
    target_name = filename or file.filename or "unnamed"
    content = await file.read()
    await stage_service.upload_file(stage_id, target_name, content)
    return {"success": True, "filename": target_name, "size": len(content)}


@router.delete("/databases/{database}/stages/{stage}/files/{filename}")
async def delete_stage_file(
    database: str,
    stage: str,
    filename: str,
    _user: CurrentUser,
):
    """Delete a file from a stage by name + database."""
    from app.modules.stages.service import stage_service

    stage_id = await explorer_service.get_stage_id(database, stage)
    if not stage_id:
        raise HTTPException(
            status_code=404,
            detail=f"Stage '{stage}' not found in {database}",
        )
    await stage_service.delete_file(stage_id, filename)
    return {"success": True, "filename": filename}
