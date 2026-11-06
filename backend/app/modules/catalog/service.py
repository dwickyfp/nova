"""Nova Catalog service: catalogs as the caller sees them, described by the store.

* **Visibility is the engine's.** A catalog is listed only when the caller's own
  session lists it under their active role (:mod:`.visibility`); the store adds
  description, never visibility.
* **Changes run as the caller.** ``CREATE/ALTER/DROP CATALOG`` goes through the
  shared ``QueryService`` pipeline on the caller's connection, so the engine and
  Ranger decide who may manage catalogs, and the guard, audit row, and
  credential redaction apply exactly as for a typed statement.
* **No secret leaves.** Storage secrets are resolved by the connector at
  statement-build time; the store keeps a connection *name*; ``SHOW CREATE
  CATALOG`` is redacted before it is returned (StarRocks 4.1.4 echoes
  ``aws.s3.session_token`` and ``hive.metastore.password`` in full).
* **Refresh is an admin action**, gated on a security-admin active role and
  audited, including refusals.
"""

from __future__ import annotations

import logging
from typing import Any

from app.common.audit import write_audit_log
from app.common.sql_guard import redact_sql_credentials
from app.core.database import db
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.access_control.service import AccessControlError, AccessControlService
from app.modules.query.service import query_service

from . import visibility
from .connectors import CatalogError, connector_for, safe_catalog_name
from .repository import CatalogRepository, catalog_repo, credential_free
from .schemas import (
    CatalogColumn,
    CatalogDetail,
    CatalogListResponse,
    CatalogObject,
    CatalogSummary,
    ExternalCatalogAlter,
    SyncRun,
)
from .sync import nova_now, reconcile_catalogs, sync_catalog


async def ensure_metadata_schema() -> None:
    """Create the securable registry, business metadata, lineage, and link tables."""
    from .lineage import lineage_repo
    from .links import link_repo
    from .metadata import metadata_repo
    from .securables import securable_repo

    await securable_repo.ensure_schema()
    await metadata_repo.ensure_schema()
    await lineage_repo.ensure_schema()
    await link_repo.ensure_schema()

log = logging.getLogger(__name__)


class CatalogAccessDenied(CatalogError):
    """The caller may not perform this catalog action."""


