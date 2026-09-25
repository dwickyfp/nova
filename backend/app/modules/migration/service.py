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

Worker-side execution also uses this service to plan, apply DDL, copy table
data, and verify results. The HTTP router only enqueues that work.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.common.audit import write_audit_log
from app.common.sql_guard import CredentialsRedactionError, redact_sql_credentials
from app.core.config import get_storage_connection, settings, to_docker_endpoint
from app.modules.migration import verdicts
from app.modules.migration.data_mover import (
    CopyColumn,
    DataMovementError,
    build_table_copy,
    stage_path_for,
)
from app.modules.migration.engine import migration_engine
from app.modules.migration.planner import ApplyPlan, PlanCandidate, build_plan
from app.modules.migration.preflight import (
    PlanShape,
    PreflightResult,
    analyze_grants,
    parse_grants,
)
from app.modules.migration.repository import migration_repo
from app.modules.migration.schemas import (
    BlockedObjectResponse,
    DatabasesResponse,
    DryRunItem,
    DryRunResponse,
    DryRunSummary,
    EngineStatusResponse,
    EnumerateResponse,
    ExecuteResponse,
    ExecuteStepResult,
    MigrationVerdict,
    ObjectKind,
    PlanResponse,
    PlanStepKind,
    PlanStepResponse,
    PreflightCheckResponse,
    PreflightResponse,
    SourceConnectionListResponse,
    SourceConnectionResponse,
    SourceConnectionTestResponse,
    SourceObject,
    TableCopyResult,
    _migratable_database,
)
from app.modules.migration.source import (
    SourceConnection,
    SourceConnectionError,
    connection_from_row,
    open_source_connection,
)
from app.modules.query.dialect.injector import get_credential_params
from app.modules.query.service import query_service


class MigrationExecuteGateError(RuntimeError):
    """Execute is disabled by configuration (the #7 backup gate)."""


class MigrationExecuteConfirmationError(RuntimeError):
    """The caller did not confirm — a bad target name or unacknowledged omissions."""


