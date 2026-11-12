"""Business metadata on securable ids: tags, comments, owners, classifications.

* ``nova_meta.config_object_tags`` holds tags, one row per securable id and tag
  key; ``nova_meta.catalog_annotations`` holds the business comment, the owner,
  and, for columns, the classification. The engine's own comment stays in the
  synced object and column rows; this comment is Nova's.
* Rows outlive the object they describe: a dropped securable keeps its metadata.
* Reads and writes go through :class:`.metadata_repository.CatalogMetadataRepository`
  in the caller's transaction; :mod:`.governance` authorizes and audits them.

Before Nova Catalog, ``CONFIG_OBJECT_TAGS`` keyed tags by an object type and a
``db.table[.column]`` name. Those rows (kept as ``CONFIG_OBJECT_TAGS_LEGACY``)
are mapped to securable ids by the one-shot metadata import, not at startup;
:func:`legacy_securable` is the mapping it applies.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .metadata_repository import catalog_transaction
from .securables import column_id, database_id, relation_id

#: Pre-catalog tag rows are kept here, untouched, by the legacy tag move.
LEGACY_TAGS_TABLE = "CONFIG_OBJECT_TAGS_LEGACY"


def legacy_securable(object_type: str, object_name: str) -> tuple[str, str] | None:
    """Map a pre-catalog ``(object_type, 'db.table[.column]')`` tag to an id."""
    kind = object_type.strip().lower()
    parts = [part.strip("`") for part in object_name.split(".")]
    if kind == "database" and len(parts) in (1, 2):
        catalog, database = parts if len(parts) == 2 else ["default_catalog", parts[0]]
        return database_id(catalog, database), "database"
    if kind in {"table", "view", "materialized_view"} and len(parts) in (2, 3):
        catalog, database, name = parts if len(parts) == 3 else ["default_catalog", *parts]
        return relation_id(catalog, database, name), kind
    if kind == "column" and len(parts) in (3, 4):
        catalog, database, name, column = parts if len(parts) == 4 else ["default_catalog", *parts]
        return column_id(catalog, database, name, column), "column"
    return None


class MetadataRepository:
    async def tags_for(self, ids: Sequence[str]) -> dict[str, list[dict]]:
        async with catalog_transaction(read_only=True) as repo:
            return await repo.tags_for(ids)

    async def put_tag(
        self,
        securable_id: str,
        securable_type: str,
        key: str,
        value: str | None,
        user: str,
        now: datetime,
    ) -> None:
        async with catalog_transaction() as repo:
            if repo.actor.principal != user:
                from app.core.metadata_db import MetadataPermissionDenied

                raise MetadataPermissionDenied()
            await repo.put_tag(securable_id, securable_type, key, value)

    async def delete_tag(self, securable_id: str, key: str) -> bool:
        async with catalog_transaction() as repo:
            return await repo.delete_tag(securable_id, key)

    async def search_tags(
        self,
        *,
        term: str | None = None,
        key: str | None = None,
        value: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        async with catalog_transaction(read_only=True) as repo:
            return await repo.search_tags(term=term, key=key, value=value, limit=limit)

    async def annotations_for(self, ids: Sequence[str]) -> dict[str, dict]:
        async with catalog_transaction(read_only=True) as repo:
            return await repo.annotations_for(ids)

    async def put_annotation(
        self,
        securable_id: str,
        securable_type: str,
        changes: dict[str, Any],
        user: str,
        now: datetime,
    ) -> dict:
        async with catalog_transaction() as repo:
            if repo.actor.principal != user:
                from app.core.metadata_db import MetadataPermissionDenied

                raise MetadataPermissionDenied()
            previous = (await repo.annotations_for([securable_id])).get(securable_id)
            return await repo.put_annotation(
                securable_id,
                securable_type,
                changes,
                expected_revision=previous["metadata_revision"] if previous else 0,
            )

    async def search_annotations(self, term: str, limit: int = 500) -> list[dict]:
        async with catalog_transaction(read_only=True) as repo:
            return await repo.search_annotations(term, limit)

    async def search_engine_comments(self, term: str, limit: int = 500) -> list[str]:
        async with catalog_transaction(read_only=True) as repo:
            rows = await repo.search_engine_comments(term, limit)
        return [
            row["id"]
            if "id" in row
            else column_id(
                row["catalog_name"], row["database_name"], row["object_name"], row["column_name"]
            )
            for row in rows
        ]

    async def engine_comments(self, ids: Sequence[str]) -> dict[str, str]:
        async with catalog_transaction(read_only=True) as repo:
            rows = await repo.get_objects(ids)
        return {row["id"]: row["comment"] for row in rows if row.get("comment") is not None}

    async def engine_column_comments(
        self, catalog: str, database: str, relation: str
    ) -> dict[str, str]:
        async with catalog_transaction(read_only=True) as repo:
            rows = await repo.list_columns(catalog, database, relation)
        return {
            column_id(catalog, database, relation, row["column_name"]): row["column_comment"]
            for row in rows
            if row.get("column_comment") is not None
        }


metadata_repo = MetadataRepository()
