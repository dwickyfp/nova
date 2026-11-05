"""Business metadata, search, and lineage, as one caller may see and change them.

**Reads.** Every securable, search hit, lineage endpoint, and dependent link is
cut by what the caller's own engine session lists (:class:`.access.CallerView`).
The store only describes; it never makes something visible.

**Writes.** Changing a tag, comment, or classification needs, on top of seeing
the securable:

* an active security-admin role (the gate access-control administration uses), or
* for a live relation or one of its columns, ``ALTER`` or ``ALL`` on that
  relation for the active role in Ranger, read through
  :meth:`AccessControlService.effective_access`, the same Ranger reading that
  Verify Access uses. Nova keeps no permission table of its own.

Setting the owner is a governance assignment and needs a security-admin role.
Catalogs and databases are edited by security admins only.

Every change and every refusal is written to ``AUDIT_LOG`` (``event_type``
``CATALOG``). The row names the securable and the field. It never contains a
tag value or comment text, and the summary still passes credential redaction.
"""

from __future__ import annotations

import logging
from typing import Any

from app.common.audit import write_audit_log
from app.common.sql_guard import redact_sql_credentials
from app.modules.access_control.security_context import SecurityContext, SecurityContextError

from .access import CallerView
from .lineage import LineageRepository, lineage_repo
from .links import ENTITY, SEMANTIC_VIEW, LinkRepository, link_repo
from .metadata import MetadataRepository, metadata_repo
from .metadata_schemas import (
    AnnotationWrite,
    LineageNode,
    LineageResponse,
    LinkedOwner,
    SearchHit,
    SearchResponse,
    SecurableDetail,
    SecurableSummary,
    Tag,
    TagWrite,
)
from .securables import (
    RELATION_TYPES,
    SECURABLE_TYPES,
    Securable,
    SecurableRepository,
    catalog_id,
    column_id,
    database_id,
    relation_id,
    securable_repo,
)
from .sync import nova_now

log = logging.getLogger(__name__)

#: Rows read per search source before visibility filtering.
SEARCH_CANDIDATES = 500
#: Dependents checked against their owner's own listing rules per detail page.
MAX_LINKS_CHECKED = 20

_EDIT_GRANTS = frozenset({"ALTER", "ALL"})


class MetadataNotFound(LookupError):
    """No securable with that id is visible to the caller."""


class MetadataDenied(PermissionError):
    """The caller may see the securable but not make this change."""


class MetadataInvalid(ValueError):
    """The change does not apply to this securable."""


def _status(row: Securable) -> str:
    return "dropped" if row.dropped_at is not None else "live"


