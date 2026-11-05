"""Semantic Views and entities point at securable ids.

A Semantic View's datasets and an entity's relation are stored by their owners
(``CONFIG_SEMANTIC_VIEW_VERSIONS.definition`` and ``CONFIG_ENTITIES``) as
``[catalog.]database.table`` strings, and those rows stay exactly as they are.
``NOVA_SYSTEM.CATALOG_SECURABLE_LINKS`` records which securable id each one
uses, so a relation's page can show what depends on it and the dependency
survives a drop: the link keeps pointing at the dropped id.

* A Semantic View links the datasets of its active version, or of its newest
  version while none is active. An entity links its relation while it is
  ``ACTIVE``.
* :func:`reconcile_links` rebuilds every link from the owners' current rows; it
  runs after each catalog pass, so links that existed before this table (the
  migration of existing rows) and links of later edits both converge.
  :func:`refresh_owner_links` re-links one owner right after it is written,
  so its relation pages are current at once.
* A link carries no authority. Reading a Semantic View still goes through its
  own access checks, and a caller sees a link only when they can see both the
  relation (engine visibility) and the owner (its own listing rules).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.core.database import db

from .repository import row_id
from .securables import relation_id
from .sync import nova_now

log = logging.getLogger(__name__)

_PK_PROPERTIES = 'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'

CATALOG_SECURABLE_LINKS_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_SECURABLE_LINKS (
    id            VARCHAR(64) NOT NULL,
    owner_type    VARCHAR(32) NOT NULL,
    owner_id      VARCHAR(64) NOT NULL,
    owner_label   VARCHAR(512),
    securable_id  VARCHAR(64) NOT NULL,
    relation_name VARCHAR(1024) NOT NULL,
    updated_at    DATETIME
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
{_PK_PROPERTIES}
"""

SEMANTIC_VIEW = "semantic_view"
ENTITY = "entity"

_COLUMNS = (
    "id", "owner_type", "owner_id", "owner_label", "securable_id", "relation_name",
    "updated_at",
)


def relation_securable(source: str) -> tuple[str, str] | None:
    """``(securable id, catalog.database.table)`` of a dataset/relation string."""
    parts = [part.strip().strip("`") for part in str(source or "").split(".")]
    if len(parts) == 2:
        parts = ["default_catalog", *parts]
    if len(parts) != 3 or not all(parts):
        return None
    return relation_id(*parts), ".".join(parts)


def _link(owner_type: str, owner_id: str, label: str, source: str, at: datetime) -> dict | None:
    resolved = relation_securable(source)
    if resolved is None:
        return None
    securable_id, name = resolved
    return {
        "id": row_id(owner_type, owner_id, securable_id),
        "owner_type": owner_type,
        "owner_id": owner_id,
        "owner_label": label[:512],
        "securable_id": securable_id,
        "relation_name": name,
        "updated_at": at,
    }


def _sources(definition: Any) -> list[str]:
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except (TypeError, ValueError):
            return []
    if not isinstance(definition, dict):
        return []
    return [
        str(dataset.get("source") or "")
        for dataset in definition.get("datasets") or []
        if isinstance(dataset, dict)
    ]


class LinkRepository:
    async def ensure_schema(self) -> None:
        await db.execute_system(CATALOG_SECURABLE_LINKS_DDL)

    async def for_securable(self, securable_id: str) -> list[dict]:
        result = await db.execute_system(
            "SELECT " + ", ".join(_COLUMNS) + " FROM NOVA_SYSTEM.CATALOG_SECURABLE_LINKS "
            "WHERE securable_id = %s ORDER BY owner_type, owner_label",
            [securable_id],
        )
        return [dict(zip(_COLUMNS, row, strict=False)) for row in result.get("rows") or []]

    async def all(self) -> list[dict]:
        result = await db.execute_system(
            "SELECT " + ", ".join(_COLUMNS) + " FROM NOVA_SYSTEM.CATALOG_SECURABLE_LINKS"
        )
        return [dict(zip(_COLUMNS, row, strict=False)) for row in result.get("rows") or []]

    async def replace_owner(self, owner_type: str, owner_id: str, links: Iterable[dict]) -> None:
        wanted = {link["id"]: link for link in links}
        result = await db.execute_system(
            "SELECT id FROM NOVA_SYSTEM.CATALOG_SECURABLE_LINKS "
            "WHERE owner_type = %s AND owner_id = %s",
            [owner_type, owner_id],
        )
        stale = [str(row[0]) for row in result.get("rows") or [] if str(row[0]) not in wanted]
        await self.write(list(wanted.values()))
        await self.delete(stale)

    async def write(self, links: list[dict]) -> None:
        for start in range(0, len(links), 200):
            batch = links[start : start + 200]
            placeholders = "(" + ", ".join(["%s"] * len(_COLUMNS)) + ")"
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CATALOG_SECURABLE_LINKS (" + ", ".join(_COLUMNS)
                + ") VALUES " + ", ".join([placeholders] * len(batch)),
                [link.get(column) for link in batch for column in _COLUMNS],
            )

    async def delete(self, ids: list[str]) -> None:
        for start in range(0, len(ids), 200):
            chunk = ids[start : start + 200]
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CATALOG_SECURABLE_LINKS "
                f"WHERE id IN ({', '.join(['%s'] * len(chunk))})",
                chunk,
            )


