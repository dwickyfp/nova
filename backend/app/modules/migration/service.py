"""Migration Connector service — assessment + dry-run orchestration.

The service is the seam that keeps the domain pure: it resolves the registered
**source**, reads metadata over a connection to it, classifies through
``verdicts``, and guarantees every string that can carry a credential is passed
through the **existing** ``sql_guard.redact_sql_credentials`` before it leaves.
There is deliberately no second redactor here (Ruling 3 hardening: reuse, do not
rebuild).

QA Finding 1 (High): enumerate and dry-run take a ``source`` name and read from
that source's cluster. If the source is absent or unknown the request fails
closed — the local engine is never used as a silent fallback. QA Finding 2
(Medium): source registration and dry-run write ``NOVA_SYSTEM.AUDIT_LOG`` rows
through the existing ``app.common.audit`` writer; no new audit path is added.

Execute is not part of this module. The only engine interaction is
``engine.status()``, which is a filesystem check.
"""

from __future__ import annotations

from app.common.audit import write_audit_log
from app.common.sql_guard import CredentialsRedactionError, redact_sql_credentials
from app.modules.migration import verdicts
from app.modules.migration.engine import migration_engine
from app.modules.migration.repository import migration_repo
from app.modules.migration.schemas import (
    DryRunItem,
    DryRunResponse,
    DryRunSummary,
    EngineStatusResponse,
    EnumerateResponse,
    MigrationVerdict,
    ObjectKind,
    SourceConnectionListResponse,
    SourceConnectionResponse,
    SourceObject,
)
from app.modules.migration.source import (
    SourceConnection,
    SourceConnectionError,
    connection_from_row,
    open_source_connection,
)

#: Map the repository's string kinds to the schema enum. Kept explicit so a new
#: object family is a visible, typed addition rather than a silent skip.
_KIND_BY_NAME: dict[str, ObjectKind] = {
    "table": ObjectKind.TABLE,
    "view": ObjectKind.VIEW,
    "materialized_view": ObjectKind.MATERIALIZED_VIEW,
    "function": ObjectKind.FUNCTION,
    "task": ObjectKind.TASK,
    "pipe": ObjectKind.PIPE,
    "masking_policy": ObjectKind.MASKING_POLICY,
    "row_access_policy": ObjectKind.ROW_ACCESS_POLICY,
}


