"""Nova Catalog connections API (``/api/v1/catalog``), phase 2.

  GET    /connections                       → connections (security admin)
  POST   /connections                       → register a connection
  GET    /connections/{name}                → one connection
  PUT    /connections/{name}/credentials    → enter credentials (write-only)
  PUT    /connections/{name}/write-credentials → enter the notebook write-back credential
  DELETE /connections/{name}/write-credentials → remove it
  DELETE /connections/{name}                → drop an unused connection
  POST   /foreign-catalogs                  → create a catalog from a connection

No response carries an endpoint, a secret reference, or a credential: a
connection is shown by name, type, and whether its credentials are present.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user

from .connections import CatalogAccessDenied, connection_service
from .connectors import CatalogError
from .schemas import (
    CatalogDetail,
    ConnectionCreate,
    ConnectionCredentials,
    ConnectionListResponse,
    ConnectionSummary,
    ForeignCatalogCreate,
)
from .service import catalog_service

router = APIRouter()

CurrentUser = Annotated[dict, Depends(get_current_user)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, CatalogAccessDenied):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/connections", response_model=ConnectionListResponse,
            response_class=SanitizingJSONResponse)
async def list_connections(user: CurrentUser):
    try:
        connections = await connection_service.list_connections(user)
    except CatalogError as exc:
        raise _http(exc) from exc
    return ConnectionListResponse(connections=connections, count=len(connections))


@router.post("/connections", response_model=ConnectionSummary, status_code=201,
             response_class=SanitizingJSONResponse)
async def create_connection(body: ConnectionCreate, user: CurrentUser):
    try:
        created = await connection_service.create(
            name=body.name,
            connector_type=body.connector_type,
            endpoint=body.endpoint,
            secret_ref=body.secret_ref,
            comment=body.comment,
            username=body.credentials.username if body.credentials else None,
            password=body.credentials.password if body.credentials else None,
            user=user,
        )
    except CatalogError as exc:
        raise _http(exc) from exc
    if created is None:
        raise HTTPException(status_code=404, detail=f"Connection '{body.name}' not found")
    return created


@router.get("/connections/{name}", response_model=ConnectionSummary,
            response_class=SanitizingJSONResponse)
async def get_connection(name: str, user: CurrentUser):
    try:
        found = await connection_service.describe(name, user)
    except CatalogError as exc:
        raise _http(exc) from exc
    if found is None:
        raise HTTPException(status_code=404, detail=f"Connection '{name}' not found")
    return found


@router.put("/connections/{name}/credentials", response_model=ConnectionSummary,
            response_class=SanitizingJSONResponse)
async def set_connection_credentials(name: str, body: ConnectionCredentials, user: CurrentUser):
    try:
        updated = await connection_service.set_credentials(
            name, body.username, body.password, user
        )
    except CatalogError as exc:
        raise _http(exc) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Connection '{name}' not found")
    return updated


@router.put("/connections/{name}/write-credentials", response_model=ConnectionSummary,
            response_class=SanitizingJSONResponse)
async def set_connection_write_credentials(
    name: str, body: ConnectionCredentials, user: CurrentUser
):
    return await _write_credentials(name, body.username, body.password, user)


@router.delete("/connections/{name}/write-credentials", response_model=ConnectionSummary,
               response_class=SanitizingJSONResponse)
async def clear_connection_write_credentials(name: str, user: CurrentUser):
    return await _write_credentials(name, None, None, user)


async def _write_credentials(
    name: str, username: str | None, password: str | None, user: dict
) -> ConnectionSummary:
    try:
        updated = await connection_service.set_write_credentials(name, username, password, user)
    except CatalogError as exc:
        raise _http(exc) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Connection '{name}' not found")
    return updated


@router.delete("/connections/{name}", response_class=SanitizingJSONResponse)
async def drop_connection(name: str, user: CurrentUser):
    try:
        await connection_service.drop(name, user)
    except CatalogError as exc:
        raise _http(exc) from exc
    return {"success": True, "message": f"Connection '{name}' dropped"}


@router.post("/foreign-catalogs", response_model=CatalogDetail, status_code=201,
             response_class=SanitizingJSONResponse)
async def create_foreign_catalog(body: ForeignCatalogCreate, user: CurrentUser):
    try:
        name = await connection_service.create_foreign_catalog(
            name=body.name,
            connection=body.connection,
            comment=body.comment,
            properties=body.properties,
            user=user,
        )
        detail = await catalog_service.get_catalog(name, user)
    except CatalogError as exc:
        raise _http(exc) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Catalog '{body.name}' not found")
    return detail