link_repo = LinkRepository()


async def _semantic_view_links(at: datetime, view_id: str | None = None) -> list[dict]:
    where, params = ("WHERE v.id = %s", [view_id]) if view_id else ("", [])
    views = await db.execute_system(
        "SELECT v.id, v.catalog_name, v.database_name, v.schema_name, v.name, "
        "COALESCE(v.active_version, latest.version) "
        "FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS v LEFT JOIN ("
        "SELECT view_id, MAX(version) AS version "
        "FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS GROUP BY view_id) latest "
        f"ON latest.view_id = v.id {where}",
        params,
    )
    rows = views.get("rows") or []
    wanted = {(str(row[0]), int(row[5])) for row in rows if row[5] is not None}
    if not wanted:
        return []
    labels = {str(row[0]): ".".join(str(part) for part in row[1:5]) for row in rows}
    definitions = await db.execute_system(
        "SELECT view_id, version, definition FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS"
        + (" WHERE view_id = %s" if view_id else ""),
        params,
    )
    links = []
    for row_view, version, definition in definitions.get("rows") or []:
        if (str(row_view), int(version)) not in wanted:
            continue
        for source in _sources(definition):
            link = _link(SEMANTIC_VIEW, str(row_view), labels[str(row_view)], source, at)
            if link:
                links.append(link)
    return links


async def _entity_links(at: datetime, entity_id: str | None = None) -> list[dict]:
    result = await db.execute_system(
        "SELECT id, catalog_name, database_name, schema_name, name, relation_name "
        "FROM NOVA_SYSTEM.CONFIG_ENTITIES WHERE status = 'ACTIVE'"
        + (" AND id = %s" if entity_id else ""),
        [entity_id] if entity_id else [],
    )
    links = []
    for row in result.get("rows") or []:
        link = _link(ENTITY, str(row[0]), ".".join(str(p) for p in row[1:5]), str(row[5]), at)
        if link:
            links.append(link)
    return links


_LOADERS = {SEMANTIC_VIEW: _semantic_view_links, ENTITY: _entity_links}


async def reconcile_links(repo: LinkRepository | None = None) -> dict[str, int]:
    """Rebuild every link from the owners' current rows.

    When an owner table cannot be read, nothing is changed: a missing table
    must not erase the links that were recorded.
    """
    repo = repo or link_repo
    now = nova_now()
    wanted: dict[str, dict] = {}
    for loader in _LOADERS.values():
        wanted.update({link["id"]: link for link in await loader(now)})
    existing = {link["id"]: link for link in await repo.all()}
    changed = [
        link for key, link in wanted.items()
        if key not in existing or existing[key].get("owner_label") != link["owner_label"]
    ]
    await repo.write(changed)
    await repo.delete([key for key in existing if key not in wanted])
    return {"links": len(wanted), "links_written": len(changed)}


async def refresh_owner_links(owner_type: str, owner_id: str) -> None:
    """Re-link one Semantic View or entity from its current rows. Best-effort.

    Owners call this after a create, a new version, a publish, a deprecation,
    or a drop, so the relation page is current before the next catalog pass.
    """
    try:
        links = await _LOADERS[owner_type](nova_now(), owner_id)
        await link_repo.replace_owner(owner_type, owner_id, links)
    except Exception:  # noqa: BLE001 - the next catalog pass reconciles
        log.warning("Could not link %s %s to securables", owner_type, owner_id, exc_info=True)
