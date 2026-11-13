"""Sync engine metadata into the catalog store.

A sync reads the engine as the control plane (system pool): which catalogs
exist, their databases, relations, and columns. It writes descriptive metadata
only. It is not an authorization source, so reading as the system is allowed
here: the explorer and every other user-facing read still list objects through
the caller's own session before showing anything the store describes.

* :func:`reconcile_catalogs` adds catalogs the engine has but the store does
  not (one created in raw SQL is recorded with ``source='engine'``), marks the
  ones that disappeared as dropped along with their objects, then syncs each
  live catalog.
* :func:`sync_catalog` refreshes one catalog: objects seen now are upserted,
  objects no longer seen get ``dropped_at``, and columns are rewritten only
  when an object's column fingerprint changed. Every run is recorded in
  ``nova_meta.catalog_sync_runs``; an error message is redacted before it is
  stored.
* **Fenced.** A sync first claims its catalog (``claim_sync`` advances the
  catalog's sync generation) and records its run under that generation; the
  inventory, securables, run outcome and catalog status are then written in
  one transaction naming the generation. A later claim makes every write of
  this harvest stale, so an older harvest never overwrites a newer one. The
  engine is read between the two transactions, never inside one.
* A foreign catalog's relations are classified by its source
  (:func:`classify_from_source`): the engine reports every JDBC relation as a
  table, so the source's own metadata says which are views and materialized
  views. The source is read with the connection's credential, by the control
  plane, and only kinds are kept.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.common.identifiers import check_identifier
from app.common.sql_guard import CredentialsRedactionError, redact_sql_credentials
from app.core.database import configured_timezone, db
from app.core.metadata_db import MetadataConflict
from app.modules.access_control.security_context import SecurityContext

from . import securables
from .connectors import INTERNAL_CATALOG
from .metadata_repository import CatalogMetadataRepository, catalog_transaction
from .repository import row_id

log = logging.getLogger(__name__)

SYSTEM_DBS = frozenset({"_statistics_", "information_schema", "sys"})

#: Columns are described one relation at a time when a catalog has no
#: ``information_schema``; past this many relations a sync records names only.
MAX_DESCRIBED_RELATIONS = 2000

_ERROR_LIMIT = 2000

Execute = Callable[..., Awaitable[dict]]
#: Sets each relation's kind from the catalog's source; see :func:`classify_from_source`.
Classifier = Callable[[dict | None, "Harvest", SecurityContext], Awaitable[None]]

#: The run error of a harvest a newer claim made stale.
SUPERSEDED = "A newer sync of this catalog superseded this one"


def nova_now() -> datetime:
    """Now as the engine's ``NOW()`` writes it: naive, in the deployment timezone.

    Every Nova engine session pins ``time_zone`` to the deployment zone, so
    naive ``DATETIME`` values read from the engine share that clock.
    """
    return datetime.now(ZoneInfo(configured_timezone())).replace(tzinfo=None)


def engine_time(value: datetime) -> datetime:
    """An engine ``DATETIME`` (naive, deployment zone) as an aware time."""
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=ZoneInfo(configured_timezone()))


def to_engine_time(value: datetime) -> datetime:
    """An aware time as the naive deployment-zone value the engine compares."""
    if value.tzinfo is None:
        return value
    return value.astimezone(ZoneInfo(configured_timezone())).replace(tzinfo=None)


def redact_error(exc: BaseException | str) -> str:
    """An error message safe to store: credentials redacted, length bounded.

    When the redactor cannot vouch for the text it refuses it, and the stored
    message becomes a fixed one rather than the sync run failing on its own
    error handling.
    """
    try:
        return redact_sql_credentials(str(exc))[:_ERROR_LIMIT]
    except CredentialsRedactionError:
        return "The catalog could not be read; the error was withheld because it held credentials."


def catalog_kind(engine_type: str) -> str:
    return "native" if (engine_type or "").strip().lower() == "internal" else "external"


@dataclass
class Relation:
    database: str
    name: str
    object_type: str
    engine: str | None = None
    comment: str | None = None
    row_count: int | None = None
    data_size: int | None = None
    columns: list[dict] = field(default_factory=list)

    def fingerprint(self) -> str:
        payload = json.dumps(self.columns, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class Harvest:
    databases: list[str]
    relations: list[Relation]


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _nullable(value: Any) -> bool | None:
    if value is None:
        return None
    return str(value).strip().upper() in {"YES", "TRUE", "1"}


def _relation_type(table_type: str | None, name: str, mvs: set[str]) -> str:
    if name in mvs:
        return "materialized_view"
    return "view" if "VIEW" in (table_type or "").upper() else "table"


class EngineHarvester:
    """Reads one catalog's metadata from the engine on the system pool."""

    def __init__(self, execute: Execute | None = None) -> None:
        self._execute = execute or db.execute_system

    async def catalogs(self) -> list[dict]:
        result = await self._execute("SHOW CATALOGS")
        return [
            {
                "name": str(row[0]),
                "type": str(row[1]) if len(row) > 1 and row[1] else "Internal",
                "comment": row[2] if len(row) > 2 else None,
            }
            for row in result.get("rows") or []
        ]

    async def harvest(self, catalog: str) -> Harvest:
        catalog = check_identifier(catalog, field="catalog")
        databases = [
            str(row[0])
            for row in (await self._execute(f"SHOW DATABASES FROM `{catalog}`")).get("rows") or []
            if str(row[0]) not in SYSTEM_DBS
        ]
        try:
            relations = await self._from_information_schema(catalog, databases)
        except Exception as exc:  # noqa: BLE001 - fall back to SHOW/DESC
            log.debug("information_schema unavailable for %s: %s", catalog, exc)
            relations = await self._from_show(catalog, databases)
        return Harvest(databases=databases, relations=relations)

    async def _from_information_schema(
        self, catalog: str, databases: list[str]
    ) -> list[Relation]:
        if not databases:
            return []
        schema = (
            "information_schema"
            if catalog == INTERNAL_CATALOG
            else f"`{catalog}`.information_schema"
        )
        placeholders = ", ".join(["%s"] * len(databases))
        tables = await self._execute(
            "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_ROWS, "
            f"DATA_LENGTH, TABLE_COMMENT FROM {schema}.tables "
            f"WHERE TABLE_SCHEMA IN ({placeholders})",
            databases,
        )
        columns = await self._execute(
            "SELECT TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, "
            f"COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT FROM {schema}.columns "
            f"WHERE TABLE_SCHEMA IN ({placeholders}) "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION",
            databases,
        )
        mvs: set[tuple[str, str]] = set()
        if catalog == INTERNAL_CATALOG:
            with_mvs = await self._execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.materialized_views "
                f"WHERE TABLE_SCHEMA IN ({placeholders})",
                databases,
            )
            mvs = {(str(r[0]), str(r[1])) for r in with_mvs.get("rows") or []}

        by_key: dict[tuple[str, str], Relation] = {}
        for row in tables.get("rows") or []:
            database, name = str(row[0]), str(row[1])
            by_key[(database, name)] = Relation(
                database=database,
                name=name,
                object_type=_relation_type(
                    row[2], name, {n for d, n in mvs if d == database}
                ),
                engine=row[3],
                row_count=_int(row[4]),
                data_size=_int(row[5]),
                comment=row[6] or None,
            )
        for row in columns.get("rows") or []:
            relation = by_key.get((str(row[0]), str(row[1])))
            if relation is None:
                continue
            relation.columns.append(
                {
                    "ordinal": _int(row[2]) or len(relation.columns) + 1,
                    "column_name": str(row[3]),
                    "data_type": row[4],
                    "is_nullable": _nullable(row[5]),
                    "column_comment": row[6] or None,
                }
            )
        return list(by_key.values())

    async def _from_show(self, catalog: str, databases: list[str]) -> list[Relation]:
        relations: list[Relation] = []
        for database in databases:
            try:
                database = check_identifier(database, field="database")
                result = await self._execute(f"SHOW FULL TABLES FROM `{catalog}`.`{database}`")
            except Exception as exc:  # noqa: BLE001 - an unreadable database is skipped
                log.debug("Cannot list %s.%s: %s", catalog, database, exc)
                continue
            for row in result.get("rows") or []:
                name = str(row[0])
                relations.append(
                    Relation(
                        database=database,
                        name=name,
                        object_type=_relation_type(row[1] if len(row) > 1 else None, name, set()),
                    )
                )
        for relation in relations[:MAX_DESCRIBED_RELATIONS]:
            try:
                name = check_identifier(relation.name, field="table name")
                described = await self._execute(
                    f"DESC `{catalog}`.`{relation.database}`.`{name}`"
                )
            except Exception as exc:  # noqa: BLE001 - names without columns
                log.debug("Cannot describe %s.%s.%s: %s", catalog, relation.database,
                          relation.name, exc)
                continue
            relation.columns = [
                {
                    "ordinal": index,
                    "column_name": str(row[0]),
                    "data_type": row[1] if len(row) > 1 else None,
                    "is_nullable": _nullable(row[2]) if len(row) > 2 else None,
                    "column_comment": (row[6] or None) if len(row) > 6 else None,
                }
                for index, row in enumerate(described.get("rows") or [], start=1)
            ]
        return relations


