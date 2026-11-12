"""Catalog control metadata in the caller's PostgreSQL transaction.

The prepared replacement for the ``NOVA_SYSTEM`` paths of the catalog
repositories (:mod:`.repository`, :mod:`.connections`, :mod:`.securables`,
:mod:`.metadata`, :mod:`.lineage`, :mod:`.links`), switched in at the
all-domain cutover. It closes the Wave G gaps in the new store:

* definitions (catalogs, connections, annotations) change only at the
  revision the caller read, and return the row that transaction wrote;
* a sync claims its catalog by advancing ``sync_generation``; inventory
  writes name that generation, so an older harvest can no longer mark newly
  discovered objects dropped or overwrite a newer harvest's columns;
* sync runs are created once by id and finished once;
* column refresh, owner-link replacement and connection deletion (with both
  credential rows) each happen in one transaction;
* credentials are stored as ciphertext of :func:`app.common.crypto.encrypt`
  and decrypted only for engine use; no method returns or audits them.

Engine observations (what the engine lists for a caller, SHOW CREATE, source
reads) stay with their engine adapters; this repository holds Nova rows only.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus

from app.common.crypto import decrypt, encrypt
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

from .repository import credential_free, row_id

#: Catalog definition fields a change may name (``properties`` is stored as JSON).
CATALOG_DEFINITION = (
    "kind", "catalog_type", "managed", "source", "metastore_type", "metastore_uri",
    "storage_connection", "comment", "properties",
)
CONNECTION_DEFINITION = (
    "connector_type", "host", "port", "database_name", "options", "secret_ref", "comment",
)
ANNOTATION_FIELDS = ("comment", "owner_name", "owner_type", "classification")
LINEAGE_RUN = "lineage:audit"
_LINK_OWNERS = ("semantic_view", "entity")
_CREDENTIAL_TABLES = {
    False: "nova_meta.catalog_connection_credentials",
    True: "nova_meta.catalog_connection_write_credentials",
}


def like_pattern(term: str) -> str:
    """A case-insensitive ``LIKE`` substring pattern with wildcards escaped."""
    escaped = term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def link_row(owner_type: str, owner_id: str, label: str, relation: str) -> dict[str, Any] | None:
    """A link of an owner to ``[catalog.]database.table``, as ``links._link`` builds it."""
    parts = [part.strip().strip("`") for part in str(relation or "").split(".")]
    if len(parts) == 2:
        parts = ["default_catalog", *parts]
    if len(parts) != 3 or not all(parts):
        return None
    securable_id = row_id(parts[0], parts[1], parts[2], "relation")
    return {
        "id": row_id(owner_type, owner_id, securable_id),
        "owner_type": owner_type,
        "owner_id": owner_id,
        "owner_label": label[:512],
        "securable_id": securable_id,
        "relation_name": ".".join(parts),
    }


def _generation(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("A positive sync generation is required")
    return value


def _revision(value: int, *, allow_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ValueError("A valid metadata revision is required")
    return value


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Lineage times must be timezone-aware")
    return value


def _json_object(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _catalog(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row = dict(row)
    row["properties"] = credential_free(_json_object(row.pop("properties_json", None)))
    return row


def _connection(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row = dict(row)
    row["options"] = _json_object(row.pop("options_json", None))
    return row


def _run(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row = dict(row)
    row["counts"] = _json_object(row.pop("counts_json", None))
    return row


class CatalogMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Catalog metadata requires an active metadata transaction")
        cursor = await self.connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version)),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    async def _one(self, query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchone()

    async def _all(self, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(query, params)
        return list(await cursor.fetchall())

    async def _fence(self, catalog: str, generation: int | None) -> None:
        """Name the sync generation that the next inventory writes belong to."""
        await self.connection.execute(
            "SELECT set_config('nova.catalog_sync_catalog',%s,true),"
            "set_config('nova.catalog_sync_generation',%s,true)",
            (catalog, "" if generation is None else str(_generation(generation))),
        )

    async def _unfence(self) -> None:
        await self.connection.execute(
            "SELECT set_config('nova.catalog_sync_catalog','',true),"
            "set_config('nova.catalog_sync_generation','',true)",
        )

    # ── Catalogs ──────────────────────────────────────────────────────────

    async def list_catalogs(self, *, include_dropped: bool = False) -> list[dict[str, Any]]:
        await self._bound()
        rows = await self._all(
            "SELECT * FROM nova_meta.catalog_catalogs"
            + ("" if include_dropped else " WHERE dropped_at IS NULL") + " ORDER BY name",
        )
        return [row for row in map(_catalog, rows) if row is not None]

    async def get_catalog(self, name: str) -> dict[str, Any] | None:
        await self._bound()
        return _catalog(await self._one(
            "SELECT * FROM nova_meta.catalog_catalogs WHERE name=%s", (name,),
        ))

    async def create_catalog(self, data: dict[str, Any]) -> dict[str, Any]:
        """Register a catalog; an existing name is a conflict, never an overwrite."""
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.catalog_catalogs (name,kind,catalog_type,managed,source,"
            "metastore_type,metastore_uri,storage_connection,comment,properties_json) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (data["name"], data["kind"], data["catalog_type"], bool(data.get("managed")),
             data["source"], data.get("metastore_type"), data.get("metastore_uri"),
             data.get("storage_connection"), data.get("comment"),
             json.dumps(credential_free(data.get("properties")), sort_keys=True)),
        )
        assert row is not None
        return _catalog(row) or {}

    async def update_catalog(
        self, name: str, changes: dict[str, Any], *, expected_revision: int,
        revive: bool = False,
    ) -> dict[str, Any]:
        """Change a definition only at ``expected_revision``; ``revive`` clears the drop."""
        await self._bound()
        unknown = set(changes) - set(CATALOG_DEFINITION)
        if unknown or not (changes or revive):
            raise ValueError(f"Unsupported catalog changes: {sorted(unknown)}")
        assignments: list[str] = []
        values: list[Any] = []
        for field, value in changes.items():
            if field == "properties":
                assignments.append("properties_json=%s")
                values.append(json.dumps(credential_free(value), sort_keys=True))
            else:
                assignments.append(f"{field}=%s")
                values.append(bool(value) if field == "managed" else value)
        if revive:
            assignments.append("dropped_at=NULL")
        row = await self._one(
            f"UPDATE nova_meta.catalog_catalogs SET {','.join(assignments)},"
            "metadata_revision=metadata_revision+1 WHERE name=%s AND metadata_revision=%s "
            "RETURNING *",
            (*values, name, _revision(expected_revision)),
        )
        if row is None:
            raise MetadataConflict()
        return _catalog(row) or {}

    async def drop_catalog(self, name: str, *, expected_revision: int) -> dict[str, Any]:
        """Tombstone a catalog at ``expected_revision``; its business metadata stays."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.catalog_catalogs SET dropped_at=clock_timestamp(),"
            "metadata_revision=metadata_revision+1 WHERE name=%s AND metadata_revision=%s "
            "RETURNING *",
            (name, _revision(expected_revision)),
        )
        if row is None:
            raise MetadataConflict()
        return _catalog(row) or {}

    async def claim_sync(self, name: str) -> int | None:
        """Take the catalog's next sync generation; waits for in-flight writes."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.catalog_catalogs SET sync_generation=sync_generation+1 "
            "WHERE name=%s RETURNING sync_generation", (name,),
        )
        return int(row["sync_generation"]) if row else None

    async def record_sync_status(
        self, name: str, *, generation: int, status: str, error: str | None = None,
    ) -> bool:
        """Record a sync outcome on the catalog, only for the current generation."""
        await self._bound()
        if status not in ("ok", "error"):
            raise ValueError("A sync status is ok or error")
        await self._fence(name, generation)
        row = await self._one(
            "UPDATE nova_meta.catalog_catalogs SET status=%s,last_sync_error=%s,"
            "last_sync_at=clock_timestamp() WHERE name=%s AND sync_generation=%s RETURNING name",
            (status, error if status == "error" else None, name, generation),
        )
        await self._unfence()
        return row is not None

    # ── Objects and columns (fenced) ──────────────────────────────────────

    async def list_objects(
        self, catalog: str, database: str | None = None, *, include_dropped: bool = False,
    ) -> list[dict[str, Any]]:
        await self._bound()
        query = "SELECT * FROM nova_meta.catalog_objects WHERE catalog_name=%s"
        params: list[Any] = [catalog]
        if database is not None:
            query += " AND database_name=%s"
            params.append(database)
        if not include_dropped:
            query += " AND dropped_at IS NULL"
        return await self._all(query + " ORDER BY database_name,object_name", params)

    async def upsert_objects(
        self, catalog: str, objects: Iterable[dict[str, Any]], *, generation: int,
    ) -> int:
        """Record objects this harvest saw (ids derived from their names)."""
        await self._bound()
        rows = []
        for item in objects:
            database_name, object_name = item["database_name"], item.get("object_name") or ""
            object_type = item["object_type"]
            identity = (row_id(catalog, database_name, "", "database")
                        if object_type == "database"
                        else row_id(catalog, database_name, object_name, "relation"))
            rows.append((identity, catalog, database_name, object_name, object_type,
                         item.get("engine"), item.get("comment"), item.get("row_count"),
                         item.get("data_size"), item.get("fingerprint")))
        await self._fence(catalog, generation)
        async with self.connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO nova_meta.catalog_objects (id,catalog_name,database_name,object_name,"
                "object_type,engine,comment,row_count,data_size,fingerprint) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
                "object_type=EXCLUDED.object_type,engine=EXCLUDED.engine,"
                "comment=EXCLUDED.comment,row_count=EXCLUDED.row_count,"
                "data_size=EXCLUDED.data_size,fingerprint=EXCLUDED.fingerprint,dropped_at=NULL",
                rows,
            )
        await self._unfence()
        return len(rows)

    async def mark_objects_dropped(
        self, catalog: str, ids: Sequence[str], *, generation: int,
    ) -> int:
        await self._bound()
        await self._fence(catalog, generation)
        cursor = await self.connection.execute(
            "UPDATE nova_meta.catalog_objects SET dropped_at=clock_timestamp() "
            "WHERE catalog_name=%s AND id=ANY(%s) AND dropped_at IS NULL",
            (catalog, list(ids)),
        )
        await self._unfence()
        return cursor.rowcount

    async def catalogs_with_live_objects(self) -> set[str]:
        await self._bound()
        rows = await self._all(
            "SELECT DISTINCT catalog_name FROM nova_meta.catalog_objects WHERE dropped_at IS NULL",
        )
        return {str(row["catalog_name"]) for row in rows}

    async def retire_catalog_objects(self, catalog: str, *, generation: int | None) -> int:
        """Tombstone a dropped catalog's live objects and remove its columns.

        ``generation`` is the claim on the catalog row; an imported orphan with
        no catalog row is retired without one.
        """
        await self._bound()
        await self._fence(catalog, generation)
        cursor = await self.connection.execute(
            "UPDATE nova_meta.catalog_objects SET dropped_at=clock_timestamp() "
            "WHERE catalog_name=%s AND dropped_at IS NULL", (catalog,),
        )
        await self.connection.execute(
            "DELETE FROM nova_meta.catalog_columns WHERE catalog_name=%s", (catalog,),
        )
        await self._unfence()
        return cursor.rowcount

    async def list_columns(self, catalog: str, database: str, obj: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.catalog_columns WHERE catalog_name=%s AND database_name=%s "
            "AND object_name=%s ORDER BY ordinal", (catalog, database, obj),
        )

    async def _write_columns(
        self, catalog: str, rows: Iterable[dict[str, Any]],
    ) -> int:
        values = [
            (row_id(catalog, row["database_name"], row["object_name"], int(row["ordinal"])),
             catalog, row["database_name"], row["object_name"], int(row["ordinal"]),
             row["column_name"], row.get("data_type"), row.get("is_nullable"),
             row.get("column_comment"))
            for row in rows
        ]
        async with self.connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO nova_meta.catalog_columns (id,catalog_name,database_name,object_name,"
                "ordinal,column_name,data_type,is_nullable,column_comment) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
                "column_name=EXCLUDED.column_name,data_type=EXCLUDED.data_type,"
                "is_nullable=EXCLUDED.is_nullable,column_comment=EXCLUDED.column_comment",
                values,
            )
        return len(values)

    async def replace_columns(
        self, catalog: str, database: str, obj: str, columns: Sequence[dict[str, Any]], *,
        generation: int,
    ) -> int:
        """Make one object's stored columns exactly ``columns``, in this transaction."""
        await self._bound()
        await self._fence(catalog, generation)
        await self.connection.execute(
            "DELETE FROM nova_meta.catalog_columns WHERE catalog_name=%s AND database_name=%s "
            "AND object_name=%s", (catalog, database, obj),
        )
        count = await self._write_columns(
            catalog, ({**column, "database_name": database, "object_name": obj}
                      for column in columns),
        )
        await self._unfence()
        return count

    async def insert_columns(
        self, catalog: str, rows: Iterable[dict[str, Any]], *, generation: int,
    ) -> int:
        """Add (or overwrite by key) columns of objects first seen by this harvest."""
        await self._bound()
        await self._fence(catalog, generation)
        count = await self._write_columns(catalog, rows)
        await self._unfence()
        return count

    async def search_engine_comments(self, term: str, limit: int = 500) -> list[dict[str, Any]]:
        """Objects and columns whose synced engine comment matches ``term``."""
        await self._bound()
        pattern = like_pattern(term)
        objects = await self._all(
            "SELECT id FROM nova_meta.catalog_objects WHERE lower(comment) LIKE %s LIMIT %s",
            (pattern, int(limit)),
        )
        columns = await self._all(
            "SELECT catalog_name,database_name,object_name,column_name "
            "FROM nova_meta.catalog_columns WHERE lower(column_comment) LIKE %s LIMIT %s",
            (pattern, int(limit)),
        )
        return [{"id": row["id"]} for row in objects] + columns

    # ── Sync runs ─────────────────────────────────────────────────────────

    async def create_sync_run_once(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Start a run by id, or return the existing one untouched."""
        await self._bound()
        generation = data.get("sync_generation")
        row = await self._one(
            "INSERT INTO nova_meta.catalog_sync_runs (id,catalog_name,trigger,status,"
            "sync_generation) VALUES (%s,%s,%s,'running',%s) ON CONFLICT (id) DO NOTHING "
            "RETURNING *",
            (data["id"], data["catalog_name"], data["trigger"],
             None if generation is None else _generation(generation)),
        )
        if row is not None:
            return _run(row) or {}, True
        existing = await self._one(
            "SELECT * FROM nova_meta.catalog_sync_runs WHERE id=%s", (data["id"],),
        )
        if existing is None or existing["catalog_name"] != data["catalog_name"]:
            raise MetadataConflict()
        return _run(existing) or {}, False

    async def finish_sync_run(
        self, run_id: str, status: str, *, counts: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """Record a running run's outcome once; a finished run is never rewritten."""
        await self._bound()
        if status not in ("ok", "error"):
            raise ValueError("A finished run is ok or error")
        row = await self._one(
            "UPDATE nova_meta.catalog_sync_runs SET status=%s,counts_json=%s,error=%s "
            "WHERE id=%s AND status='running' RETURNING id",
            (status, json.dumps(counts or {}, sort_keys=True, default=str), error, run_id),
        )
        return row is not None

    async def list_sync_runs(self, catalog: str, limit: int = 20) -> list[dict[str, Any]]:
        await self._bound()
        rows = await self._all(
            "SELECT * FROM nova_meta.catalog_sync_runs WHERE catalog_name=%s "
            "ORDER BY started_at DESC NULLS LAST,id DESC LIMIT %s", (catalog, int(limit)),
        )
        return [row for row in map(_run, rows) if row is not None]

    async def lineage_watermark(self) -> datetime | None:
        await self._bound()
        row = await self._one(
            "SELECT counts_json FROM nova_meta.catalog_sync_runs WHERE catalog_name=%s "
            "AND status='ok' ORDER BY started_at DESC NULLS LAST LIMIT 1", (LINEAGE_RUN,),
        )
        value = _json_object(row["counts_json"]).get("watermark") if row else None
        try:
            return datetime.fromisoformat(value) if isinstance(value, str) else None
        except ValueError:
            return None

    # ── Connections and credentials ───────────────────────────────────────

    async def list_connections(self) -> list[dict[str, Any]]:
        await self._bound()
        rows = await self._all("SELECT * FROM nova_meta.catalog_connections ORDER BY name")
        return [row for row in map(_connection, rows) if row is not None]

    async def get_connection(self, name: str) -> dict[str, Any] | None:
        await self._bound()
        return _connection(await self._one(
            "SELECT * FROM nova_meta.catalog_connections WHERE name=%s", (name,),
        ))

    async def create_connection(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.catalog_connections (name,connector_type,host,port,"
            "database_name,options_json,secret_ref,comment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING *",
            (data["name"], data["connector_type"], data["host"], data.get("port"),
             data.get("database_name"), json.dumps(data.get("options") or {}, sort_keys=True),
             data["secret_ref"], data.get("comment")),
        )
        assert row is not None
        return _connection(row) or {}

    async def update_connection(
        self, name: str, changes: dict[str, Any], *, expected_revision: int,
    ) -> dict[str, Any]:
        await self._bound()
        unknown = set(changes) - set(CONNECTION_DEFINITION)
        if unknown or not changes:
            raise ValueError(f"Unsupported connection changes: {sorted(unknown)}")
        assignments: list[str] = []
        values: list[Any] = []
        for field, value in changes.items():
            if field == "options":
                assignments.append("options_json=%s")
                values.append(json.dumps(value or {}, sort_keys=True))
            else:
                assignments.append(f"{field}=%s")
                values.append(value)
        row = await self._one(
            f"UPDATE nova_meta.catalog_connections SET {','.join(assignments)},"
            "metadata_revision=metadata_revision+1 WHERE name=%s AND metadata_revision=%s "
            "RETURNING *",
            (*values, name, _revision(expected_revision)),
        )
        if row is None:
            raise MetadataConflict()
        return _connection(row) or {}

    async def delete_connection(self, name: str) -> bool:
        """Remove a connection and both of its credentials together."""
        await self._bound()
        for table in _CREDENTIAL_TABLES.values():
            await self.connection.execute(f"DELETE FROM {table} WHERE connection_name=%s", (name,))
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.catalog_connections WHERE name=%s", (name,),
        )
        return cursor.rowcount == 1

    async def has_credentials(self, name: str, *, write: bool = False) -> bool:
        await self._bound()
        row = await self._one(
            f"SELECT connection_name FROM {_CREDENTIAL_TABLES[write]} WHERE connection_name=%s",
            (name,),
        )
        return row is not None

    async def put_credentials(
        self, name: str, username: str, password: str, *, write: bool = False,
    ) -> int:
        """Store an entered credential encrypted; returns its revision only.

        A plain update, then an insert: ``ON CONFLICT DO UPDATE`` would read the
        excluded ciphertext, which a management workload must not be able to
        select. Two first entries racing give one success and one conflict.
        """
        await self._bound()
        if not username or not password:
            raise ValueError("A username and a password are required")
        table = _CREDENTIAL_TABLES[write]
        values = (encrypt(username), encrypt(password), name)
        row = await self._one(
            f"UPDATE {table} SET username_enc=%s,password_enc=%s,"
            "metadata_revision=metadata_revision+1 WHERE connection_name=%s "
            "RETURNING metadata_revision", values,
        )
        if row is None:
            row = await self._one(
                f"INSERT INTO {table} (username_enc,password_enc,connection_name) "
                "VALUES (%s,%s,%s) RETURNING metadata_revision", values,
            )
        assert row is not None
        return int(row["metadata_revision"])

    async def delete_credentials(self, name: str, *, write: bool = False) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            f"DELETE FROM {_CREDENTIAL_TABLES[write]} WHERE connection_name=%s", (name,),
        )
        return cursor.rowcount == 1

    async def read_credentials(self, name: str, *, write: bool = False) -> tuple[str, str] | None:
        """Decrypt a stored credential for engine use only. Never return it to a client."""
        await self._bound()
        row = await self._one(
            f"SELECT username_enc,password_enc FROM {_CREDENTIAL_TABLES[write]} "
            "WHERE connection_name=%s", (name,),
        )
        if row is None:
            return None
        username, password = decrypt(row["username_enc"]), decrypt(row["password_enc"])
        if not username or not password:
            return None
        return username, password

    # ── Securables (fenced) ───────────────────────────────────────────────

    async def get_securables(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.catalog_securables WHERE id=ANY(%s) ORDER BY id",
            (list(dict.fromkeys(ids)),),
        )

    async def securable_children(self, parent_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.catalog_securables WHERE parent_id=%s "
            "ORDER BY ordinal NULLS LAST,column_name", (parent_id,),
        )

    async def securables_in_catalog(self, catalog: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.catalog_securables WHERE catalog_name=%s ORDER BY id",
            (catalog,),
        )

    async def securables_of_type(self, securable_type: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.catalog_securables WHERE securable_type=%s ORDER BY id",
            (securable_type,),
        )

    async def search_securable_names(self, term: str, limit: int) -> list[dict[str, Any]]:
        await self._bound()
        pattern = like_pattern(term)
        return await self._all(
            "SELECT * FROM nova_meta.catalog_securables WHERE lower(object_name) LIKE %s "
            "OR lower(column_name) LIKE %s OR lower(database_name) LIKE %s "
            "OR lower(catalog_name) LIKE %s ORDER BY securable_type,catalog_name,"
            "database_name,object_name,ordinal NULLS LAST LIMIT %s",
            (pattern, pattern, pattern, pattern, int(limit)),
        )

    async def write_securables(
        self, catalog: str, rows: Iterable[dict[str, Any]], *, generation: int,
    ) -> int:
        """Register new, changed or returning securables of one catalog."""
        await self._bound()
        values = [
            (row["id"], row["securable_type"], catalog, row.get("database_name") or "",
             row.get("object_name") or "", row.get("column_name") or "", row.get("parent_id"),
             row.get("data_type"), row.get("ordinal"))
            for row in rows
        ]
        await self._fence(catalog, generation)
        async with self.connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO nova_meta.catalog_securables (id,securable_type,catalog_name,"
                "database_name,object_name,column_name,parent_id,data_type,ordinal) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
                "securable_type=EXCLUDED.securable_type,column_name=EXCLUDED.column_name,"
                "data_type=EXCLUDED.data_type,ordinal=EXCLUDED.ordinal,dropped_at=NULL",
                values,
            )
        await self._unfence()
        return len(values)

    async def mark_securables_dropped(
        self, catalog: str, ids: Sequence[str] | None, *, generation: int | None,
    ) -> int:
        """Tombstone the given securables, or every live one of a dropped catalog."""
        await self._bound()
        await self._fence(catalog, generation)
        if ids is None:
            cursor = await self.connection.execute(
                "UPDATE nova_meta.catalog_securables SET dropped_at=clock_timestamp() "
                "WHERE catalog_name=%s AND dropped_at IS NULL", (catalog,),
            )
        else:
            cursor = await self.connection.execute(
                "UPDATE nova_meta.catalog_securables SET dropped_at=clock_timestamp() "
                "WHERE catalog_name=%s AND id=ANY(%s) AND dropped_at IS NULL",
                (catalog, list(ids)),
            )
        await self._unfence()
        return cursor.rowcount

    # ── Annotations and tags ──────────────────────────────────────────────

    async def annotations_for(self, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        await self._bound()
        rows = await self._all(
            "SELECT * FROM nova_meta.catalog_annotations WHERE securable_id=ANY(%s)",
            (list(dict.fromkeys(ids)),),
        )
        return {row["securable_id"]: row for row in rows}

    async def put_annotation(
        self, securable_id: str, securable_type: str, changes: dict[str, Any], *,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Merge ``changes`` at ``expected_revision``; revision 0 creates the row."""
        await self._bound()
        unknown = set(changes) - set(ANNOTATION_FIELDS)
        if unknown or not changes:
            raise ValueError(f"Unsupported annotation changes: {sorted(unknown)}")
        if _revision(expected_revision, allow_zero=True) == 0:
            values = {field: changes.get(field) for field in ANNOTATION_FIELDS}
            row = await self._one(
                "INSERT INTO nova_meta.catalog_annotations (securable_id,securable_type,"
                "comment,owner_name,owner_type,classification) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (securable_id) DO NOTHING RETURNING *",
                (securable_id, securable_type, *values.values()),
            )
        else:
            row = await self._one(
                "UPDATE nova_meta.catalog_annotations SET "
                + ",".join(f"{field}=%s" for field in changes)
                + ",metadata_revision=metadata_revision+1 WHERE securable_id=%s "
                "AND securable_type=%s AND metadata_revision=%s RETURNING *",
                (*changes.values(), securable_id, securable_type, expected_revision),
            )
        if row is None:
            raise MetadataConflict()
        return row

    async def search_annotations(self, term: str, limit: int = 500) -> list[dict[str, Any]]:
        await self._bound()
        pattern = like_pattern(term)
        return await self._all(
            "SELECT * FROM nova_meta.catalog_annotations WHERE lower(comment) LIKE %s "
            "OR lower(owner_name) LIKE %s OR lower(classification) LIKE %s "
            "ORDER BY securable_id LIMIT %s", (pattern, pattern, pattern, int(limit)),
        )

    async def tags_for(self, ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        await self._bound()
        found: dict[str, list[dict[str, Any]]] = {}
        for row in await self._all(
            "SELECT * FROM nova_meta.config_object_tags WHERE securable_id=ANY(%s) "
            "ORDER BY securable_id,tag_key", (list(dict.fromkeys(ids)),),
        ):
            found.setdefault(row["securable_id"], []).append(row)
        return found

    async def put_tag(
        self, securable_id: str, securable_type: str, key: str, value: str | None, *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Set one tag atomically; with ``expected_revision`` only at that revision."""
        await self._bound()
        guard = ""
        params: list[Any] = [securable_id, key, value, securable_type]
        if expected_revision is not None:
            if _revision(expected_revision, allow_zero=True) == 0:
                guard = " WHERE false"
            else:
                guard = " WHERE t.metadata_revision=%s"
                params.append(expected_revision)
        row = await self._one(
            "INSERT INTO nova_meta.config_object_tags AS t (securable_id,tag_key,tag_value,"
            "securable_type) VALUES (%s,%s,%s,%s) ON CONFLICT (securable_id,tag_key) DO UPDATE "
            "SET tag_value=EXCLUDED.tag_value,metadata_revision=t.metadata_revision+1"
            + guard + " RETURNING *",
            params,
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def delete_tag(self, securable_id: str, key: str) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_object_tags WHERE securable_id=%s AND tag_key=%s",
            (securable_id, key),
        )
        return cursor.rowcount == 1

    async def search_tags(
        self, *, term: str | None = None, key: str | None = None, value: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        await self._bound()
        clauses: list[str] = []
        params: list[Any] = []
        if term:
            pattern = like_pattern(term)
            clauses.append("(lower(tag_key) LIKE %s OR lower(tag_value) LIKE %s)")
            params += [pattern, pattern]
        if key:
            clauses.append("lower(tag_key)=%s")
            params.append(key.casefold())
        if value is not None:
            clauses.append("lower(tag_value)=%s")
            params.append(value.casefold())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return await self._all(
            f"SELECT * FROM nova_meta.config_object_tags{where} "
            "ORDER BY securable_id,tag_key LIMIT %s", (*params, int(limit)),
        )

    # ── Lineage ───────────────────────────────────────────────────────────

    async def lineage_edges_of(self, securable_id: str, limit: int = 200) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.catalog_lineage WHERE source_id=%s OR target_id=%s "
            "ORDER BY last_seen_at DESC NULLS LAST,id LIMIT %s",
            (securable_id, securable_id, int(limit)),
        )

    async def upsert_lineage(self, edges: Iterable[dict[str, Any]]) -> int:
        """Record harvested edges; a span only widens, whatever the arrival order."""
        await self._bound()
        values = [
            (row_id(edge["source_id"], edge["target_id"], "lineage"), edge["source_id"],
             edge["target_id"], edge["source_name"], edge["target_name"], edge["statement_kind"],
             _aware(edge["first_seen_at"]), _aware(edge["last_seen_at"]),
             edge.get("last_query_id"))
            for edge in edges
        ]
        async with self.connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO nova_meta.catalog_lineage (id,source_id,target_id,source_name,"
                "target_name,statement_kind,first_seen_at,last_seen_at,last_query_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
                "source_name=EXCLUDED.source_name,target_name=EXCLUDED.target_name,"
                "statement_kind=EXCLUDED.statement_kind,first_seen_at=EXCLUDED.first_seen_at,"
                "last_seen_at=EXCLUDED.last_seen_at,last_query_id=EXCLUDED.last_query_id",
                values,
            )
        return len(values)

    # ── Semantic View and entity links ────────────────────────────────────

    async def links_for_securable(self, securable_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.catalog_securable_links WHERE securable_id=%s "
            "ORDER BY owner_type,owner_label,id", (securable_id,),
        )

    async def all_links(self) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all("SELECT * FROM nova_meta.catalog_securable_links ORDER BY id")

    async def write_links(self, links: Iterable[dict[str, Any]]) -> int:
        await self._bound()
        values = [
            (link["id"], link["owner_type"], link["owner_id"], link.get("owner_label"),
             link["securable_id"], link["relation_name"])
            for link in links
        ]
        async with self.connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO nova_meta.catalog_securable_links (id,owner_type,owner_id,"
                "owner_label,securable_id,relation_name) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET owner_label=EXCLUDED.owner_label,"
                "relation_name=EXCLUDED.relation_name",
                values,
            )
        return len(values)

    async def delete_links(self, ids: Sequence[str]) -> int:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.catalog_securable_links WHERE id=ANY(%s)", (list(ids),),
        )
        return cursor.rowcount

    async def replace_owner_links(
        self, owner_type: str, owner_id: str, links: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Make one owner's links exactly ``links``, in this transaction."""
        await self._bound()
        if owner_type not in _LINK_OWNERS:
            raise ValueError("Unknown link owner")
        wanted = {link["id"]: link for link in links}
        if any(link["owner_type"] != owner_type or link["owner_id"] != owner_id
               for link in wanted.values()):
            raise ValueError("Every link must belong to the owner being replaced")
        await self.connection.execute(
            "DELETE FROM nova_meta.catalog_securable_links WHERE owner_type=%s AND owner_id=%s "
            "AND id<>ALL(%s)", (owner_type, owner_id, list(wanted)),
        )
        await self.write_links(wanted.values())
        return await self._all(
            "SELECT * FROM nova_meta.catalog_securable_links WHERE owner_type=%s "
            "AND owner_id=%s ORDER BY id", (owner_type, owner_id),
        )
