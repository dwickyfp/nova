"""Business metadata on securable ids: tags, comments, owners, classifications.

* ``NOVA_SYSTEM.CONFIG_OBJECT_TAGS`` holds tags, one row per securable id and
  tag key. The table existed before Nova Catalog keyed by an object type and a
  name string and no code used it; :meth:`MetadataRepository.ensure_schema`
  moves it to the securable-id shape (see :func:`_migrate_legacy_tags`).
* ``NOVA_SYSTEM.CATALOG_ANNOTATIONS`` holds the business comment, the owner,
  and, for columns, the classification. The engine's own comment stays in the
  synced ``CATALOG_OBJECTS``/``CATALOG_COLUMNS`` rows; this comment is Nova's.

Rows outlive the object they describe: a dropped securable keeps its metadata.
Writes go through :mod:`.governance`, which authorizes and audits them; this
module only stores.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.core.database import db

from .securables import column_id, database_id, like_pattern, relation_id

log = logging.getLogger(__name__)

_PK_PROPERTIES = 'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'

OBJECT_TAGS_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_OBJECT_TAGS (
    securable_id   VARCHAR(64) NOT NULL,
    tag_key        VARCHAR(128) NOT NULL,
    tag_value      VARCHAR(512),
    securable_type VARCHAR(32) NOT NULL,
    created_at     DATETIME,
    created_by     VARCHAR(128),
    updated_at     DATETIME,
    updated_by     VARCHAR(128)
) PRIMARY KEY(securable_id, tag_key)
DISTRIBUTED BY HASH(securable_id) BUCKETS 1
{_PK_PROPERTIES}
"""

ANNOTATIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_ANNOTATIONS (
    securable_id   VARCHAR(64) NOT NULL,
    securable_type VARCHAR(32) NOT NULL,
    comment        VARCHAR(4096),
    owner_name     VARCHAR(128),
    owner_type     VARCHAR(16),
    classification VARCHAR(32),
    updated_at     DATETIME,
    updated_by     VARCHAR(128)
) PRIMARY KEY(securable_id)
DISTRIBUTED BY HASH(securable_id) BUCKETS 1
{_PK_PROPERTIES}
"""

#: Pre-catalog tag rows are kept here, untouched, when the old table had any.
LEGACY_TAGS_TABLE = "CONFIG_OBJECT_TAGS_LEGACY"

_TAG_COLUMNS = (
    "securable_id", "tag_key", "tag_value", "securable_type", "created_at", "created_by",
    "updated_at", "updated_by",
)
_ANNOTATION_COLUMNS = (
    "securable_id", "securable_type", "comment", "owner_name", "owner_type",
    "classification", "updated_at", "updated_by",
)
_BATCH = 200


def _records(result: dict, columns: Sequence[str]) -> list[dict]:
    return [dict(zip(columns, row, strict=False)) for row in result.get("rows") or []]


def _in(ids: Sequence[str]) -> str:
    return ", ".join(["%s"] * len(ids))


async def _columns_of(table: str) -> set[str]:
    result = await db.execute_system(f"SHOW COLUMNS FROM NOVA_SYSTEM.{table}")
    return {str(row[0]).lower() for row in result.get("rows") or []}


def _legacy_securable(object_type: str, object_name: str) -> tuple[str, str] | None:
    """Map a pre-catalog ``(object_type, 'db.table[.column]')`` tag to an id."""
    kind = object_type.strip().lower()
    parts = [part.strip("`") for part in object_name.split(".")]
    if kind == "database" and len(parts) in (1, 2):
        catalog, database = (parts if len(parts) == 2 else ["default_catalog", parts[0]])
        return database_id(catalog, database), "database"
    if kind in {"table", "view", "materialized_view"} and len(parts) in (2, 3):
        catalog, database, name = parts if len(parts) == 3 else ["default_catalog", *parts]
        return relation_id(catalog, database, name), kind
    if kind == "column" and len(parts) in (3, 4):
        catalog, database, name, column = (
            parts if len(parts) == 4 else ["default_catalog", *parts]
        )
        return column_id(catalog, database, name, column), "column"
    return None


async def _migrate_legacy_tags() -> None:
    """Move a name-keyed ``CONFIG_OBJECT_TAGS`` aside and copy what maps.

    The legacy table keyed tags by ``(object_type, object_name)``. When it is
    empty it is simply replaced. Otherwise it is renamed to
    ``CONFIG_OBJECT_TAGS_LEGACY`` (kept as it was) and each row whose name maps
    to a securable is copied into the new table.
    """
    try:
        columns = await _columns_of("CONFIG_OBJECT_TAGS")
    except Exception:  # noqa: BLE001 - no table yet: nothing to migrate
        return
    if "securable_id" in columns or "object_name" not in columns:
        return
    legacy = await db.execute_system(
        "SELECT object_type, object_name, tag_key, tag_value, created_at, created_by "
        "FROM NOVA_SYSTEM.CONFIG_OBJECT_TAGS"
    )
    rows = legacy.get("rows") or []
    if not rows:
        await db.execute_system("DROP TABLE NOVA_SYSTEM.CONFIG_OBJECT_TAGS")
        await db.execute_system(OBJECT_TAGS_DDL)
        return
    await db.execute_system(
        f"ALTER TABLE NOVA_SYSTEM.CONFIG_OBJECT_TAGS RENAME {LEGACY_TAGS_TABLE}"
    )
    await db.execute_system(OBJECT_TAGS_DDL)
    copied = []
    for object_type, object_name, key, value, created_at, created_by in rows:
        mapped = _legacy_securable(str(object_type or ""), str(object_name or ""))
        if mapped is None:
            continue
        copied.append(
            {
                "securable_id": mapped[0], "securable_type": mapped[1], "tag_key": key,
                "tag_value": value, "created_at": created_at, "created_by": created_by,
                "updated_at": created_at, "updated_by": created_by,
            }
        )
    await _insert("CONFIG_OBJECT_TAGS", _TAG_COLUMNS, copied)
    log.info(
        "Moved %d legacy object tags to %s; %d mapped to securable ids",
        len(rows), LEGACY_TAGS_TABLE, len(copied),
    )


async def _insert(table: str, columns: Sequence[str], rows: Sequence[dict]) -> None:
    for start in range(0, len(rows), _BATCH):
        batch = rows[start : start + _BATCH]
        placeholders = "(" + ", ".join(["%s"] * len(columns)) + ")"
        await db.execute_system(
            f"INSERT INTO NOVA_SYSTEM.{table} ("
            + ", ".join(f"`{name}`" for name in columns)
            + ") VALUES "
            + ", ".join([placeholders] * len(batch)),
            [row.get(column) for row in batch for column in columns],
        )


class MetadataRepository:
    async def ensure_schema(self) -> None:
        await _migrate_legacy_tags()
        await db.execute_system(OBJECT_TAGS_DDL)
        await db.execute_system(ANNOTATIONS_DDL)

    # ── Tags ────────────────────────────────────────────────────

    async def tags_for(self, ids: Sequence[str]) -> dict[str, list[dict]]:
        found: dict[str, list[dict]] = {}
        unique = list(dict.fromkeys(ids))
        for start in range(0, len(unique), _BATCH):
            chunk = unique[start : start + _BATCH]
            result = await db.execute_system(
                "SELECT " + ", ".join(f"`{c}`" for c in _TAG_COLUMNS)
                + f" FROM NOVA_SYSTEM.CONFIG_OBJECT_TAGS WHERE securable_id IN ({_in(chunk)}) "
                "ORDER BY tag_key",
                chunk,
            )
            for row in _records(result, _TAG_COLUMNS):
                found.setdefault(row["securable_id"], []).append(row)
        return found

    async def put_tag(
        self, securable_id: str, securable_type: str, key: str, value: str | None,
        user: str, now: datetime,
    ) -> None:
        existing = (await self.tags_for([securable_id])).get(securable_id, [])
        previous = next((row for row in existing if row["tag_key"] == key), None)
        await _insert(
            "CONFIG_OBJECT_TAGS",
            _TAG_COLUMNS,
            [
                {
                    "securable_id": securable_id, "securable_type": securable_type,
                    "tag_key": key, "tag_value": value,
                    "created_at": previous["created_at"] if previous else now,
                    "created_by": previous["created_by"] if previous else user,
                    "updated_at": now, "updated_by": user,
                }
            ],
        )

    async def delete_tag(self, securable_id: str, key: str) -> bool:
        existing = (await self.tags_for([securable_id])).get(securable_id, [])
        if not any(row["tag_key"] == key for row in existing):
            return False
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_OBJECT_TAGS WHERE securable_id = %s AND tag_key = %s",
            [securable_id, key],
        )
        return True

    async def search_tags(
        self, *, term: str | None = None, key: str | None = None, value: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        clauses, params = [], []
        if term:
            pattern = like_pattern(term)
            clauses.append("(LOWER(tag_key) LIKE %s OR LOWER(tag_value) LIKE %s)")
            params += [pattern, pattern]
        if key:
            clauses.append("LOWER(tag_key) = %s")
            params.append(key.casefold())
        if value is not None:
            clauses.append("LOWER(tag_value) = %s")
            params.append(value.casefold())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        result = await db.execute_system(
            "SELECT " + ", ".join(f"`{c}`" for c in _TAG_COLUMNS)
            + f" FROM NOVA_SYSTEM.CONFIG_OBJECT_TAGS{where} ORDER BY securable_id LIMIT %s",
            [*params, int(limit)],
        )
        return _records(result, _TAG_COLUMNS)

    # ── Annotations ─────────────────────────────────────────────

    async def annotations_for(self, ids: Sequence[str]) -> dict[str, dict]:
        found: dict[str, dict] = {}
        unique = list(dict.fromkeys(ids))
        for start in range(0, len(unique), _BATCH):
            chunk = unique[start : start + _BATCH]
            result = await db.execute_system(
                "SELECT " + ", ".join(f"`{c}`" for c in _ANNOTATION_COLUMNS)
                + f" FROM NOVA_SYSTEM.CATALOG_ANNOTATIONS WHERE securable_id IN ({_in(chunk)})",
                chunk,
            )
            for row in _records(result, _ANNOTATION_COLUMNS):
                found[row["securable_id"]] = row
        return found

    async def put_annotation(
        self, securable_id: str, securable_type: str, changes: dict[str, Any],
        user: str, now: datetime,
    ) -> dict:
        """Merge ``changes`` into the securable's annotation row and write it."""
        current = (await self.annotations_for([securable_id])).get(securable_id) or {}
        row = {
            **{column: current.get(column) for column in _ANNOTATION_COLUMNS},
            **changes,
            "securable_id": securable_id,
            "securable_type": securable_type,
            "updated_at": now,
            "updated_by": user,
        }
        await _insert("CATALOG_ANNOTATIONS", _ANNOTATION_COLUMNS, [row])
        return row

    async def search_annotations(self, term: str, limit: int = 500) -> list[dict]:
        pattern = like_pattern(term)
        result = await db.execute_system(
            "SELECT " + ", ".join(f"`{c}`" for c in _ANNOTATION_COLUMNS)
            + " FROM NOVA_SYSTEM.CATALOG_ANNOTATIONS WHERE LOWER(comment) LIKE %s "
            "OR LOWER(owner_name) LIKE %s OR LOWER(classification) LIKE %s LIMIT %s",
            [pattern, pattern, pattern, int(limit)],
        )
        return _records(result, _ANNOTATION_COLUMNS)

    async def search_engine_comments(self, term: str, limit: int = 500) -> list[str]:
        """Securable ids whose synced engine comment matches ``term``."""
        pattern = like_pattern(term)
        objects = await db.execute_system(
            "SELECT id FROM NOVA_SYSTEM.CATALOG_OBJECTS WHERE LOWER(comment) LIKE %s LIMIT %s",
            [pattern, int(limit)],
        )
        columns = await db.execute_system(
            "SELECT catalog_name, database_name, object_name, column_name "
            "FROM NOVA_SYSTEM.CATALOG_COLUMNS WHERE LOWER(column_comment) LIKE %s LIMIT %s",
            [pattern, int(limit)],
        )
        return [str(row[0]) for row in objects.get("rows") or []] + [
            column_id(str(row[0]), str(row[1]), str(row[2]), str(row[3]))
            for row in columns.get("rows") or []
        ]

    async def engine_comments(self, ids: Sequence[str]) -> dict[str, str]:
        """Synced engine comments of relations and databases by securable id."""
        found: dict[str, str] = {}
        unique = list(dict.fromkeys(ids))
        for start in range(0, len(unique), _BATCH):
            chunk = unique[start : start + _BATCH]
            result = await db.execute_system(
                "SELECT id, comment FROM NOVA_SYSTEM.CATALOG_OBJECTS "
                f"WHERE id IN ({_in(chunk)}) AND comment IS NOT NULL",
                chunk,
            )
            found.update({str(r[0]): str(r[1]) for r in result.get("rows") or [] if r[1]})
        return found

    async def engine_column_comments(
        self, catalog: str, database: str, relation: str
    ) -> dict[str, str]:
        result = await db.execute_system(
            "SELECT column_name, column_comment FROM NOVA_SYSTEM.CATALOG_COLUMNS "
            "WHERE catalog_name = %s AND database_name = %s AND object_name = %s",
            [catalog, database, relation],
        )
        return {
            column_id(catalog, database, relation, str(row[0])): str(row[1])
            for row in result.get("rows") or []
            if row[1]
        }


metadata_repo = MetadataRepository()