def _without_system_schemas(harvest: Harvest, catalog: dict | None) -> Harvest:
    """Drop a source's own catalog schemas (Postgres ``pg_catalog``, ...)."""
    from .sources import describe_catalog_source

    hidden = describe_catalog_source((catalog or {}).get("catalog_type")).system_schemas
    if not hidden:
        return harvest
    return Harvest(
        databases=[db for db in harvest.databases if db not in hidden],
        relations=[r for r in harvest.relations if r.database not in hidden],
    )


async def classify_from_source(
    catalog: dict | None, harvest: Harvest, actor: SecurityContext
) -> None:
    """Give a foreign catalog's relations their kinds from the source itself.

    Only a catalog built from a Nova connection is classified. When the source
    cannot be read, relations keep the kinds the last sync stored, so a brief
    outage does not move views back under tables. The connection and its
    stored credential are read in a short read-only transaction; the source is
    read after it closes.
    """
    from .connections import CONNECTION_PROPERTY, connection_service, endpoint_of
    from .sources import SOURCES

    connection_name = ((catalog or {}).get("properties") or {}).get(CONNECTION_PROPERTY)
    if not connection_name or not harvest.relations:
        return
    try:
        async with catalog_transaction(actor, read_only=True) as repo:
            connection = await repo.get_connection(connection_name)
            stored = (
                await connection_service.stored_credentials(connection, repo)
                if connection is not None else None
            )
        if connection is None:
            return
        source = SOURCES.get(connection["connector_type"])
        if source is None:
            return
        kinds = await source.read_objects(
            endpoint_of(connection),
            await connection_service.credentials_for(connection, stored),
            harvest.databases,
        )
    except Exception as exc:  # noqa: BLE001 - the engine's harvest still syncs
        log.warning("Cannot read object kinds of %s from its source: %s",
                    catalog and catalog.get("name"), redact_error(exc))
        async with catalog_transaction(actor, read_only=True) as repo:
            rows = await repo.list_objects(str((catalog or {}).get("name")))
        stored_objects = {
            (row["database_name"], row["object_name"]): row
            for row in rows
            if row["object_type"] != "database"
        }
        for relation in harvest.relations:
            previous = stored_objects.get((relation.database, relation.name))
            if previous:
                relation.object_type = previous["object_type"]
                relation.engine = previous.get("engine") or relation.engine
        return
    for relation in harvest.relations:
        kind = kinds.get((relation.database, relation.name))
        if kind is not None:
            relation.object_type = kind.object_type
            relation.engine = kind.label