class GovernanceService:
    def __init__(
        self,
        securables: SecurableRepository | None = None,
        metadata: MetadataRepository | None = None,
        lineage: LineageRepository | None = None,
        links: LinkRepository | None = None,
    ) -> None:
        self.securables = securables or securable_repo
        self.metadata = metadata or metadata_repo
        self.edges = lineage or lineage_repo
        self.links = links or link_repo

    # ── Lookup ──────────────────────────────────────────────────

    async def _visible(self, securable_id: str, view: CallerView) -> Securable:
        row = await self.securables.get(securable_id)
        if row is None or not await view.can_see(row):
            raise MetadataNotFound(securable_id)
        return row

    async def resolve(
        self,
        user: dict,
        *,
        catalog: str,
        database: str | None = None,
        object_name: str | None = None,
        column: str | None = None,
    ) -> SecurableDetail:
        """The securable a name currently points at, as :meth:`describe` shows it."""
        if column and not (database and object_name):
            raise MetadataInvalid("A column needs its database and relation")
        if object_name and not database:
            raise MetadataInvalid("A relation needs its database")
        if column:
            key = column_id(catalog, database or "", object_name or "", column)
        elif object_name:
            key = relation_id(catalog, database or "", object_name)
        elif database:
            key = database_id(catalog, database)
        else:
            key = catalog_id(catalog)
        return await self.describe(key, user)

    async def _summaries(self, rows: list[Securable]) -> list[SecurableSummary]:
        ids = [row.id for row in rows]
        tags = await self.metadata.tags_for(ids)
        annotations = await self.metadata.annotations_for(ids)
        engine = await self.metadata.engine_comments(ids)
        columns_by_relation: dict[str, list[Securable]] = {}
        for row in rows:
            if row.securable_type == "column":
                columns_by_relation.setdefault(row.relation_id or "", []).append(row)
        for column_rows in columns_by_relation.values():
            sample = column_rows[0]
            engine.update(
                await self.metadata.engine_column_comments(
                    sample.catalog_name, sample.database_name, sample.object_name
                )
            )
        return [self._summary(row, tags.get(row.id, []), annotations.get(row.id), engine)
                for row in rows]

    @staticmethod
    def _summary(
        row: Securable, tags: list[dict], annotation: dict | None, engine: dict[str, str]
    ) -> SecurableSummary:
        annotation = annotation or {}
        return SecurableSummary(
            id=row.id,
            securable_type=row.securable_type,
            full_name=row.full_name,
            catalog_name=row.catalog_name,
            database_name=row.database_name or None,
            object_name=row.object_name or None,
            column_name=row.column_name or None,
            data_type=row.data_type,
            status=_status(row),
            dropped_at=row.dropped_at,
            comment=annotation.get("comment"),
            engine_comment=engine.get(row.id),
            owner_name=annotation.get("owner_name"),
            owner_type=annotation.get("owner_type"),
            classification=annotation.get("classification"),
            tags=[
                Tag(key=tag["tag_key"], value=tag.get("tag_value"),
                    updated_by=tag.get("updated_by"), updated_at=tag.get("updated_at"))
                for tag in tags
            ],
        )

    async def describe(self, securable_id: str, user: dict) -> SecurableDetail:
        view = CallerView(user)
        row = await self._visible(securable_id, view)
        children = await self.securables.children(row.id) if row.is_relation else []
        if row.dropped_at is None:
            children = [child for child in children if child.dropped_at is None or view.admin]
        summaries = await self._summaries([row, *children])
        can_edit = await self._may_edit(row, view, owner=False)
        return SecurableDetail(
            **summaries[0].model_dump(),
            first_seen_at=row.first_seen_at,
            parent_id=row.parent_id,
            can_edit=can_edit,
            can_set_owner=can_edit and view.admin,
            columns=summaries[1:],
            used_by=await self._used_by(row, user) if row.is_relation else [],
        )

    async def _used_by(self, row: Securable, user: dict) -> list[LinkedOwner]:
        visible = []
        for link in (await self.links.for_securable(row.id))[:MAX_LINKS_CHECKED]:
            if await _owner_visible(link["owner_type"], link["owner_id"], user):
                visible.append(
                    LinkedOwner(
                        owner_type=link["owner_type"],
                        owner_id=link["owner_id"],
                        label=link.get("owner_label") or link["owner_id"],
                    )
                )
        return visible

    # ── Authorization and audit ─────────────────────────────────

    async def _may_edit(self, row: Securable, view: CallerView, *, owner: bool) -> bool:
        try:
            SecurityContext.from_session(view.user)
        except SecurityContextError:
            return False
        if view.admin:
            return True
        if owner or row.dropped_at is not None:
            return False
        if row.securable_type not in RELATION_TYPES | {"column"}:
            return False
        from app.modules.access_control.service import access_control_service

        try:
            access = await access_control_service.effective_access(
                principal=str(view.user.get("username") or ""),
                active_role=str(view.user.get("active_role") or ""),
                resource=f"{row.catalog_name}.{row.database_name}.{row.object_name}",
            )
        except Exception:  # noqa: BLE001 - Ranger unreachable: refuse, never assume
            log.warning("Could not read Ranger access for a catalog edit", exc_info=True)
            return False
        return bool(
            access.get("object_access_comparison_supported")
            and _EDIT_GRANTS.intersection(access.get("object_access") or [])
        )

    async def _audit(
        self, user: dict, action: str, target: Securable | str, status: str,
        detail: str, error: str | None = None,
    ) -> None:
        name = target.full_name if isinstance(target, Securable) else target
        kind = target.securable_type.upper() if isinstance(target, Securable) else "SECURABLE"
        try:
            await write_audit_log(
                event_type="CATALOG",
                user_name=str(user.get("username") or ""),
                action=action,
                object_type=kind,
                object_name=name[:512],
                status=status,
                sql_text=detail,
                error_message=_safe_error(error) if error else None,
                session_id=user.get("session_id"),
                active_role=user.get("active_role"),
                security_context_version=user.get("security_context_version"),
            )
        except Exception:  # noqa: BLE001 - audit outage never replaces the answer
            log.exception("Could not write catalog metadata audit entry")

    async def _authorize(
        self, securable_id: str, user: dict, action: str, detail: str, *, owner: bool = False
    ) -> Securable:
        view = CallerView(user)
        try:
            row = await self._visible(securable_id, view)
        except MetadataNotFound:
            await self._audit(user, action, securable_id, "DENIED", detail,
                              "Securable not found or not visible")
            raise
        if not await self._may_edit(row, view, owner=owner):
            reason = (
                "Setting an owner requires a security-admin active role"
                if owner
                else "The active role may not change metadata of this object"
            )
            await self._audit(user, action, row, "DENIED", detail, reason)
            raise MetadataDenied(reason)
        return row

    # ── Writes ──────────────────────────────────────────────────

    async def put_tags(
        self, securable_id: str, tags: list[TagWrite], user: dict
    ) -> SecurableDetail:
        detail = "SET TAG " + ", ".join(tag.key for tag in tags)
        row = await self._authorize(securable_id, user, "SET_TAG", detail)
        now = nova_now()
        try:
            for tag in tags:
                await self.metadata.put_tag(
                    row.id, row.securable_type, tag.key, tag.value, str(user["username"]), now
                )
        except Exception as exc:
            await self._audit(user, "SET_TAG", row, "ERROR", detail, str(exc))
            raise
        await self._audit(user, "SET_TAG", row, "SUCCESS", detail)
        return await self.describe(row.id, user)

    async def delete_tag(self, securable_id: str, key: str, user: dict) -> SecurableDetail:
        detail = f"UNSET TAG {key[:128]}"
        row = await self._authorize(securable_id, user, "UNSET_TAG", detail)
        try:
            removed = await self.metadata.delete_tag(row.id, key)
        except Exception as exc:
            await self._audit(user, "UNSET_TAG", row, "ERROR", detail, str(exc))
            raise
        await self._audit(user, "UNSET_TAG", row, "SUCCESS" if removed else "NOOP", detail)
        return await self.describe(row.id, user)

    async def annotate(
        self, securable_id: str, body: AnnotationWrite, user: dict
    ) -> SecurableDetail:
        changes = body.model_dump(include=body.model_fields_set, mode="json")
        if not changes:
            raise MetadataInvalid("Nothing to change")
        owner = bool({"owner_name", "owner_type"} & changes.keys())
        detail = _annotation_detail(changes)
        row = await self._authorize(securable_id, user, "ANNOTATE", detail, owner=owner)
        if "classification" in changes and row.securable_type != "column":
            await self._audit(user, "ANNOTATE", row, "DENIED", detail,
                              "Only columns have a classification")
            raise MetadataInvalid("Only columns have a classification")
        if changes.get("owner_name") and not changes.get("owner_type"):
            changes["owner_type"] = "role"
        if "owner_name" in changes and changes["owner_name"] is None:
            changes["owner_type"] = None
        try:
            await self.metadata.put_annotation(
                row.id, row.securable_type, changes, str(user["username"]), nova_now()
            )
        except Exception as exc:
            await self._audit(user, "ANNOTATE", row, "ERROR", detail, str(exc))
            raise
        await self._audit(user, "ANNOTATE", row, "SUCCESS", detail)
        return await self.describe(row.id, user)

    # ── Search ──────────────────────────────────────────────────

    async def search(
        self,
        user: dict,
        *,
        query: str | None = None,
        tag: str | None = None,
        types: list[str] | None = None,
        include_dropped: bool = False,
        limit: int = 25,
    ) -> SearchResponse:
        term = (query or "").strip() or None
        tag_filter = (tag or "").strip() or None
        if term is None and tag_filter is None:
            raise MetadataInvalid("Give a search term or a tag")
        wanted_types = {t for t in (types or []) if t in SECURABLE_TYPES} or None
        matched: dict[str, set[str]] = {}
        loaded: dict[str, Securable] = {}

        def hit(key: str, why: str) -> None:
            matched.setdefault(key, set()).add(why)

        if term:
            for row in await self.securables.search_names(term, SEARCH_CANDIDATES):
                loaded[row.id] = row
                hit(row.id, "name")
            for annotation in await self.metadata.search_annotations(term, SEARCH_CANDIDATES):
                lowered = term.casefold()
                for field in ("comment", "owner_name", "classification"):
                    if lowered in str(annotation.get(field) or "").casefold():
                        hit(annotation["securable_id"], "owner" if field == "owner_name"
                            else field)
            for tag_row in await self.metadata.search_tags(term=term, limit=SEARCH_CANDIDATES):
                hit(tag_row["securable_id"], "tag")
            for key in await self.metadata.search_engine_comments(term, SEARCH_CANDIDATES):
                hit(key, "comment")
        if tag_filter:
            key, _, value = tag_filter.partition("=")
            tagged = {
                row["securable_id"]
                for row in await self.metadata.search_tags(
                    key=key.strip(), value=value.strip() if "=" in tag_filter else None,
                    limit=SEARCH_CANDIDATES,
                )
            }
            if term:
                matched = {k: v for k, v in matched.items() if k in tagged}
            for key in tagged if not term else matched:
                hit(key, "tag")

        missing = [key for key in matched if key not in loaded]
        loaded.update({row.id: row for row in await self.securables.get_many(missing)})
        view = CallerView(user)
        show_dropped = include_dropped and view.admin
        candidates = sorted(
            (
                loaded[key] for key in matched
                if key in loaded
                and (wanted_types is None or loaded[key].securable_type in wanted_types)
                and (show_dropped or loaded[key].dropped_at is None)
            ),
            key=lambda row: _rank(row, term, matched[row.id]),
        )
        visible: list[Securable] = []
        truncated = False
        for row in candidates:
            if not await view.can_see(row):
                continue
            if len(visible) >= limit:
                truncated = True
                break
            visible.append(row)
        summaries = await self._summaries(visible)
        results = [
            SearchHit(**summary.model_dump(), matched=sorted(matched[summary.id]))
            for summary in summaries
        ]
        return SearchResponse(
            query=term, tag=tag_filter, results=results, count=len(results), truncated=truncated
        )

    # ── Lineage ─────────────────────────────────────────────────

    async def lineage(self, securable_id: str, user: dict) -> LineageResponse:
        view = CallerView(user)
        row = await self._visible(securable_id, view)
        if not row.is_relation:
            raise MetadataInvalid("Lineage is recorded between tables and views")
        edges = await self.edges.edges_of(row.id)
        other_ids = [
            edge["source_id"] if edge["target_id"] == row.id else edge["target_id"]
            for edge in edges
        ]
        known = {item.id: item for item in await self.securables.get_many(other_ids)}
        upstream: list[LineageNode] = []
        downstream: list[LineageNode] = []
        for edge, other_id in zip(edges, other_ids, strict=True):
            name = edge["source_name"] if other_id == edge["source_id"] else edge["target_name"]
            other = known.get(other_id) or _unregistered(other_id, name)
            if other is None or not await view.can_see(other):
                continue
            node = LineageNode(
                id=other.id,
                full_name=other.full_name,
                status=_status(other),
                statement_kind=edge["statement_kind"],
                last_seen_at=edge.get("last_seen_at"),
            )
            (upstream if edge["target_id"] == row.id else downstream).append(node)
        return LineageResponse(
            id=row.id, full_name=row.full_name, upstream=upstream, downstream=downstream
        )


