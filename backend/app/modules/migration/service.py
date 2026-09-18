"""Migration Connector service — v1 Assessment + Dry-run.

This service answers two questions and nothing else:

1. **Assessment** — what objects exist on the source deployment?
2. **Dry-run** — for each object, will it migrate, migrate partially (``lossy``),
   or not at all (``skipped``), and why?

There is deliberately no execute path. The cutover is a separate issue gated on
roadmap #7 (backup/restore), and ``ClusterSyncEngine`` has no execute method to
call even if one were wanted.

Credential rule (NOVA-84 / NOVA-85 invariant 1): a source password is used to
open a socket and is never returned, logged, audited, or placed in a reason
string. It is held encrypted in ``MigrationSourceStore``, keyed by the caller's
session; the client only ever sees an opaque ``connection_id``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.common.sql_guard import is_credential_property, redact_sql_credentials
from app.modules.migration.engine import cluster_sync_engine
from app.modules.migration.repository import migration_repo
from app.modules.migration.schemas import (
    DryRunResponse,
    DryRunSummary,
    EnumerateResponse,
    MigrationConnectResponse,
    MigrationEngineStatus,
    ObjectKind,
    SourceObject,
    Verdict,
)
from app.modules.migration.source_store import migration_source_store

log = logging.getLogger(__name__)

#: Why an object cannot move. These strings are the visible product: a declared
#: omission is acceptable, a silent one is the defect (NOVA-84 Ruling 2).
_SKIP_MASKING = (
    "Masking policies have no DDL export surface on the source "
    "(no SHOW CREATE POLICY). The policy cannot be carried; recreate it in Nova."
)
_SKIP_ROW_ACCESS = (
    "Row-access policies have no DDL export surface on the source. The policy "
    "cannot be carried; recreate it in Nova."
)
_LOSSY_TASK = (
    "TASK has no SHOW CREATE on the source; only a partial "
    "information_schema.tasks projection is available. DDL is reconstructed "
    "lossily."
)
_LOSSY_PIPE = (
    "PIPE has no SHOW CREATE on the source; DDL is reconstructed from "
    "information_schema.pipes and the SELECT body may be lossy."
)
_LOSSY_FUNCTION = (
    "Functions have no SHOW CREATE on the source; DDL is reconstructed from "
    "SHOW FULL FUNCTIONS. Non-native (Java/Python) UDF artifacts are not "
    "carried."
)
_LOSSY_SYNC_MV = (
    "Sync materialized view: no export surface emits REFRESH/PARTITION "
    "BY/PROPERTIES for sync MVs, so the definition alone is lossy."
)


class MigrationService:
    """Business logic for the migration wizard's read-only stages."""

    def __init__(self) -> None:
        self._repo = migration_repo
        self._sources = migration_source_store

    # ── Connect ─────────────────────────────────────────────────

    async def connect(
        self,
        connection_id: str,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str | None = None,
    ) -> MigrationConnectResponse:
        """Probe the source deployment, then stash the credential server-side.

        The plaintext password is encrypted into the store and never returned.
        The caller keeps ``connection_id`` and uses it for every later step.
        """
        await self._sources.put(
            connection_id,
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
        )
        source = await self._sources.get(connection_id)
        version = await self._repo.server_version(
            host=source["host"],
            port=source["port"],
            username=source["username"],
            encrypted_password=source["encrypted_password"],
        )
        databases = await self._repo.list_databases(
            host=source["host"],
            port=source["port"],
            username=source["username"],
            encrypted_password=source["encrypted_password"],
        )
        return MigrationConnectResponse(
            connection_id=connection_id,
            connected=True,
            server_version=version,
            database_count=len(databases),
            message="Source reachable.",
        )

    # ── Enumerate ───────────────────────────────────────────────

    async def enumerate(
        self,
        connection_id: str,
        *,
        database: str,
    ) -> EnumerateResponse:
        """Enumerate the migratable objects of one source database.

        Materialized views come from ``information_schema.materialized_views``,
        never ``information_schema.tables``: the latter cannot tell an MV from a
        view, and a view mislabelled as an MV would be exported through the
        wrong DDL surface.
        """
        source = await self._sources.get(connection_id)
        objects = await self._classify_database(database, source)
        return EnumerateResponse(
            database=database,
            objects=objects,
            count=len(objects),
        )

    # ── Dry-run ─────────────────────────────────────────────────

    async def dry_run(
        self,
        connection_id: str,
        *,
        database: str | None = None,
    ) -> DryRunResponse:
        """Assess every requested database and report per-object verdicts."""
        source = await self._sources.get(connection_id)
        if database:
            databases = [database]
        else:
            databases = await self._repo.list_databases(
                host=source["host"],
                port=source["port"],
                username=source["username"],
                encrypted_password=source["encrypted_password"],
            )

        objects: list[SourceObject] = []
        for db in databases:
            objects.extend(await self._classify_database(db, source))

        summary = DryRunSummary(
            migratable=sum(1 for o in objects if o.verdict is Verdict.MIGRATABLE),
            lossy=sum(1 for o in objects if o.verdict is Verdict.LOSSY),
            skipped=sum(1 for o in objects if o.verdict is Verdict.SKIPPED),
            total=len(objects),
        )
        return DryRunResponse(
            database=database,
            objects=objects,
            summary=summary,
            has_lossy=summary.lossy > 0,
            has_skipped=summary.skipped > 0,
            generated_at=datetime.now(UTC),
        )

    # ── Engine status ───────────────────────────────────────────

    def engine_status(self) -> MigrationEngineStatus:
        """Report the operator-provided engine's availability."""
        return cluster_sync_engine.status()

    # ── Classification ──────────────────────────────────────────

    async def _classify_database(
        self,
        database: str,
        source: dict,
    ) -> list[SourceObject]:
        common = {
            "host": source["host"],
            "port": source["port"],
            "username": source["username"],
            "encrypted_password": source["encrypted_password"],
        }
        objects = [
            SourceObject(
                kind=ObjectKind.DATABASE,
                database=database,
                name=database,
                verdict=Verdict.MIGRATABLE,
            )
        ]

        for name in await self._repo.list_tables(database, **common):
            objects.append(
                SourceObject(
                    kind=ObjectKind.TABLE,
                    database=database,
                    name=name,
                    verdict=Verdict.MIGRATABLE,
                )
            )

        for name in await self._repo.list_views(database, **common):
            objects.append(
                SourceObject(
                    kind=ObjectKind.VIEW,
                    database=database,
                    name=name,
                    verdict=Verdict.MIGRATABLE,
                )
            )

        for mv in await self._repo.list_materialized_views(database, **common):
            # Async MVs carry REFRESH/PARTITION BY/PROPERTIES through
            # `SHOW CREATE MATERIALIZED VIEW`; sync MVs have no such surface.
            is_sync = (mv.get("refresh_mode") or "").upper() == "SYNC"
            objects.append(
                SourceObject(
                    kind=ObjectKind.MATERIALIZED_VIEW,
                    database=database,
                    name=mv["name"],
                    verdict=Verdict.LOSSY if is_sync else Verdict.MIGRATABLE,
                    reason=_LOSSY_SYNC_MV if is_sync else None,
                )
            )

        for name in await self._repo.list_tasks(database, **common):
            objects.append(
                SourceObject(
                    kind=ObjectKind.TASK,
                    database=database,
                    name=name,
                    verdict=Verdict.LOSSY,
                    reason=_LOSSY_TASK,
                )
            )

        for name in await self._repo.list_pipes(database, **common):
            objects.append(
                SourceObject(
                    kind=ObjectKind.PIPE,
                    database=database,
                    name=name,
                    verdict=Verdict.LOSSY,
                    reason=_LOSSY_PIPE,
                )
            )

        for name in await self._repo.list_functions(database, **common):
            objects.append(
                SourceObject(
                    kind=ObjectKind.FUNCTION,
                    database=database,
                    name=name,
                    verdict=Verdict.LOSSY,
                    reason=_LOSSY_FUNCTION,
                )
            )

        await self._append_policy_findings(database, common, objects)
        return objects

    async def _append_policy_findings(
        self,
        database: str,
        common: dict,
        objects: list[SourceObject],
    ) -> None:
        """Surface masking/row-access policy gaps as explicit ``skipped`` rows.

        StarRocks exposes no metadata view and no ``SHOW CREATE`` for these
        policies, so they cannot always be enumerated per-name. When the source
        does advertise the policy views, the dry-run must surface the rows
        loudly: a policy that disappears without a report is the exact failure
        NOVA-84 Ruling 2 forbids.
        """
        for kind, table, reason in (
            (
                ObjectKind.MASKING_POLICY,
                "information_schema.masking_policies",
                _SKIP_MASKING,
            ),
            (
                ObjectKind.ROW_ACCESS_POLICY,
                "information_schema.row_access_policies",
                _SKIP_ROW_ACCESS,
            ),
        ):
            names = await self._repo.list_policy_names(
                table,
                database,
                host=common["host"],
                port=common["port"],
                username=common["username"],
                encrypted_password=common["encrypted_password"],
            )
            for name in names:
                objects.append(
                    SourceObject(
                        kind=kind,
                        database=database,
                        name=name,
                        verdict=Verdict.SKIPPED,
                        reason=reason,
                    )
                )


def redact_consumed_ddl(ddl: str) -> str:
    """Redact credential values from a consumed DDL statement.

    The Nova-side sensitive-property filter required by NOVA-84 Ruling B. Reuses
    the single credential redactor rather than growing a second one; a caller
    that consumes MV DDL (an L3 path, or the future execute spec) must route the
    text through here before it is stored or shown.
    """
    return redact_sql_credentials(ddl)


def drop_sensitive_properties(properties: dict[str, str]) -> dict[str, str]:
    """Drop credential-named entries from a property map.

    Belt to ``redact_consumed_ddl``'s braces: a property map parsed out of MV
    DDL can carry a secret under a key the ``key = value`` redactor does not
    see. Same rule as the external-catalog read path.
    """
    return {
        key: value
        for key, value in (properties or {}).items()
        if not is_credential_property(key)
    }


migration_service = MigrationService()
