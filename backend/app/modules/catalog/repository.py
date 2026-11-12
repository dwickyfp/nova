"""Identity and property helpers shared by the catalog store.

Catalog control metadata lives in PostgreSQL (``nova_meta.catalog_*``) and is
read and written only through :class:`.metadata_repository.CatalogMetadataRepository`
in an actor-bound transaction. This module keeps the two pure helpers the
store, the import and the derived-identity checks agree on.

Rows are keyed by a hash of their natural key: primary-key values stay short
whatever the catalog, database, or table names are, and PostgreSQL recomputes
the same hash (``nova_meta.catalog_row_id``) to refuse a spoofed id.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.common.sql_guard import is_credential_property


def row_id(*parts: object) -> str:
    """Stable primary key for a natural key (catalog, database, object, ...)."""
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()


def credential_free(properties: dict[str, Any] | None) -> dict[str, str]:
    """``properties`` without any credential-named key."""
    return {
        str(key): str(value)
        for key, value in (properties or {}).items()
        if not is_credential_property(str(key))
    }


class CatalogRepository:
    """Catalog service API backed by the actor-bound PG repository."""

    async def list_catalogs(self, *, include_dropped: bool = False) -> list[dict]:
        from .metadata_repository import catalog_transaction

        async with catalog_transaction(read_only=True) as repo:
            return await repo.list_catalogs(include_dropped=include_dropped)

    async def get_catalog(self, name: str) -> dict | None:
        from .metadata_repository import catalog_transaction

        async with catalog_transaction(read_only=True) as repo:
            return await repo.get_catalog(name)

    async def list_objects(
        self,
        catalog: str,
        database: str | None = None,
        *,
        include_dropped: bool = False,
    ) -> list[dict]:
        from .metadata_repository import catalog_transaction

        async with catalog_transaction(read_only=True) as repo:
            return await repo.list_objects(catalog, database, include_dropped=include_dropped)

    async def list_columns(self, catalog: str, database: str, obj: str) -> list[dict]:
        from .metadata_repository import catalog_transaction

        async with catalog_transaction(read_only=True) as repo:
            return await repo.list_columns(catalog, database, obj)

    async def list_sync_runs(self, catalog: str, limit: int = 20) -> list[dict]:
        from .metadata_repository import catalog_transaction

        async with catalog_transaction(read_only=True) as repo:
            return await repo.list_sync_runs(catalog, limit)

    async def upsert_catalog(self, catalog: dict) -> None:
        from .metadata_repository import CATALOG_DEFINITION, catalog_transaction

        async with catalog_transaction() as repo:
            previous = await repo.get_catalog(catalog["name"])
            if previous is None:
                await repo.create_catalog(catalog)
            else:
                await repo.update_catalog(
                    catalog["name"],
                    {key: catalog[key] for key in CATALOG_DEFINITION if key in catalog},
                    expected_revision=catalog.get(
                        "metadata_revision", previous["metadata_revision"]
                    ),
                )

    async def write_sync_run(self, run: dict) -> None:
        from .metadata_repository import catalog_transaction

        async with catalog_transaction() as repo:
            await repo.create_sync_run_once(run)
            if run["status"] in {"ok", "error"}:
                await repo.finish_sync_run(
                    run["id"],
                    run["status"],
                    counts=run.get("counts"),
                    error=run.get("error"),
                )


catalog_repo = CatalogRepository()