def _safe_error(error: str) -> str:
    """A bounded error message with credentials redacted, or a placeholder.

    The redactor refuses text it cannot clean; the audit row is still written.
    """
    try:
        return redact_sql_credentials(error)[:1000]
    except Exception:  # noqa: BLE001 - never let redaction drop the audit row
        return "Error details withheld: they could not be redacted"


def _unregistered(securable_id: str, name: str) -> Securable | None:
    """A lineage endpoint the sync has not registered yet, by its recorded name."""
    parts = name.split(".", 2)
    if len(parts) != 3:
        return None
    return Securable(
        id=securable_id, securable_type="table", catalog_name=parts[0],
        database_name=parts[1], object_name=parts[2],
    )


def _rank(row: Securable, term: str | None, why: set[str]) -> tuple:
    leaf = (row.column_name or row.object_name or row.database_name or row.catalog_name)
    lowered = leaf.casefold()
    needle = (term or "").casefold()
    if needle and lowered == needle:
        closeness = 0
    elif needle and lowered.startswith(needle):
        closeness = 1
    elif "name" in why:
        closeness = 2
    elif "tag" in why:
        closeness = 3
    else:
        closeness = 4
    depth = {"catalog": 0, "database": 1, "column": 3}.get(row.securable_type, 2)
    return (row.dropped_at is not None, closeness, depth, row.full_name)


