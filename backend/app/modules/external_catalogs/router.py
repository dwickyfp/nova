"""``/external-catalogs`` API, an alias of Nova Catalog (``/catalog``).

  GET    /external-catalogs                         → external catalogs the caller can see
  GET    /external-catalogs/{name}                  → detail (redacted SHOW CREATE)
  PATCH  /external-catalogs/{name}                  → ALTER CATALOG ... SET, as the caller
  DELETE /external-catalogs/{name}                  → DROP CATALOG, as the caller
  GET    /external-catalogs/{name}/databases/{db}/tables → tables the caller can list

Every response class is ``SanitizingJSONResponse`` so a statement or engine
message that escaped the service's redaction is stripped at the boundary.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.common.identifiers import InvalidIdentifierError
from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user
from app.modules.catalog.service import CatalogAccessDenied

from .schemas import (
    CatalogTableSummary,
    ExternalCatalogAlter,
    ExternalCatalogListResponse,
    ExternalCatalogResponse,
)
from .service import ExternalCatalogError, external_catalog_service

router = APIRouter()

CurrentUser = Annotated[dict, Depends(get_current_user)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, CatalogAccessDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if str(exc).endswith("not found"):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("", response_model=ExternalCatalogListResponse,
            response_class=SanitizingJSONResponse)
async def list_external_catalogs(user: CurrentUser):
    catalogs = await external_catalog_service.list_catalogs(user)
    return ExternalCatalogListResponse(catalogs=catalogs, count=len(catalogs))


@router.get("/{name}", response_model=ExternalCatalogResponse,
            response_class=SanitizingJSONResponse)
async def get_external_catalog(name: str, user: CurrentUser):
    try:
        return await external_catalog_service.get(name, user)
    except ExternalCatalogError as exc:
        raise _http(exc) from exc


@router.patch("/{name}", response_model=ExternalCatalogResponse,
              response_class=SanitizingJSONResponse)
async def alter_external_catalog(name: str, body: ExternalCatalogAlter, user: CurrentUser):
    try:
        return await external_catalog_service.alter(name, body, user)
    except ExternalCatalogError as exc:
        raise _http(exc) from exc


@router.delete("/{name}", response_class=SanitizingJSONResponse)
async def drop_external_catalog(name: str, user: CurrentUser):
    try:
        await external_catalog_service.drop(name, user)
    except ExternalCatalogError as exc:
        raise _http(exc) from exc
    return {"success": True, "message": f"Catalog '{name}' dropped"}


@router.get("/{name}/databases/{database}/tables", response_model=list[CatalogTableSummary],
            response_class=SanitizingJSONResponse)
async def list_catalog_tables(name: str, database: str, user: CurrentUser):
    """Tables in an external catalog database, as the caller's session lists them."""
    try:
        tables = await external_catalog_service.list_tables(name, database, user)
    except (ExternalCatalogError, InvalidIdentifierError) as exc:
        raise _http(exc) from exc
    if tables is None:
        raise HTTPException(status_code=404, detail=f"Database '{database}' not found")
    return tables
