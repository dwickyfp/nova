"""Securable ids: one stable id per catalog, database, relation, and column.

Business metadata (tags, comments, owners, classifications), lineage edges, and
links from Semantic Views and entities attach to a securable id, never to a
name string. ``NOVA_SYSTEM.CATALOG_SECURABLES`` maps each id to the name it
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

from app.core.database import db

from .repository import row_id

if TYPE_CHECKING:
    from .sync import Harvest

_PK_PROPERTIES = 'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'

CATALOG_SECURABLES_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_SECURABLES (
    id             VARCHAR(64) NOT NULL,
    securable_type VARCHAR(32) NOT NULL,
    catalog_name   VARCHAR(256) NOT NULL,
    database_name  VARCHAR(256) NOT NULL,
    object_name    VARCHAR(256) NOT NULL,
    column_name    VARCHAR(256) NOT NULL,
    parent_id      VARCHAR(64),
    data_type      VARCHAR(1024),
    ordinal        INT,
    first_seen_at  DATETIME,
    updated_at     DATETIME,
    dropped_at     DATETIME
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
{_PK_PROPERTIES}
"""

RELATION_TYPES = frozenset({"table", "view", "materialized_view"})
SECURABLE_TYPES = frozenset({"catalog", "database", "column", *RELATION_TYPES})

_COLUMNS = (
    "id", "securable_type", "catalog_name", "database_name", "object_name",
    "column_name", "parent_id", "data_type", "ordinal", "first_seen_at", "updated_at",
    "dropped_at",
)
_BATCH = 200


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


def _select() -> str:
    return (
        "SELECT " + ", ".join(f"`{name}`" for name in _COLUMNS)
        + " FROM NOVA_SYSTEM.CATALOG_SECURABLES"
    )


def _securables(result: dict) -> list[Securable]:
    return [
        Securable.from_row(dict(zip(_COLUMNS, row, strict=False)))
        for row in result.get("rows") or []
    ]


def _identity(row: Securable) -> tuple:
    return (row.securable_type, row.parent_id, row.data_type, row.ordinal)


class SecurableRepository:
    """The id registry. Every statement uses the system pool."""

    async def ensure_schema(self) -> None:
        await db.execute_system(CATALOG_SECURABLES_DDL)

    async def get(self, securable_id: str) -> Securable | None:
        rows = await self.get_many([securable_id])
        return rows[0] if rows else None

    async def get_many(self, ids: Sequence[str]) -> list[Securable]:
        found: list[Securable] = []
        unique = list(dict.fromkeys(ids))
        for start in range(0, len(unique), _BATCH):
            chunk = unique[start : start + _BATCH]
            found.extend(
                _securables(
                    await db.execute_system(
                        _select() + f" WHERE id IN ({', '.join(['%s'] * len(chunk))})", chunk
                    )
                )
            )
        return found

    async def children(self, parent_id: str) -> list[Securable]:
        return _securables(
            await db.execute_system(
                _select() + " WHERE parent_id = %s ORDER BY ordinal, column_name", [parent_id]
            )
        )

    async def in_catalog(self, catalog: str) -> list[Securable]:
        return _securables(
            await db.execute_system(_select() + " WHERE catalog_name = %s", [catalog])
        )

    async def of_type(self, securable_type: str) -> list[Securable]:
        return _securables(
            await db.execute_system(_select() + " WHERE securable_type = %s", [securable_type])
        )

    async def search_names(self, term: str, limit: int) -> list[Securable]:
        pattern = like_pattern(term)
        return _securables(
            await db.execute_system(
                _select()
                + " WHERE LOWER(object_name) LIKE %s OR LOWER(column_name) LIKE %s "
                "OR LOWER(database_name) LIKE %s OR LOWER(catalog_name) LIKE %s "
                "ORDER BY securable_type, catalog_name, database_name, object_name, ordinal "
                "LIMIT %s",
                [pattern, pattern, pattern, pattern, int(limit)],
            )
        )

    async def write(self, rows: Iterable[Securable]) -> None:
        batch: list[Securable] = []

        async def flush() -> None:
            if not batch:
                return
            placeholders = "(" + ", ".join(["%s"] * len(_COLUMNS)) + ")"
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CATALOG_SECURABLES ("
                + ", ".join(f"`{name}`" for name in _COLUMNS)
                + ") VALUES "
                + ", ".join([placeholders] * len(batch)),
                [value for row in batch for value in row.as_row().values()],
            )
            batch.clear()

        for row in rows:
            batch.append(row)
            if len(batch) >= _BATCH:
                await flush()
        await flush()

    async def mark_dropped(self, ids: Sequence[str], at: datetime) -> None:
        for start in range(0, len(ids), _BATCH):
            chunk = list(ids[start : start + _BATCH])
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CATALOG_SECURABLES SET dropped_at = %s, updated_at = %s "
                f"WHERE id IN ({', '.join(['%s'] * len(chunk))})",
                [at, at, *chunk],
            )

    async def mark_catalog_dropped(self, catalog: str, at: datetime) -> None:
        """Drop a catalog and everything under it that is still live."""
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CATALOG_SECURABLES SET dropped_at = %s, updated_at = %s "
            "WHERE catalog_name = %s AND dropped_at IS NULL",
            [at, at, catalog],
        )


securable_repo = SecurableRepository()


def like_pattern(term: str) -> str:
    """A case-insensitive ``LIKE`` substring pattern with wildcards escaped."""
    escaped = term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ── Sync hooks ──────────────────────────────────────────────────


async def record_catalogs(
    names: Iterable[str], now: datetime, repo: SecurableRepository | None = None
) -> None:
    """Match catalog securables to the engine's catalog list."""
    repo = repo or securable_repo
    live = set(names)
    stored = {row.catalog_name: row for row in await repo.of_type("catalog")}
    writes = []
    for name in sorted(live):
        current = stored.get(name)
        if current is None or current.dropped_at is not None:
            writes.append(
                Securable(
                    id=current.id if current else catalog_id(name),
                    securable_type="catalog",
                    catalog_name=name,
                    first_seen_at=(current.first_seen_at if current else None) or now,
                    updated_at=now,
                )
            )
    await repo.write(writes)
    for name, row in stored.items():
        if name not in live and row.dropped_at is None:
            await repo.mark_catalog_dropped(name, now)


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
    catalog: str, harvest: Harvest, now: datetime, repo: SecurableRepository | None = None
) -> dict[str, int]:
    """Register what one catalog sync saw and mark what disappeared as dropped.

    Only new, changed, or returning securables are written. A column is marked
    dropped only when its relation is gone or the harvest read the relation's
    columns: a relation whose columns could not be described keeps them.
    """
    repo = repo or securable_repo
    existing = {row.id: row for row in await repo.in_catalog(catalog)}
    desired, described = _desired(catalog, harvest)
    writes = []
    for key, row in desired.items():
        current = existing.get(key)
        if (
            current is not None
            and current.dropped_at is None
            and _identity(current) == _identity(row)
            and current.full_name == row.full_name
        ):
            continue
        writes.append(
            Securable(
                **{
                    **row.as_row(),
                    "first_seen_at": (current.first_seen_at if current else None) or now,
                    "updated_at": now,
                    "dropped_at": None,
                }
            )
        )
    await repo.write(writes)
    gone = []
    for key, row in existing.items():
        if key in desired or row.dropped_at is not None:
            continue
        if row.securable_type == "column" and row.parent_id in desired and (
            row.parent_id not in described
        ):
            continue
        gone.append(key)
    await repo.mark_dropped(gone, now)
    return {"securables_written": len(writes), "securables_dropped": len(gone)}
