"""``/external-catalogs``: the original external-catalog API over Nova Catalog.

Kept so existing clients keep working. Every call delegates to
:mod:`app.modules.catalog.service`, so visibility, caller-run DDL, credential
resolution, and redaction have one implementation. Listings are limited to
external catalogs the caller can see.
"""

from __future__ import annotations

from app.modules.catalog.connectors import CatalogError as ExternalCatalogError
from app.modules.catalog.connectors import safe_catalog_name as _safe_catalog_name
from app.modules.catalog.schemas import (
    CatalogDetail,
    CatalogTableSummary,
    ExternalCatalogAlter,
    ExternalCatalogResponse,
)
from app.modules.catalog.service import catalog_service

__all__ = [
    "ExternalCatalogError",
    "ExternalCatalogService",
    "_safe_catalog_name",
    "external_catalog_service",
]


def _response(detail: CatalogDetail) -> ExternalCatalogResponse:
    return ExternalCatalogResponse(
        name=detail.name,
        type=detail.catalog_type,
        metastore_type=detail.metastore_type,
        metastore_uri=detail.metastore_uri,
        storage_connection=detail.storage_connection,
        comment=detail.comment,
        properties=detail.properties,
        create_statement=detail.create_statement,
        created_at=detail.created_at,
        created_by=detail.created_by,
    )


def _found(detail: CatalogDetail | None, name: str) -> ExternalCatalogResponse:
    if detail is None:
        raise ExternalCatalogError(f"Catalog '{name}' not found")
    return _response(detail)


class ExternalCatalogService:
    async def list_catalogs(self, user: dict) -> list[ExternalCatalogResponse]:
        listing = await catalog_service.list_catalogs(user)
        details = [
            await catalog_service.get_catalog(summary.name, user)
            for summary in listing.catalogs
            if summary.kind == "external"
        ]
        return [_response(detail) for detail in details if detail is not None]

    async def get(self, name: str, user: dict) -> ExternalCatalogResponse:
        return _found(await catalog_service.get_catalog(name, user), name)

    async def alter(
        self, name: str, body: ExternalCatalogAlter, user: dict
    ) -> ExternalCatalogResponse:
        return _found(await catalog_service.alter(name, body, user), name)

    async def drop(self, name: str, user: dict) -> None:
        await catalog_service.drop(name, user)

    async def list_tables(
        self, name: str, database: str, user: dict
    ) -> list[CatalogTableSummary] | None:
        objects = await catalog_service.list_objects(name, database, user)
        if objects is None:
            return None
        return [
            CatalogTableSummary(name=obj.object_name, database=database, comment=obj.comment)
            for obj in objects
        ]


external_catalog_service = ExternalCatalogService()
