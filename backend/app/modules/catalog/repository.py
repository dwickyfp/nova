"""Catalog store: ``NOVA_SYSTEM.CATALOG_*`` through the system pool.

The store records what Nova knows about every engine catalog: its Nova-side
definition, the databases/tables/views and columns the last sync saw, and each
sync run. It is control-plane metadata, so it lives in ``NOVA_SYSTEM`` and is
read and written only through ``db.execute_system``. It never decides what a
user may see: the explorer still asks the engine as the caller.

No credential-bearing column exists. ``properties_json`` holds properties the
request validator already accepted as non-secret, and :meth:`upsert_catalog`
drops any credential-named key again before it is written.

Rows are keyed by a hash of their natural key: primary-key values stay short
whatever the catalog, database, or table names are, and an upsert is a plain
``INSERT`` on the primary-key table.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from app.common.sql_guard import is_credential_property
from app.core.database import db

_PK_PROPERTIES = (
    'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'
)

CATALOG_CATALOGS_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_CATALOGS (
    name               VARCHAR(256) NOT NULL,
    kind               VARCHAR(32) NOT NULL,
    catalog_type       VARCHAR(64) NOT NULL,
    managed            BOOLEAN NOT NULL,
    source             VARCHAR(32) NOT NULL,
    metastore_type     VARCHAR(32),
    metastore_uri      VARCHAR(1024),
    storage_connection VARCHAR(256),
    comment            VARCHAR(1024),
    properties_json    STRING,
    status             VARCHAR(32),
    last_sync_at       DATETIME,
    last_sync_error    VARCHAR(2048),
    created_by         VARCHAR(128),
    created_at         DATETIME,
    updated_at         DATETIME,
    dropped_at         DATETIME
) PRIMARY KEY(name)
DISTRIBUTED BY HASH(name) BUCKETS 1
{_PK_PROPERTIES}
"""