def _unrecorded(name: str, trigger: str, run_id: str, error: str) -> dict:
    """A run that could not be recorded (no catalog row to claim)."""
    return {
        "id": run_id, "catalog_name": name, "trigger": trigger, "status": "error",
        "started_at": None, "finished_at": None, "counts": {}, "error": error,
    }


async def _finish(
    actor: SecurityContext, name: str, run: dict, generation: int, error: str,
) -> dict:
    """Record a failed or superseded run and, if still current, the catalog's error."""
    try:
        async with catalog_transaction(actor) as repo:
            await repo.record_sync_status(name, generation=generation, status="error",
                                          error=error)
            await repo.finish_sync_run(run["id"], "error", error=error)
            return await repo.get_sync_run(run["id"]) or {**run, "status": "error",
                                                           "error": error}
    except Exception:  # noqa: BLE001 - the run stays 'running' and is visible as such
        log.warning("Could not record the failed sync of %s", name, exc_info=True)
        return {**run, "status": "error", "error": error, "counts": {}}


async def sync_catalog(
    name: str,
    *,
    actor: SecurityContext,
    trigger: str = "schedule",
    harvester: EngineHarvester | None = None,
    classifier: Classifier = classify_from_source,
) -> dict:
    """Refresh one catalog in the store and record the run. Returns the run.

    ``actor`` is the caller of a manual refresh or the refresher's service
    principal; the engine is read on the system pool either way.
    """
    harvester = harvester or EngineHarvester()
    run_id = str(uuid.uuid4())
    async with catalog_transaction(actor) as repo:
        catalog = await repo.get_catalog(name)
        generation = await repo.claim_sync(name) if catalog is not None else None
        if generation is None:
            return _unrecorded(name, trigger, run_id,
                               f"Catalog '{name}' is not recorded yet; refresh all catalogs")
        run, _ = await repo.create_sync_run_once(
            {"id": run_id, "catalog_name": name, "trigger": trigger,
             "sync_generation": generation}
        )
    try:
        harvest = _without_system_schemas(await harvester.harvest(name), catalog)
        await classifier(catalog, harvest, actor)
    except Exception as exc:  # noqa: BLE001 - recorded on the run and the catalog
        error = redact_error(exc)
        log.warning("Catalog sync of %s failed: %s", name, error)
        return await _finish(actor, name, run, generation, error)
    try:
        async with catalog_transaction(actor) as repo:
            counts = await _apply(repo, name, harvest, generation)
            counts.update(await securables.record_harvest(repo, name, harvest, generation))
            await repo.record_sync_status(name, generation=generation, status="ok")
            await repo.finish_sync_run(run_id, "ok", counts=counts)
            return await repo.get_sync_run(run_id) or {**run, "status": "ok", "counts": counts}
    except MetadataConflict:
        log.info("Catalog sync of %s was superseded by a newer one", name)
        return await _finish(actor, name, run, generation, SUPERSEDED)
    except Exception as exc:  # noqa: BLE001 - recorded on the run and the catalog
        error = redact_error(exc)
        log.warning("Catalog sync of %s could not be stored: %s", name, error)
        return await _finish(actor, name, run, generation, error)