class CatalogService:
    def __init__(self, repo: CatalogRepository | None = None) -> None:
        self._repo = repo

    @property
    def repo(self) -> CatalogRepository:
        return self._repo or catalog_repo

    async def ensure_schema(self) -> None:
        """Create the store tables and copy legacy catalog rows once."""
        await self.repo.ensure_schema()
        await ensure_metadata_schema()
        copied = await self.repo.migrate_legacy_catalogs(nova_now())
        if copied:
            log.info("Copied %d legacy external catalog rows into the catalog store", copied)

    # ── Read path ───────────────────────────────────────────────

    async def list_catalogs(self, user: dict) -> CatalogListResponse:
        stored = {row["name"]: row for row in await self.repo.list_catalogs()}
        catalogs = [
            _summary(visible, stored.get(visible["name"]))
            for visible in await visibility.visible_catalogs(user)
        ]
        return CatalogListResponse(catalogs=catalogs, count=len(catalogs))

    async def _visible(self, name: str, user: dict) -> dict | None:
        name = safe_catalog_name(name)
        for visible in await visibility.visible_catalogs(user):
            if visible["name"] == name:
                return visible
        return None

    async def get_catalog(self, name: str, user: dict) -> CatalogDetail | None:
        """Detail of a catalog the caller can see, or ``None``."""
        visible = await self._visible(name, user)
        if visible is None:
            return None
        stored = await self.repo.get_catalog(visible["name"])
        return CatalogDetail(
            **_summary(visible, stored).model_dump(),
            metastore_type=(stored or {}).get("metastore_type"),
            metastore_uri=(stored or {}).get("metastore_uri"),
            storage_connection=(stored or {}).get("storage_connection"),
            properties=credential_free((stored or {}).get("properties")),
            create_statement=await redacted_create_statement(visible["name"]),
            created_at=(stored or {}).get("created_at"),
            created_by=(stored or {}).get("created_by"),
        )

    async def list_objects(
        self, name: str, database: str, user: dict
    ) -> list[CatalogObject] | None:
        """Relations of one database the caller can list, described by the store."""
        visible = await self._visible(name, user)
        if visible is None:
            return None
        if database not in visible["databases"] and database not in (
            # A database made outside Nova (raw SQL) after the cached listing
            # was taken: ask the engine again, as the caller, before a 404.
            await visibility.visible_databases(user, visible["name"], fresh=True)
        ):
            return None
        relations = await visibility.visible_relations(user, visible["name"], database)
        stored = {
            row["object_name"]: row
            for row in await self.repo.list_objects(visible["name"], database)
            if row["object_type"] != "database"
        }
        objects = []
        for relation, kind in sorted(relations.items()):
            row = stored.get(relation) or {}
            objects.append(
                CatalogObject(
                    catalog_name=visible["name"],
                    database_name=database,
                    object_name=relation,
                    object_type=row.get("object_type")
                    or ("view" if kind == "VIEW" else "table"),
                    engine=row.get("engine"),
                    comment=row.get("comment"),
                    row_count=row.get("row_count"),
                    data_size=row.get("data_size"),
                    last_seen_at=row.get("last_seen_at"),
                )
            )
        return objects

    async def columns(
        self, name: str, database: str, relation: str, user: dict
    ) -> list[CatalogColumn] | None:
        """Stored columns of a relation the caller can list, or ``None``."""
        name = safe_catalog_name(name)
        if relation not in await visibility.visible_relations(user, name, database):
            return None
        return [
            CatalogColumn(
                ordinal=row["ordinal"],
                name=row["column_name"],
                data_type=row.get("data_type"),
                is_nullable=row.get("is_nullable"),
                comment=row.get("column_comment"),
            )
            for row in await self.repo.list_columns(name, database, relation)
        ]

    async def sync_runs(self, name: str, user: dict, limit: int = 20) -> list[SyncRun] | None:
        visible = await self._visible(name, user)
        if visible is None:
            return None
        return [SyncRun(**row) for row in await self.repo.list_sync_runs(visible["name"], limit)]

    # ── Changes, as the caller ──────────────────────────────────

    async def _run_as_caller(self, sql: str, user: dict) -> None:
        """Execute one catalog statement on the caller's connection.

        ``confirm_destructive=True`` because the API call is the confirmation
        for ``DROP CATALOG``; the hard guard still runs regardless.
        """
        result = await query_service.execute(
            sql=sql,
            username=user["username"],
            encrypted_password=user.get("encrypted_password") or "",
            session_id=user.get("session_id"),
            role=user.get("active_role"),
            confirm_destructive=True,
            connection=user.get("connection"),
            security_context_version=int(user.get("security_context_version") or 1),
        )
        if result.error:
            raise CatalogError(result.error)

    async def _after_change(self, name: str, trigger: str) -> None:
        await visibility.bump()
        try:
            await sync_catalog(name, trigger=trigger, repo=self.repo)
        except Exception:  # noqa: BLE001 - the next scheduled pass catches up
            log.warning("Catalog sync after %s of %s failed", trigger, name, exc_info=True)

    async def alter(
        self, name: str, body: ExternalCatalogAlter, user: dict
    ) -> CatalogDetail | None:
        name = safe_catalog_name(name)
        existing = await self.repo.get_catalog(name)
        connector = connector_for((existing or {}).get("catalog_type") or "iceberg")
        await self._run_as_caller(connector.build_alter_sql(name, body), user)
        if existing:
            await self.repo.upsert_catalog(
                {
                    **existing,
                    "metastore_uri": body.metastore_uri or existing.get("metastore_uri"),
                    "comment": body.comment
                    if body.comment is not None
                    else existing.get("comment"),
                    "properties": {**existing.get("properties", {}), **body.properties},
                    "updated_at": nova_now(),
                }
            )
        await self._after_change(name, "alter")
        return await self.get_catalog(name, user)

    async def drop(self, name: str, user: dict) -> None:
        name = safe_catalog_name(name)
        existing = await self.repo.get_catalog(name)
        connector = connector_for((existing or {}).get("catalog_type") or "iceberg")
        await self._run_as_caller(connector.build_drop_sql(name), user)
        if existing:
            now = nova_now()
            await self.repo.upsert_catalog({**existing, "dropped_at": now, "updated_at": now})
            await self.repo.retire_catalog_objects(name, now)
        await visibility.bump()

    # ── Refresh (security admin) ────────────────────────────────

    async def refresh(self, name: str | None, user: dict) -> list[SyncRun]:
        """Sync one catalog, or reconcile every catalog when ``name`` is ``None``.

        A full refresh also harvests lineage and re-links Semantic Views and
        entities, as a scheduled pass does.
        """
        target = safe_catalog_name(name) if name else "*"
        try:
            # A SQL caller arrives with its validated context; a REST caller
            # with its session.
            security = user.get("security") or SecurityContext.from_session(user)
            AccessControlService.require_security_admin(security)
        except (AccessControlError, SecurityContextError) as exc:
            await _audit(user, target, "DENIED", str(exc))
            raise CatalogAccessDenied(str(exc)) from exc
        try:
            if name:
                stored = await self.repo.get_catalog(target)
                if stored is None or stored.get("dropped_at") is not None:
                    runs = [
                        run
                        for run in await reconcile_catalogs(trigger="manual", repo=self.repo)
                        if run["catalog_name"] == target
                    ]
                    if not runs:
                        raise CatalogError(f"Catalog '{target}' does not exist")
                else:
                    runs = [await sync_catalog(target, trigger="manual", repo=self.repo)]
            else:
                runs = await reconcile_catalogs(trigger="manual", repo=self.repo)
                from .refresher import metadata_pass

                await metadata_pass()
        except Exception as exc:
            await _audit(user, target, "ERROR", str(exc))
            raise
        await visibility.bump()
        failed = [run for run in runs if run["status"] != "ok"]
        await _audit(
            user,
            target,
            "ERROR" if failed else "SUCCESS",
            failed[0].get("error") if failed else None,
        )
        return [SyncRun(**run) for run in runs]


