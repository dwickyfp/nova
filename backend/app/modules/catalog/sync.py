"""Sync engine metadata into the catalog store.

A sync reads the engine as the control plane (system pool): which catalogs
exist, their databases, relations, and columns. It writes descriptive metadata
only. It is not an authorization source, so reading as the system is allowed
here: the explorer and every other user-facing read still list objects through
the caller's own session before showing anything the store describes.

* :func:`reconcile_catalogs` adds catalogs the engine has but the store does
  not (one created in raw SQL is recorded with ``source='engine'``), marks the
  ones that disappeared as dropped, then syncs each live catalog.
* :func:`sync_catalog` refreshes one catalog: objects seen now are upserted,
  objects no longer seen get ``dropped_at``, and columns are rewritten only
  when an object's column fingerprint changed. Every run is recorded in
  ``CATALOG_SYNC_RUNS``; an error message is redacted before it is stored.
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
from app.common.sql_guard import redact_sql_credentials
from app.core.database import configured_timezone, db

from .connectors import INTERNAL_CATALOG
from .repository import CatalogRepository, catalog_repo, row_id

log = logging.getLogger(__name__)

SYSTEM_DBS = frozenset({"_statistics_", "information_schema", "sys"})

#: Columns are described one relation at a time when a catalog has no
#: ``information_schema``; past this many relations a sync records names only.
MAX_DESCRIBED_RELATIONS = 2000

_ERROR_LIMIT = 2000

Execute = Callable[..., Awaitable[dict]]


def nova_now() -> datetime:
    """Now as ``NOW()`` writes it: naive, in the deployment timezone.

    Every Nova session pins ``time_zone`` to the deployment zone, so naive
    ``DATETIME`` values across ``NOVA_SYSTEM`` share that clock.
    """
    return datetime.now(ZoneInfo(configured_timezone())).replace(tzinfo=None)


def redact_error(exc: BaseException | str) -> str:
    """An error message safe to store: credentials redacted, length bounded."""
    return redact_sql_credentials(str(exc))[:_ERROR_LIMIT]


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


async def sync_catalog(
    name: str,
    *,
    trigger: str = "schedule",
    repo: CatalogRepository | None = None,
    harvester: EngineHarvester | None = None,
) -> dict:
    """Refresh one catalog in the store and record the run. Returns the run."""
    repo = repo or catalog_repo
    harvester = harvester or EngineHarvester()
    started = nova_now()
    run: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "catalog_name": name,
        "trigger": trigger,
        "status": "running",
        "started_at": started,
    }
    catalog = await repo.get_catalog(name)
    try:
        harvest = await harvester.harvest(name)
        counts = await _apply(repo, name, harvest, nova_now())
    except Exception as exc:  # noqa: BLE001 - recorded on the run and the catalog
        error = redact_error(exc)
        log.warning("Catalog sync of %s failed: %s", name, error)
        run.update(status="error", finished_at=nova_now(), error=error, counts={})
        if catalog is not None:
            await repo.upsert_catalog(
                {**catalog, "status": "error", "last_sync_error": error, "updated_at": nova_now()}
            )
        await repo.write_sync_run(run)
        return run
    finished = nova_now()
    run.update(status="ok", finished_at=finished, counts=counts)
    if catalog is not None:
        await repo.upsert_catalog(
            {
                **catalog,
                "status": "ok",
                "last_sync_at": finished,
                "last_sync_error": None,
                "updated_at": finished,
            }
        )
    await repo.write_sync_run(run)
    return run


async def _apply(
    repo: CatalogRepository, catalog: str, harvest: Harvest, now: datetime
) -> dict[str, int]:
    existing = {row["id"]: row for row in await repo.list_objects(catalog, include_dropped=True)}
    seen: dict[str, dict] = {}

    for database in harvest.databases:
        key = row_id(catalog, database, "", "database")
        seen[key] = {
            "id": key,
            "catalog_name": catalog,
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
            "id": key,
            "catalog_name": catalog,
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
                {**column, "catalog_name": catalog, "database_name": relation.database,
                 "object_name": relation.name}
                for column in relation.columns
            )
            changed_columns += 1
        elif (
            previous.get("fingerprint") != fingerprint
            or previous.get("dropped_at") is not None
        ):
            await repo.replace_columns(catalog, relation.database, relation.name, relation.columns)
            changed_columns += 1
    await repo.insert_columns(first_seen_columns)

    rows = []
    for key, row in seen.items():
        previous = existing.get(key)
        first_seen = previous.get("first_seen_at") if previous else None
        if previous is not None and previous.get("dropped_at") is not None:
            first_seen = None
        rows.append({**row, "first_seen_at": first_seen or now, "last_seen_at": now,
                     "dropped_at": None})
    await repo.upsert_objects(rows)

    gone = [
        key for key, row in existing.items()
        if key not in seen and row.get("dropped_at") is None
    ]
    await repo.mark_objects_dropped(gone, now)
    for key in gone:
        row = existing[key]
        if row.get("object_type") != "database":
            await repo.delete_columns(catalog, row["database_name"], row["object_name"])

    return {
        "databases": len(harvest.databases),
        "relations": len(harvest.relations),
        "columns_rewritten": changed_columns,
        "dropped": len(gone),
    }


async def reconcile_catalogs(
    *,
    trigger: str = "schedule",
    repo: CatalogRepository | None = None,
    harvester: EngineHarvester | None = None,
) -> list[dict]:
    """Match the store to the engine's catalog list, then sync each live catalog."""
    repo = repo or catalog_repo
    harvester = harvester or EngineHarvester()
    now = nova_now()
    engine = {row["name"]: row for row in await harvester.catalogs()}
    stored = {row["name"]: row for row in await repo.list_catalogs(include_dropped=True)}

    for name, row in engine.items():
        current = stored.get(name)
        if current is None:
            await repo.upsert_catalog(
                {
                    "name": name,
                    "kind": catalog_kind(row["type"]),
                    "catalog_type": row["type"].lower(),
                    "managed": False,
                    "source": "engine",
                    "comment": row.get("comment"),
                    "created_at": now,
                    "updated_at": now,
                }
            )
        elif current.get("dropped_at") is not None or (
            current.get("catalog_type") or ""
        ) != row["type"].lower():
            await repo.upsert_catalog(
                {
                    **current,
                    "kind": catalog_kind(row["type"]),
                    "catalog_type": row["type"].lower(),
                    "dropped_at": None,
                    "updated_at": now,
                }
            )
    for name, row in stored.items():
        if name not in engine and row.get("dropped_at") is None:
            await repo.upsert_catalog({**row, "dropped_at": now, "updated_at": now})

    runs = []
    for name in engine:
        try:
            runs.append(await sync_catalog(name, trigger=trigger, repo=repo, harvester=harvester))
        except Exception:  # noqa: BLE001 - one catalog never stops the others
            log.warning("Catalog sync of %s could not be recorded", name, exc_info=True)
    return runs