async def _apply(
    repo: CatalogMetadataRepository, catalog: str, harvest: Harvest, generation: int
) -> dict[str, int]:
    existing = {row["id"]: row for row in await repo.list_objects(catalog, include_dropped=True)}
    seen: dict[str, dict] = {}

    for database in harvest.databases:
        seen[row_id(catalog, database, "", "database")] = {
            "database_name": database,
            "object_name": "",
            "object_type": "database",
        }
    changed_columns = 0
    first_seen_columns: list[dict] = []
    for relation in harvest.relations:
        key = row_id(catalog, relation.database, relation.name, "relation")
        fingerprint = relation.fingerprint()
        previous = existing.get(key)
        seen[key] = {
            "database_name": relation.database,
            "object_name": relation.name,
            "object_type": relation.object_type,
            "engine": relation.engine,
            "comment": relation.comment,
            "row_count": relation.row_count,
            "data_size": relation.data_size,
            "fingerprint": fingerprint,
        }
        if previous is None:
            # Nothing stored for it yet: batch its columns with the others.
            first_seen_columns.extend(
                {**column, "database_name": relation.database, "object_name": relation.name}
                for column in relation.columns
            )
            changed_columns += 1
        elif (
            previous.get("fingerprint") != fingerprint
            or previous.get("dropped_at") is not None
        ):
            await repo.replace_columns(catalog, relation.database, relation.name,
                                       relation.columns, generation=generation)
            changed_columns += 1
    if first_seen_columns:
        await repo.insert_columns(catalog, first_seen_columns, generation=generation)
    if seen:
        await repo.upsert_objects(catalog, seen.values(), generation=generation)

    gone = [
        key for key, row in existing.items()
        if key not in seen and row.get("dropped_at") is None
    ]
    if gone:
        await repo.mark_objects_dropped(catalog, gone, generation=generation)
    for key in gone:
        row = existing[key]
        if row.get("object_type") != "database":
            await repo.replace_columns(catalog, row["database_name"], row["object_name"], [],
                                       generation=generation)

    return {
        "databases": len(harvest.databases),
        "relations": len(harvest.relations),
        "columns_rewritten": changed_columns,
        "dropped": len(gone),
    }