def _summary(visible: dict, stored: dict | None) -> CatalogSummary:
    stored = stored or {}
    engine_type = str(visible.get("type") or "Internal")
    return CatalogSummary(
        name=visible["name"],
        kind=stored.get("kind") or ("native" if engine_type.lower() == "internal" else "external"),
        catalog_type=stored.get("catalog_type") or engine_type.lower(),
        managed=bool(stored.get("managed")),
        source=stored.get("source") or "engine",
        comment=visible.get("comment") or stored.get("comment"),
        status=stored.get("status"),
        last_sync_at=stored.get("last_sync_at"),
        last_sync_error=stored.get("last_sync_error"),
        databases=list(visible.get("databases") or []),
    )


async def redacted_create_statement(name: str) -> str | None:
    """``SHOW CREATE CATALOG`` with every credential masked, or ``None``.

    Runs on the system pool only after the caller's visibility gate; redaction
    is the guarantee, not the engine's own masking.
    """
    name = safe_catalog_name(name)
    try:
        result = await db.execute_system(f"SHOW CREATE CATALOG `{name}`")
    except Exception as exc:  # noqa: BLE001 - e.g. the internal catalog
        log.debug("SHOW CREATE CATALOG %s failed: %s", name, exc)
        return None
    rows: list[Any] = result.get("rows") or []
    if not rows or len(rows[0]) < 2 or not rows[0][1]:
        return None
    return redact_sql_credentials(str(rows[0][1]))


async def _audit(user: dict, target: str, status: str, error: str | None) -> None:
    try:
        await write_audit_log(
            event_type="CATALOG",
            user_name=str(user.get("username") or ""),
            action="REFRESH_METADATA",
            object_type="CATALOG",
            object_name=target,
            status=status,
            error_message=redact_sql_credentials(error) if error else None,
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
            security_context_version=user.get("security_context_version"),
        )
    except Exception:  # noqa: BLE001 - audit is best-effort, never the failure
        log.exception("Could not write catalog refresh audit entry")


catalog_service = CatalogService()
