"""Query service — orchestrates the full SQL execution pipeline.

Pipeline:
1. Parse SQL (detect @stage references)
2. Translate @stage → FILES() (if stage references found)
3. Inject credentials (if FILES() calls present)
4. Execute against StarRocks
5. Return standardized result
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import wraps
from typing import Any, ParamSpec

import asyncmy

from app.common.audit import write_audit_log
from app.common.ml_intercept import (
    MLForecastCall,
    MLPredictCall,
    detect_ml_forecast,
    detect_ml_predict,
    detect_ml_predict_table,
    rewrite_ml_predict_projection,
)
from app.common.sql_guard import (
    CredentialsRedactionError,
    split_sql_statements,
)
from app.common.user_flags import set_must_change_password
from app.core.config import get_storage_connection, settings, to_docker_endpoint
from app.core.database import db
from app.core.exceptions import ForbiddenSQLError
from app.core.security import decrypt_password
from app.modules.access_control.security_context import require_security_context
from app.modules.access_control.statement_router import security_statement_router
from app.modules.query.dialect.force_password_change import (
    is_force_password_change,
    parse_force_password_change,
)
from app.modules.query.dialect.injector import resolve_storage_credentials
from app.modules.query.dialect.ml_model import is_create_ml_model, parse_create_ml_model
from app.modules.query.dialect.parser import CommandType, parse_sql
from app.modules.query.dialect.translator import StorageConfig
from app.modules.query.repository import QueryRepository, QueryResult
from app.modules.query.sql_pipeline import (
    guard_user_statement,
    prepare_stage_sql,
    redact_for_output,
)
from app.modules.stages.access import check_stage_access
from app.modules.task_orchestration.ddl import TaskDDLError, is_create_task, parse_create_task
from app.modules.task_orchestration.lowering import TaskLoweringError, persist_lowered_task
from app.modules.task_orchestration.repository import task_orchestration_repository
from app.observability.metrics import SQL_QUERIES, SQL_QUERY_DURATION, SQL_SOURCE
from app.storage.secrets import (
    SecretResolutionError,
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


class QueryService:
    """Orchestrates SQL execution with @stage dialect support."""

    def __init__(self):
        self._repo = QueryRepository()

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
        # Normalize Nova's editor-friendly db.default.table notation to the
        # StarRocks-compatible db.table form before validation/execution.
        normalized_sql = self._normalize_default_schema_qualification(sql)

        if settings.RANGER_ENABLED:
            security = require_security_context(
                principal=username,
                active_role=role,
                database=database,
                session_id=session_id,
            )
            routed = await security_statement_router.route(normalized_sql, security=security)
            if routed.handled:
                assert routed.result is not None
                return routed.result

        # 1. Guard: block dangerous SQL, per statement (shared with ml_engine).
        #
        # A refusal is an *event*, not just an exception: `DROP ROLE
        # ACCOUNTADMIN` is the most security-relevant thing a client can attempt
        # through this pipeline, and AGENTS.md requires every action to reach
        # NOVA_SYSTEM.AUDIT_LOG. Without the audit call below, the pre-engine
        # refusals left no trace at all while engine failures were recorded as
        # ERROR — so "no ERROR rows" did not mean "no failed attempts".
        #
        # The guard itself moved to `sql_pipeline.guard_user_statement` so
        # ml_engine applies the identical rule; the audit stays here because it
        # is this service's record of the attempt, not part of the rule.
        try:
            guard_user_statement(
                normalized_sql,
                confirm_destructive=confirm_destructive,
                allow_stage_export=allow_stage_export,
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

        # Nova ML DDL is handled by the Python ML engine, not sent to StarRocks.
        if is_create_ml_model(normalized_sql):
            return await self._execute_create_ml_model(
                tenant=tenant,
                sql=sql,
                normalized_sql=normalized_sql,
                username=username,
                encrypted_password=encrypted_password,
                database=database,
                role=role,
                session_id=session_id,
                file_id=file_id,
                schema=schema,
            )

        ml_forecast_call = detect_ml_forecast(normalized_sql)
        if ml_forecast_call:
            return await self._execute_ml_forecast(
                tenant=tenant,
                sql=sql,
                normalized_sql=normalized_sql,
                call=ml_forecast_call,
                username=username,
                database=database,
                session_id=session_id,
                file_id=file_id,
                schema=schema,
            )

        table_prediction = detect_ml_predict_table(normalized_sql)
        if table_prediction:
            from app.modules.ml_engine.service import ml_engine_service
            from app.modules.ml_engine.spec import MLSecurityContext

            alias, input_sql = table_prediction
            started = time.monotonic()
            try:
                if connection is not None and not encrypted_password:
                    raise ValueError(
                        "Materialized ML prediction requires an API session. "
                        "Use bounded ML_PREDICT for a relayed client connection."
                    )
                result = await ml_engine_service.materialize_prediction(
                    alias,
                    input_sql,
                    MLSecurityContext(
                        username=username,
                        password=decrypt_password(encrypted_password),
                        database=database,
                        schema=schema,
                        role=role,
                        tenant=tenant,
                        security_context_version=security_context_version,
                    ),
                )
                await write_audit_log(
                    event_type="query",
                    user_name=username,
                    action="ml_predict_materialize",
                    object_type="ml_model",
                    object_name=alias,
                    status="SUCCESS",
                    rows_affected=result["total_rows"],
                    database_name=database,
                    schema_name=schema,
                )
                return QueryResult(
                    columns=["result_id", "total_rows", "parts"],
                    rows=[[result["result_id"], result["total_rows"], result["parts"]]],
                    row_count=1,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                    original_sql=sql,
                    executed_sql=redact_for_output(normalized_sql),
                )
            except Exception as exc:
                await write_audit_log(
                    event_type="query",
                    user_name=username,
                    action="ml_predict_materialize",
                    object_type="ml_model",
                    object_name=alias,
                    status="ERROR",
                    error_message=_redact_error_message(str(exc)),
                )
                raise

        ml_predict_match = detect_ml_predict(normalized_sql)
        if ml_predict_match:
            return await self._execute_ml_predict(
                tenant=tenant,
                sql=sql,
                normalized_sql=normalized_sql,
                match=ml_predict_match,
                username=username,
                encrypted_password=encrypted_password,
                database=database,
                role=role,
                session_id=session_id,
                file_id=file_id,
                schema=schema,
                connection=connection,
                max_rows=max_rows,
            )

        # Nova `CREATE TASK` is a Nova statement, not an engine one: it is
        # lowered to CONFIG_TASK* metadata and **never** sent to StarRocks. The
        # same interception shape as `CREATE ML_MODEL` above, so the pipeline has
        # one pattern for Nova DDL rather than a second one.
        if is_create_task(normalized_sql):
            return await self._execute_create_task(
                sql=sql,
                normalized_sql=normalized_sql,
                username=username,
                role=role,
                database=database,
                session_id=session_id,
                file_id=file_id,
                schema=schema,
            )

        # Nova `ALTER USER … REQUIRE PASSWORD CHANGE` is metadata, not engine SQL:
        # StarRocks has no such attribute, so the flag is recorded in
        # NOVA_SYSTEM and the statement is never sent to the engine. Same
        # interception shape as the two above.
        if is_force_password_change(normalized_sql):
            return await self._execute_force_password_change(
                sql=sql,
                normalized_sql=normalized_sql,
                username=username,
                database=database,
                session_id=session_id,
                file_id=file_id,
                schema=schema,
            )

        # 2. Parse: detect @stage references
        parsed = parse_sql(normalized_sql)

        executed_sql = normalized_sql
        warnings = []
        csv_column_names: list[str] | None = None

        # 3. Translate @stage → FILES() and inject credentials, through the
        # shared pipeline so this path and ml_engine's cannot drift apart.
        if parsed.stage_refs:
            try:
                # Load stage configs from NOVA_SYSTEM. This is where a
                # connection's secret reference is resolved, so it belongs
                # inside the same try as preparation: a broken reference must be
                # reported as a query error, not escape as a 500.
                parsed, stage_configs_by_ref = await self._resolve_stage_refs(
                    parsed, database=database, schema=schema, username=username,
                    password="" if connection is not None else decrypt_password(encrypted_password),
                    role=role, connection=connection,
                )

                # 3b. CSV auto-detect: read file header to detect delimiter &
                # columns. I/O, so it happens here and its result is passed into
                # the pure preparation step.
                csv_params_by_ref = {}
                for index, ref in enumerate(parsed.stage_refs):
                    if parsed.command_type == CommandType.STAGE_EXPORT and index == 0:
                        continue
                    if parsed.command_type == CommandType.STAGE_BROWSE:
                        continue
                    params, columns = await self._detect_csv_params(
                        replace(parsed, stage_refs=[ref]),
                        {ref.stage_name: stage_configs_by_ref[ref.start]},
                    )
                    if params:
                        csv_params_by_ref[ref.start] = params
                    if len(parsed.stage_refs) == 1:
                        csv_column_names = columns

                prepared = await prepare_stage_sql(
                    normalized_sql,
                    parsed=parsed,
                    stage_configs_by_ref=stage_configs_by_ref,
                    csv_params_by_ref=csv_params_by_ref,
                    csv_columns=csv_column_names,
                )
            except (ValueError, SecretResolutionError) as e:
                # The statement never reached the engine: no result object is
                # built by the repository, so this is the only place the failure
                # can be recorded. ``error`` (not ``warnings``) is what the
                # router reads for ``success``.
                #
                # "The only place the failure can be recorded" is why the audit
                # row is written here too: the engine is never called, so the
                # catch-all around the repository below cannot see this. A
                # rejected ``@stage`` reference is a refused attempt like any
                # other and belongs in the log for the same reason.
                #
                # ``SecretResolutionError`` is caught here rather than allowed
                # to reach the catch-all so the failure is *audited* and
                # reported as a query error, not a 500. Its message is already
                # value-free (provider + reference only), and any credential
                # that did resolve is redacted below before it leaves.
                await self._audit_secret_resolutions(username=username)
                await self._audit_engine_result(
                    status="ERROR",
                    sql=sql,
                    username=username,
                    role=role,
                    database=database,
                    schema=schema,
                    session_id=session_id,
                    file_id=file_id,
                    error_message=str(e),
                )
                return QueryResult(
                    original_sql=sql,
                    executed_sql=normalized_sql,
                    warnings=[f"❌ {e}"],
                    error=str(e),
                )

            executed_sql = prepared.engine_sql
            warnings = prepared.warnings
            csv_column_names = prepared.csv_columns
            await self._audit_secret_resolutions(username=username)
        else:
            prepared = await prepare_stage_sql(normalized_sql)
            executed_sql = prepared.engine_sql
            warnings = prepared.warnings

        # 5. Execute
        #
        # ``connection`` is an already-authenticated engine session supplied by
        # the MySQL proxy, which relays StarRocks' own challenge and therefore
        # never holds a password (see ``app/proxy/auth.py``). Only the
        # connection-opening path needs the plaintext, so the decrypt is skipped
        # when one was injected — otherwise an empty ``encrypted_password``
        # would raise ``InvalidToken`` before the statement ever ran.
        password = "" if connection is not None else decrypt_password(encrypted_password)

        # The statement sent to the engine carries real storage credentials —
        # that is unavoidable, FILES() needs them. Everything derived from it
        # that leaves the process (audit row, API response) must carry the
        # redacted form instead: NOVA_SYSTEM and API JSON are on the
        # never-store-credentials list in AGENTS.md.
        #
        # Redacted up front rather than read back off the result: the ERROR
        # branch below needs it too, and the engine call may never return.
        # ``QueryResult`` redacts ``executed_sql`` as well, so the value the
        # repository hands back is independently safe.
        redacted_sql = redact_for_output(executed_sql)
        try:
            result = await self._repo.execute_as_user(
                sql=executed_sql,
                username=username,
                password=password,
                database=database,
                role=role,
                max_rows=max_rows,
                connected=connection,
            )

            result.original_sql = sql
            result.executed_sql = redacted_sql
            result.warnings = warnings

            # Rename $1, $2 columns with CSV header names if detected
            if csv_column_names and result.columns:
                for i, col_name in enumerate(csv_column_names):
                    if i < len(result.columns):
                        result.columns[i] = col_name
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="sql",
                object_name=(database or "") if database else "workspace",
                status="SUCCESS",
                sql_text=redacted_sql,
                rewritten_sql=redacted_sql,
                duration_ms=int(result.elapsed_ms),
                rows_affected=result.affected_rows or result.row_count,
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
                active_role=role,
            )
            return result
        except Exception as exc:
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="sql",
                object_name=(database or "") if database else "workspace",
                status="ERROR",
                sql_text=redacted_sql,
                rewritten_sql=redacted_sql,
                error_message=_redact_error_message(str(exc)),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
                active_role=role,
            )
            raise

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
                sql_text=sql,
                rewritten_sql=None,
                error_message=error_message,
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

        metric_source = source if source in {"web", "mysql_proxy", "internal"} else "internal"
        results: list[QueryResult] = []
        for stmt_sql in statements:
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
            except Exception as exc:
                # Return error result for this statement and stop.
                #
                # ``error`` is the explicit failure marker the router reads;
                # ``warnings`` keeps carrying the message for the operator.
                # Setting only ``warnings`` is what made this path depend on a
                # shape-based guess downstream.
                error_result = QueryResult(
                    original_sql=stmt_sql,
                    executed_sql=stmt_sql,
                    warnings=[str(exc)],
                    error=str(exc),
                )
                results.append(error_result)
                break
            finally:
                SQL_SOURCE.reset(metric_token)
        return results

    async def _execute_create_ml_model(
        self,
        *,
        sql: str,
        normalized_sql: str,
        username: str,
        encrypted_password: str,
        database: str | None,
        role: str | None,
        session_id: str | None,
        file_id: str | None,
        schema: str | None,
        tenant: str = "default",
    ) -> QueryResult:
        """Execute Nova CREATE ML_MODEL DDL through the ML engine."""
        start = time.monotonic()
        try:
            statement = parse_create_ml_model(normalized_sql)
            password = decrypt_password(encrypted_password)

            from app.modules.ml_engine.service import ml_engine_service

            result = await ml_engine_service.train_model(
                **({"tenant": tenant} if tenant != "default" else {}),
                model_name=statement.model_name,
                model_type=statement.model_type,
                algorithm=statement.algorithm,
                training_sql=statement.training_sql,
                target_column=statement.target_column,
                feature_columns=statement.feature_columns,
                hyperparameters=statement.hyperparameters,
                test_size=statement.test_size,
                database_name=database,
                created_by=username,
                username=username,
                password=password,
                role=role,
                timestamp_column=statement.timestamp_column,
                series_column=statement.series_column,
                horizon=statement.horizon,
                frequency=statement.frequency,
                mode=statement.mode,
            )
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            columns = [
                "model_id",
                "model_name",
                "model_type",
                "algorithm",
                "version",
                "status",
                "training_rows",
                "feature_columns",
                "metrics",
            ]
            row = [
                result.get("model_id"),
                result.get("model_name"),
                result.get("model_type"),
                result.get("algorithm"),
                result.get("version"),
                result.get("status"),
                result.get("training_rows"),
                json.dumps(result.get("feature_columns", [])),
                json.dumps(result.get("metrics", {})),
            ]
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="ml_model",
                object_name=statement.model_name,
                status="SUCCESS",
                sql_text=sql,
                rewritten_sql=normalized_sql,
                duration_ms=int(elapsed_ms),
                rows_affected=result.get("training_rows"),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            return QueryResult(
                columns=columns,
                rows=[row],
                row_count=1,
                affected_rows=int(result.get("training_rows") or 0),
                elapsed_ms=elapsed_ms,
                original_sql=sql,
                executed_sql=normalized_sql,
            )
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="ml_model",
                object_name=database or "workspace",
                status="ERROR",
                sql_text=sql,
                rewritten_sql=normalized_sql,
                error_message=str(exc),
                duration_ms=int(elapsed_ms),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            raise

    async def _execute_ml_predict(
        self,
        *,
        sql: str,
        normalized_sql: str,
        match: MLPredictCall,
        username: str,
        encrypted_password: str,
        database: str | None,
        role: str | None,
        session_id: str | None,
        file_id: str | None,
        schema: str | None,
        connection: asyncmy.Connection | None,
        max_rows: int | None,
        tenant: str = "default",
    ) -> QueryResult:
        """Execute Nova ``ML_PREDICT`` as one columnar, vectorized batch."""
        start = time.monotonic()
        alias = match.group(1)
        feature_sql = ""
        password = "" if connection is not None else decrypt_password(encrypted_password)
        from app.modules.ml_engine.service import ml_engine_service

        try:
            rewrite = rewrite_ml_predict_projection(normalized_sql, match)
            alias = rewrite.alias
            feature_sql = rewrite.feature_sql
            metadata, result_table = await ml_engine_service.batch_predict_projected(
                **({"tenant": tenant} if tenant != "default" else {}),
                model_alias=alias,
                prediction_sql=feature_sql,
                feature_source_columns=rewrite.feature_columns,
                prediction_index=rewrite.prediction_index,
                prediction_name=rewrite.prediction_name,
                database_name=database,
                username=username,
                password=password,
                role=role,
                connection=connection,
                max_rows=max_rows,
                **({"predictions": rewrite.predictions} if len(rewrite.predictions) > 1 else {}),
            )
            del metadata
            columns = result_table.column_names
            rows: list[list[Any]] = []
            for batch in result_table.to_batches(max_chunksize=4096):
                values = [column.to_pylist() for column in batch.columns]
                rows.extend([list(row) for row in zip(*values, strict=True)])
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="ml_predict_batch",
                object_type="ml_model",
                object_name=alias,
                status="SUCCESS",
                sql_text=sql,
                rewritten_sql=feature_sql,
                duration_ms=int(elapsed_ms),
                rows_affected=len(rows),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                elapsed_ms=elapsed_ms,
                original_sql=sql,
                executed_sql=feature_sql,
                warnings=["ML_PREDICT executed in bounded vectorized Nova batches"],
            )
        except Exception as exc:
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="ml_predict_batch",
                object_type="ml_model",
                object_name=alias,
                status="ERROR",
                sql_text=sql,
                rewritten_sql=feature_sql,
                error_message=_redact_error_message(str(exc)),
                duration_ms=int((time.monotonic() - start) * 1000),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            raise

    async def _execute_ml_forecast(
        self,
        *,
        sql: str,
        normalized_sql: str,
        call: MLForecastCall,
        username: str,
        database: str | None,
        session_id: str | None,
        file_id: str | None,
        schema: str | None,
        tenant: str = "default",
    ) -> QueryResult:
        """Execute persisted forecast SQL without pretending it is row inference."""
        start = time.monotonic()
        from app.modules.ml_engine.service import ml_engine_service

        object_name = call.model_alias or call.model_id or "forecast"
        try:
            if call.model_alias is not None:
                result = await ml_engine_service.forecast_alias(
                    call.model_alias,
                    call.horizon,
                    owner_name=username,
                    database_name=database,
                    level=call.confidence_level,
                    series=call.series,
                    **({"tenant": tenant} if tenant != "default" else {}),
                )
            else:
                assert call.model_id is not None and call.version is not None
                result = await ml_engine_service.forecast_version(
                    call.model_id,
                    call.version,
                    call.horizon,
                    owner_name=username,
                    database_name=database,
                    level=call.confidence_level,
                    series=call.series,
                    **({"tenant": tenant} if tenant != "default" else {}),
                )
            forecast = result["forecast"]
            columns = ["timestamp", "series", "prediction", "lower", "upper"]
            rows = [[row.get(column) for column in columns] for row in forecast]
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="ml_forecast",
                object_type="ml_model",
                object_name=object_name,
                status="SUCCESS",
                sql_text=sql,
                rewritten_sql=normalized_sql,
                duration_ms=int(elapsed_ms),
                rows_affected=len(rows),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                elapsed_ms=elapsed_ms,
                original_sql=sql,
                executed_sql=normalized_sql,
                warnings=["ML_FORECAST executed with persisted forecast semantics"],
            )
        except Exception as exc:
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="ml_forecast",
                object_type="ml_model",
                object_name=object_name,
                status="ERROR",
                sql_text=sql,
                rewritten_sql=normalized_sql,
                error_message=_redact_error_message(str(exc)),
                duration_ms=int((time.monotonic() - start) * 1000),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            raise

    async def _execute_create_task(
        self,
        *,
        sql: str,
        normalized_sql: str,
        username: str,
        role: str | None,
        database: str | None,
        session_id: str | None,
        file_id: str | None,
        schema: str | None,
    ) -> QueryResult:
        """Lower Nova ``CREATE TASK`` to ``CONFIG_TASK*`` metadata.

        The raw statement is **never** executed: it is parsed, validated, and
        written to Nova's own metadata tables. The engine statement for a node
        is produced later by the worker via ``execution.build_submit_task`` on
        the owner's connection (delegate-first, design D9.4).

        No credential is accepted here — the statement cannot embed one, and the
        body is stored opaquely.
        """
        start = time.monotonic()
        try:
            if settings.RANGER_ENABLED and not role:
                raise TaskLoweringError("CREATE TASK requires an explicit execution role")
            timezone = await task_orchestration_repository.get_engine_timezone()
            if not timezone:
                raise TaskLoweringError(
                    "cannot determine the engine timezone; CREATE TASK stores an "
                    "explicit IANA zone and will not assume UTC"
                )
            task = parse_create_task(
                normalized_sql, database=database, schema=schema, timezone=timezone
            )
            persisted = await persist_lowered_task(task, created_by=username, owner_role=role)
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            task_row = persisted.task
            edge_count = len(persisted.edges)

            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="task",
                object_name=task.qualified_name,
                status="SUCCESS",
                sql_text=sql,
                rewritten_sql=normalized_sql,
                duration_ms=int(elapsed_ms),
                rows_affected=1,
                session_id=session_id,
                file_id=file_id,
                database_name=task.database_name,
                schema_name=task.schema_name,
            )
            return QueryResult(
                columns=[
                    "task_id",
                    "name",
                    "database_name",
                    "schema_name",
                    "schedule_kind",
                    "schedule_expr",
                    "overlap_policy",
                    "edges",
                ],
                rows=[
                    [
                        task_row["id"],
                        task_row["name"],
                        task_row["database_name"],
                        task_row["schema_name"],
                        task_row["schedule_kind"],
                        task_row["schedule_expr"],
                        task_row["overlap_policy"],
                        edge_count,
                    ]
                ],
                row_count=1,
                affected_rows=1,
                elapsed_ms=elapsed_ms,
                original_sql=sql,
                # The raw CREATE TASK is metadata, not the executed SQL; the run
                # statement is built per node at execution time. Surfacing the
                # normalized statement here is honest about what Nova did with it
                # and never implies the engine saw it.
                executed_sql=normalized_sql,
                warnings=[
                    "CREATE TASK is Nova metadata; no statement was sent to StarRocks. "
                    "The task's SUBMIT TASK is issued by the worker when the graph runs."
                ],
            )
        except (TaskDDLError, TaskLoweringError) as exc:
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="task",
                object_name=database or "workspace",
                status="ERROR",
                sql_text=sql,
                rewritten_sql=normalized_sql,
                error_message=str(exc),
                duration_ms=int(elapsed_ms),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            # Return, rather than raise, so the Nova-surface validation message
            # reaches the worksheet as an explicit failure instead of being lost
            # behind a generic engine error.
            return QueryResult(
                error=str(exc),
                elapsed_ms=elapsed_ms,
                original_sql=sql,
                executed_sql=normalized_sql,
            )

    async def _execute_force_password_change(
        self,
        *,
        sql: str,
        normalized_sql: str,
        username: str,
        database: str | None,
        session_id: str | None,
        file_id: str | None,
        schema: str | None,
    ) -> QueryResult:
        """Record the first-login password-change flag for a user.

        The statement is Nova metadata: StarRocks has no such attribute, so it is
        parsed here and written to ``NOVA_SYSTEM.CONFIG_USER_PREFERENCES``; the
        engine never sees it. Only the flag is stored — no password.
        """
        start = time.monotonic()
        try:
            parsed = parse_force_password_change(normalized_sql)
        except ValueError as exc:
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="user",
                object_name=username,
                status="ERROR",
                sql_text=sql,
                rewritten_sql=normalized_sql,
                error_message=str(exc),
                duration_ms=int(elapsed_ms),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            return QueryResult(
                error=str(exc),
                elapsed_ms=elapsed_ms,
                original_sql=sql,
                executed_sql=normalized_sql,
            )

        await set_must_change_password(parsed.username, required=parsed.required)
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        await write_audit_log(
            event_type="query",
            user_name=username,
            action="execute",
            object_type="user",
            object_name=parsed.username,
            status="SUCCESS",
            sql_text=sql,
            rewritten_sql=normalized_sql,
            duration_ms=int(elapsed_ms),
            rows_affected=1,
            session_id=session_id,
            file_id=file_id,
            database_name=database,
            schema_name=schema,
        )
        return QueryResult(
            columns=["user", "must_change_password"],
            rows=[[parsed.username, parsed.required]],
            row_count=1,
            affected_rows=1,
            elapsed_ms=elapsed_ms,
            original_sql=sql,
            executed_sql=normalized_sql,
            warnings=[
                "ALTER USER … REQUIRE PASSWORD CHANGE is Nova metadata; no statement "
                "was sent to StarRocks. The user is asked to change the password at "
                "their next login."
            ],
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
        normalized_sql = self._normalize_default_schema_qualification(sql)
        guard_user_statement(normalized_sql)

        parsed = parse_sql(normalized_sql)
        executed_sql = normalized_sql

        if parsed.stage_refs:
            try:
                # Loading stage configs resolves each stage's secret reference,
                # so it belongs inside the try: an unresolvable reference must
                # be reported as a redacted query error, not escape to the
                # generic handler as an unredacted 500 (NOVA-66).
                parsed, stage_configs_by_ref = await self._resolve_stage_refs(
                    parsed, database=database, schema=schema, username=username,
                    password=decrypt_password(encrypted_password), role=role,
                )
                prepared = await prepare_stage_sql(
                    normalized_sql, parsed=parsed, stage_configs_by_ref=stage_configs_by_ref
                )
            except (ValueError, SecretResolutionError) as e:
                # ``normalized_sql`` is the user's own text and carries no
                # injected credential, but it is redacted all the same so every
                # return path out of this method is uniform.
                #
                # ``error`` carries the failure the same way ``execute()`` does:
                # the statement never reached the engine, so nothing else can
                # record it, and ``QueryResult.success`` (``error is None``)
                # would otherwise report a refused translation as a success.
                #
                # A secret-resolution failure is audited here too, exactly as
                # ``execute()`` does, so the fact of the failed fetch reaches
                # NOVA_SYSTEM rather than only the HTTP response.
                await self._audit_secret_resolutions(username=username)
                return QueryResult(
                    original_sql=sql,
                    executed_sql=normalized_sql,
                    warnings=[f"❌ {e}"],
                    error=str(e),
                )

            executed_sql = prepared.engine_sql

        explain_sql = (
            executed_sql if re.match(r"(?is)^\s*EXPLAIN\b", executed_sql)
            else f"EXPLAIN {executed_sql}"
        )
        password = decrypt_password(encrypted_password)

        result = await self._repo.execute_as_user(
            sql=explain_sql,
            username=username,
            password=password,
            database=database,
            role=role,
        )
        result.original_sql = sql
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
                stage, database, folder=folder, schema=schema,
                username=username, password=password, role=role,
            )
            return {"items": self._stage_file_completion_items(rows, prefix)}

        objects = await self._list_objects(username, password, database, role)
        return {"items": self._filter_strings(objects, prefix, "object")}

    async def list_schemas(
        self, database: str | None, *, username: str, password: str,
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
                    action="read", username=username, password=password, active_role=role,
                )
            except ValueError:
                continue
            schemas.append(row[0])
        return schemas or ["default"]

    async def _resolve_stage_refs(
        self,
        parsed,
        *,
        database: str | None,
        schema: str | None,
        username: str,
        password: str,
        role: str | None,
        connection: asyncmy.Connection | None = None,
    ) -> tuple[Any, dict[int, StorageConfig]]:
        """Bind each reference to one authorized metadata row before resolving secrets."""
        result = await db.execute_system(
            "SELECT name, database_name, schema_name, storage_connection, base_prefix "
            "FROM NOVA_SYSTEM.CONFIG_STAGES"
        )
        rows = [
            {"name": row[0], "database_name": row[1], "schema_name": row[2],
             "storage_connection": row[3], "base_prefix": row[4]}
            for row in result["rows"]
        ]
        selected = []
        for index, ref in enumerate(parsed.stage_refs):
            candidates = [(database, schema, ref.stage_name, 0)]
            if ref.path_parts:
                candidates.append((database, ref.stage_name, ref.path_parts[0], 1))
            if len(ref.path_parts) > 1:
                candidates.append(
                    (ref.stage_name, ref.path_parts[0], ref.path_parts[1], 2)
                )
            matches = []
            for db_name, schema_name, name, consumed in candidates:
                found = [
                    row for row in rows
                    if row["name"] == name
                    and (db_name is None or row["database_name"] == db_name)
                    and (schema_name is None or row["schema_name"] == schema_name)
                ]
                if found:
                    matches = [(row, consumed) for row in found]
            if len(matches) != 1:
                raise ValueError(
                    f"Stage reference {ref.full_match!r} is "
                    + ("ambiguous" if matches else "not found")
                )
            row, consumed = matches[0]
            action = (
                "write" if parsed.command_type == CommandType.STAGE_EXPORT and index == 0
                else "read"
            )
            selected.append((ref, row, consumed, action))

        for _, row, _, action in selected:
            await check_stage_access(
                row, action=action, username=username, password=password,
                active_role=role, connection=connection,
            )

        configs: dict[int, StorageConfig] = {}
        refs = []
        for ref, row, consumed, _ in selected:
            storage_conn = row["storage_connection"]
            conn = get_storage_connection(storage_conn)
            access_key, secret_key = resolve_storage_credentials(storage_conn)
            prefix = (row["base_prefix"] or "").strip("/")
            if not prefix:
                prefix = f"{row['database_name']}/{row['schema_name']}/{row['name']}"
            refs.append(replace(ref, stage_name=row["name"], path_parts=ref.path_parts[consumed:]))
            configs[ref.start] = StorageConfig(
                storage_type=conn.type, endpoint=to_docker_endpoint(conn.endpoint),
                bucket=conn.bucket, base_prefix=prefix,
                access_key=access_key, secret_key=secret_key,
                region=conn.region or "us-east-1", storage_connection=storage_conn,
            )
        return replace(parsed, stage_refs=refs), configs

    async def _detect_csv_params(
        self,
        parsed,
        stage_configs: dict,
    ) -> tuple[dict[str, str], list[str] | None]:
        """Pre-read CSV file from MinIO to detect delimiter and header.

        Returns (params_dict, column_names_or_None).
        params_dict: FILES() params like {"csv.column_separator": ",", "csv.skip_header": "1"}
        column_names: list of header column names if detected, else None
        """
        if not parsed.stage_refs:
            logger.warning("CSV detect: no stage references in the parsed statement")
            return {}, None

        ref = parsed.stage_refs[0]
        # Detect format from file extension
        ext = ""
        if ref.file_name and "." in ref.file_name:
            ext = ref.file_name.rsplit(".", 1)[-1].lower()
        if ext not in ("csv", "tsv"):
            logger.warning("CSV detect: %r is not csv/tsv (ext=%r)", ref.file_name, ext)
            return {}, None

        config = stage_configs.get(ref.stage_name)
        if not config:
            # The stage exists as a row but could not be resolved into a
            # StorageConfig, so the FILES() call gets no delimiter/header
            # tuning and the caller sees untuned rows rather than an error.
            logger.warning(
                "CSV detect: stage %r is not in the resolved stage configs %r",
                ref.stage_name,
                sorted(stage_configs),
            )
            return {}, None

        try:
            import boto3
            from botocore.config import Config as BotoConfig

            # Build S3 key
            parts = ref.path_parts + [ref.file_name] if ref.file_name else ref.path_parts
            s3_key = "/".join([config.base_prefix] + parts)

            def read_header() -> str:
                s3 = boto3.client(
                    "s3",
                    endpoint_url=(
                        get_storage_connection(config.storage_connection).endpoint
                        if config.storage_connection else config.endpoint
                    ),
                    aws_access_key_id=config.access_key,
                    aws_secret_access_key=config.secret_key,
                    config=BotoConfig(signature_version="s3v4"),
                    region_name=config.region or "us-east-1",
                )
                resp = s3.get_object(Bucket=config.bucket, Key=s3_key, Range="bytes=0-8191")
                body = resp["Body"]
                try:
                    return body.read(8192).decode("utf-8", errors="replace")
                finally:
                    body.close()

            raw = await asyncio.to_thread(read_header)
            lines = raw.split("\n")
            if len(lines) < 2:
                logger.warning(
                    "CSV detect: object %r/%r returned %d line(s); cannot detect",
                    config.bucket,
                    s3_key,
                    len(lines),
                )
                return {}, None

            first_line = lines[0].strip()

            # Detect delimiter by counting occurrences in first line
            candidates = [
                (",", first_line.count(",")),
                (";", first_line.count(";")),
                ("\t", first_line.count("\t")),
                ("|", first_line.count("|")),
            ]
            # Pick the delimiter with highest count (must be > 0)
            best_delim, best_count = max(candidates, key=lambda x: x[1])
            if best_count == 0:
                best_delim = ","

            # Detect enclosure
            enclose = ""
            if first_line.startswith('"') and first_line.endswith('"'):
                enclose = '"'

            # Detect if first line is a header:
            # Headers typically contain text, not numbers
            second_line = lines[1].strip() if len(lines) > 1 else ""
            first_fields = first_line.split(best_delim)
            second_fields = second_line.split(best_delim)

            is_header = False
            column_names = None
            if first_fields and second_fields and len(first_fields) == len(second_fields):
                # Check if first row looks like text (header) and second like data
                text_count = sum(
                    1
                    for f in first_fields
                    if not f.strip().replace("-", "").replace(".", "").isdigit()
                )
                is_header = text_count > len(first_fields) / 2
                if is_header:
                    # Extract clean column names from header
                    column_names = [f.strip().strip('"').strip("'") for f in first_fields]

            params: dict[str, str] = {
                "csv.column_separator": best_delim,
                "csv.trim_space": "true",
            }
            if enclose:
                params["csv.enclose"] = enclose
                params["csv.escape"] = "\\\\"
            if is_header:
                params["csv.skip_header"] = "1"

            return params, column_names

        except Exception:
            # Falling back to defaults is correct — a CSV that cannot be
            # pre-read still loads, without delimiter or header tuning. What is
            # NOT correct is doing it silently: a boto3 failure here (unreachable
            # endpoint, missing credential) makes the query return typed rows
            # where the caller expected a header, which surfaces as a confusing
            # shape assertion far from the cause. Name it in the log instead.
            logger.warning("CSV parameter detection failed for stage %r", ref.stage_name)
            return {}, None

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
                    action="read", username=username, password=password, active_role=role,
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
                action="read", username=username, password=password, active_role=role,
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