CATALOG_OBJECTS_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_OBJECTS (
    id            VARCHAR(64) NOT NULL,
    catalog_name  VARCHAR(256) NOT NULL,
    database_name VARCHAR(256) NOT NULL,
    object_name   VARCHAR(256) NOT NULL,
    object_type   VARCHAR(32) NOT NULL,
    engine        VARCHAR(64),
    comment       VARCHAR(2048),
    row_count     BIGINT,
    data_size     BIGINT,
    fingerprint   VARCHAR(64),
    first_seen_at DATETIME,
    last_seen_at  DATETIME,
    dropped_at    DATETIME
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
{_PK_PROPERTIES}
"""

CATALOG_COLUMNS_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_COLUMNS (
    id             VARCHAR(64) NOT NULL,
    catalog_name   VARCHAR(256) NOT NULL,
    database_name  VARCHAR(256) NOT NULL,
    object_name    VARCHAR(256) NOT NULL,
    ordinal        INT NOT NULL,
    column_name    VARCHAR(256) NOT NULL,
    data_type      VARCHAR(1024),
    is_nullable    BOOLEAN,
    column_comment VARCHAR(2048)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
{_PK_PROPERTIES}
"""

CATALOG_SYNC_RUNS_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_SYNC_RUNS (
    id           VARCHAR(64) NOT NULL,
    catalog_name VARCHAR(256) NOT NULL,
    `trigger`    VARCHAR(32) NOT NULL,
    status       VARCHAR(32) NOT NULL,
    started_at   DATETIME,
    finished_at  DATETIME,
    counts_json  STRING,
    error        VARCHAR(2048)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
{_PK_PROPERTIES}
"""

#: External sources a foreign catalog is built from (phase 2). An endpoint is
#: metadata; ``secret_ref`` is a reference, never a value.
CATALOG_CONNECTIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_CONNECTIONS (
    name             VARCHAR(256) NOT NULL,
    connector_type   VARCHAR(64) NOT NULL,
    host             VARCHAR(512) NOT NULL,
    port             INT,
    database_name    VARCHAR(256),
    options_json     STRING,
    secret_ref       VARCHAR(1024) NOT NULL,
    owner            VARCHAR(128),
    comment          VARCHAR(1024),
    created_at       DATETIME,
    updated_at       DATETIME
) PRIMARY KEY(name)
DISTRIBUTED BY HASH(name) BUCKETS 1
{_PK_PROPERTIES}
"""

#: Credentials entered through Nova for a connection whose ``secret_ref`` is
#: ``nova://<name>``. Both values are Fernet ciphertext (``enc:``), written by
#: the entry flow only and never read back to a client.
CATALOG_CONNECTION_CREDENTIALS_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_CONNECTION_CREDENTIALS (
    connection_name  VARCHAR(256) NOT NULL,
    username_enc     STRING NOT NULL,
    password_enc     STRING NOT NULL,
    updated_by       VARCHAR(128),
    updated_at       DATETIME
) PRIMARY KEY(connection_name)
DISTRIBUTED BY HASH(connection_name) BUCKETS 1
{_PK_PROPERTIES}
"""

#: The credential notebook write-back uses for a connection, kept apart from
#: the read credential the engine's catalog holds (the catalog stays read-only).
CATALOG_CONNECTION_WRITE_CREDENTIALS_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_CONNECTION_WRITE_CREDENTIALS (
    connection_name  VARCHAR(256) NOT NULL,
    username_enc     STRING NOT NULL,
    password_enc     STRING NOT NULL,
    updated_by       VARCHAR(128),
    updated_at       DATETIME
) PRIMARY KEY(connection_name)
DISTRIBUTED BY HASH(connection_name) BUCKETS 1
{_PK_PROPERTIES}
"""

CATALOG_DDLS = (
    CATALOG_CATALOGS_DDL,
    CATALOG_OBJECTS_DDL,
    CATALOG_COLUMNS_DDL,
    CATALOG_SYNC_RUNS_DDL,
    CATALOG_CONNECTIONS_DDL,
    CATALOG_CONNECTION_CREDENTIALS_DDL,
    CATALOG_CONNECTION_WRITE_CREDENTIALS_DDL,
)

_CATALOG_COLUMNS = (
    "name", "kind", "catalog_type", "managed", "source", "metastore_type",
    "metastore_uri", "storage_connection", "comment", "properties_json", "status",
    "last_sync_at", "last_sync_error", "created_by", "created_at", "updated_at",
    "dropped_at",
)
_OBJECT_COLUMNS = (
    "id", "catalog_name", "database_name", "object_name", "object_type", "engine",
    "comment", "row_count", "data_size", "fingerprint", "first_seen_at",
    "last_seen_at", "dropped_at",
)
_COLUMN_COLUMNS = (
    "id", "catalog_name", "database_name", "object_name", "ordinal", "column_name",
    "data_type", "is_nullable", "column_comment",
)
_RUN_COLUMNS = (
    "id", "catalog_name", "trigger", "status", "started_at", "finished_at",
    "counts_json", "error",
)

#: Rows per multi-row ``INSERT``; keeps one statement well under packet limits.
_BATCH = 200


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


def _columns_sql(columns: Sequence[str]) -> str:
    return ", ".join(f"`{name}`" for name in columns)


def _records(result: dict, columns: Sequence[str]) -> list[dict]:
    return [dict(zip(columns, row, strict=False)) for row in result.get("rows") or []]


class CatalogRepository:
    """CRUD for the catalog store. Every statement uses the system pool."""

    async def ensure_schema(self) -> None:
        for ddl in CATALOG_DDLS:
            await db.execute_system(ddl)

    # ── Catalogs ─────────────────────────────────────────────────

    async def list_catalogs(self, *, include_dropped: bool = False) -> list[dict]:
        where = "" if include_dropped else " WHERE dropped_at IS NULL"
        result = await db.execute_system(
            f"SELECT {_columns_sql(_CATALOG_COLUMNS)} FROM NOVA_SYSTEM.CATALOG_CATALOGS"
            f"{where} ORDER BY name"
        )
        return [self._catalog(row) for row in _records(result, _CATALOG_COLUMNS)]

    async def get_catalog(self, name: str) -> dict | None:
        result = await db.execute_system(
            f"SELECT {_columns_sql(_CATALOG_COLUMNS)} FROM NOVA_SYSTEM.CATALOG_CATALOGS "
            "WHERE name = %s",
            [name],
        )
        rows = _records(result, _CATALOG_COLUMNS)
        return self._catalog(rows[0]) if rows else None

    async def upsert_catalog(self, catalog: dict) -> None:
        """Write the whole row for ``catalog['name']``; missing fields become NULL."""
        row = dict(catalog)
        row["properties_json"] = json.dumps(credential_free(row.pop("properties", None)))
        row["managed"] = bool(row.get("managed"))
        values = [row.get(column) for column in _CATALOG_COLUMNS]
        await db.execute_system(
            f"INSERT INTO NOVA_SYSTEM.CATALOG_CATALOGS ({_columns_sql(_CATALOG_COLUMNS)}) "
            f"VALUES ({', '.join(['%s'] * len(_CATALOG_COLUMNS))})",
            values,
        )

    @staticmethod
    def _catalog(row: dict) -> dict:
        raw = row.pop("properties_json", None)
        properties: dict[str, str] = {}
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                properties = credential_free(parsed)
        row["properties"] = properties
        row["managed"] = bool(row.get("managed"))
        return row

    # ── Objects and columns ─────────────────────────────────────

    async def list_objects(
        self,
        catalog: str,
        database: str | None = None,
        *,
        include_dropped: bool = False,
    ) -> list[dict]:
        sql = (
            f"SELECT {_columns_sql(_OBJECT_COLUMNS)} FROM NOVA_SYSTEM.CATALOG_OBJECTS "
            "WHERE catalog_name = %s"
        )
        params: list[Any] = [catalog]
        if database is not None:
            sql += " AND database_name = %s"
            params.append(database)
        if not include_dropped:
            sql += " AND dropped_at IS NULL"
        result = await db.execute_system(sql + " ORDER BY database_name, object_name", params)
        return _records(result, _OBJECT_COLUMNS)

    async def upsert_objects(self, objects: Iterable[dict]) -> None:
        await self._insert_many("CATALOG_OBJECTS", _OBJECT_COLUMNS, objects)

    async def mark_objects_dropped(self, ids: Sequence[str], at: datetime) -> None:
        for start in range(0, len(ids), _BATCH):
            chunk = list(ids[start : start + _BATCH])
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CATALOG_OBJECTS SET dropped_at = %s "
                f"WHERE id IN ({', '.join(['%s'] * len(chunk))})",
                [at, *chunk],
            )

    async def catalogs_with_live_objects(self) -> set[str]:
        """Names of catalogs that still have objects without ``dropped_at``."""
        result = await db.execute_system(
            "SELECT DISTINCT catalog_name FROM NOVA_SYSTEM.CATALOG_OBJECTS "
            "WHERE dropped_at IS NULL"
        )
        return {str(row[0]) for row in result.get("rows") or []}

    async def retire_catalog_objects(self, catalog: str, at: datetime) -> None:
        """Mark every live object of a dropped catalog dropped and remove its columns."""
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CATALOG_OBJECTS SET dropped_at = %s "
            "WHERE catalog_name = %s AND dropped_at IS NULL",
            [at, catalog],
        )
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CATALOG_COLUMNS WHERE catalog_name = %s", [catalog]
        )

    async def list_columns(self, catalog: str, database: str, obj: str) -> list[dict]:
        result = await db.execute_system(
            f"SELECT {_columns_sql(_COLUMN_COLUMNS)} FROM NOVA_SYSTEM.CATALOG_COLUMNS "
            "WHERE catalog_name = %s AND database_name = %s AND object_name = %s "
            "ORDER BY ordinal",
            [catalog, database, obj],
        )
        rows = _records(result, _COLUMN_COLUMNS)
        for row in rows:
            if row.get("is_nullable") is not None:
                row["is_nullable"] = bool(row["is_nullable"])
        return rows

    async def replace_columns(
        self, catalog: str, database: str, obj: str, columns: Sequence[dict]
    ) -> None:
        """Make the stored columns of one object exactly ``columns``."""
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CATALOG_COLUMNS "
            "WHERE catalog_name = %s AND database_name = %s AND object_name = %s",
            [catalog, database, obj],
        )
        await self._insert_many(
            "CATALOG_COLUMNS",
            _COLUMN_COLUMNS,
            (
                {
                    **column,
                    "id": row_id(catalog, database, obj, column["ordinal"]),
                    "catalog_name": catalog,
                    "database_name": database,
                    "object_name": obj,
                }
                for column in columns
            ),
        )

    async def insert_columns(self, rows: Iterable[dict]) -> None:
        """Add columns of objects that have none stored yet, in batches.

        Each row names its ``catalog_name``, ``database_name``, and
        ``object_name``; an existing row with the same key is overwritten.
        """
        await self._insert_many(
            "CATALOG_COLUMNS",
            _COLUMN_COLUMNS,
            (
                {
                    **row,
                    "id": row_id(
                        row["catalog_name"], row["database_name"], row["object_name"],
                        row["ordinal"],
                    ),
                }
                for row in rows
            ),
        )

    async def delete_columns(self, catalog: str, database: str, obj: str) -> None:
        await self.replace_columns(catalog, database, obj, [])

    # ── Sync runs ───────────────────────────────────────────────

    async def write_sync_run(self, run: dict) -> None:
        row = dict(run)
        row["counts_json"] = json.dumps(row.pop("counts", None) or {})
        await self._insert_many("CATALOG_SYNC_RUNS", _RUN_COLUMNS, [row])

    async def list_sync_runs(self, catalog: str, limit: int = 20) -> list[dict]:
        result = await db.execute_system(
            f"SELECT {_columns_sql(_RUN_COLUMNS)} FROM NOVA_SYSTEM.CATALOG_SYNC_RUNS "
            "WHERE catalog_name = %s ORDER BY started_at DESC LIMIT %s",
            [catalog, int(limit)],
        )
        runs = []
        for row in _records(result, _RUN_COLUMNS):
            raw = row.pop("counts_json", None)
            try:
                counts = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, TypeError):
                counts = {}
            row["counts"] = counts if isinstance(counts, dict) else {}
            runs.append(row)
        return runs

    # ── Legacy migration ────────────────────────────────────────

    async def migrate_legacy_catalogs(self, now: datetime) -> int:
        """Copy ``CONFIG_EXTERNAL_CATALOGS`` rows the store does not have yet.

        Idempotent: a catalog already in the store is left alone, so a restart
        never overwrites what a later sync or edit wrote. The legacy table is
        kept as it was and no longer written.
        """
        try:
            legacy = await db.execute_system(
                "SELECT name, catalog_type, metastore_type, metastore_uri, "
                "storage_connection, comment, properties_json, created_at, created_by "
                "FROM NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS"
            )
        except Exception:  # noqa: BLE001 - no legacy table means nothing to copy
            return 0
        known = {row["name"] for row in await self.list_catalogs(include_dropped=True)}
        copied = 0
        for row in _records(
            legacy,
            (
                "name", "catalog_type", "metastore_type", "metastore_uri",
                "storage_connection", "comment", "properties_json", "created_at",
                "created_by",
            ),
        ):
            if row["name"] in known:
                continue
            try:
                properties = json.loads(row.pop("properties_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                properties = {}
            await self.upsert_catalog(
                {
                    **row,
                    "kind": "external",
                    "managed": True,
                    "source": "nova",
                    "properties": properties if isinstance(properties, dict) else {},
                    "created_at": row.get("created_at") or now,
                    "updated_at": now,
                }
            )
            copied += 1
        return copied

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    async def _insert_many(table: str, columns: Sequence[str], rows: Iterable[dict]) -> None:
        batch: list[dict] = []

        async def flush() -> None:
            if not batch:
                return
            placeholders = "(" + ", ".join(["%s"] * len(columns)) + ")"
            await db.execute_system(
                f"INSERT INTO NOVA_SYSTEM.{table} ({_columns_sql(columns)}) VALUES "
                + ", ".join([placeholders] * len(batch)),
                [row.get(column) for row in batch for column in columns],
            )
            batch.clear()

        for row in rows:
            batch.append(row)
            if len(batch) >= _BATCH:
                await flush()
        await flush()


catalog_repo = CatalogRepository()
