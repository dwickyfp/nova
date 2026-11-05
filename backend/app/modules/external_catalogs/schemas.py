"""Request/response models for ``/external-catalogs``, owned by the catalog module."""

from app.modules.catalog.schemas import (
    CatalogTableSummary,
    CatalogType,
    ExternalCatalogAlter,
    ExternalCatalogCreate,
    ExternalCatalogListResponse,
    ExternalCatalogResponse,
    MetastoreType,
    is_secret_property,
)

__all__ = [
    "CatalogTableSummary",
    "CatalogType",
    "ExternalCatalogAlter",
    "ExternalCatalogCreate",
    "ExternalCatalogListResponse",
    "ExternalCatalogResponse",
    "MetastoreType",
    "is_secret_property",
]
