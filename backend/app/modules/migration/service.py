"""Migration Connector service — assessment + dry-run orchestration.

The service is the seam that keeps the domain pure: it reads metadata through
the repository, classifies through ``verdicts``, and guarantees every string that
can carry a credential is passed through the **existing**
``sql_guard.redact_sql_credentials`` before it leaves. There is deliberately no
second redactor here (Ruling 3 hardening: reuse, do not rebuild).

Execute is not part of this module. The only engine interaction is
``engine.status()``, which is a filesystem check.
"""

from __future__ import annotations

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
        self, *, name: str, storage_connection: str, comment: str, username: str
    ) -> SourceConnectionResponse:
        created = await migration_repo.create_source(
            name=name,
            storage_connection=storage_connection,
            comment=comment,
            username=username,
        )
        return SourceConnectionResponse(**created)

    # ── Enumeration ─────────────────────────────────────────────

    async def enumerate(self, database: str) -> EnumerateResponse:
        """Enumerate every migratable-family object in ``database``.

        Enumeration is intentionally DDL-free for the bulk listing: shipping a
        definition for every object on the first pass is both expensive and
        unnecessary, and ``dry-run`` fetches the DDL it needs with the right
        per-kind surface. MVs come from ``materialized_views`` — a test asserts
        this — never from ``information_schema.tables``.
        """
        raw: list[dict] = []
        raw.extend(await migration_repo.list_tables(database))
        raw.extend(await migration_repo.list_views(database))
        raw.extend(await migration_repo.list_materialized_views(database))
        raw.extend(await migration_repo.list_functions(database))
        raw.extend(await migration_repo.list_tasks(database))
        raw.extend(await migration_repo.list_pipes(database))
        raw.extend(await migration_repo.list_masking_policies(database))
        raw.extend(await migration_repo.list_row_access_policies(database))

        objects = [self._to_source_object(database, item) for item in raw]
        return EnumerateResponse(database=database, objects=objects, count=len(objects))

    # ── Dry-run ─────────────────────────────────────────────────

    async def dry_run(self, database: str, objects: list[str]) -> DryRunResponse:
        """Classify each object. Read-only; never touches the engine.

        ``objects`` narrows the selection by name; an empty list assesses
        everything enumeration finds. Objects that are not found are reported as
        ``skipped`` with an explicit reason so an operator never mistakes a typo
        for a clean run.
        """
        enumerated = await self.enumerate(database)
        selected = {name for name in objects}
        candidates = [obj for obj in enumerated.objects if not selected or obj.name in selected]

        items: list[DryRunItem] = []
        for obj in candidates:
            items.append(await self._classify_object(database, obj))

        for missing in sorted(selected - {obj.name for obj in enumerated.objects}):
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

    async def _classify_object(self, database: str, obj: SourceObject) -> DryRunItem:
        """Fetch the right DDL for the kind, redact it, then classify."""
        if obj.kind is ObjectKind.TABLE:
            verdict = verdicts.classify_table()
            detail = await migration_repo.get_table_ddl(database, obj.name)
        elif obj.kind is ObjectKind.VIEW:
            verdict = verdicts.classify_view()
            detail = await migration_repo.get_view_ddl(database, obj.name)
        elif obj.kind is ObjectKind.MATERIALIZED_VIEW:
            verdict = verdicts.classify(obj.kind, refresh_type=obj.extra.get("refresh_type"))
            detail = await migration_repo.get_materialized_view_ddl(database, obj.name)
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
