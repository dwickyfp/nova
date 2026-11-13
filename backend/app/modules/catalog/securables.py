"""Securable ids: one stable id per catalog, database, relation, and column.

Business metadata (tags, comments, owners, classifications), lineage edges, and
links from Semantic Views and entities attach to a securable id, never to a
name string. ``nova_meta.catalog_securables`` maps each id to the name it
currently has and records when it was first seen and when it disappeared.

* The id of a newly seen securable is derived from its natural key, so two
  control-plane processes syncing at once agree on it and a name that has never
  been synced (an old audit row, a legacy tag) still resolves to the id the sync
  will register. After that the id is only ever read from this table.
* A securable that disappears from the engine keeps its row with
  ``dropped_at`` set, so its tags and lineage stay attached and show as dropped.
  If it comes back under the same name it gets the same id and is live again.
* The database and relation ids equal the ``CATALOG_OBJECTS`` ids of phase 1.

The registry is descriptive. It never decides visibility: callers see a
securable only when their own engine session lists it (:mod:`.access`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .metadata_repository import catalog_transaction, like_pattern
from .repository import row_id

if TYPE_CHECKING:
    from .metadata_repository import CatalogMetadataRepository
    from .sync import Harvest

__all__ = [
    "RELATION_TYPES", "SECURABLE_TYPES", "Securable", "catalog_id", "column_id",
    "database_id", "like_pattern", "record_harvest", "relation_id", "securables",
]

RELATION_TYPES = frozenset({"table", "view", "materialized_view"})
SECURABLE_TYPES = frozenset({"catalog", "database", "column", *RELATION_TYPES})

_COLUMNS = (
    "id", "securable_type", "catalog_name", "database_name", "object_name",
    "column_name", "parent_id", "data_type", "ordinal", "first_seen_at", "updated_at",
    "dropped_at",
)


def catalog_id(catalog: str) -> str:
    return row_id(catalog, "", "", "catalog")


def database_id(catalog: str, database: str) -> str:
    return row_id(catalog, database, "", "database")


def relation_id(catalog: str, database: str, name: str) -> str:
    return row_id(catalog, database, name, "relation")


def column_id(catalog: str, database: str, relation: str, column: str) -> str:
    # StarRocks column names are case-insensitive; one column has one id.
    return row_id(catalog, database, relation, "column", column.casefold())


@dataclass(frozen=True, slots=True)
class Securable:
    id: str
    securable_type: str
    catalog_name: str
    database_name: str = ""
    object_name: str = ""
    column_name: str = ""
    parent_id: str | None = None
    data_type: str | None = None
    ordinal: int | None = None
    first_seen_at: datetime | None = None
    updated_at: datetime | None = None
    dropped_at: datetime | None = None

    @property
    def is_relation(self) -> bool:
        return self.securable_type in RELATION_TYPES

    @property
    def full_name(self) -> str:
        parts = [self.catalog_name, self.database_name, self.object_name, self.column_name]
        return ".".join(part for part in parts if part)

    @property
    def relation_id(self) -> str | None:
        """Id of the relation this securable is or belongs to."""
        if self.is_relation:
            return self.id
        if self.securable_type == "column":
            return relation_id(self.catalog_name, self.database_name, self.object_name)
        return None

    def as_row(self) -> dict[str, Any]:
        return {column: getattr(self, column) for column in _COLUMNS}

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Securable:
        ordinal = row.get("ordinal")
        return cls(
            id=str(row["id"]),
            securable_type=str(row["securable_type"]),
            catalog_name=str(row["catalog_name"]),
            database_name=str(row.get("database_name") or ""),
            object_name=str(row.get("object_name") or ""),
            column_name=str(row.get("column_name") or ""),
            parent_id=row.get("parent_id"),
            data_type=row.get("data_type"),
            ordinal=int(ordinal) if ordinal is not None else None,
            first_seen_at=row.get("first_seen_at"),
            updated_at=row.get("updated_at"),
            dropped_at=row.get("dropped_at"),
        )


def securables(rows: Iterable[dict[str, Any]]) -> list[Securable]:
    return [Securable.from_row(row) for row in rows]


def _identity(row: Securable) -> tuple:
    return (row.securable_type, row.parent_id, row.data_type, row.ordinal)


# ── Sync hook ───────────────────────────────────────────────────


def _desired(catalog: str, harvest: Harvest) -> tuple[dict[str, Securable], set[str]]:
    """Securables a harvest saw, and the relations whose columns it read."""
    root = catalog_id(catalog)
    desired: dict[str, Securable] = {
        root: Securable(id=root, securable_type="catalog", catalog_name=catalog)
    }
    for database in harvest.databases:
        key = database_id(catalog, database)
        desired[key] = Securable(
            id=key, securable_type="database", catalog_name=catalog,
            database_name=database, parent_id=root,
        )
    described: set[str] = set()
    for relation in harvest.relations:
        key = relation_id(catalog, relation.database, relation.name)
        object_type = relation.object_type if relation.object_type in RELATION_TYPES else "table"
        desired[key] = Securable(
            id=key, securable_type=object_type, catalog_name=catalog,
            database_name=relation.database, object_name=relation.name,
            parent_id=database_id(catalog, relation.database),
        )
        if relation.columns:
            described.add(key)
        for column in relation.columns:
            name = str(column["column_name"])
            column_key = column_id(catalog, relation.database, relation.name, name)
            desired[column_key] = Securable(
                id=column_key, securable_type="column", catalog_name=catalog,
                database_name=relation.database, object_name=relation.name,
                column_name=name, parent_id=key,
                data_type=str(column["data_type"]) if column.get("data_type") else None,
                ordinal=column.get("ordinal"),
            )
    return desired, described


async def record_harvest(
    repo: CatalogMetadataRepository, catalog: str, harvest: Harvest, generation: int
) -> dict[str, int]:
    """Register what one catalog sync saw and mark what disappeared as dropped.

    Runs in the sync's transaction under the generation it claimed. Only new,
    changed, or returning securables are written. A column is marked dropped
    only when its relation is gone or the harvest read the relation's columns:
    a relation whose columns could not be described keeps them.
    """
    existing = {row.id: row for row in securables(await repo.securables_in_catalog(catalog))}
    desired, described = _desired(catalog, harvest)
    writes = [
        row for key, row in desired.items()
        if not (
            (current := existing.get(key)) is not None
            and current.dropped_at is None
            and _identity(current) == _identity(row)
            and current.full_name == row.full_name
        )
    ]
    if writes:
        await repo.write_securables(
            catalog, [row.as_row() for row in writes], generation=generation
        )
    gone = []
    for key, row in existing.items():
        if key in desired or row.dropped_at is not None:
            continue
        if row.securable_type == "column" and row.parent_id in desired and (
            row.parent_id not in described
        ):
            continue
        gone.append(key)
    if gone:
        await repo.mark_securables_dropped(catalog, gone, generation=generation)
    return {"securables_written": len(writes), "securables_dropped": len(gone)}


class SecurableRepository:
    async def get(self, securable_id: str) -> Securable | None:
        rows = await self.get_many([securable_id])
        return rows[0] if rows else None

    async def get_many(self, ids: Sequence[str]) -> list[Securable]:
        async with catalog_transaction(read_only=True) as repo:
            return securables(await repo.get_securables(ids))

    async def children(self, parent_id: str) -> list[Securable]:
        async with catalog_transaction(read_only=True) as repo:
            return securables(await repo.securable_children(parent_id))

    async def in_catalog(self, catalog: str) -> list[Securable]:
        async with catalog_transaction(read_only=True) as repo:
            return securables(await repo.securables_in_catalog(catalog))

    async def of_type(self, securable_type: str) -> list[Securable]:
        async with catalog_transaction(read_only=True) as repo:
            return securables(await repo.securables_of_type(securable_type))

    async def search_names(self, term: str, limit: int) -> list[Securable]:
        async with catalog_transaction(read_only=True) as repo:
            return securables(await repo.search_securable_names(term, limit))


securable_repo = SecurableRepository()
