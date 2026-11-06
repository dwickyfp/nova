"""Request/response models for ``/external-catalogs``, owned by the catalog module."""

from app.modules.catalog.schemas import (
    CatalogTableSummary,
    ExternalCatalogAlter,
    ExternalCatalogListResponse,
    ExternalCatalogResponse,
    is_secret_property,
)

__all__ = [
    "CatalogTableSummary",
    "ExternalCatalogAlter",
    "ExternalCatalogListResponse",
    "ExternalCatalogResponse",
    "is_secret_property",
]