def _type_changed(stored: dict, engine_type: str) -> bool:
    """Whether the engine now reports a different type for a stored catalog.

    A foreign catalog stores its source type (``postgres``, ``snowflake``, ...)
    while the engine reports every one of them as ``jdbc``; that is the same
    catalog, not a change.
    """
    engine_type = (engine_type or "").lower()
    stored_type = (stored.get("catalog_type") or "").lower()
    if _connection_of(stored) and engine_type == "jdbc":
        return False
    return stored_type != engine_type


def _connection_of(stored: dict) -> str | None:
    """The Nova connection a foreign catalog was built from, if any."""
    from .connections import CONNECTION_PROPERTY

    return (stored.get("properties") or {}).get(CONNECTION_PROPERTY)


async def _heal_foreign(repo: CatalogMetadataRepository, stored: dict) -> None:
    """Restore a foreign catalog's source type and kind from its connection.

    Earlier syncs replaced them with the engine's ``jdbc``/``external``.
    """
    name = _connection_of(stored)
    if not name:
        return
    connection = await repo.get_connection(name)
    if connection is None:
        return
    source_type = connection["connector_type"]
    if (stored.get("kind"), stored.get("catalog_type")) != ("foreign", source_type):
        await repo.update_catalog(
            stored["name"], {"kind": "foreign", "catalog_type": source_type},
            expected_revision=stored["metadata_revision"],
        )


async def reconcile_catalogs(
    *,
    actor: SecurityContext,
    trigger: str = "schedule",
    harvester: EngineHarvester | None = None,
) -> list[dict]:
    """Match the store to the engine's catalog list, then sync each live catalog.

    The engine's list is read first; the catalog rows, their tombstones and the
    retirement of vanished catalogs' inventory are then written in one
    transaction, each under the revision or sync generation it read or claimed.
    """
    harvester = harvester or EngineHarvester()
    engine = {row["name"]: row for row in await harvester.catalogs()}
    async with catalog_transaction(actor) as repo:
        stored = {row["name"]: row for row in await repo.list_catalogs(include_dropped=True)}
        for name, row in engine.items():
            current = stored.get(name)
            if current is None:
                await repo.create_catalog(
                    {
                        "name": name,
                        "kind": catalog_kind(row["type"]),
                        "catalog_type": row["type"].lower(),
                        "managed": False,
                        "source": "engine",
                        "comment": row.get("comment"),
                    }
                )
            elif current.get("dropped_at") is None and _connection_of(current):
                await _heal_foreign(repo, current)
            elif current.get("dropped_at") is not None or _type_changed(current, row["type"]):
                await repo.update_catalog(
                    name,
                    {"kind": catalog_kind(row["type"]), "catalog_type": row["type"].lower()},
                    expected_revision=current["metadata_revision"],
                    revive=current.get("dropped_at") is not None,
                )
        for name, row in stored.items():
            if name not in engine and row.get("dropped_at") is None:
                await repo.drop_catalog(name, expected_revision=row["metadata_revision"])
        registered = {
            str(row["catalog_name"]): row for row in await repo.securables_of_type("catalog")
        }
        # A live catalog has its catalog securable even when its sync fails.
        for name in sorted(engine):
            current_securable = registered.get(name)
            if current_securable is None or current_securable.get("dropped_at") is not None:
                generation = await repo.claim_sync(name)
                if generation is not None:
                    root = securables.Securable(
                        id=securables.catalog_id(name), securable_type="catalog",
                        catalog_name=name,
                    )
                    await repo.write_securables(name, [root.as_row()], generation=generation)
        # Objects and securables of a catalog the engine no longer has,
        # including one dropped through Nova before this pass, are retired with it.
        live_securables = {
            name for name, row in registered.items() if row.get("dropped_at") is None
        }
        vanished = (await repo.catalogs_with_live_objects() | live_securables) - engine.keys()
        for name in sorted(vanished):
            generation = await repo.claim_sync(name)
            await repo.retire_catalog_objects(name, generation=generation)
            await repo.mark_securables_dropped(name, None, generation=generation)

    runs = []
    for name in engine:
        try:
            runs.append(
                await sync_catalog(name, actor=actor, trigger=trigger, harvester=harvester)
            )
        except Exception:  # noqa: BLE001 - one catalog never stops the others
            log.warning("Catalog sync of %s could not be recorded", name, exc_info=True)
    return runs