class MigrationPreflightError(RuntimeError):
    """The caller lacks a privilege (or storage) the plan needs."""


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
    """Worker-side source assessment, planning, execution, and data copy."""

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

    async def test_source_connection(
        self, *, name: str, host: str, port: int, username: str, secret_ref: str
    ) -> SourceConnectionTestResponse:
        """Authenticate and run a read-only probe without registering the source."""
        source = SourceConnection(
            name=name,
            host=host,
            port=port,
            username=username,
            secret_ref=secret_ref,
        )
        try:
            async with asyncio.timeout(15):
                async with open_source_connection(source) as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT 1")
                        row = await cur.fetchone()
        except SourceConnectionError:
            raise
        except Exception as exc:
            raise SourceConnectionError("Could not validate source connection") from exc
        if row != (1,):
            raise SourceConnectionError("Source connection test returned an unexpected result")
        return SourceConnectionTestResponse(connected=True)

    async def databases(self, source_name: str) -> DatabasesResponse:
        source = await self.resolve_source(source_name)
        async with open_source_connection(source) as conn:
            discovered = await migration_repo.list_databases(conn)
        databases: list[str] = []
        unsupported: list[str] = []
        for name in discovered:
            try:
                databases.append(_migratable_database(name))
            except ValueError:
                unsupported.append(name)
        return DatabasesResponse(
            source=source_name,
            databases=databases,
            count=len(databases),
            unsupported=unsupported,
        )

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

    async def _enumerate_over_connection(
        self, conn, database: str, *, include_global_functions: bool = True
    ) -> list[SourceObject]:
        """Read object families over an already-open source connection.

        Global functions are visible during discovery, but a whole-database
        migration must not copy the same cluster-wide functions for every
        selected database.
        """
        raw: list[dict] = []
        raw.extend(await migration_repo.list_tables(conn, database))
        raw.extend(await migration_repo.list_views(conn, database))
        raw.extend(await migration_repo.list_materialized_views(conn, database))
        raw.extend(await migration_repo.list_functions(conn, database))
        if include_global_functions:
            raw.extend(await migration_repo.list_global_functions(conn))
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
        database-scoped objects. Global functions require explicit selection.
        Objects that are not found are reported as
        ``skipped`` with an explicit reason so an operator never mistakes a typo
        for a clean run. An audit row records the assessment without any
        credential.
        """
        source = await self.resolve_source(source_name)
        async with open_source_connection(source) as conn:
            enumerated = await self._enumerate_over_connection(
                conn, database, include_global_functions=bool(objects)
            )

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

    # ── Apply planning (read-only) ──────────────────────────────

    async def plan(
        self,
        source_name: str,
        database: str,
        *,
        target_database: str,
        objects: list[str],
        create_database: bool,
        actor: str,
    ) -> PlanResponse:
        """Build a dependency-ordered apply plan. Executes nothing.

        Reuses enumeration + classification so the plan reflects the same objects
        the dry-run reported, then retargets each definition for the target
        database. Objects with no usable definition land in ``blocked`` — the
        operator sees exactly what a cutover would omit before any execute path
        exists. An audit row records the planning action.
        """
        built = await self._build_apply_plan(
            source_name,
            database,
            target_database=target_database,
            objects=objects,
            create_database=create_database,
        )

        await write_audit_log(
            event_type="migration",
            user_name=actor,
            action="plan",
            object_type="migration_source",
            object_name=source_name,
            status="success",
            database_name=database,
            rows_affected=built.step_count,
        )

        return self._to_plan_response(built)

    async def _build_apply_plan(
        self,
        source_name: str,
        database: str,
        *,
        target_database: str,
        objects: list[str],
        create_database: bool,
    ) -> ApplyPlan:
        """Enumerate, classify, and plan — shared by ``plan`` and ``execute``.

        Both callers must see the same plan shape; centralising the pipeline here
        means execution can never drift from what the operator reviewed.
        """
        source = await self.resolve_source(source_name)
        async with open_source_connection(source) as conn:
            enumerated = await self._enumerate_over_connection(
                conn, database, include_global_functions=bool(objects)
            )
            selected = {name for name in objects}
            plan_candidates: list[PlanCandidate] = []
            for obj in enumerated:
                if selected and obj.name not in selected:
                    continue
                item = await self._classify_object(conn, database, obj)
                plan_candidates.append(
                    PlanCandidate(
                        name=item.name,
                        kind=item.kind,
                        verdict=item.verdict,
                        # ``_classify_object`` already applied the sensitive
                        # filter; reuse its output rather than re-running it.
                        ddl=item.detail,
                        scope=obj.extra.get("scope", "database"),
                    )
                )

        resolved_target = target_database or database
        # Clamp replication to what the target (the local engine in v1) can
        # place, so a multi-backend source does not yield an unplaceable table.
        target_replication = await migration_repo.target_replication_num()
        try:
            return build_plan(
                plan_candidates,
                source_database=database,
                target_database=resolved_target,
                create_database=create_database,
                target_replication_num=target_replication,
            )
        except ValueError as exc:
            raise SourceConnectionError(str(exc)) from exc

    # ── Preflight (read-only) ───────────────────────────────────

    async def preflight(
        self,
        source_name: str,
        database: str,
        *,
        target_database: str,
        objects: list[str],
        create_database: bool,
        include_data: bool,
        actor: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> PreflightResponse:
        """Check the caller's target privileges and shared storage before execute.

        Reads the caller's own grants (``SHOW GRANTS`` on the target, as the
        caller) and, for data movement, probes whether the transfer stage is
        reachable. Read-only: nothing is created. A missing privilege is reported
        with the reason it is needed, so execute can fail fast instead of
        half-applying a plan.
        """
        built = await self._build_apply_plan(
            source_name,
            database,
            target_database=target_database,
            objects=objects,
            create_database=create_database,
        )
        return await self._preflight_built_plan(
            built,
            include_data=include_data,
            actor=actor,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
            source_name=source_name,
            database=database,
            audit=True,
        )

    async def _preflight_built_plan(
        self,
        built: ApplyPlan,
        *,
        include_data: bool,
        actor: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None,
        source_name: str,
        database: str,
        audit: bool,
    ) -> PreflightResponse:
        """Preflight an already-built plan.

        ``execute`` calls this with the plan it just built, so the caller's
        grants are checked against the exact plan about to run — no second
        enumeration, and no window where the plan changes between check and run.
        """
        shape = self._plan_shape(built, include_data=include_data)

        grant_rows = await self._read_caller_grants(
            actor=actor,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        granted = parse_grants(grant_rows)
        result = analyze_grants(
            granted=granted,
            target_database=built.target_database,
            create_database=shape.create_database,
            has_tables=shape.has_tables,
            has_views=shape.has_views,
            has_materialized_views=shape.has_materialized_views,
            has_functions=shape.has_functions,
            include_data=include_data,
        )

        storage_ok: bool | None = None
        storage_reason = ""
        if include_data:
            storage_ok, storage_reason = self._storage_preflight()
            result = PreflightResult(
                checks=result.checks,
                storage_checked=True,
                storage_ok=storage_ok,
                storage_reason=storage_reason,
            )

        if audit:
            await write_audit_log(
                event_type="migration",
                user_name=actor,
                action="preflight",
                object_type="migration_source",
                object_name=source_name,
                status="success" if result.ok else "failure",
                database_name=database,
                rows_affected=len(result.missing),
            )

        return PreflightResponse(
            source_database=built.source_database,
            target_database=built.target_database,
            database_step_planned=shape.create_database,
            checks=[
                PreflightCheckResponse(
                    privilege=check.privilege,
                    reason=check.reason,
                    satisfied=check.satisfied,
                )
                for check in result.checks
            ],
            missing=[check.privilege for check in result.missing],
            storage_checked=result.storage_checked,
            storage_ok=result.storage_ok,
            storage_reason=result.storage_reason,
            ok=result.ok,
        )

    @staticmethod
    def _plan_shape(built: ApplyPlan, *, include_data: bool) -> PlanShape:
        kinds = {step.kind for step in built.steps}
        return PlanShape(
            create_database=PlanStepKind.DATABASE in kinds,
            has_tables=PlanStepKind.TABLE in kinds,
            has_views=PlanStepKind.VIEW in kinds,
            has_materialized_views=PlanStepKind.MATERIALIZED_VIEW in kinds,
            has_functions=PlanStepKind.FUNCTION in kinds,
            include_data=include_data,
            tables=[s.object_name for s in built.steps if s.kind is PlanStepKind.TABLE],
        )

    async def _read_caller_grants(
        self,
        *,
        actor: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None,
    ) -> list[tuple]:
        try:
            result = await query_service.execute(
                sql="SHOW GRANTS",
                username=actor,
                encrypted_password=encrypted_password,
                session_id=session_id,
                role=role,
            )
            if getattr(result, "error", None):
                return []
            return [tuple(row) for row in (getattr(result, "rows", None) or [])]
        except Exception:  # noqa: BLE001 - a failed read is reported as no grants
            return []

    @staticmethod
    def _storage_preflight() -> tuple[bool, str]:
        """Whether a transfer stage is configured and its credentials resolve.

        This cannot prove the engine can reach the bucket (that requires the
        engine), but it catches the common misconfiguration — no credentials,
        or a secret reference that does not resolve — before a copy starts.
        """
        try:
            connection = get_storage_connection(None)
            params = get_credential_params(connection.type, None)
        except Exception as exc:  # noqa: BLE001
            return False, f"storage credentials could not be resolved: {exc}"
        if not params:
            return (
                False,
                "no storage credentials are configured; data movement needs a "
                "transfer stage the source and target can both reach",
            )
        if not connection.bucket:
            return False, "the storage connection has no bucket configured"
        return True, ""

    @staticmethod
    def _to_plan_response(built: ApplyPlan) -> PlanResponse:
        return PlanResponse(
            source_database=built.source_database,
            target_database=built.target_database,
            steps=[
                PlanStepResponse(
                    order=step.order,
                    kind=PlanStepKind(step.kind.value),
                    object_name=step.object_name,
                    statement=step.statement,
                    dropped_properties=list(step.dropped_properties),
                )
                for step in built.steps
            ],
            blocked=[
                BlockedObjectResponse(name=obj.name, kind=obj.kind, reason=obj.reason)
                for obj in built.blocked
            ],
            step_count=built.step_count,
        )

    # ── Execute (gated on #7) ───────────────────────────────────

    async def execute(
        self,
        source_name: str,
        database: str,
        *,
        target_database: str,
        objects: list[str],
        create_database: bool,
        acknowledge_omissions: bool,
        confirmation: str,
        actor: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
        include_data: bool = False,
        stage_connection: str = "",
    ) -> ExecuteResponse:
        """Apply the plan to the target. Runs as the caller, never root.

        Three gates, all enforced here so the router stays thin:

        1. ``MIGRATION_EXECUTE_ENABLED`` — the operator's acknowledgement that a
           restorable backup exists (#7). Off by default.
        2. **Omission acknowledgement** — when the plan has lossy or blocked
           objects, the caller must set ``acknowledge_omissions``. The report is
           the product; execution without reading it is refused.
        3. **Confirmation** — when required, ``confirmation`` must equal the
           target database name.

        Statements run through ``query_service`` as the authenticated user, so
        StarRocks RBAC is the real authority and each mutation is audited. A
        failing step does not stop the run (the plan is dependency-ordered, so
        independent branches can still succeed); the result records every step.

        ``include_data`` (11-C) additionally copies rows for every base table
        that was created successfully, using a shared stage as the transfer. It
        requires the source and target to reach the same object storage.
        """
        if not settings.MIGRATION_EXECUTE_ENABLED:
            raise MigrationExecuteGateError(
                "Migration execute is disabled. Set MIGRATION_EXECUTE_ENABLED "
                "only after a restorable backup exists (issue #7)."
            )

        resolved_target = target_database or database
        if settings.MIGRATION_EXECUTE_REQUIRE_CONFIRMATION and confirmation != resolved_target:
            raise MigrationExecuteConfirmationError(
                "Confirmation does not match the target database name."
            )

        # Recompute the plan at execution time — a client-held plan could be
        # stale, and execution must match what the source looks like now.
        built = await self._build_apply_plan(
            source_name,
            database,
            target_database=resolved_target,
            objects=objects,
            create_database=create_database,
        )

        if not acknowledge_omissions:
            raise MigrationExecuteConfirmationError(
                "Set acknowledge_omissions=true to confirm the migration. "
                + (
                    f"{len(built.blocked)} object(s) cannot be migrated and are listed in the plan."
                    if built.blocked
                    else "The plan has no blocked objects."
                )
            )

        # Fail fast on a missing privilege rather than half-applying the plan.
        # Reuse the plan just built so the check matches exactly what will run.
        if settings.MIGRATION_EXECUTE_PREFLIGHT:
            check = await self._preflight_built_plan(
                built,
                include_data=include_data,
                actor=actor,
                encrypted_password=encrypted_password,
                session_id=session_id,
                role=role,
                source_name=source_name,
                database=database,
                audit=False,
            )
            if not check.ok:
                parts = []
                if check.missing:
                    parts.append("missing privileges: " + ", ".join(check.missing))
                if check.storage_ok is False:
                    parts.append("storage: " + (check.storage_reason or "unusable"))
                raise MigrationPreflightError("Preflight failed — " + "; ".join(parts))

        results: list[ExecuteStepResult] = []
        for step in built.steps:
            error: str | None = None
            try:
                result = await query_service.execute(
                    sql=step.statement,
                    username=actor,
                    encrypted_password=encrypted_password,
                    session_id=session_id,
                    role=role,
                    confirm_destructive=True,
                )
                if getattr(result, "error", None):
                    error = str(result.error)
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                error = str(exc)
            results.append(
                ExecuteStepResult(
                    order=step.order,
                    kind=PlanStepKind(step.kind.value),
                    object_name=step.object_name,
                    statement=step.statement,
                    status="failed" if error else "ok",
                    error=error,
                )
            )

        succeeded = sum(1 for r in results if r.status == "ok")
        failed = sum(1 for r in results if r.status == "failed")

        data_results: list[TableCopyResult] = []
        rows_moved = 0
        if include_data:
            tables = self._tables_created(results, built)
            data_results = await self._copy_table_data(
                source_name=source_name,
                source_database=database,
                target_database=resolved_target,
                tables=tables,
                stage_connection=stage_connection,
                actor=actor,
                encrypted_password=encrypted_password,
                session_id=session_id,
                role=role,
            )
            rows_moved = sum(r.rows_imported for r in data_results if r.verified)

        await write_audit_log(
            event_type="migration",
            user_name=actor,
            action="execute",
            object_type="migration_source",
            object_name=source_name,
            status="failure" if failed else "success",
            database_name=database,
            rows_affected=succeeded,
        )

        return ExecuteResponse(
            source_database=database,
            target_database=resolved_target,
            results=results,
            blocked=[
                BlockedObjectResponse(name=obj.name, kind=obj.kind, reason=obj.reason)
                for obj in built.blocked
            ],
            succeeded=succeeded,
            failed=failed,
            skipped=len(built.blocked),
            data=data_results,
            rows_moved=rows_moved,
        )

    # ── Data movement (11-C) ────────────────────────────────────

    @staticmethod
    def _tables_created(results: list[ExecuteStepResult], built: ApplyPlan) -> list[str]:
        """Tables whose create step succeeded — the only safe copy candidates.

        Copying into a table that failed to create (or was never in the plan)
        would be a silent partial move, so the selection is driven by the execute
        results, not by enumeration.
        """
        created = {
            r.object_name for r in results if r.kind is PlanStepKind.TABLE and r.status == "ok"
        }
        return sorted(created)

    async def _copy_table_data(
        self,
        *,
        source_name: str,
        source_database: str,
        target_database: str,
        tables: list[str],
        stage_connection: str,
        actor: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None,
    ) -> list[TableCopyResult]:
        """Copy rows for each table: export on the source, import + verify.

        Export runs over the **source connection**; import runs on the **target**
        through ``query_service`` as the caller. Both must reach the same stage.
        A table that fails is reported, not raised — one bad table must not abort
        the others.
        """
        if not tables:
            return []

        migration_id = uuid4().hex
        connection = get_storage_connection(stage_connection or None)
        stage_root = f"s3://{connection.bucket}/migration-staging"
        credential_params = get_credential_params(connection.type, stage_connection or None)
        if not credential_params:
            raise DataMovementError(
                "No storage credentials are configured for data movement; the "
                "transfer stage cannot be reached."
            )
        # The FILES() endpoint must be reachable **from the engine**, which runs
        # in Docker: a host-side 127.0.0.1 endpoint is rewritten to the internal
        # service name. The full parameter set mirrors the @stage FILES() builder
        # (translator.build_files_function) — the same set the engine is known to
        # accept for MinIO / S3-compatible storage.
        engine_endpoint = to_docker_endpoint(connection.endpoint)
        if engine_endpoint:
            credential_params["aws.s3.endpoint"] = engine_endpoint
            if not engine_endpoint.startswith("https"):
                credential_params["aws.s3.enable_ssl"] = "false"
        credential_params["aws.s3.enable_path_style_access"] = "true"
        credential_params["aws.s3.use_aws_sdk_default_behavior"] = "false"
        credential_params["aws.s3.use_instance_profile"] = "false"
        files_credential_sql = ", ".join(
            f"'{key}'='{value}'" for key, value in credential_params.items()
        )

        source = await self.resolve_source(source_name)
        results: list[TableCopyResult] = []
        async with open_source_connection(source) as conn:
            for table in tables:
                results.append(
                    await self._copy_one_table(
                        conn=conn,
                        source_database=source_database,
                        target_database=target_database,
                        table=table,
                        migration_id=migration_id,
                        stage_root=stage_root,
                        files_credential_sql=files_credential_sql,
                        actor=actor,
                        encrypted_password=encrypted_password,
                        session_id=session_id,
                        role=role,
                    )
                )
        return results

    async def _copy_one_table(
        self,
        *,
        conn,
        source_database: str,
        target_database: str,
        table: str,
        migration_id: str,
        stage_root: str,
        files_credential_sql: str,
        actor: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None,
    ) -> TableCopyResult:
        errors: list[str] = []
        try:
            raw_columns = await migration_repo.list_columns(conn, source_database, table)
            columns = tuple(CopyColumn(name=name, data_type=dtype) for name, dtype in raw_columns)
            stage_path = stage_path_for(stage_root, migration_id, source_database, table)
            plan = build_table_copy(
                source_database=source_database,
                target_database=target_database,
                table=table,
                columns=columns,
                stage_path=stage_path,
                files_credential_sql=files_credential_sql,
            )
        except Exception as exc:  # noqa: BLE001
            return TableCopyResult(
                table=table,
                rows_exported=0,
                rows_imported=0,
                verified=False,
                note="could not build the copy plan",
                errors=[str(exc)],
            )

        source_count_before = await migration_repo.count_rows(conn, source_database, table)
        source_digest = (
            await migration_repo.scalar(conn, plan.digest_sql_source)
            if plan.digest_sql_source
            else None
        )

        # 1. Export on the source.
        export_error = await self._run_on_source(conn, plan.export_sql)
        if export_error:
            errors.append(f"export: {export_error}")

        rows_imported = 0
        if not export_error:
            # 2. Import on the target, as the caller.
            import_error, import_result = await self._run_on_target(
                plan.import_sql,
                actor=actor,
                encrypted_password=encrypted_password,
                session_id=session_id,
                role=role,
            )
            if import_error:
                errors.append(f"import: {import_error}")
            else:
                rows_imported = int(getattr(import_result, "affected_rows", 0) or 0)

        # 3. Verify on the target.
        verified = False
        digest_match: bool | None = None
        if not errors:
            count_error, target_count = await self._scalar_on_target(
                plan.count_sql_target,
                actor=actor,
                encrypted_password=encrypted_password,
                session_id=session_id,
                role=role,
            )
            if count_error:
                errors.append(f"verify count: {count_error}")
            else:
                verified = (
                    source_count_before is not None
                    and target_count is not None
                    and int(source_count_before) == int(target_count)
                )
                if not verified:
                    errors.append(
                        f"row count mismatch: source={source_count_before}, target={target_count}"
                    )
                if plan.digest_sql_target:
                    digest_error, target_digest = await self._scalar_on_target(
                        plan.digest_sql_target,
                        actor=actor,
                        encrypted_password=encrypted_password,
                        session_id=session_id,
                        role=role,
                    )
                    if not digest_error and source_digest is not None and target_digest is not None:
                        # A float compare with a small tolerance: SUM over DOUBLE
                        # is not bit-exact across engines.
                        digest_match = abs(source_digest - target_digest) <= (
                            1e-6 * max(1.0, abs(source_digest))
                        )

        note = ""
        if plan.digest_sql_target is None:
            note = "no numeric column; verification is row-count only"

        return TableCopyResult(
            table=table,
            rows_exported=int(source_count_before or 0),
            rows_imported=rows_imported,
            verified=verified,
            digest_match=digest_match,
            note=note,
            errors=errors,
        )

    @staticmethod
    async def _run_on_source(conn, statement: str) -> str | None:
        """Run a statement on the source connection; return the error or None."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(statement)
            return None
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    async def _run_on_target(
        self,
        statement: str,
        *,
        actor: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None,
    ):
        try:
            result = await query_service.execute(
                sql=statement,
                username=actor,
                encrypted_password=encrypted_password,
                session_id=session_id,
                role=role,
                confirm_destructive=True,
            )
            error = getattr(result, "error", None)
            return (str(error) if error else None), result
        except Exception as exc:  # noqa: BLE001
            return str(exc), None

    async def _scalar_on_target(
        self,
        statement: str,
        *,
        actor: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None,
    ) -> tuple[str | None, float | None]:
        try:
            result = await query_service.execute(
                sql=statement,
                username=actor,
                encrypted_password=encrypted_password,
                session_id=session_id,
                role=role,
            )
            if getattr(result, "error", None):
                return str(result.error), None
            rows = getattr(result, "rows", None) or []
            if not rows or rows[0][0] is None:
                return None, None
            return None, float(rows[0][0])
        except Exception as exc:  # noqa: BLE001
            return str(exc), None

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
        for key in (
            "refresh_type",
            "signature",
            "return_type",
            "function_type",
            "properties",
            "scope",
            "schedule",
            "definition",
        ):
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
            # No ``SHOW CREATE FUNCTION`` on 4.1.4: rebuild SQL UDFs from the
            # ``SHOW FULL FUNCTIONS`` body. A non-SQL body (or one whose argument
            # arity cannot be recovered) yields ``None`` — the verdict already
            # says ``lossy``, so no definition is invented.
            detail = verdicts.reconstruct_sql_function_ddl(
                name=obj.name,
                signature=obj.extra.get("signature"),
                body=obj.extra.get("properties"),
                scope=obj.extra.get("scope", "database"),
            )
        elif obj.kind is ObjectKind.TASK:
            verdict = verdicts.classify_task()
            # Reconstruct CREATE TASK from information_schema.tasks
            # (SCHEDULE + DEFINITION). Stays lossy — properties may differ.
            detail = verdicts.reconstruct_task_ddl(
                name=obj.name,
                schedule=obj.extra.get("schedule"),
                definition=obj.extra.get("definition"),
                properties=obj.extra.get("properties"),
            )
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
