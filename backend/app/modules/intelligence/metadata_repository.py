"""Intelligence control metadata in the caller's PostgreSQL transaction.

The prepared replacement for the ``NOVA_SYSTEM`` paths of the Semantic View
service, :class:`IntelligenceRepository`, the Business Event revision store,
News feedback and AI Search metadata, switched in at the all-domain cutover.
It closes the Wave E and Wave H gaps in the new store:

* Semantic View versions are allocated MAX+1 inside the transaction, and a
  writer that read an older latest version gets a conflict instead of a gap or
  a duplicate; a published version is immutable and can only be retired;
* publication moves the active pointer with a revision compare-and-swap and a
  null-safe expected active version (the ``<=>`` of the source);
* revision stores accept a new revision only when it extends the newest one by
  exactly one, never on top of an imported branch, and never under another
  scope (a guessed id cannot overwrite another principal's record);
* Search versions are created once, and a build claim advances a generation so
  a builder that lost its claim can neither record progress nor finish;
* Feature View and Group versions are allocated MAX+1, only built versions are
  served, and a view pinned by any group version cannot be dropped;
* the legacy agent semantic models are read-only, and verified-query usage
  counters only move forward on the caller's own rows.

Whether a caller may see a Semantic View, story or decision remains the owning
service's (and the engine's) check. Vector/full-text index payloads and
materialized feature tables keep their engine owner and News access proofs stay
in Redis; this repository holds only Nova control rows.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

#: Revision stores by the kind names of ``ENGINE_TABLES`` and ``EVENT_TABLES``.
REVISION_TABLES = {
    "nodes": "config_context_nodes",
    "edges": "config_context_edges",
    "monitors": "config_intelligence_monitors",
    "observations": "config_intelligence_observations",
    "news": "config_intelligence_news",
    "investigations": "config_intelligence_investigations",
    "decisions": "config_intelligence_decisions",
    "events": "config_decision_events",
    "outcomes": "config_decision_outcomes",
    "actions": "config_intelligence_actions",
    "action_events": "config_intelligence_action_events",
    "comparisons": "config_intelligence_comparisons",
    "editions": "config_intelligence_editions",
    "stories": "config_intelligence_stories",
    "signals": "config_intelligence_signals",
    "incidents": "config_intelligence_incidents",
    "updates": "config_intelligence_investigation_updates",
}
#: Deployment-wide Business Event stores: ``actor`` is the writer, not an owner.
ACTOR_KINDS = frozenset({"signals", "incidents", "updates"})
#: Fields a retried save may differ in without being a new revision.
_UNCHANGED = frozenset({"revision", "created_at", "updated_at"})
BUILDING = ("BUILDING", "AUTO_BUILDING")
CLAIMABLE = ("PENDING", "BUILDING", "AUTO_PENDING", "AUTO_BUILDING")


def operation_id(payload: dict[str, Any]) -> str:
    """The source's operation id: the record fingerprint, cut to 32 characters."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _table(kind: str) -> str:
    try:
        return REVISION_TABLES[kind]
    except KeyError:
        raise ValueError("Unsupported Intelligence record kind") from None


def _positive(value: int, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"A positive {label} is required")
    return value


def _json_path(path: str) -> list[str]:
    parts = path.split(".")
    if not all(part.isidentifier() for part in parts):
        raise ValueError("Unsupported record field")
    return parts


