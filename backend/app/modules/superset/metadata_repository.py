"""Superset author roles, the Dataviz publication journal and the Semantic View
dataset map in PostgreSQL.

The prepared replacement for the ``NOVA_SYSTEM`` paths of ``access.py``,
``publication.py``, ``semantic.py`` and ``dashboard_context.py``, switched in at
the all-domain cutover. Superset roles gate product features only; Ranger
decides which rows a viewer sees, and dashboards follow roles in Superset
(``DASHBOARD_RBAC``). Compared with the source it:

* replaces the author role set only from the set revision the caller read, in
  one transaction with its audit, so two administrators cannot interleave;
* keeps each publication journal row with the person and role that started
  it; another person never reads it, and only a fixed projection of successful
  publications' business context is shared for dashboards a viewer already sees;
* refuses a journal transition out of ``SUCCEEDED`` or ``FAILED_FINAL``.
"""

from __future__ import annotations

import re
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
#: Journal fields the saga may change after an operation starts.
_PROGRESS = (
    "status", "target_mode", "dashboard_id", "chart_id", "dataset_id", "artifact_id",
    "created_objects", "query_fingerprint", "visualization_fingerprint", "strategy_id",
    "revision_before", "revision_after", "business_context", "error",
)
UNRESOLVED = ("NEEDS_RECONCILIATION", "FAILED_RETRYABLE", "ATTACHING", "COMPENSATING")


class SupersetMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Superset metadata requires an active metadata transaction")
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

    # ── Author roles ──────────────────────────────────────────────────────

    async def author_roles(self) -> tuple[frozenset[str], int]:
        """The roles whose holders author in Superset, and the set revision read."""
        await self._bound()
        head = await self._one(
            "SELECT revision FROM nova_meta.config_bi_author_role_set WHERE singleton", (),
        )
        rows = await self._all(
            "SELECT role_name FROM nova_meta.config_bi_author_roles ORDER BY role_name", (),
        )
        return frozenset(row["role_name"] for row in rows), int(head["revision"]) if head else 0

    async def replace_author_roles(
        self, roles: list[str], *, expected_revision: int,
    ) -> tuple[list[str], int]:
        """Replace the author role set from ``expected_revision`` (0: never set)."""
        await self._bound()
        if self.actor.active_role != "ACCOUNTADMIN":
            raise MetadataPermissionDenied()
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("A non-negative revision is required")
        wanted = sorted(set(roles))
        if not all(_ROLE_NAME.fullmatch(role) for role in wanted):
            raise ValueError("Invalid role name")
        if expected_revision == 0:
            head = await self._one(
                "INSERT INTO nova_meta.config_bi_author_role_set (singleton,revision) "
                "VALUES (true,1) ON CONFLICT (singleton) DO NOTHING RETURNING revision", (),
            )
        else:
            head = await self._one(
                "UPDATE nova_meta.config_bi_author_role_set SET revision=revision+1 "
                "WHERE singleton AND revision=%s RETURNING revision", (expected_revision,),
            )
        if head is None:
            raise MetadataConflict()
        await self.connection.execute(
            "DELETE FROM nova_meta.config_bi_author_roles WHERE role_name<>ALL(%s)", (wanted,),
        )
        for role in wanted:
            await self.connection.execute(
                "INSERT INTO nova_meta.config_bi_author_roles (role_name,created_by) "
                "VALUES (%s,%s) ON CONFLICT (role_name) DO NOTHING",
                (role, self.actor.principal),
            )
        return wanted, int(head["revision"])

    # ── Publication journal ───────────────────────────────────────────────

    async def start_publication(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Journal a new operation once by id; returns ``(row, created)``."""
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.config_dataviz_publications (operation_id,owner_name,"
            "idempotency_scope,request_fingerprint,status,target_mode,source_thread_id,"
            "source_message_id,source_content_id,active_role,security_context_version) "
            "VALUES (%s,%s,%s,%s,'VALIDATING',%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (operation_id) DO NOTHING RETURNING *",
            (data["operation_id"], self.actor.principal, data["idempotency_scope"],
             data["request_fingerprint"], data.get("target_mode"),
             data.get("source_thread_id"), data.get("source_message_id"),
             data.get("source_content_id"), self.actor.active_role,
             self.actor.security_context_version),
        )
        if row is not None:
            return row, True
        existing = await self.get_publication(data["operation_id"])
        if existing is None or (existing["idempotency_scope"], existing["request_fingerprint"]) != (
            data["idempotency_scope"], data["request_fingerprint"],
        ):
            raise MetadataConflict()
        return existing, False

    async def advance_publication(
        self, operation_id: str, from_status: str, changes: dict[str, Any],
    ) -> dict[str, Any]:
        """Record saga progress, only from the status the caller last saw."""
        await self._bound()
        unknown = set(changes) - set(_PROGRESS)
        if unknown or not changes:
            raise ValueError(f"Unsupported publication changes: {sorted(unknown)}")
        values = [Jsonb(value) if field == "business_context" and value is not None else value
                  for field, value in changes.items()]
        row = await self._one(
            "UPDATE nova_meta.config_dataviz_publications SET "
            + ",".join(f"{field}=%s" for field in changes)
            + " WHERE operation_id=%s AND owner_name=%s AND status=%s RETURNING *",
            (*values, operation_id, self.actor.principal, from_status),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def get_publication(self, operation_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_dataviz_publications WHERE operation_id=%s "
            "AND owner_name=%s", (operation_id, self.actor.principal),
        )

    async def publication_by_scope(self, scope: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_dataviz_publications WHERE owner_name=%s "
            "AND idempotency_scope=%s ORDER BY created_at DESC NULLS LAST,operation_id DESC "
            "LIMIT 1", (self.actor.principal, scope),
        )

    async def list_publications(
        self, *, unresolved: bool = False, limit: int = 50,
    ) -> list[dict[str, Any]]:
        await self._bound()
        scope = " AND status=ANY(%s)" if unresolved else ""
        params: tuple[Any, ...] = (self.actor.principal,)
        if unresolved:
            params += (list(UNRESOLVED),)
        return await self._all(
            "SELECT * FROM nova_meta.config_dataviz_publications WHERE owner_name=%s" + scope
            + " ORDER BY created_at DESC NULLS LAST,operation_id DESC LIMIT %s",
            (*params, max(1, min(limit, 200))),
        )

    async def reusable_chart(self, query_fp: str, viz_fp: str) -> dict[str, Any] | None:
        """The caller's last successful publication of the same query and chart."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_dataviz_publications WHERE owner_name=%s "
            "AND status='SUCCEEDED' AND query_fingerprint=%s AND visualization_fingerprint=%s "
            "ORDER BY created_at DESC NULLS LAST LIMIT 1",
            (self.actor.principal, query_fp, viz_fp),
        )

    async def published_contexts(self, chart_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Business context recorded for charts the caller already sees in Superset."""
        await self._bound()
        if not chart_ids:
            return {}
        rows = await self._all(
            "SELECT chart_id,business_context FROM nova_meta.dataviz_published_contexts(%s)",
            ([int(chart_id) for chart_id in chart_ids],),
        )
        return {int(row["chart_id"]): row["business_context"] for row in rows}

    # ── Semantic View datasets ────────────────────────────────────────────

    async def record_semantic_dataset(
        self, view_id: str, name: str, *, status: str, bi_dataset_id: int | None = None,
        version: int | None = None, message: str | None = None,
    ) -> dict[str, Any]:
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.config_semantic_bi_datasets (view_id,dataset_name,"
            "bi_dataset_id,owner_name,version,status,message) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (view_id,dataset_name) DO UPDATE SET bi_dataset_id=EXCLUDED.bi_dataset_id,"
            "owner_name=EXCLUDED.owner_name,version=EXCLUDED.version,status=EXCLUDED.status,"
            "message=EXCLUDED.message RETURNING *",
            (view_id, name, bi_dataset_id, self.actor.principal, version, status,
             (message or "")[:1024] or None),
        )
        assert row is not None
        return row

    async def forget_semantic_dataset(self, view_id: str, name: str) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_semantic_bi_datasets WHERE view_id=%s "
            "AND dataset_name=%s", (view_id, name),
        )
        return cursor.rowcount == 1

    async def synced_datasets(self, view_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_semantic_bi_datasets WHERE view_id=%s "
            "ORDER BY dataset_name", (view_id,),
        )

    async def semantic_views_for(self, dataset_ids: list[int]) -> list[dict[str, Any]]:
        await self._bound()
        if not dataset_ids:
            return []
        return await self._all(
            "SELECT * FROM nova_meta.config_semantic_bi_datasets WHERE bi_dataset_id=ANY(%s)",
            ([int(dataset_id) for dataset_id in dataset_ids],),
        )