class MigrationService:
    """Assessment + dry-run. Never executes a migration."""

    # ── Source connections ──────────────────────────────────────

    async def list_sources(self) -> SourceConnectionListResponse:
        rows = await migration_repo.list_sources()
        connections = [SourceConnectionResponse(**row) for row in rows]
        return SourceConnectionListResponse(connections=connections, count=len(connections))

    async def create_source(
        self,
        *,
        name: str,
        host: str,
        port: int,
        username: str,
        secret_ref: str,
        comment: str,
        username_actor: str,
    ) -> SourceConnectionResponse:
        """Register a source cluster address and audit the action.

        Persists the address and the *secret reference* only — never a password
        value. The audit row records the connection name, never the reference or
        a credential.
        """
        created = await migration_repo.create_source(
            name=name,
            host=host,
            port=port,
            username=username,
            secret_ref=secret_ref,
            comment=comment,
            created_by=username_actor,
        )
        await write_audit_log(
            event_type="migration",
            user_name=username_actor,
            action="register_source",
            object_type="migration_source",
            object_name=name,
            status="success",
        )
        return SourceConnectionResponse(**created)

    async def resolve_source(self, name: str) -> SourceConnection:
        """Resolve a registered source to a cluster address; fail closed.

        Raises ``SourceConnectionError`` when the source is unknown or cannot be
        addressed. Callers must treat that as terminal — reading the local engine
        instead is exactly the High finding this revision fixes.
        """
        row = await migration_repo.get_source(name)
        if row is None:
            raise SourceConnectionError(f"Unknown migration source '{name}'")
        return connection_from_row(row)

    # ── Enumeration ─────────────────────────────────────────────

    async def enumerate(self, source_name: str, database: str) -> EnumerateResponse:
        """Enumerate every migratable-family object in ``database`` on ``source``.

        Enumeration is intentionally DDL-free for the bulk listing: shipping a
        definition for every object on the first pass is both expensive and
        unnecessary, and ``dry-run`` fetches the DDL it needs with the right
        per-kind surface. MVs come from ``materialized_views`` — a test asserts
        this — never from ``information_schema.tables``.
        """
        source = await self.resolve_source(source_name)
        async with open_source_connection(source) as conn:
            objects = await self._enumerate_over_connection(conn, database)
        return EnumerateResponse(database=database, objects=objects, count=len(objects))

    async def _enumerate_over_connection(self, conn, database: str) -> list[SourceObject]:
        """Read all object families over an already-open source connection."""
        raw: list[dict] = []
        raw.extend(await migration_repo.list_tables(conn, database))
        raw.extend(await migration_repo.list_views(conn, database))
        raw.extend(await migration_repo.list_materialized_views(conn, database))
        raw.extend(await migration_repo.list_functions(conn, database))
        raw.extend(await migration_repo.list_tasks(conn, database))
        raw.extend(await migration_repo.list_pipes(conn, database))
        raw.extend(await migration_repo.list_masking_policies(conn, database))
        raw.extend(await migration_repo.list_row_access_policies(conn, database))
        return [self._to_source_object(database, item) for item in raw]

    # ── Dry-run ─────────────────────────────────────────────────

    async def dry_run(
        self, source_name: str, database: str, objects: list[str], *, actor: str
    ) -> DryRunResponse:
        """Classify each object on ``source``. Read-only; never touches the engine.

        ``objects`` narrows the selection by name; an empty list assesses
        everything enumeration finds. Objects that are not found are reported as
        ``skipped`` with an explicit reason so an operator never mistakes a typo
        for a clean run. An audit row records the assessment without any
        credential.
        """
        source = await self.resolve_source(source_name)
        async with open_source_connection(source) as conn:
            enumerated = await self._enumerate_over_connection(conn, database)

            selected = {name for name in objects}
            candidates = [obj for obj in enumerated if not selected or obj.name in selected]

            items: list[DryRunItem] = []
            for obj in candidates:
                items.append(await self._classify_object(conn, database, obj))

            for missing in sorted(selected - {obj.name for obj in enumerated}):
                items.append(
                    DryRunItem(
                        name=missing,
                        kind=ObjectKind.TABLE,
                        verdict=MigrationVerdict.SKIPPED,
                        reason="Object was not found on the source cluster.",
                    )
                )

        summary = DryRunSummary()
        for item in items:
            if item.verdict is MigrationVerdict.MIGRATABLE:
                summary.migratable += 1
            elif item.verdict is MigrationVerdict.LOSSY:
                summary.lossy += 1
            else:
                summary.skipped += 1

        await write_audit_log(
            event_type="migration",
            user_name=actor,
            action="dry_run",
            object_type="migration_source",
            object_name=source_name,
            status="success",
            database_name=database,
            rows_affected=summary.total,
        )

        status = migration_engine.status()
        return DryRunResponse(
            database=database,
            items=items,
            summary=summary,
            engine_available=status.available,
            engine_path=status.resolved_path,
        )

    async def engine_status(self) -> EngineStatusResponse:
        status = migration_engine.status()
        return EngineStatusResponse(
            available=status.available,
            configured_path=status.configured_path,
            resolved_path=status.resolved_path,
            reason=status.reason,
        )

    # ── Internals ───────────────────────────────────────────────

    def _to_source_object(self, database: str, item: dict) -> SourceObject:
        kind = _KIND_BY_NAME[item["kind"]]
        extra: dict[str, str] = {}
        for key in ("refresh_type", "signature", "return_type", "function_type", "properties"):
            value = item.get(key)
            if value is not None:
                extra[key] = str(value)
        return SourceObject(name=item["name"], kind=kind, database=database, extra=extra)

    async def _classify_object(self, conn, database: str, obj: SourceObject) -> DryRunItem:
        """Fetch the right DDL for the kind, redact it, then classify."""
        if obj.kind is ObjectKind.TABLE:
            verdict = verdicts.classify_table()
            detail = await migration_repo.get_table_ddl(conn, database, obj.name)
        elif obj.kind is ObjectKind.VIEW:
            verdict = verdicts.classify_view()
            detail = await migration_repo.get_view_ddl(conn, database, obj.name)
        elif obj.kind is ObjectKind.MATERIALIZED_VIEW:
            verdict = verdicts.classify(obj.kind, refresh_type=obj.extra.get("refresh_type"))
            detail = await migration_repo.get_materialized_view_ddl(conn, database, obj.name)
        elif obj.kind is ObjectKind.FUNCTION:
            verdict = verdicts.classify(obj.kind, function_type=obj.extra.get("function_type"))
            detail = None
        elif obj.kind is ObjectKind.TASK:
            verdict = verdicts.classify_task()
            detail = None
        elif obj.kind is ObjectKind.PIPE:
            verdict = verdicts.classify_pipe()
            detail = None
        else:
            verdict = verdicts.classify(obj.kind)
            detail = None

        return DryRunItem(
            name=obj.name,
            kind=obj.kind,
            verdict=verdict.verdict,
            reason=self._reason_with_notes(verdict.reason, verdict.notes),
            detail=self._filter_sensitive(detail),
        )

    @staticmethod
    def _reason_with_notes(reason: str, notes: tuple[str, ...]) -> str:
        if not notes:
            return reason
        return reason + " " + " ".join(notes)

    @staticmethod
    def _filter_sensitive(detail: str | None) -> str | None:
        """Apply the Nova-side sensitive-property filter to an engine DDL.

        This is the Ruling 3 hardening: ``SHOW CREATE MATERIALIZED VIEW`` has no
        ``hidePassword`` equivalent, so a future or custom MV property that
        holds a secret would be emitted verbatim. Reusing
        ``redact_sql_credentials`` means there is one redaction rule in Nova, not
        two. Fails closed: a statement the redactor refuses is replaced by a
        placeholder rather than returned raw.
        """
        if not detail:
            return detail
        try:
            return redact_sql_credentials(detail)
        except CredentialsRedactionError:
            return "[redacted: unredactable credential value]"


migration_service = MigrationService()