def _branch_free(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest of up to two revisions; a same-revision pair is a fork."""
    if len(rows) > 1 and rows[0]["revision"] == rows[1]["revision"]:
        raise MetadataConflict()
    return rows[0] if rows else None


class IntelligenceMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Intelligence metadata requires an active metadata transaction")
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

    async def _one(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchone()

    async def _all(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(query, params)
        return list(await cursor.fetchall())

    # ── Semantic Views ────────────────────────────────────────────────────

    async def create_view(
        self, *, catalog: str, database: str, schema: str, name: str, view_id: str,
        definition: dict[str, Any], fingerprint: str, visibility: str = "PUBLIC",
    ) -> dict[str, Any]:
        """Create a draft view owned by the caller, with its first draft version."""
        await self._bound()
        view = await self._one(
            "INSERT INTO nova_meta.config_semantic_views (catalog_name,database_name,schema_name,"
            "name,id,owner_name,visibility,status) VALUES (%s,%s,%s,%s,%s,%s,%s,'DRAFT') "
            "RETURNING *",
            (catalog, database, schema, name, view_id, self.actor.principal, visibility),
        )
        assert view is not None
        await self.add_version(view_id, definition, fingerprint, expected_latest=0)
        return view

    async def get_view(self, view_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_semantic_views WHERE id=%s", (view_id,),
        )

    async def find_view(
        self, catalog: str, database: str, schema: str, name: str,
    ) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_semantic_views WHERE catalog_name=%s "
            "AND database_name=%s AND schema_name=%s AND name=%s",
            (catalog, database, schema, name),
        )

    async def list_views(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Candidate views; the caller still filters by the service's visibility check."""
        await self._bound()
        if active_only:
            return await self._all(
                "SELECT * FROM nova_meta.config_semantic_views WHERE status='ACTIVE' "
                "AND active_version IS NOT NULL ORDER BY name,id", (),
            )
        return await self._all(
            "SELECT * FROM nova_meta.config_semantic_views WHERE status<>'DEPRECATED' "
            "ORDER BY name,id", (),
        )

    async def news_views(self) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_semantic_views WHERE news_enabled "
            "AND status<>'DEPRECATED' ORDER BY name,id", (),
        )

    async def get_version(self, view_id: str, version: int) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_semantic_view_versions WHERE view_id=%s "
            "AND version=%s", (view_id, version),
        )

    async def latest_version(self, view_id: str) -> int:
        await self._bound()
        row = await self._one(
            "SELECT coalesce(max(version),0) AS latest FROM "
            "nova_meta.config_semantic_view_versions WHERE view_id=%s", (view_id,),
        )
        return int(row["latest"]) if row else 0

    async def add_version(
        self, view_id: str, definition: dict[str, Any], fingerprint: str, *,
        expected_latest: int,
    ) -> dict[str, Any]:
        """Add the next draft version, only if ``expected_latest`` is still the newest."""
        await self._bound()
        if type(expected_latest) is not int or expected_latest < 0:
            raise ValueError("The latest version read is required")
        row = await self._one(
            "INSERT INTO nova_meta.config_semantic_view_versions (view_id,version,definition,"
            "fingerprint,status) VALUES (%s,%s,%s,%s,'DRAFT') RETURNING *",
            (view_id, expected_latest + 1, Jsonb(definition), fingerprint),
        )
        assert row is not None
        return row

    async def record_validation(
        self, view_id: str, version: int, report: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Store a validation report on an unpublished version; None if it is published."""
        await self._bound()
        status = "VALIDATED" if report.get("valid") is True else "DRAFT"
        return await self._one(
            "UPDATE nova_meta.config_semantic_view_versions SET validation=%s,status=%s "
            "WHERE view_id=%s AND version=%s AND status IN ('DRAFT','VALIDATED') RETURNING *",
            (Jsonb(report), status, view_id, version),
        )

    async def publish(
        self, view_id: str, version: int, *, expected_revision: int,
        expected_active: int | None,
    ) -> dict[str, Any]:
        """Activate ``version`` and move the pointer from ``expected_active``, atomically."""
        await self._bound()
        _positive(expected_revision, "revision")
        await self.connection.execute(
            "UPDATE nova_meta.config_semantic_view_versions SET status='ACTIVE' "
            "WHERE view_id=%s AND version=%s AND status='VALIDATED'", (view_id, version),
        )
        view = await self._one(
            "UPDATE nova_meta.config_semantic_views SET active_version=%s,status='ACTIVE',"
            "metadata_revision=metadata_revision+1 WHERE id=%s AND metadata_revision=%s "
            "AND active_version IS NOT DISTINCT FROM %s RETURNING *",
            (version, view_id, expected_revision, expected_active),
        )
        if view is None:
            raise MetadataConflict()
        await self.connection.execute(
            "UPDATE nova_meta.config_semantic_view_versions SET status='DEPRECATED' "
            "WHERE view_id=%s AND status='ACTIVE' AND version<>%s", (view_id, version),
        )
        return view

    async def deprecate(self, view_id: str, *, expected_revision: int) -> dict[str, Any]:
        await self._bound()
        view = await self._one(
            "UPDATE nova_meta.config_semantic_views SET status='DEPRECATED',active_version=NULL,"
            "metadata_revision=metadata_revision+1 WHERE id=%s AND metadata_revision=%s "
            "RETURNING *", (view_id, _positive(expected_revision, "revision")),
        )
        if view is None:
            raise MetadataConflict()
        return view

    async def save_news(
        self, view_id: str, enabled: bool, config: dict[str, Any] | None, *,
        expected_revision: int,
    ) -> dict[str, Any]:
        await self._bound()
        view = await self._one(
            "UPDATE nova_meta.config_semantic_views SET news_enabled=%s,news_config=%s,"
            "metadata_revision=metadata_revision+1 WHERE id=%s AND metadata_revision=%s "
            "RETURNING *",
            (enabled, Jsonb(config) if config is not None else None, view_id,
             _positive(expected_revision, "revision")),
        )
        if view is None:
            raise MetadataConflict()
        return view

    async def drop(self, view_id: str, *, expected_revision: int) -> bool:
        """Remove the view, then its versions, in the caller's transaction."""
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_semantic_views WHERE id=%s AND metadata_revision=%s",
            (view_id, _positive(expected_revision, "revision")),
        )
        if cursor.rowcount != 1:
            raise MetadataConflict()
        await self.connection.execute(
            "DELETE FROM nova_meta.config_semantic_view_versions WHERE view_id=%s", (view_id,),
        )
        return True

    async def list_versions(self, view_id: str) -> list[dict[str, Any]]:
        """Every version of one view, newest first; the service filters what a caller sees."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_semantic_view_versions WHERE view_id=%s "
            "ORDER BY version DESC", (view_id,),
        )

    async def view_link_sources(self, view_id: str | None = None) -> list[dict[str, Any]]:
        """Each view (deprecated ones too) with the definition the catalog links.

        That is the active version, or the newest version while none is active.
        """
        await self._bound()
        return await self._all(
            "SELECT v.id,v.catalog_name,v.database_name,v.schema_name,v.name,s.version,"
            "s.definition FROM nova_meta.config_semantic_views v "
            "JOIN nova_meta.config_semantic_view_versions s ON s.view_id=v.id "
            "AND s.version=coalesce(v.active_version,(SELECT max(l.version) FROM "
            "nova_meta.config_semantic_view_versions l WHERE l.view_id=v.id)) "
            + ("WHERE v.id=%s " if view_id is not None else "") + "ORDER BY v.id",
            (view_id,) if view_id is not None else (),
        )

    # ── Revision stores ───────────────────────────────────────────────────

    async def get_record(
        self, kind: str, record_id: str, *, revision: int | None = None,
    ) -> dict[str, Any] | None:
        """The newest (or the given) revision visible to the bound actor."""
        await self._bound()
        table = _table(kind)
        if revision is None:
            rows = await self._all(
                f"SELECT * FROM nova_meta.{table} WHERE id=%s ORDER BY revision DESC LIMIT 2",
                (record_id,),
            )
        else:
            rows = await self._all(
                f"SELECT * FROM nova_meta.{table} WHERE id=%s AND revision=%s LIMIT 2",
                (record_id, revision),
            )
        return _branch_free(rows)

    async def page(
        self, kind: str, *, after: str = "", limit: int = 50,
    ) -> list[dict[str, Any]]:
        """The current revision of each visible record, by id; a branch is a conflict."""
        await self._bound()
        table = _table(kind)
        rows = await self._all(
            "SELECT * FROM (SELECT r.*,count(*) OVER (PARTITION BY id,revision) AS branches,"
            "row_number() OVER (PARTITION BY id ORDER BY revision DESC) AS rn "
            f"FROM nova_meta.{table} r WHERE id>%s) current_records WHERE rn=1 "
            "ORDER BY id LIMIT %s",
            (after, min(max(limit, 1), 101)),
        )
        if any(row["branches"] != 1 for row in rows):
            raise MetadataConflict()
        return rows

    async def current_records(
        self, kind: str, *, scope: SecurityContext | None = None,
        match: dict[str, str] | None = None, ids: list[str] | None = None,
        between: tuple[str, str, str] | None = None, after: str | None = None,
        endpoint: str | None = None, search: str | None = None, limit: int = 101,
    ) -> list[dict[str, Any]]:
        """The current revision of each matching record, ordered by id.

        ``scope`` keeps only records of that principal, role and security
        version (the source's scoped reads; RLS already limits owner stores to
        the bound principal and role). ``match`` and ``between`` compare payload
        text at a dotted path; ``endpoint`` matches a graph edge's source or
        target; ``search`` is a case-insensitive substring of a node's name.
        Filters apply to the current revision; a branched record is a conflict.
        """
        await self._bound()
        table = _table(kind)
        inner: list[str] = []
        outer: list[str] = []
        inner_params: list[Any] = []
        outer_params: list[Any] = []
        if scope is not None:
            owner = "actor" if kind in ACTOR_KINDS else "principal"
            inner.append(f"{owner}=%s AND active_role=%s AND security_context_version=%s")
            inner_params += [scope.principal, scope.active_role, scope.security_context_version]
        if ids is not None:
            inner.append("id=ANY(%s)")
            inner_params.append(list(ids))
        if after is not None:
            inner.append("id>%s")
            inner_params.append(after)
        for path, value in (match or {}).items():
            outer.append("payload#>>%s=%s")
            outer_params += [_json_path(path), value]
        if between is not None:
            path, low, high = between
            outer.append("payload#>>%s BETWEEN %s AND %s")
            outer_params += [_json_path(path), low, high]
        if endpoint is not None:
            outer.append("(payload->>'source'=%s OR payload->>'target'=%s)")
            outer_params += [endpoint, endpoint]
        if search is not None:
            outer.append("strpos(lower(payload->>'name'),lower(%s))>0")
            outer_params.append(search)
        rows = await self._all(
            "SELECT * FROM (SELECT r.*,count(*) OVER (PARTITION BY id,revision) AS branches,"
            "row_number() OVER (PARTITION BY id ORDER BY revision DESC) AS rn "
            f"FROM nova_meta.{table} r WHERE {' AND '.join(inner) or 'TRUE'}) current_records "
            f"WHERE rn=1{''.join(' AND ' + item for item in outer)} ORDER BY id LIMIT %s",
            (*inner_params, *outer_params, min(max(int(limit), 1), 20001)),
        )
        if any(row["branches"] != 1 for row in rows):
            raise MetadataConflict()
        return rows

    async def save_record(
        self, kind: str, payload: dict[str, Any], *, expected_revision: int = 0,
    ) -> dict[str, Any]:
        """Append the next revision of a record; return the payload that was stored.

        ``payload`` is the record's JSON form (``model_dump(mode="json")``).

        A retry whose content equals the newest revision returns that revision
        instead of writing a duplicate. The database refuses a stale revision, a
        branched record and a record that belongs to another scope.
        """
        await self._bound()
        table = _table(kind)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("The revision read is required")
        record_id = payload.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("A record id is required")
        current = await self.get_record(kind, record_id)
        if current is not None:
            stored = current["payload"]
            if {k: v for k, v in stored.items() if k not in _UNCHANGED} == {
                k: v for k, v in payload.items() if k not in _UNCHANGED
            }:
                return stored
        record = {**payload, "revision": expected_revision + 1}
        if kind in ACTOR_KINDS:
            owner = record.get("actor") or {}
            writer_column = "actor"
        else:
            owner = record.get("scope") or {}
            writer_column = "principal"
        # No RETURNING: a reviewer may append to a record it cannot read back.
        await self.connection.execute(
            f"INSERT INTO nova_meta.{table} (id,revision,operation_id,{writer_column},active_role,"
            "security_context_version,payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (record_id, record["revision"], operation_id(record), owner.get("principal"),
             owner.get("active_role"), owner.get("security_context_version"), Jsonb(record)),
        )
        return record

    async def shared_decision(self, record_id: str, owner_name: str) -> dict[str, Any] | None:
        """A decision named with its owner; the decision service authorizes the reader."""
        await self._bound()
        rows = await self._all(
            "SELECT payload,revision FROM nova_meta.intelligence_shared_revisions"
            "('decision',%s,%s)", (record_id, owner_name),
        )
        row = _branch_free(rows)
        return row["payload"] if row else None

    async def action_for_review(self, record_id: str) -> dict[str, Any] | None:
        """An action for its reviewer; the review service authorizes before returning it."""
        await self._bound()
        rows = await self._all(
            "SELECT payload,revision FROM nova_meta.intelligence_shared_revisions"
            "('action_review',%s,NULL)", (record_id,),
        )
        row = _branch_free(rows)
        return row["payload"] if row else None

    async def latest_edition(
        self, view_id: str, principal: str, active_role: str, security_context_version: int,
    ) -> dict[str, Any] | None:
        """The newest edition pressed by the view's execution account."""
        await self._bound()
        rows = await self._all(
            "SELECT payload,branches FROM (SELECT payload,"
            "row_number() OVER (PARTITION BY id ORDER BY revision DESC) AS rn,"
            "count(*) OVER (PARTITION BY id,revision) AS branches "
            "FROM nova_meta.config_intelligence_editions WHERE principal=%s AND active_role=%s "
            "AND security_context_version=%s AND payload->'semantic'->>'view_id'=%s) latest "
            "WHERE rn=1 ORDER BY payload->>'edition_date' DESC LIMIT 1",
            (principal, active_role, security_context_version, view_id),
        )
        if any(row["branches"] != 1 for row in rows):
            raise MetadataConflict()
        return rows[0]["payload"] if rows else None

    # ── News feedback ─────────────────────────────────────────────────────

    async def set_feedback(
        self, story_id: str, reaction: str | None, features: dict[str, str] | None = None,
    ) -> None:
        """Record or clear the caller's own reaction to a story."""
        await self._bound()
        if reaction is None:
            await self.connection.execute(
                "DELETE FROM nova_meta.config_intelligence_story_feedback "
                "WHERE user_name=%s AND story_id=%s", (self.actor.principal, story_id),
            )
            return
        await self.connection.execute(
            "INSERT INTO nova_meta.config_intelligence_story_feedback (user_name,story_id,"
            "reaction,features) VALUES (%s,%s,%s,%s) ON CONFLICT (user_name,story_id) "
            "DO UPDATE SET reaction=EXCLUDED.reaction,features=EXCLUDED.features",
            (self.actor.principal, story_id, reaction, Jsonb(features or {})),
        )

    async def feedback(self, limit: int = 500) -> list[dict[str, Any]]:
        """The caller's own reactions, newest first (at most 2000, the ranking history)."""
        await self._bound()
        return await self._all(
            "SELECT story_id,reaction,features,updated_at FROM "
            "nova_meta.config_intelligence_story_feedback WHERE user_name=%s "
            "ORDER BY updated_at DESC LIMIT %s",
            (self.actor.principal, min(max(int(limit), 1), 2000)),
        )

    # ── AI Search ─────────────────────────────────────────────────────────

    async def create_index(
        self, definition: dict[str, Any], version: dict[str, Any],
    ) -> dict[str, Any]:
        """Create an index owned by the caller, with its first pending version."""
        await self._bound()
        index = await self._one(
            "INSERT INTO nova_meta.config_ai_search_indexes (name,id,source_relation,key_columns,"
            "content_columns,filter_columns,entity_id,model_alias,owner_name,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'BUILDING') RETURNING *",
            (definition["name"], definition["id"], definition["source_relation"],
             Jsonb(definition["key_columns"]), Jsonb(definition["content_columns"]),
             Jsonb(definition["filter_columns"]), definition.get("entity_id"),
             definition.get("model_alias"), self.actor.principal),
        )
        assert index is not None
        await self.add_search_version(index["name"], index["id"], version, expected_latest=0)
        return index

    async def get_index(self, name: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_ai_search_indexes WHERE name=%s", (name,),
        )

    async def get_search_version(self, name: str, version: int) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_ai_search_versions WHERE index_name=%s "
            "AND version=%s", (name, version),
        )

    async def add_search_version(
        self, name: str, index_id: str, model: dict[str, Any], *, expected_latest: int,
        automatic: bool = False,
    ) -> dict[str, Any]:
        """Create the next version and its sync state once; a racing creator conflicts."""
        await self._bound()
        if type(expected_latest) is not int or expected_latest < 0:
            raise ValueError("The latest version read is required")
        row = await self._one(
            "INSERT INTO nova_meta.config_ai_search_versions (index_name,version,index_id,"
            "model_id,model_revision,dimensions,metric,build_status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (name, expected_latest + 1, index_id, model.get("model_id"),
             model.get("model_revision"), int(model.get("dimensions") or 0),
             model.get("metric") or "cosine", "AUTO_PENDING" if automatic else "PENDING"),
        )
        assert row is not None
        await self.connection.execute(
            "INSERT INTO nova_meta.config_ai_search_sync_state (index_name,version,status) "
            "VALUES (%s,%s,'PENDING')", (name, row["version"]),
        )
        return row

    async def list_indexes(
        self, *, active_only: bool = False, limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Indexes that are not dropped (or only those serving a version), by name."""
        await self._bound()
        condition = (
            "status='ACTIVE' AND active_version IS NOT NULL" if active_only
            else "status<>'DEPRECATED'"
        )
        return await self._all(
            f"SELECT * FROM nova_meta.config_ai_search_indexes WHERE {condition} "
            "ORDER BY name LIMIT %s", (None if limit is None else max(limit, 1),),
        )

    async def list_search_versions(self, name: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_ai_search_versions WHERE index_name=%s "
            "ORDER BY version DESC", (name,),
        )

    async def latest_search_version(self, name: str) -> int:
        """The newest version number of an index (0 without versions)."""
        await self._bound()
        row = await self._one(
            "SELECT coalesce(max(version),0) AS latest FROM nova_meta.config_ai_search_versions "
            "WHERE index_name=%s", (name,),
        )
        return int(row["latest"]) if row else 0

    async def get_sync_state(self, name: str, version: int) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_ai_search_sync_state WHERE index_name=%s "
            "AND version=%s", (name, version),
        )

    async def set_model_alias(
        self, name: str, model_alias: str | None, *, expected_revision: int,
    ) -> dict[str, Any]:
        """Change the embedding alias of a live index under a revision CAS."""
        await self._bound()
        _positive(expected_revision, "revision")
        row = await self._one(
            "UPDATE nova_meta.config_ai_search_indexes SET model_alias=%s,"
            "metadata_revision=metadata_revision+1 WHERE name=%s AND metadata_revision=%s "
            "AND status<>'DEPRECATED' RETURNING *", (model_alias, name, expected_revision),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def retire_index(self, name: str, *, expected_revision: int) -> dict[str, Any]:
        """Drop an index from service: it stops serving and its active version is retired.

        Rows are kept (no workload may delete Search metadata); the engine
        projections are the caller's to remove after this commits.
        """
        await self._bound()
        _positive(expected_revision, "revision")
        await self.connection.execute(
            "UPDATE nova_meta.config_ai_search_versions SET build_status='DEPRECATED' "
            "WHERE index_name=%s AND build_status='ACTIVE'", (name,),
        )
        row = await self._one(
            "UPDATE nova_meta.config_ai_search_indexes SET status='DEPRECATED',"
            "active_version=NULL,metadata_revision=metadata_revision+1 WHERE name=%s "
            "AND metadata_revision=%s AND status<>'DEPRECATED' RETURNING *",
            (name, expected_revision),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def pending_builds(
        self, limit: int = 100, *, lease_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Claimable builds of live indexes.

        With ``lease_seconds``, a version that is already building is returned
        only when its builder has not recorded progress for that long (a lapsed
        claim the poller may take over); pending versions are always returned.
        """
        await self._bound()
        lease_filter = ""
        parameters: tuple[Any, ...] = (list(CLAIMABLE),)
        if lease_seconds is not None:
            _positive(lease_seconds, "lease")
            lease_filter = (
                " AND (v.build_status<>ALL(%s) OR s.updated_at IS NULL "
                "OR s.updated_at<clock_timestamp()-%s)"
            )
            parameters += (list(BUILDING), timedelta(seconds=lease_seconds))
        return await self._all(
            "SELECT v.index_name,v.version,v.build_status,v.build_generation FROM "
            "nova_meta.config_ai_search_versions v JOIN nova_meta.config_ai_search_indexes i "
            "ON i.name=v.index_name AND i.status<>'DEPRECATED' "
            "LEFT JOIN nova_meta.config_ai_search_sync_state s "
            "ON s.index_name=v.index_name AND s.version=v.version "
            "WHERE v.build_status=ANY(%s)" + lease_filter
            + " ORDER BY v.index_name,v.version LIMIT %s",
            (*parameters, min(max(limit, 1), 100)),
        )

    async def claim_build(self, name: str, version: int) -> int | None:
        """Start (or take over) a build under a new generation; None if not claimable."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_ai_search_versions SET build_status=CASE WHEN "
            "build_status LIKE 'AUTO\\_%%' THEN 'AUTO_BUILDING' ELSE 'BUILDING' END,"
            "build_generation=build_generation+1 WHERE index_name=%s AND version=%s "
            "AND build_status=ANY(%s) RETURNING build_generation",
            (name, version, list(CLAIMABLE)),
        )
        return int(row["build_generation"]) if row else None

    async def record_build_progress(
        self, name: str, version: int, *, generation: int, source_rows: int, indexed_rows: int,
    ) -> bool:
        """Record progress for the builder holding ``generation`` only."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_ai_search_sync_state SET source_rows=%s,indexed_rows=%s,"
            "status='BUILDING',build_generation=%s WHERE index_name=%s AND version=%s "
            "AND EXISTS (SELECT 1 FROM nova_meta.config_ai_search_versions v WHERE "
            "v.index_name=%s AND v.version=%s AND v.build_generation=%s "
            "AND v.build_status=ANY(%s)) RETURNING index_name",
            (source_rows, indexed_rows, _positive(generation, "generation"), name, version,
             name, version, generation, list(BUILDING)),
        )
        return row is not None

    async def finish_build(
        self, name: str, version: int, *, generation: int, indexed_rows: int = 0,
        watermark: str | None = None, failure_code: str | None = None,
    ) -> bool:
        """Mark the build READY (or FAILED with a code) for the current claim only."""
        await self._bound()
        _positive(generation, "generation")
        status = "FAILED" if failure_code else "READY"
        state = await self._one(
            "UPDATE nova_meta.config_ai_search_sync_state SET status=%s,"
            "source_rows=CASE WHEN %s='READY' THEN %s ELSE source_rows END,"
            "indexed_rows=CASE WHEN %s='READY' THEN %s ELSE indexed_rows END,"
            "last_watermark=CASE WHEN %s='READY' THEN %s ELSE last_watermark END,"
            "build_generation=%s WHERE index_name=%s AND version=%s "
            "AND EXISTS (SELECT 1 FROM nova_meta.config_ai_search_versions v WHERE "
            "v.index_name=%s AND v.version=%s AND v.build_generation=%s "
            "AND v.build_status=ANY(%s)) RETURNING index_name",
            (status, status, indexed_rows, status, indexed_rows, status, watermark, generation,
             name, version, name, version, generation, list(BUILDING)),
        )
        if state is None:
            return False
        row = await self._one(
            "UPDATE nova_meta.config_ai_search_versions SET build_status=%s,"
            "indexed_rows=CASE WHEN %s='READY' THEN %s ELSE indexed_rows END,failure_code=%s "
            "WHERE index_name=%s AND version=%s AND build_generation=%s "
            "AND build_status=ANY(%s) RETURNING index_name",
            (status, status, indexed_rows, failure_code, name, version, generation,
             list(BUILDING)),
        )
        return row is not None

    async def activate(
        self, name: str, version: int, *, expected_revision: int,
    ) -> dict[str, Any]:
        """Serve a built version; the previously active version is retired atomically."""
        await self._bound()
        _positive(expected_revision, "revision")
        await self.connection.execute(
            "UPDATE nova_meta.config_ai_search_versions SET build_status='DEPRECATED' "
            "WHERE index_name=%s AND build_status='ACTIVE' AND version<>%s", (name, version),
        )
        activated = await self._one(
            "UPDATE nova_meta.config_ai_search_versions SET build_status='ACTIVE' "
            "WHERE index_name=%s AND version=%s AND build_status IN ('READY','ACTIVE',"
            "'DEPRECATED') RETURNING version", (name, version),
        )
        if activated is None:
            raise MetadataConflict()
        index = await self._one(
            "UPDATE nova_meta.config_ai_search_indexes SET active_version=%s,status='ACTIVE',"
            "metadata_revision=metadata_revision+1 WHERE name=%s AND metadata_revision=%s "
            "RETURNING *", (version, name, expected_revision),
        )
        if index is None:
            raise MetadataConflict()
        return index

    async def log_query(self, entry: dict[str, Any]) -> None:
        await self._bound()
        await self.connection.execute(
            "INSERT INTO nova_meta.ai_search_query_log (id,index_name,index_version,user_name,"
            "mode,elapsed_ms,lexical_candidates,semantic_candidates,result_count,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (entry["id"], entry["index_name"], entry["index_version"], self.actor.principal,
             entry["mode"], entry["elapsed_ms"], entry["lexical_candidates"],
             entry["semantic_candidates"], entry["result_count"],
             entry.get("status") or "SUCCESS"),
        )

    async def record_evaluation(self, run: dict[str, Any]) -> None:
        await self._bound()
        await self.connection.execute(
            "INSERT INTO nova_meta.ai_search_eval_runs (id,index_name,index_version,user_name,"
            "mode,query_count,metrics) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (run["id"], run["index_name"], run["index_version"], self.actor.principal,
             run["mode"], run["query_count"], Jsonb(run["metrics"])),
        )

    # ── Entity registry ───────────────────────────────────────────────────

    async def create_entity(self, data: dict[str, Any]) -> dict[str, Any]:
        """Register an active entity owned by the caller; a taken scoped name conflicts."""
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.config_entities (catalog_name,database_name,schema_name,name,"
            "id,description,relation_name,key_columns,owner_name,tags,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE') RETURNING *",
            (data["catalog"], data["database"], data["schema"], data["name"], data["id"],
             data.get("description"), data["relation"], Jsonb(data["key_columns"]),
             self.actor.principal, Jsonb(data.get("tags") or [])),
        )
        assert row is not None
        return row

    async def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """An active entity; the service still checks the caller's source access."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_entities WHERE id=%s AND status='ACTIVE'",
            (entity_id,),
        )

    async def list_entities(self) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_entities WHERE status='ACTIVE' ORDER BY name,id", (),
        )

    async def deprecate_entity(self, entity_id: str, *, expected_revision: int) -> dict[str, Any]:
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_entities SET status='DEPRECATED',"
            "metadata_revision=metadata_revision+1 WHERE id=%s AND status='ACTIVE' "
            "AND metadata_revision=%s RETURNING *",
            (entity_id, _positive(expected_revision, "revision")),
        )
        if row is None:
            raise MetadataConflict()
        return row

    # ── Feature Views ─────────────────────────────────────────────────────

    async def create_feature_view(
        self, definition: dict[str, Any], *, relation_name: str,
    ) -> dict[str, Any]:
        """Create a building view owned by the caller, with its first building version."""
        await self._bound()
        view = await self._one(
            "INSERT INTO nova_meta.config_feature_views (name,id,entity_id,source_relation,"
            "entity_keys,event_timestamp,feature_columns,freshness_seconds,ttl_seconds,"
            "owner_name,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'BUILDING') RETURNING *",
            (definition["name"], definition["id"], definition["entity_id"],
             definition["source_relation"], Jsonb(definition["entity_keys"]),
             definition["event_timestamp"], Jsonb(definition["feature_columns"]),
             definition["freshness_seconds"], definition["ttl_seconds"],
             self.actor.principal),
        )
        assert view is not None
        await self.add_feature_view_version(
            view["name"], relation_name, definition, expected_latest=0,
        )
        return view

    async def get_feature_view(self, name: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_feature_views WHERE name=%s", (name,),
        )

    async def list_feature_views(self) -> list[dict[str, Any]]:
        """Views that are not retired; the service still checks source access."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_feature_views WHERE status<>'DEPRECATED' "
            "ORDER BY name", (),
        )

    async def feature_view_versions(self, name: str) -> list[dict[str, Any]]:
        """Every version of a view, oldest first (DROP removes their engine tables)."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_feature_view_versions WHERE view_name=%s "
            "ORDER BY version", (name,),
        )

    async def latest_feature_view_version(self, name: str) -> int:
        """The highest allocated version, the ``expected_latest`` of the next one."""
        await self._bound()
        row = await self._one(
            "SELECT coalesce(max(version),0) AS latest "
            "FROM nova_meta.config_feature_view_versions WHERE view_name=%s", (name,),
        )
        return int(row["latest"]) if row else 0

    async def get_feature_view_version(self, name: str, version: int) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_feature_view_versions WHERE view_name=%s "
            "AND version=%s", (name, version),
        )

    async def add_feature_view_version(
        self, name: str, relation_name: str, definition: dict[str, Any], *,
        expected_latest: int,
    ) -> dict[str, Any]:
        """Allocate the next building version; a refresh that read an older one conflicts."""
        await self._bound()
        if type(expected_latest) is not int or expected_latest < 0:
            raise ValueError("The latest version read is required")
        row = await self._one(
            "INSERT INTO nova_meta.config_feature_view_versions (view_name,version,"
            "relation_name,definition,status) VALUES (%s,%s,%s,%s,'BUILDING') RETURNING *",
            (name, expected_latest + 1, relation_name, Jsonb(definition)),
        )
        assert row is not None
        return row

    async def finish_feature_view_version(
        self, name: str, version: int, *, row_count: int | None,
    ) -> bool:
        """Mark a building version READY with its row count, or FAILED when None."""
        await self._bound()
        if row_count is None:
            row = await self._one(
                "UPDATE nova_meta.config_feature_view_versions SET status='FAILED' "
                "WHERE view_name=%s AND version=%s AND status='BUILDING' RETURNING version",
                (name, version),
            )
        else:
            row = await self._one(
                "UPDATE nova_meta.config_feature_view_versions SET status='READY',row_count=%s "
                "WHERE view_name=%s AND version=%s AND status='BUILDING' RETURNING version",
                (row_count, name, version),
            )
        return row is not None

    async def activate_feature_view(
        self, name: str, version: int, *, expected_revision: int,
    ) -> dict[str, Any]:
        """Serve a built version; the previously active one is retired atomically."""
        await self._bound()
        _positive(expected_revision, "revision")
        await self.connection.execute(
            "UPDATE nova_meta.config_feature_view_versions SET status='DEPRECATED' "
            "WHERE view_name=%s AND status='ACTIVE' AND version<>%s", (name, version),
        )
        activated = await self._one(
            "UPDATE nova_meta.config_feature_view_versions SET status='ACTIVE' "
            "WHERE view_name=%s AND version=%s AND status IN ('READY','ACTIVE','DEPRECATED') "
            "RETURNING version", (name, version),
        )
        if activated is None:
            raise MetadataConflict()
        view = await self._one(
            "UPDATE nova_meta.config_feature_views SET active_version=%s,status='ACTIVE',"
            "metadata_revision=metadata_revision+1 WHERE name=%s AND metadata_revision=%s "
            "RETURNING *", (version, name, expected_revision),
        )
        if view is None:
            raise MetadataConflict()
        return view

    async def drop_feature_view(self, name: str, *, expected_revision: int) -> bool:
        """Remove an unpinned view and then its versions; a pinned view is refused."""
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_feature_views WHERE name=%s AND metadata_revision=%s",
            (name, _positive(expected_revision, "revision")),
        )
        if cursor.rowcount != 1:
            raise MetadataConflict()
        await self.connection.execute(
            "DELETE FROM nova_meta.config_feature_view_versions WHERE view_name=%s", (name,),
        )
        return True

    # ── Feature Groups ────────────────────────────────────────────────────

    async def create_feature_group(
        self, *, name: str, group_id: str, entity_id: str, members: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a group owned by the caller and activate its first version."""
        await self._bound()
        await self.connection.execute(
            "INSERT INTO nova_meta.config_feature_groups (name,id,entity_id,owner_name,status) "
            "VALUES (%s,%s,%s,%s,'ACTIVE')", (name, group_id, entity_id, self.actor.principal),
        )
        await self.connection.execute(
            "INSERT INTO nova_meta.config_feature_group_versions (group_name,version,members,"
            "status) VALUES (%s,1,%s,'ACTIVE')", (name, Jsonb(members)),
        )
        group = await self._one(
            "UPDATE nova_meta.config_feature_groups SET active_version=1,"
            "metadata_revision=metadata_revision+1 WHERE name=%s RETURNING *", (name,),
        )
        assert group is not None
        return group

    async def get_feature_group(self, name: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_feature_groups WHERE name=%s", (name,),
        )

    async def list_feature_groups(self) -> list[dict[str, Any]]:
        """Groups that are not retired; the service still checks member access."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_feature_groups WHERE status<>'DEPRECATED' "
            "ORDER BY name", (),
        )

    async def latest_feature_group_version(self, name: str) -> int:
        await self._bound()
        row = await self._one(
            "SELECT coalesce(max(version),0) AS latest "
            "FROM nova_meta.config_feature_group_versions WHERE group_name=%s", (name,),
        )
        return int(row["latest"]) if row else 0

    async def get_feature_group_version(self, name: str, version: int) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_feature_group_versions WHERE group_name=%s "
            "AND version=%s", (name, version),
        )

    async def add_feature_group_version(
        self, name: str, members: list[dict[str, Any]], *, expected_latest: int,
    ) -> dict[str, Any]:
        await self._bound()
        if type(expected_latest) is not int or expected_latest < 0:
            raise ValueError("The latest version read is required")
        row = await self._one(
            "INSERT INTO nova_meta.config_feature_group_versions (group_name,version,members,"
            "status) VALUES (%s,%s,%s,'DRAFT') RETURNING *",
            (name, expected_latest + 1, Jsonb(members)),
        )
        assert row is not None
        return row

    async def activate_feature_group(
        self, name: str, version: int, *, expected_revision: int,
    ) -> dict[str, Any]:
        await self._bound()
        _positive(expected_revision, "revision")
        await self.connection.execute(
            "UPDATE nova_meta.config_feature_group_versions SET status='DEPRECATED' "
            "WHERE group_name=%s AND status='ACTIVE' AND version<>%s", (name, version),
        )
        activated = await self._one(
            "UPDATE nova_meta.config_feature_group_versions SET status='ACTIVE' "
            "WHERE group_name=%s AND version=%s RETURNING version", (name, version),
        )
        if activated is None:
            raise MetadataConflict()
        group = await self._one(
            "UPDATE nova_meta.config_feature_groups SET active_version=%s,status='ACTIVE',"
            "metadata_revision=metadata_revision+1 WHERE name=%s AND metadata_revision=%s "
            "RETURNING *", (version, name, expected_revision),
        )
        if group is None:
            raise MetadataConflict()
        return group

    async def drop_feature_group(self, name: str, *, expected_revision: int) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_feature_groups WHERE name=%s AND metadata_revision=%s",
            (name, _positive(expected_revision, "revision")),
        )
        if cursor.rowcount != 1:
            raise MetadataConflict()
        await self.connection.execute(
            "DELETE FROM nova_meta.config_feature_group_versions WHERE group_name=%s", (name,),
        )
        return True

    # ── Training lineage ──────────────────────────────────────────────────

    async def record_training_set(self, data: dict[str, Any]) -> None:
        await self._bound()
        await self.connection.execute(
            "INSERT INTO nova_meta.config_feature_training_sets (id,group_name,group_version,"
            "label_relation,generated_sql,lineage,owner_name) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (data["id"], data["group_name"], data["group_version"], data["label_relation"],
             data["generated_sql"], Jsonb(data["lineage"]), self.actor.principal),
        )

    async def get_training_set(self, training_set_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_feature_training_sets WHERE id=%s",
            (training_set_id,),
        )

    async def find_training_set(
        self, group_name: str, label_relation: str,
    ) -> dict[str, Any] | None:
        """The caller's oldest training set of a group over one label relation."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_feature_training_sets WHERE owner_name=%s "
            "AND group_name=%s AND label_relation=%s ORDER BY created_at,id LIMIT 1",
            (self.actor.principal, group_name, label_relation),
        )

    async def link_model(self, data: dict[str, Any]) -> None:
        """Record which model version was trained from the caller's training set."""
        await self._bound()
        await self.connection.execute(
            "INSERT INTO nova_meta.config_feature_ml_links (id,model_id,model_version,"
            "training_set_id,group_name,group_version,lineage) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (data["id"], data["model_id"], data["model_version"], data["training_set_id"],
             data["group_name"], data["group_version"], Jsonb(data["lineage"])),
        )

    async def model_links(self, training_set_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_feature_ml_links WHERE training_set_id=%s "
            "ORDER BY created_at,id", (training_set_id,),
        )

    # ── Legacy semantic models and usage ──────────────────────────────────

    async def legacy_semantic_models(self) -> list[dict[str, Any]]:
        """Every legacy model, for the one-time Semantic View migration."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_semantic_models ORDER BY semantic_model_id", (),
        )

    async def legacy_verified_queries(self) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_semantic_verified_queries "
            "ORDER BY verified_query_id", (),
        )

    async def record_semantic_usage(
        self, usage: dict[str, Any], *, verified_query_id: str | None = None,
    ) -> None:
        """Append the caller's redacted query shape; count a verified query's use."""
        await self._bound()
        await self.connection.execute(
            "INSERT INTO nova_meta.audit_semantic_query_usage (usage_id,owner_name,"
            "semantic_model_id,model_fingerprint,metric_names,dimension_names,filter_shape,"
            "time_grain,execution_latency_ms,scan_bytes,succeeded,active_role,"
            "security_context_version,semantic_version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (usage["usage_id"], self.actor.principal, usage["semantic_model_id"],
             usage["model_fingerprint"], Jsonb(usage.get("metrics") or []),
             Jsonb(usage.get("dimensions") or []), Jsonb(usage.get("filter_shape") or []),
             usage.get("time_grain"), usage.get("execution_latency_ms"), None,
             bool(usage["succeeded"]), self.actor.active_role,
             self.actor.security_context_version, usage.get("semantic_version")),
        )
        if verified_query_id:
            await self.connection.execute(
                "UPDATE nova_meta.config_semantic_verified_queries SET usage_count=usage_count+1,"
                "success_count=success_count+%s WHERE verified_query_id=%s AND owner_name=%s",
                (1 if usage["succeeded"] else 0, verified_query_id, self.actor.principal),
            )

    async def semantic_usage(
        self, semantic_model_id: str, model_fingerprint: str, semantic_version: int, *,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """The caller's own usage for one model version, newest first."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.audit_semantic_query_usage WHERE owner_name=%s "
            "AND active_role=%s AND security_context_version=%s AND semantic_model_id=%s "
            "AND model_fingerprint=%s AND semantic_version=%s "
            "ORDER BY created_at DESC,usage_id DESC LIMIT %s",
            (self.actor.principal, self.actor.active_role, self.actor.security_context_version,
             semantic_model_id, model_fingerprint, semantic_version, min(max(limit, 1), 2000)),
        )

    async def semantic_usage_for_models(
        self, semantic_model_ids: list[str], *, limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """The caller's own usage across the given models (any version), newest first."""
        await self._bound()
        if not semantic_model_ids:
            return []
        return await self._all(
            "SELECT * FROM nova_meta.audit_semantic_query_usage WHERE owner_name=%s "
            "AND active_role=%s AND security_context_version=%s "
            "AND semantic_model_id=ANY(%s) "
            "ORDER BY created_at DESC,usage_id DESC LIMIT %s",
            (self.actor.principal, self.actor.active_role, self.actor.security_context_version,
             [str(item) for item in semantic_model_ids], min(max(limit, 1), 2000)),
        )


# ── Transaction boundary ──────────────────────────────────────────────────


def scope_actor(scope: Any) -> SecurityContext:
    """The metadata actor of an Intelligence ``Scope`` built from a validated session.

    Scheduled handlers carry the scope of the account they run for (a monitor's
    owner, a News execution account), so their writes stay bound to it.
    """
    return SecurityContext(
        principal=scope.principal,
        active_role=scope.active_role,
        session_id=getattr(scope, "session_id", None),
        security_context_version=scope.security_context_version,
    )


def user_actor(user: dict[str, Any]) -> SecurityContext:
    """The metadata actor of a request's session (``get_current_user``); 403 if invalid."""
    from app.modules.assistant.security import session_security

    return session_security(user)


def service_actor(service: str) -> SecurityContext:
    """An explicit service-principal context for maintenance and scheduled work."""
    return SecurityContext(
        principal=f"nova-service:{service}", active_role="NOVA_SERVICE",
        principal_type="SERVICE",
    )


@asynccontextmanager
async def intelligence_metadata(
    actor: SecurityContext, *, read_only: bool = False,
) -> AsyncIterator[IntelligenceMetadataRepository]:
    """One actor-bound PG transaction holding the Intelligence repository.

    There is no ``NOVA_SYSTEM`` fallback: without an installed metadata
    database the call fails with 503.
    """
    from app.core.metadata_scope import metadata_transaction

    async with metadata_transaction(actor, read_only=read_only) as scope:
        yield IntelligenceMetadataRepository(scope.connection, scope.actor)
