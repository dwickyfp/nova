"""Nova Catalog API (``/api/v1/catalog``).

  GET    /catalogs                                    → catalogs the caller can see
  POST   /catalogs/refresh                            → reconcile every catalog (admin)
  GET    /catalogs/{name}                             → detail, redacted engine DDL
  PATCH  /catalogs/{name}                             → alter (as the caller)
  DELETE /catalogs/{name}                             → drop (as the caller)
  POST   /catalogs/{name}/refresh                     → sync one catalog (admin)
  GET    /catalogs/{name}/sync-runs                   → recent syncs
  GET    /catalogs/{name}/databases/{db}/objects      → relations the caller can list
  GET    /catalogs/{name}/databases/{db}/objects/{o}/columns → stored columns

Every response passes ``SanitizingJSONResponse``, so an engine message that
escaped the service's redaction is still stripped at the boundary.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.common.identifiers import InvalidIdentifierError
from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user

from .connectors import CatalogError
from .schemas import (
    CatalogColumn,
    CatalogDetail,
    CatalogListResponse,
    CatalogObject,
    ExternalCatalogAlter,
    SyncRunListResponse,
)
from .service import CatalogAccessDenied, catalog_service

router = APIRouter()

CurrentUser = Annotated[dict, Depends(get_current_user)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, CatalogAccessDenied):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Catalog '{name}' not found")


@router.get("/catalogs", response_model=CatalogListResponse,
            response_class=SanitizingJSONResponse)
async def list_catalogs(user: CurrentUser):
    return await catalog_service.list_catalogs(user)


@router.post("/catalogs/refresh", response_model=SyncRunListResponse,
             response_class=SanitizingJSONResponse)
async def refresh_all(user: CurrentUser):
    try:
        runs = await catalog_service.refresh(None, user)
    except CatalogError as exc:
        raise _http(exc) from exc
    return SyncRunListResponse(runs=runs)


@router.get("/catalogs/{name}", response_model=CatalogDetail,
            response_class=SanitizingJSONResponse)
async def get_catalog(name: str, user: CurrentUser):
    try:
        detail = await catalog_service.get_catalog(name, user)
    except CatalogError as exc:
        raise _http(exc) from exc
    if detail is None:
        raise _not_found(name)
    return detail


@router.patch("/catalogs/{name}", response_model=CatalogDetail,
              response_class=SanitizingJSONResponse)
async def alter_catalog(name: str, body: ExternalCatalogAlter, user: CurrentUser):
    try:
        detail = await catalog_service.alter(name, body, user)
    except CatalogError as exc:
        raise _http(exc) from exc
    if detail is None:
        raise _not_found(name)
    return detail


@router.delete("/catalogs/{name}", response_class=SanitizingJSONResponse)
async def drop_catalog(name: str, user: CurrentUser):
    try:
        await catalog_service.drop(name, user)
    except CatalogError as exc:
        raise _http(exc) from exc
    return {"success": True, "message": f"Catalog '{name}' dropped"}


@router.post("/catalogs/{name}/refresh", response_model=SyncRunListResponse,
             response_class=SanitizingJSONResponse)
async def refresh_catalog(name: str, user: CurrentUser):
    try:
        runs = await catalog_service.refresh(name, user)
    except CatalogError as exc:
        raise _http(exc) from exc
    return SyncRunListResponse(runs=runs)


@router.get("/catalogs/{name}/sync-runs", response_model=SyncRunListResponse,
            response_class=SanitizingJSONResponse)
async def list_sync_runs(name: str, user: CurrentUser,
                         limit: Annotated[int, Query(ge=1, le=100)] = 20):
    try:
        runs = await catalog_service.sync_runs(name, user, limit)
    except CatalogError as exc:
        raise _http(exc) from exc
    if runs is None:
        raise _not_found(name)
    return SyncRunListResponse(runs=runs)


@router.get("/catalogs/{name}/databases/{database}/objects",
            response_model=list[CatalogObject], response_class=SanitizingJSONResponse)
async def list_objects(name: str, database: str, user: CurrentUser):
    try:
        objects = await catalog_service.list_objects(name, database, user)
    except (CatalogError, InvalidIdentifierError) as exc:
        raise _http(exc) from exc
    if objects is None:
        raise HTTPException(status_code=404, detail=f"Database '{database}' not found")
    return objects


@router.get("/catalogs/{name}/databases/{database}/objects/{relation}/columns",
            response_model=list[CatalogColumn], response_class=SanitizingJSONResponse)
async def list_columns(name: str, database: str, relation: str, user: CurrentUser):
    try:
        columns = await catalog_service.columns(name, database, relation, user)
    except (CatalogError, InvalidIdentifierError) as exc:
        raise _http(exc) from exc
    if columns is None:
        raise HTTPException(status_code=404, detail=f"Object '{relation}' not found")
    return columns
