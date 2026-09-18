"""External Catalog API router — Iceberg + Hive catalog management.

Endpoints:
  GET    /external-catalogs              → list Nova-managed catalogs
  GET    /external-catalogs/{name}       → catalog detail (redacted SHOW CREATE)
  POST   /external-catalogs              → CREATE EXTERNAL CATALOG
  PATCH  /external-catalogs/{name}       → ALTER CATALOG ... SET
  DELETE /external-catalogs/{name}       → DROP CATALOG
  GET    /{name}/databases/{db}/tables   → external table listing

Every response class is ``SanitizingJSONResponse`` so a statement or engine
message that somehow escaped the service's redaction is stripped at the
boundary (AGENTS.md §2).
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user

from .schemas import (
    CatalogTableSummary,
    ExternalCatalogAlter,
    ExternalCatalogCreate,
    ExternalCatalogListResponse,
    ExternalCatalogResponse,
)
from .service import ExternalCatalogError, external_catalog_service

router = APIRouter()

CurrentUser = Annotated[dict, Depends(get_current_user)]


@router.get(
    "",
    response_model=ExternalCatalogListResponse,
    response_class=SanitizingJSONResponse,
)
async def list_external_catalogs(user: CurrentUser):
    """List every Nova-managed external catalog (no credentials in the payload)."""
    catalogs = await external_catalog_service.list_catalogs()
    return ExternalCatalogListResponse(catalogs=catalogs, count=len(catalogs))


@router.get(
    "/{name}",
    response_model=ExternalCatalogResponse,
    response_class=SanitizingJSONResponse,
)
async def get_external_catalog(name: str, user: CurrentUser):
    """Catalog detail. ``create_statement`` is the redacted engine DDL."""
    return await external_catalog_service.get(name)


@router.post(
    "",
    response_model=ExternalCatalogResponse,
    status_code=201,
    response_class=SanitizingJSONResponse,
)
async def create_external_catalog(body: ExternalCatalogCreate, user: CurrentUser):
    """Create an Iceberg or Hive catalog.

    Runs as the caller, so StarRocks ``CREATE EXTERNAL CATALOG ON SYSTEM`` is the
    authority. Storage secrets are resolved server-side from the named
    connection; none are accepted here.
    """
    try:
        return await external_catalog_service.create(
            body,
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            session_id=user["session_id"],
            role=user.get("active_role"),
        )
    except ExternalCatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/{name}",
    response_model=ExternalCatalogResponse,
    response_class=SanitizingJSONResponse,
)
async def alter_external_catalog(name: str, body: ExternalCatalogAlter, user: CurrentUser):
    """Alter a catalog. Only engine-supported property updates are accepted."""
    try:
        return await external_catalog_service.alter(
            name,
            body,
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            session_id=user["session_id"],
            role=user.get("active_role"),
        )
    except ExternalCatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/{name}",
    response_class=SanitizingJSONResponse,
)
async def drop_external_catalog(name: str, user: CurrentUser):
    """Drop a catalog and its Nova metadata row."""
    try:
        await external_catalog_service.drop(
            name,
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            session_id=user["session_id"],
            role=user.get("active_role"),
        )
    except ExternalCatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "message": f"Catalog '{name}' dropped"}


@router.get(
    "/{name}/databases/{database}/tables",
    response_model=list[CatalogTableSummary],
    response_class=SanitizingJSONResponse,
)
async def list_catalog_tables(name: str, database: str, user: CurrentUser):
    """List tables in an external catalog database (surface for the catalog tree)."""
    from app.core.database import db

    from .service import _safe_catalog_name

    try:
        catalog = _safe_catalog_name(name)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", database):
            raise ExternalCatalogError(f"Invalid database name: {database!r}")
        result = await db.execute_system(
            f"SHOW TABLES FROM `{catalog}`.`{database}`"
        )
    except ExternalCatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        CatalogTableSummary(name=str(row[0]), database=database)
        for row in result.get("rows", [])
    ]
