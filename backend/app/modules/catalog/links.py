"""Semantic Views and entities point at securable ids.

A Semantic View's datasets and an entity's relation are stored by their owners
(``config_semantic_view_versions.definition`` and ``config_entities`` in the
PostgreSQL metadata store) as
``[catalog.]database.table`` strings, and those rows stay exactly as they are.
``nova_meta.catalog_securable_links`` records which securable id each one
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

from .metadata_repository import catalog_transaction
from .repository import row_id
from .securables import relation_id
from .sync import nova_now

log = logging.getLogger(__name__)

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
    async def for_securable(self, securable_id: str) -> list[dict]:
        async with catalog_transaction(read_only=True) as repo:
            return await repo.links_for_securable(securable_id)

    async def all(self) -> list[dict]:
        async with catalog_transaction(read_only=True) as repo:
            return await repo.all_links()

    async def replace_owner(self, owner_type: str, owner_id: str, links: Iterable[dict]) -> None:
        async with catalog_transaction() as repo:
            await repo.replace_owner_links(owner_type, owner_id, links)

    async def write(self, links: list[dict]) -> None:
        async with catalog_transaction() as repo:
            await repo.write_links(links)

    async def delete(self, ids: list[str]) -> None:
        async with catalog_transaction() as repo:
            await repo.delete_links(ids)


link_repo = LinkRepository()


def _owner_reader():
    """Read source metadata as the caller or the explicitly bound maintenance actor."""
    from app.core.metadata_actor import current_actor
    from app.modules.intelligence.metadata_repository import intelligence_metadata

    return intelligence_metadata(current_actor(), read_only=True)


async def _semantic_view_links(at: datetime, view_id: str | None = None) -> list[dict]:
    async with _owner_reader() as repo:
        rows = await repo.view_link_sources(view_id)
    links = []
    for row in rows:
        owner = str(row["id"])
        label = ".".join(
            str(row[part]) for part in ("catalog_name", "database_name", "schema_name", "name")
        )
        for source in _sources(row["definition"]):
            link = _link(SEMANTIC_VIEW, owner, label, source, at)
            if link:
                links.append(link)
    return links


async def _entity_links(at: datetime, entity_id: str | None = None) -> list[dict]:
    async with _owner_reader() as repo:
        if entity_id:
            entity = await repo.get_entity(entity_id)
            rows = [entity] if entity else []
        else:
            rows = await repo.list_entities()
    links = []
    for row in rows:
        label = ".".join(
            str(row[part]) for part in ("catalog_name", "database_name", "schema_name", "name")
        )
        link = _link(ENTITY, str(row["id"]), label, str(row["relation_name"]), at)
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