def _annotation_detail(changes: dict[str, Any]) -> str:
    parts = []
    if "comment" in changes:
        comment = changes["comment"]
        parts.append(f"SET COMMENT ({len(comment)} chars)" if comment else "UNSET COMMENT")
    if "owner_name" in changes or "owner_type" in changes:
        owner = changes.get("owner_name")
        parts.append(f"SET OWNER {changes.get('owner_type') or 'role'} {owner}"
                     if owner else "UNSET OWNER")
    if "classification" in changes:
        parts.append(f"SET CLASSIFICATION {changes['classification'] or 'NONE'}")
    return "; ".join(parts)


async def _owner_visible(owner_type: str, owner_id: str, user: dict) -> bool:
    """Whether the owner's own listing rules show this Semantic View or entity."""
    try:
        if owner_type == SEMANTIC_VIEW:
            from app.modules.intelligence.semantic_views import semantic_view_service

            return await semantic_view_service.is_visible(owner_id, user)
        if owner_type == ENTITY:
            from app.modules.intelligence.entities import entity_registry

            return await entity_registry.get(owner_id, user) is not None
    except Exception:  # noqa: BLE001 - an owner check failing hides the link
        log.debug("Could not check %s %s visibility", owner_type, owner_id, exc_info=True)
    return False


governance_service = GovernanceService()
