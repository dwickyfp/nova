"""Query lifecycle, history, metrics and responses around the SQL frontend."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import asdict
from functools import wraps
from typing import Any, ParamSpec

import asyncmy

from app.common.audit import write_audit_log
from app.common.sql_guard import (
    CredentialsRedactionError,
    split_sql_statements,
)
from app.common.user_flags import set_must_change_password
from app.core.config import get_storage_connection, settings
from app.core.database import db
from app.core.exceptions import ForbiddenSQLError
from app.core.security import decrypt_password
from app.modules.access_control.security_context import require_security_context
from app.modules.access_control.service import AccessControlError
from app.modules.query.dialect.injector import resolve_storage_credentials
from app.modules.query.repository import QueryRepository, QueryResult
from app.modules.query.sql_pipeline import (
    guard_user_statement,
    redact_for_output,
)
from app.modules.stages.access import check_stage_access
from app.modules.task_orchestration.repository import task_orchestration_repository
from app.observability.metrics import SQL_QUERIES, SQL_QUERY_DURATION, SQL_SOURCE
from app.sql_frontend.analysis.analyzer import Analysis
from app.sql_frontend.ast.builder import AstBuilderRegistry, ast_builders
from app.sql_frontend.binding.catalog import Binder, CatalogProvider
from app.sql_frontend.binding.starrocks import StarRocksCatalogProvider
from app.sql_frontend.capabilities.starrocks import EngineCapabilities, resolve_engine_capabilities
from app.sql_frontend.context import ExecutionContext, PlanningContext
from app.sql_frontend.errors import ConfirmationRequiredError, SemanticError
from app.sql_frontend.execution.adapters import FeatureAdapters
from app.sql_frontend.parser import parse_statement, parsing_scope
from app.sql_frontend.planning.planner import SQLPlanner
from app.storage.secrets import (
    drain_secret_resolution_facts,
)

logger = logging.getLogger(__name__)
_P = ParamSpec("_P")


def _observe_sql_execution(
    execute: Callable[_P, Awaitable[QueryResult]],
) -> Callable[_P, Awaitable[QueryResult]]:
    @wraps(execute)
    async def observed(*args: _P.args, **kwargs: _P.kwargs) -> QueryResult:
        started = time.perf_counter()
        status = "error"
        source = SQL_SOURCE.get()
        try:
            result = await execute(*args, **kwargs)
            status = "error" if result.error else "success"
            return result
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        finally:
            SQL_QUERIES.labels(source=source, status=status).inc()
            SQL_QUERY_DURATION.labels(source=source, status=status).observe(
                time.perf_counter() - started
            )

    return observed


def _redact_error_message(message: str) -> str:
    """The credential-free form of an engine error message, for the audit row.

    An engine failure message can echo the rejected statement, and a statement
    that carried resolved storage credentials would put the keys in
    ``NOVA_SYSTEM.AUDIT_LOG``. ``redact_for_output`` fails closed on a
    credential value it cannot rewrite; this runs on the exception path, so a
    refusal must not replace the original error with a redaction error — the
    row gets a fixed placeholder instead, never the raw string.
    """
    try:
        return redact_for_output(message)
    except CredentialsRedactionError:
        return "[redacted: unredactable error message]"


#: A table reference whose schema segment is the UI's ``default`` placeholder:
#: ``<db>.default.<table>`` preceded by a table-introducing keyword.
#:
#: The positional anchor is what makes this safe. Without it,
#: ``config.default.value`` (a catalogue path) and ``mydb.default.orders`` (a
#: table reference) are the same token shape and cannot be told apart; with it,
#: only a position the engine would read as a table is rewritten.
#:
#: Identifier quoting accepted on each segment, matching the rest of the dialect.
_SEGMENT = r"`?[A-Za-z_][\w$]*`?"

#: A position the engine reads as a table: after ``FROM``/``JOIN``/``INTO``/
#: ``UPDATE``/``TABLE``, or after a comma separating entries in a table list.
#:
#: The middle segment must be exactly ``default`` (bare or backticked) — that
#: placeholder is the whole point, and requiring it keeps the routine from
#: touching ordinary three-part names such as ``mydb.bronze.orders``.
#:
#: The comma alternative is intentionally *not* anchored to a FROM clause: doing
#: so needs real clause tracking, which is the parser's job, not this routine's.
#: A comma-separated list inside a function call (``f(a.default.b, c.default.d)``)
#: would therefore also be collapsed. That shape is not valid as a table position
#: and the placeholder is a UI-only affordance, so the exposure is a rewrite that
#: the user asked for elsewhere, not corruption of a legal name — and it is
#: recorded here rather than hidden.
_DEFAULT_SCHEMA_TABLE_REF = re.compile(
    rf"""(?:\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+|,\s*)
         (?P<db>{_SEGMENT})\s*\.\s*(?:`default`|default)\s*\.\s*(?P<table>{_SEGMENT})""",
    re.IGNORECASE | re.VERBOSE,
)

#: A ``DESCRIBE``/``DESC`` target carrying the UI's ``default`` placeholder.
#:
#: ``DESCRIBE`` is its own statement shape, not a table position after a
#: keyword: the target starts immediately after ``DESCRIBE``/``DESC`` (with an
#: optional ``EXTENDED``/``FORMATTED`` modifier the engine rejects but the UI
#: can still emit). The table-reference anchor above does not cover it, so
#: ``DESCRIBE NOVA_ANALYTICS.default.channel_performance`` reached StarRocks
#: untranslated and the engine answered with a syntax error at the first dot —
#: ``DESCRIBE`` accepts ``[catalog.]db.table`` but has no ``default``
#: placeholder. This anchor collapses the middle segment the same way the table
#: anchor does.
#:
#: The optional leading ``catalog.`` is part of the match so a three-part
#: ``catalog.db.default.table`` collapses to ``catalog.db.table`` rather than
#: leaving a stray ``catalog.`` behind; only the ``db.default.table`` span is
#: replaced below.
_DESCRIBE_TABLE_REF = re.compile(
    rf"""\b(?:DESCRIBE|DESC)\s+
         (?:{_SEGMENT}\s*\.\s*)?
         (?P<db>{_SEGMENT})\s*\.\s*(?:`default`|default)\s*\.\s*(?P<table>{_SEGMENT})""",
    re.IGNORECASE | re.VERBOSE,
)


def _mask_literals_and_comments(sql: str) -> str:
    """Blank out string literals, comments and ``@stage`` paths, preserving offsets.

    Masked spans are replaced character-for-character with ``\\x00`` so
    :data:`_DEFAULT_SCHEMA_TABLE_REF` sees a string of identical length that
    cannot match inside them, while match spans still map onto the original
    text. ``\\x00`` cannot occur in SQL text and is not whitespace, so it can
    never create or destroy a word boundary around live SQL.

    Backtick-quoted *identifiers* are deliberately **not** masked: a backticked
    ``db.default.table`` is exactly what this routine exists to rewrite. Single
    quotes are always a string literal. Double quotes are ambiguous in StarRocks
    (identifier or string depending on ``ANSI_QUOTES``), and masking them is the
    safe direction — a genuine string must never be rewritten, and an identifier
    whose *name* contains ``.default.`` is not the placeholder this routine
    looks for.
    """
    chars = list(sql)
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]

        # ``--`` line comment: to end of line.
        if ch == "-" and i + 1 < length and sql[i + 1] == "-":
            while i < length and sql[i] != "\n":
                chars[i] = "\x00"
                i += 1
            continue

        # ``/* ... */`` block comment; first ``*/`` closes it, as the engine does.
        if ch == "/" and i + 1 < length and sql[i + 1] == "*":
            chars[i] = "\x00"
            chars[i + 1] = "\x00"
            i += 2
            while i < length and not (sql[i] == "*" and i + 1 < length and sql[i + 1] == "/"):
                chars[i] = "\x00"
                i += 1
            if i < length:
                chars[i] = "\x00"
                chars[i + 1] = "\x00"
                i += 2
            continue

        # Single-quoted literal with ``''`` escaping.
        if ch == "'":
            chars[i] = "\x00"
            i += 1
            while i < length:
                if sql[i] == "'":
                    chars[i] = "\x00"
                    if i + 1 < length and sql[i + 1] == "'":
                        chars[i + 1] = "\x00"
                        i += 2
                        continue
                    i += 1
                    break
                chars[i] = "\x00"
                i += 1
            continue

        # Double-quoted spans: masked as a string (see the docstring on the
        # ANSI_QUOTES ambiguity).
        if ch == '"':
            chars[i] = "\x00"
            i += 1
            while i < length and sql[i] != '"':
                chars[i] = "\x00"
                i += 1
            if i < length:
                chars[i] = "\x00"
                i += 1
            continue

        # ``@stage`` reference: ``@name`` plus its ``.part.part`` path, which may
        # itself contain a ``default`` segment. Masked whole so no part of a stage
        # path is ever treated as a table reference.
        if ch == "@":
            chars[i] = "\x00"
            i += 1
            while i < length and (sql[i].isalnum() or sql[i] in "_-."):
                chars[i] = "\x00"
                i += 1
            continue

        i += 1

    return "".join(chars)


#: Owner-bound connection for a scheduled automation run, which has no session.
#: Set by the automation runner for one run; used only for the matching username.
_DELEGATED: contextvars.ContextVar[tuple[str, Any] | None] = contextvars.ContextVar(
    "nova_delegated_connection", default=None
)


@contextlib.contextmanager
def delegated_connection(username: str, connection: Any) -> Iterator[None]:
    token = _DELEGATED.set((username, connection))
    try:
        yield
    finally:
        _DELEGATED.reset(token)


def delegated_connection_for(username: str) -> Any | None:
    current = _DELEGATED.get()
    if current is None or current[0] != username:
        return None
    return current[1]


def delegated_username() -> str | None:
    current = _DELEGATED.get()
    return current[0] if current else None


class QueryService:
    """Orchestrates SQL execution with @stage dialect support."""

    def __init__(
        self,
        *,
        builders: AstBuilderRegistry | None = None,
        planner: SQLPlanner | None = None,
        catalog_provider_factory: Callable[..., CatalogProvider] = StarRocksCatalogProvider,
        capability_resolver: Callable[
            [], Awaitable[EngineCapabilities]
        ] = resolve_engine_capabilities,
    ) -> None:
        self._repo = QueryRepository()
        self._frontend = parse_statement
        self._builders = builders if builders is not None else ast_builders
        self._planner = planner if planner is not None else SQLPlanner()
        self._catalog_provider_factory = catalog_provider_factory
        self._capability_resolver = capability_resolver

    @_observe_sql_execution
    async def execute(
        self,
        sql: str,
        username: str,
        encrypted_password: str,
        database: str | None = None,
        schema: str | None = None,
        role: str | None = None,
        max_rows: int | None = None,
        session_id: str | None = None,
        confirm_destructive: bool = False,
        file_id: str | None = None,
        connection: asyncmy.Connection | None = None,
        tenant: str = "default",
        security_context_version: int = 1,
        allow_stage_export: bool = False,
    ) -> QueryResult:
        """Execute SQL with full @stage dialect pipeline.

        Args:
            sql: The SQL statement to execute
            username: Authenticated username
            encrypted_password: Fernet-encrypted DB password from session
            database: Optional database context
            schema: Optional schema context

        Returns:
            QueryResult with columns, rows, metadata
        """
        if connection is None:
            delegated = delegated_connection_for(username)
            if delegated is not None:
                connection, encrypted_password = delegated, ""
        # Normalize Nova's editor-friendly db.default.table notation to the
        # StarRocks-compatible db.table form before validation/execution.
        normalized_sql = self._normalize_default_schema_qualification(sql)

        try:
            guard_user_statement(
                normalized_sql,
                confirm_destructive=confirm_destructive,
                allow_stage_export=allow_stage_export,
                check_confirmation=False,
            )
        except ForbiddenSQLError as exc:
            await self._audit_engine_result(
                status="ERROR",
                sql=sql,
                username=username,
                role=role,
                database=database,
                schema=schema,
                session_id=session_id,
                file_id=file_id,
                error_message=str(exc),
            )
            raise
        if settings.RANGER_ENABLED:
            require_security_context(
                principal=username,
                active_role=role,
                database=database,
                session_id=session_id,
                security_context_version=security_context_version,
            )
        context = ExecutionContext(
            username=username,
            encrypted_password=encrypted_password,
            database=database,
            schema=schema,
            role=role,
            max_rows=max_rows,
            session_id=session_id,
            file_id=file_id,
            tenant=tenant,
            security_context_version=security_context_version,
            connection=connection,
            confirm_destructive=confirm_destructive,
            allow_stage_export=allow_stage_export,
        )
        try:
            if len(split_sql_statements(normalized_sql)) > 1:
                # Direct single-result callers still receive a confirmation refusal.
                for item in split_sql_statements(normalized_sql):
                    candidate = self._builders.build(self._frontend(item))
                    analysis = self._planner.preflight(candidate)
                    if analysis.requires_confirmation and not confirm_destructive:
                        raise ConfirmationRequiredError(analysis.effects, analysis.statement_kind)
            parsed = self._frontend(normalized_sql, original_sql=sql)
            statement = self._builders.build(parsed)
            context.statements[0] = statement
            binder = Binder(
                self._catalog_provider_factory(
                    self._repo, context, lambda: decrypt_password(encrypted_password)
                )
            )
            planning = PlanningContext(
                database=database,
                schema=schema,
                binder=binder,
                capabilities=await self._capability_resolver(),
                ranger_enabled=settings.RANGER_ENABLED,
                confirm_destructive=confirm_destructive,
            )
            from app.sql_frontend.binding.relations import RelationBinder

            context.capabilities = planning.capabilities
            context.binder = binder
            planning.relation_binder = RelationBinder(
                binder,
                database=database,
                stage_schema=self._adapters().stage_schema_provider(context),
            )
            analysis = self._planner.preflight(statement)
            plan = await self._planner.plan(statement, planning)
            bound = self._planner.preflight(statement)
            if not bound.effects.includes(plan.effects) or (
                plan.requires_confirmation and not bound.requires_confirmation
            ):
                raise SemanticError("Execution plan exceeds declared semantic effects")
            if plan.requires_confirmation and not confirm_destructive:
                raise ConfirmationRequiredError(plan.effects, type(statement).__name__)
            context.validated = planning.validated
        except (ValueError, AccessControlError, ForbiddenSQLError) as exc:
            await self._audit_engine_result(
                status="ERROR",
                sql=sql,
                username=username,
                role=role,
                database=database,
                schema=schema,
                session_id=session_id,
                file_id=file_id,
                error_message=_redact_error_message(str(exc)),
            )
            if isinstance(exc, (AccessControlError, ForbiddenSQLError)):
                raise
            failure = QueryResult(
                original_sql=sql,
                executed_sql=normalized_sql,
                error=_redact_error_message(str(exc)),
                error_code=getattr(exc, "code", "semantic_error"),
            )
            if context.statements:
                statement = context.statements[0]
                analysis = self._planner.preflight(statement)
                failure.statement_kind = analysis.statement_kind
                failure.effects = asdict(analysis.effects)
                failure.destructive = analysis.requires_confirmation
            return failure
        try:
            result = await self._adapters().executor().execute(plan, context)
        except SemanticError as exc:
            await self._audit_engine_result(
                status="ERROR",
                sql=sql,
                username=username,
                role=role,
                database=database,
                schema=schema,
                session_id=session_id,
                file_id=file_id,
                error_message=_redact_error_message(str(exc)),
            )
            raise
        result.destructive = plan.requires_confirmation
        result.statement_kind = type(statement).__name__
        result.effects = asdict(plan.effects)
        return result

    def _adapters(self):
        return FeatureAdapters(
            self,
            audit=write_audit_log,
            decrypt=decrypt_password,
            task_repository=task_orchestration_repository,
            set_flag=set_must_change_password,
            check_stage_access=check_stage_access,
            get_storage_connection=get_storage_connection,
            resolve_storage_credentials=resolve_storage_credentials,
        )

    async def _audit_secret_resolutions(self, *, username: str) -> None:
        """Persist the auditable *facts* of any secret reference resolved above.

        ``resolve_secret_reference`` is synchronous and runs during SQL
        preparation, so it buffers one ``SecretResolutionRecord`` per attempt
        (provider + reference + success) instead of awaiting the audit writer.
        This drains that buffer and records each fact.

        A row is written for the **fact**, never the value: ``object_name`` is
        the reference and ``error_message`` (on failure) the exception type.
        Best-effort, like the pre-engine refusal audit: an audit outage must not
        turn a resolvable stage query into an error.

        No-op when no reference was configured, so the ``nova.yaml``-only path
        writes exactly the rows it wrote before.
        """
        facts = drain_secret_resolution_facts()
        for fact in facts:
            try:
                await write_audit_log(
                    event_type="secret_fetch",
                    user_name=username,
                    action="resolve",
                    object_type="secret_reference",
                    object_name=fact.reference,
                    status="SUCCESS" if fact.succeeded else "ERROR",
                    error_message=fact.error_type or None,
                )
            except Exception:
                logger.exception("failed to audit a secret reference resolution; continuing")

    async def _audit_engine_result(
        self,
        *,
        status: str,
        sql: str,
        username: str,
        role: str | None,
        database: str | None,
        schema: str | None,
        session_id: str | None,
        file_id: str | None,
        error_message: str,
    ) -> None:
        """Record a statement that never reached the engine.

        The two paths that use this — the guard refusing a statement and the
        ``@stage`` translation failing — both return or raise *before* the
        repository call, so the success/exception audit pair further down
        ``execute`` never runs for them. Without this, a refused
        ``DROP ROLE ACCOUNTADMIN`` left no row at all: audit showed engine
        failures as ``status=ERROR`` and pre-engine refusals as nothing, so the
        absence of an ERROR row did not mean the absence of a failed attempt.

        ``rewritten_sql`` is passed explicitly as ``None``: nothing was
        executed, so there is no rewritten form to report. Passing it rather
        than omitting it keeps the row's shape identical to every other query
        row, which is what a consumer selecting the column expects.

        The write is **best-effort**. It runs on the failure path, so a failure
        to *record* a refusal must not replace the refusal: without the guard a
        broken audit sink would turn a precise "ACCOUNTADMIN role cannot be
        dropped" into an unrelated pool error, and the client would lose the
        reason its statement was rejected. The caller re-raises the original
        exception regardless.
        """
        try:
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="sql",
                object_name=(database or "") if database else "workspace",
                status=status,
                sql_text=_redact_error_message(sql),
                rewritten_sql=None,
                error_message=_redact_error_message(error_message),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
                active_role=role,
            )
        except Exception:
            logger.exception(
                "Could not write the audit row for a pre-engine rejection (user=%r)", username
            )

    async def execute_statements(
        self,
        sql: str,
        username: str,
        encrypted_password: str,
        database: str | None = None,
        schema: str | None = None,
        role: str | None = None,
        max_rows: int | None = None,
        session_id: str | None = None,
        confirm_destructive: bool = False,
        file_id: str | None = None,
        connection: asyncmy.Connection | None = None,
        tenant: str = "default",
        security_context_version: int = 1,
        allow_stage_export: bool = False,
        source: str = "internal",
    ) -> list[QueryResult]:
        """Split SQL into statements and execute each sequentially.

        Stops on first error — returns results collected so far plus an error result.
        """
        statements = split_sql_statements(sql)
        if not statements:
            return [QueryResult(original_sql=sql, warnings=["Empty SQL"], error="Empty SQL")]

        with parsing_scope():
            analyses: list[Analysis] = []
            analysis = None
            try:
                from app.sql_frontend.analysis.effects import PlanEffects

                script_effects = PlanEffects()
                confirmation_statement = None
                for stmt_sql in statements:
                    analysis = None
                    normalized = self._normalize_default_schema_qualification(stmt_sql)
                    guard_user_statement(
                        normalized,
                        allow_stage_export=allow_stage_export,
                        check_confirmation=False,
                    )
                    statement = self._builders.build(
                        self._frontend(normalized, original_sql=stmt_sql)
                    )
                    analysis = self._planner.preflight(statement)
                    analyses.append(analysis)
                    self._planner.semantics.validate_preflight(
                        statement,
                        PlanningContext(
                            database=database, schema=schema, ranger_enabled=settings.RANGER_ENABLED
                        ),
                    )
                    script_effects |= analysis.effects
                    if analysis.requires_confirmation and confirmation_statement is None:
                        confirmation_statement = (stmt_sql, analysis.statement_kind)
                if confirmation_statement and not confirm_destructive:
                    stmt_sql, kind = confirmation_statement
                    raise ConfirmationRequiredError(
                        script_effects, "script" if len(statements) > 1 else kind
                    )
            except (ValueError, AccessControlError, ForbiddenSQLError) as exc:
                await self._audit_engine_result(
                    status="ERROR",
                    sql=stmt_sql,
                    username=username,
                    role=role,
                    database=database,
                    schema=schema,
                    session_id=session_id,
                    file_id=file_id,
                    error_message=_redact_error_message(str(exc)),
                )
                return [self._error_result(stmt_sql, exc, analysis)]

            metric_source = source if source in {"web", "mysql_proxy", "internal"} else "internal"
            results: list[QueryResult] = []
            for stmt_sql, analysis in zip(statements, analyses, strict=True):
                metric_token = SQL_SOURCE.set(metric_source)
                try:
                    result = await self.execute(
                        tenant=tenant,
                        security_context_version=security_context_version,
                        sql=stmt_sql,
                        username=username,
                        encrypted_password=encrypted_password,
                        database=database,
                        schema=schema,
                        role=role,
                        max_rows=max_rows,
                        session_id=session_id,
                        confirm_destructive=confirm_destructive,
                        allow_stage_export=allow_stage_export,
                        file_id=file_id,
                        connection=connection,
                    )
                    results.append(result)
                    if result.error:
                        break
                except Exception as exc:
                    # Return error result for this statement and stop.
                    #
                    # ``error`` is the explicit failure marker the router reads;
                    # ``warnings`` keeps carrying the message for the operator.
                    # Setting only ``warnings`` is what made this path depend on a
                    # shape-based guess downstream.
                    error_result = self._error_result(stmt_sql, exc, analysis)
                    results.append(error_result)
                    break
                finally:
                    SQL_SOURCE.reset(metric_token)
            return results

    @staticmethod
    def _error_result(sql: str, exc: Exception, analysis: Analysis | None = None) -> QueryResult:
        confirmation = isinstance(exc, ConfirmationRequiredError)
        return QueryResult(
            original_sql=sql,
            executed_sql="",
            error=_redact_error_message(str(exc)),
            warnings=[_redact_error_message(str(exc))],
            destructive=confirmation or bool(analysis and analysis.requires_confirmation),
            needs_confirmation=confirmation,
            error_code=getattr(
                exc,
                "code",
                "security_rejection" if isinstance(exc, ForbiddenSQLError) else "execution_error",
            ),
            statement_kind=getattr(exc, "statement_kind", None)
            or (analysis.statement_kind if analysis else None),
            effects=asdict(exc.effects)
            if isinstance(exc, ConfirmationRequiredError)
            else asdict(analysis.effects)
            if analysis
            else None,
            execution_failure=getattr(exc, "execution_failure", None),
        )

    async def get_history(
        self,
        *,
        username: str,
        file_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        database_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_duration_ms: int | None = None,
    ) -> dict:
        """Retrieve query execution history from AUDIT_LOG."""
        where, params = self._build_history_filters(
            username=username,
            file_id=file_id,
            status=status,
            search=search,
            database_name=database_name,
            date_from=date_from,
            date_to=date_to,
            min_duration_ms=min_duration_ms,
        )

        count_result = await db.execute_system(
            f"SELECT COUNT(*) FROM NOVA_SYSTEM.AUDIT_LOG WHERE {where}",
            params,
        )
        total = count_result["rows"][0][0] if count_result["rows"] else 0

        result = await db.execute_system(
            f"""
            SELECT log_id, query_id, event_time, user_name, object_name, action,
                   sql_text, status, duration_ms, rows_affected, error_message,
                   file_id, database_name, schema_name, session_id
            FROM NOVA_SYSTEM.AUDIT_LOG
            WHERE {where}
            ORDER BY event_time DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )

        items = []
        for row in result["rows"]:
            items.append(
                {
                    "log_id": str(row[0]) if row[0] is not None else "",
                    "query_id": row[1] or "",
                    "event_time": str(row[2]) if row[2] else "",
                    "user_name": row[3] or "",
                    "object_name": row[4] or "",
                    "action": row[5] or "",
                    "sql_text": row[6] or "",
                    "status": row[7] or "",
                    "duration_ms": row[8],
                    "rows_affected": row[9],
                    "error_message": row[10],
                    "file_id": row[11],
                    "database_name": row[12],
                    "schema_name": row[13],
                    "session_id": row[14],
                }
            )

        return {"items": items, "total": total}

    async def get_history_stats(
        self,
        *,
        username: str,
        file_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        database_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_duration_ms: int | None = None,
    ) -> dict:
        """Return aggregate statistics for query execution history."""
        where, params = self._build_history_filters(
            username=username,
            file_id=file_id,
            status=status,
            search=search,
            database_name=database_name,
            date_from=date_from,
            date_to=date_to,
            min_duration_ms=min_duration_ms,
        )

        result = await db.execute_system(
            f"""
            SELECT COUNT(*) AS total,
                   AVG(duration_ms) AS avg_duration_ms,
                   SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) AS error_count,
                   SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_count
            FROM NOVA_SYSTEM.AUDIT_LOG
            WHERE {where}
            """,
            params,
        )

        row = result["rows"][0] if result["rows"] else (0, None, 0, 0)
        total = row[0] or 0
        avg_duration_ms = float(row[1]) if row[1] is not None else None
        error_count = row[2] or 0
        success_count = row[3] or 0
        error_rate = (error_count / total) if total > 0 else 0.0

        return {
            "total": total,
            "avg_duration_ms": avg_duration_ms,
            "error_count": error_count,
            "success_count": success_count,
            "error_rate": error_rate,
        }

    @staticmethod
    def _build_history_filters(
        *,
        username: str,
        file_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        database_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_duration_ms: int | None = None,
    ) -> tuple[str, list]:
        """Build shared WHERE clause and params for history queries."""
        conditions = ["event_type = 'query'", "user_name = %s"]
        params: list = [username]

        if file_id:
            conditions.append("file_id = %s")
            params.append(file_id)
        if status:
            conditions.append("status = %s")
            params.append(status.upper())
        if search:
            conditions.append("sql_text LIKE %s")
            params.append(f"%{search}%")
        if database_name:
            conditions.append("database_name = %s")
            params.append(database_name)
        if date_from:
            conditions.append("event_time >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("event_time <= %s")
            params.append(date_to)
        if min_duration_ms is not None:
            conditions.append("duration_ms >= %s")
            params.append(min_duration_ms)

        return " AND ".join(conditions), params

    async def explain(
        self,
        sql: str,
        username: str,
        encrypted_password: str,
        database: str | None = None,
        role: str | None = None,
        schema: str | None = None,
    ) -> QueryResult:
        """Get EXPLAIN plan for a SQL statement.

        Translates @stage references first, then runs EXPLAIN.

        The signal path is the mirror of ``execute()``'s and leaks the same way:
        the translation injects real storage credentials. The statement handed
        to the engine therefore keeps them (EXPLAIN has to plan against a real
        path and real credentials), while ``QueryResult`` replaces the values
        with ``***`` before the result — the only object the router serialises
        into the HTTP body — can leave this method.
        """
        guard_user_statement(self._normalize_default_schema_qualification(sql))
        result = await self.execute(
            sql=sql if re.match(r"^\s*EXPLAIN\b", sql, re.IGNORECASE) else "EXPLAIN " + sql,
            username=username,
            encrypted_password=encrypted_password,
            database=database,
            schema=schema,
            role=role,
        )
        result.original_sql = redact_for_output(sql)
        if not result.error:
            result.warnings = []
        return result

    async def get_context(
        self,
        username: str,
        encrypted_password: str,
        active_role: str | None = None,
    ) -> dict:
        password = decrypt_password(encrypted_password)
        if not active_role:
            raise ValueError("Query context requires an explicit active role")
        databases = await self._list_user_databases(username, password, active_role)
        prefs = await db.execute_system(
            """
            SELECT pref_key, pref_value
            FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES
            WHERE user_name = %s AND pref_key IN
            ('workspace.last_database', 'workspace.last_schema', 'workspace.last_role')
            """,
            [username],
        )
        pref_map = {row[0]: row[1] for row in prefs["rows"]}
        roles = await self._get_roles(username, password)
        default_db = pref_map.get("workspace.last_database") or (
            databases[0] if databases else None
        )
        schemas = await self.list_schemas(
            database=default_db, username=username, password=password, role=active_role
        )
        # The session's active role wins over the persisted ``last_role`` pref:
        # the UI treats the bottom-left switcher as the one active role, and the
        # engine executes under exactly that role, so the context must not
        # advertise a different one. Missing role state fails at the request
        # boundary; role ordering never determines authorization.
        context_role = active_role
        return {
            "roles": roles,
            "databases": databases,
            "schemas": schemas,
            "defaults": {
                "database": default_db,
                "schema": pref_map.get("workspace.last_schema")
                or (schemas[0] if schemas else None),
                "role": context_role,
            },
        }

    async def get_completions(
        self,
        *,
        username: str,
        encrypted_password: str,
        kind: str,
        prefix: str = "",
        database: str | None = None,
        schema: str | None = None,
        role: str | None = None,
        table: str | None = None,
        stage: str | None = None,
        folder: str | None = None,
    ) -> dict:
        password = decrypt_password(encrypted_password)
        if kind == "role":
            items = await self._get_roles(username, password)
            return {"items": self._filter_strings(items, prefix, "role")}
        if kind == "database":
            items = await self._list_user_databases(username, password, role)
            return {"items": self._filter_strings(items, prefix, "database")}
        if kind == "schema":
            items = await self.list_schemas(
                database, username=username, password=password, role=role
            )
            return {"items": self._filter_strings(items, prefix, "schema")}
        if kind == "column" and table and database:
            columns = await self._list_columns(username, password, database, table, role)
            return {"items": self._filter_strings(columns, prefix, "column")}
        if kind == "stage":
            if not database:
                return {"items": []}
            stages = await self._list_stages(
                database, schema=schema, username=username, password=password, role=role
            )
            return {"items": self._stage_completion_items(stages, prefix)}
        if kind == "stage_file" and stage:
            if not database:
                return {"items": []}
            rows = await self._list_stage_files(
                stage,
                database,
                folder=folder,
                schema=schema,
                username=username,
                password=password,
                role=role,
            )
            return {"items": self._stage_file_completion_items(rows, prefix)}

        objects = await self._list_objects(username, password, database, role)
        return {"items": self._filter_strings(objects, prefix, "object")}

    async def list_schemas(
        self,
        database: str | None,
        *,
        username: str,
        password: str,
        role: str | None = None,
    ) -> list[str]:
        if not database:
            return ["default"]
        result = await db.execute_system(
            """
            SELECT DISTINCT schema_name
            FROM NOVA_SYSTEM.CONFIG_STAGES
            WHERE database_name = %s
            ORDER BY schema_name
            """,
            [database],
        )
        schemas = []
        for row in result["rows"]:
            if not row[0]:
                continue
            try:
                await check_stage_access(
                    {"database_name": database, "schema_name": row[0]},
                    action="read",
                    username=username,
                    password=password,
                    active_role=role,
                )
            except ValueError:
                continue
            schemas.append(row[0])
        return schemas or ["default"]

    async def _resolve_stage_refs(self, parsed, **kwargs):
        return await self._adapters()._resolve_stage_refs(parsed, **kwargs)

    async def _detect_csv_params(self, parsed, stage_configs):
        return await self._adapters()._detect_csv_params(parsed, stage_configs)

    async def _list_user_databases(
        self,
        username: str,
        password: str,
        role: str | None = None,
    ) -> list[str]:
        result = await self._repo.execute_as_user(
            sql="SHOW DATABASES",
            username=username,
            password=password,
            role=role,
        )
        return [
            row[0]
            for row in result.rows
            if row and row[0] not in ("_statistics_", "information_schema", "sys")
        ]

    async def _get_roles(self, username: str, password: str) -> list[str]:
        from app.modules.auth.service import auth_service

        return await auth_service.get_user_roles(username, password)

    async def _list_objects(
        self,
        username: str,
        password: str,
        database: str | None,
        role: str | None,
    ) -> list[str]:
        if not database:
            return []
        tables = await self._repo.execute_as_user(
            sql=f"SHOW TABLES FROM `{database}`",
            username=username,
            password=password,
            role=role,
            database=database,
        )
        return [row[0] for row in tables.rows]

    async def _list_columns(
        self,
        username: str,
        password: str,
        database: str,
        table: str,
        role: str | None,
    ) -> list[str]:
        result = await self._repo.execute_as_user(
            sql=f"DESC `{database}`.`{table}`",
            username=username,
            password=password,
            role=role,
            database=database,
        )
        return [row[0] for row in result.rows]

    async def _list_stages(
        self,
        database: str,
        *,
        schema: str | None,
        username: str,
        password: str,
        role: str | None,
    ) -> list[str]:
        result = await db.execute_system(
            """
            SELECT name, schema_name
            FROM NOVA_SYSTEM.CONFIG_STAGES
            WHERE database_name = %s
            ORDER BY name
            """,
            [database],
        )
        names = []
        for name, schema_name in result["rows"]:
            if schema and schema_name != schema:
                continue
            try:
                await check_stage_access(
                    {"database_name": database, "schema_name": schema_name},
                    action="read",
                    username=username,
                    password=password,
                    active_role=role,
                )
            except ValueError:
                continue
            names.append(name)
        return list(dict.fromkeys(names))

    async def _list_stage_files(
        self,
        stage_name: str,
        database: str,
        folder: str | None = None,
        *,
        schema: str | None,
        username: str,
        password: str,
        role: str | None,
    ) -> list[dict]:
        from app.modules.stages.service import stage_service

        result = await db.execute_system(
            """
            SELECT id, schema_name
            FROM NOVA_SYSTEM.CONFIG_STAGES
            WHERE name = %s AND database_name = %s
            ORDER BY schema_name
            """,
            [stage_name, database],
        )
        rows = [row for row in result["rows"] if schema is None or row[1] == schema]
        if len(rows) != 1:
            return []
        try:
            await check_stage_access(
                {"database_name": database, "schema_name": rows[0][1]},
                action="read",
                username=username,
                password=password,
                active_role=role,
            )
        except ValueError:
            return []
        storage_prefix = folder.replace(".", "/") if folder else ""
        return await stage_service.list_files(rows[0][0], prefix=storage_prefix)

    @staticmethod
    def _filter_strings(items: list[str], prefix: str, item_type: str) -> list[dict]:
        lowered = prefix.lower()
        filtered = [item for item in items if item.lower().startswith(lowered)]
        return [{"label": item, "type": item_type} for item in filtered[:50]]

    @staticmethod
    def _stage_completion_items(items: list[str], prefix: str) -> list[dict]:
        lowered = prefix.lower()
        filtered = [item for item in items if item.lower().startswith(lowered)]
        return [
            {
                "label": item,
                "type": "stage",
                "insert_text": f"{item}.",
                "detail": "Stage",
            }
            for item in filtered[:50]
        ]

    @staticmethod
    def _stage_file_completion_items(items: list[dict], prefix: str) -> list[dict]:
        lowered = prefix.lower()
        filtered = sorted(
            [item for item in items if str(item.get("name", "")).lower().startswith(lowered)],
            key=lambda item: (
                not bool(item.get("is_dir")),
                str(item.get("name", "")).lower(),
            ),
        )
        completions = []
        for item in filtered[:50]:
            name = str(item.get("name", ""))
            is_dir = bool(item.get("is_dir"))
            completions.append(
                {
                    "label": name,
                    "type": "stage_folder" if is_dir else "stage_file",
                    "insert_text": f"{name}." if is_dir else name,
                    "detail": "Folder" if is_dir else "Stage file",
                    "size": item.get("size"),
                    "last_modified": item.get("last_modified"),
                }
            )
        return completions

    @staticmethod
    def _normalize_default_schema_qualification(sql: str) -> str:
        """Collapse the workspace UI's ``db.default.table`` to ``db.table``.

        ``default`` is a *placeholder* for the connection's default schema, so
        ``mydb.default.orders`` means "the ``orders`` table in mydb's default
        schema" and StarRocks, which has no such placeholder, must receive
        ``mydb.orders``.

        The previous implementation was a bare regex over the whole string and
        corrupted valid SQL in four distinct ways (all reproduced as regression
        fixtures in ``tests/unit/test_default_schema_normalization.py``):

        1. A ``.default.`` segment *inside an ``@stage`` path* was collapsed —
           ``@stage1.data.default.csv`` became ``@stage1.data.csv``, silently
           pointing the stage reference at a different file. The old
           ``(?<!@)`` only guarded the character immediately before the first
           identifier, so a ``.default.`` later in the path was unprotected.
        2. A three-part *object path* that is not a table reference was
           collapsed — ``config.default.value`` became ``config.value``. That
           is a well-formed ``catalog.schema.object`` reference, not a UI path.
           A bare dotted triple is syntactically identical whether it is a
           table reference or a column reference, so it cannot be resolved by
           regex; it has to be resolved by position.
        3. ``.default.`` inside a *string literal* was rewritten, changing
           user data (``SELECT 'a.default.b'`` returned ``'a.b'``).
        4. ``.default.`` inside a *comment* was rewritten, corrupting the
           statement text that is echoed back and audited.

        The fix is therefore positional, not textual: ``default`` is collapsed
        only when it is the middle segment of a *table reference*, meaning it
        is preceded by ``FROM``/``JOIN``/``INTO``/``UPDATE``/``TABLE`` and
        followed by a table name. A ``DESCRIBE``/``DESC`` target is a second
        position, because the target follows the statement keyword rather than
        a table-introducing one; see :data:`_DESCRIBE_TABLE_REF`. Strings,
        comments and ``@stage`` paths are masked out first so nothing inside
        them is considered, and mask characters preserve offsets so the rewrite
        is exact.

        This is deliberately the narrow fix, not the parser fix. NOVA-17 has
        accepted ANTLR4 with the official StarRocks grammar as the real
        solution for the whole dialect; this routine keeps its exact
        pre-parser role and the cases below become grammar-level assertions
        once that lands.
        """
        masked = _mask_literals_and_comments(sql)
        out = sql
        # Applied right-to-left so each replacement is expressed in the
        # original string's coordinates and earlier offsets stay valid.
        for match in reversed(list(_DEFAULT_SCHEMA_TABLE_REF.finditer(masked))):
            # Replace only the ``db.default.table`` span, keeping the leading
            # keyword and any whitespace exactly as the user wrote them. The
            # mask is offset-preserving, so spans map onto ``out`` directly.
            start = match.start("db")
            end = match.end("table")
            out = out[:start] + f"{match.group('db')}.{match.group('table')}" + out[end:]
        # ``DESCRIBE db.default.table`` is a separate anchor: the target is not
        # in a table-introducing-keyword position, so the pattern above misses
        # it. Same right-to-left, same span replacement.
        for match in reversed(list(_DESCRIBE_TABLE_REF.finditer(masked))):
            start = match.start("db")
            end = match.end("table")
            out = out[:start] + f"{match.group('db')}.{match.group('table')}" + out[end:]
        return out


query_service = QueryService()
