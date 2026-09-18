"""Query service — orchestrates the full SQL execution pipeline.

Pipeline:
1. Parse SQL (detect @stage references)
2. Translate @stage → FILES() (if stage references found)
3. Inject credentials (if FILES() calls present)
4. Execute against StarRocks
5. Return standardized result
"""

from __future__ import annotations

import json
import logging
import re
import time

import asyncmy

from app.common.audit import write_audit_log
from app.common.sql_guard import split_sql_statements
from app.core.config import get_storage_connection, settings, to_docker_endpoint
from app.core.database import db
from app.core.exceptions import ForbiddenSQLError
from app.core.security import decrypt_password
from app.modules.query.dialect.injector import resolve_storage_credentials
from app.modules.query.dialect.ml_model import is_create_ml_model, parse_create_ml_model
from app.modules.query.dialect.parser import parse_sql
from app.modules.query.dialect.translator import StorageConfig
from app.modules.query.repository import QueryRepository, QueryResult
from app.modules.query.sql_pipeline import (
    guard_user_statement,
    prepare_stage_sql,
    redact_for_output,
)
from app.modules.task_orchestration.ddl import TaskDDLError, is_create_task, parse_create_task
from app.modules.task_orchestration.lowering import TaskLoweringError, persist_lowered_task
from app.modules.task_orchestration.repository import task_orchestration_repository
from app.storage.secrets import (
    SecretResolutionError,
    drain_secret_resolution_facts,
)

logger = logging.getLogger(__name__)

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
            guard_user_statement(normalized_sql, confirm_destructive=confirm_destructive)
        except ForbiddenSQLError as exc:
            await self._audit_engine_result(
                status="ERROR",
                sql=sql,
                username=username,
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

        # Nova `CREATE TASK` is a Nova statement, not an engine one: it is
        # lowered to CONFIG_TASK* metadata and **never** sent to StarRocks. The
        # same interception shape as `CREATE ML_MODEL` above, so the pipeline has
        # one pattern for Nova DDL rather than a second one.
        if is_create_task(normalized_sql):
            return await self._execute_create_task(
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
                stage_configs = await self._load_stage_configs(database, schema)

                # 3b. CSV auto-detect: read file header to detect delimiter &
                # columns. I/O, so it happens here and its result is passed into
                # the pure preparation step.
                csv_params, csv_column_names = await self._detect_csv_params(
                    parsed, stage_configs
                )

                prepared = await prepare_stage_sql(
                    normalized_sql,
                    stage_configs=stage_configs,
                    csv_params=csv_params,
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
                sql_text=sql,
                rewritten_sql=redacted_sql,
                duration_ms=int(result.elapsed_ms),
                rows_affected=result.affected_rows or result.row_count,
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
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
                sql_text=sql,
                rewritten_sql=redacted_sql,
                error_message=str(exc),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
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
                logger.exception(
                    "failed to audit a secret reference resolution; continuing"
                )

    async def _audit_engine_result(
        self,
        *,
        status: str,
        sql: str,
        username: str,
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
    ) -> list[QueryResult]:
        """Split SQL into statements and execute each sequentially.

        Stops on first error — returns results collected so far plus an error result.
        """
        statements = split_sql_statements(sql)
        if not statements:
            return [QueryResult(original_sql=sql, warnings=["Empty SQL"], error="Empty SQL")]

        results: list[QueryResult] = []
        for stmt_sql in statements:
            try:
                result = await self.execute(
                    sql=stmt_sql,
                    username=username,
                    encrypted_password=encrypted_password,
                    database=database,
                    schema=schema,
                    role=role,
                    max_rows=max_rows,
                    session_id=session_id,
                    confirm_destructive=confirm_destructive,
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
    ) -> QueryResult:
        """Execute Nova CREATE ML_MODEL DDL through the ML engine."""
        start = time.monotonic()
        try:
            statement = parse_create_ml_model(normalized_sql)
            password = decrypt_password(encrypted_password)

            from app.modules.ml_engine.service import ml_engine_service

            result = await ml_engine_service.train_model(
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

    async def _execute_create_task(
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
            timezone = await task_orchestration_repository.get_engine_timezone()
            if not timezone:
                raise TaskLoweringError(
                    "cannot determine the engine timezone; CREATE TASK stores an "
                    "explicit IANA zone and will not assume UTC"
                )
            task = parse_create_task(normalized_sql, database=database, timezone=timezone)
            persisted = await persist_lowered_task(task, created_by=username)
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            task_row = persisted.task
            edge_count = len(persisted.edges)

            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="task",
                object_name=task.name,
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
                columns=[
                    "task_id",
                    "name",
                    "schedule_kind",
                    "schedule_expr",
                    "overlap_policy",
                    "edges",
                ],
                rows=[
                    [
                        task_row["id"],
                        task_row["name"],
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
            stage_configs = await self._load_stage_configs(database, None)
            try:
                prepared = await prepare_stage_sql(
                    normalized_sql, stage_configs=stage_configs
                )
            except ValueError as e:
                # ``normalized_sql`` is the user's own text and carries no
                # injected credential, but it is redacted all the same so every
                # return path out of this method is uniform.
                #
                # ``error`` carries the failure the same way ``execute()`` does:
                # the statement never reached the engine, so nothing else can
                # record it, and ``QueryResult.success`` (``error is None``)
                # would otherwise report a refused translation as a success.
                return QueryResult(
                    original_sql=sql,
                    executed_sql=normalized_sql,
                    warnings=[f"❌ {e}"],
                    error=str(e),
                )

            executed_sql = prepared.engine_sql

        explain_sql = f"EXPLAIN {executed_sql}"
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
    ) -> dict:
        password = decrypt_password(encrypted_password)
        databases = await self._list_user_databases(username, password)
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
        schemas = await self.list_schemas(database=default_db)
        return {
            "roles": roles,
            "databases": databases,
            "schemas": schemas,
            "defaults": {
                "database": default_db,
                "schema": pref_map.get("workspace.last_schema")
                or (schemas[0] if schemas else None),
                "role": pref_map.get("workspace.last_role") or (roles[0] if roles else None),
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
            items = await self.list_schemas(database)
            return {"items": self._filter_strings(items, prefix, "schema")}
        if kind == "column" and table and database:
            columns = await self._list_columns(username, password, database, table, role)
            return {"items": self._filter_strings(columns, prefix, "column")}
        if kind == "stage":
            if not database:
                return {"items": []}
            stages = await self._list_stages(database)
            return {"items": self._stage_completion_items(stages, prefix)}
        if kind == "stage_file" and stage:
            if not database:
                return {"items": []}
            rows = await self._list_stage_files(stage, database, folder=folder)
            return {"items": self._stage_file_completion_items(rows, prefix)}

        objects = await self._list_objects(username, password, database, role)
        return {"items": self._filter_strings(objects, prefix, "object")}

    async def list_schemas(self, database: str | None) -> list[str]:
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
        schemas = [row[0] for row in result["rows"] if row[0]]
        return schemas or ["default"]

    async def _load_stage_configs(
        self,
        database: str | None,
        schema: str | None,
    ) -> dict[str, StorageConfig]:
        """Load stage configurations from NOVA_SYSTEM.

        Returns a map of stage_name → StorageConfig.
        First tries to filter by database/schema context.
        Falls back to loading ALL stages if none match (cross-database access).
        """
        try:
            # Try with database/schema filter first
            configs = await self._load_stage_configs_filtered(database, schema)
            if configs:
                return configs
            # Fallback: load all stages (cross-database access)
            return await self._load_stage_configs_filtered(None, None)
        except SecretResolutionError:
            # A configured secret reference that cannot be resolved is a real
            # configuration failure, not a metadata-DB hiccup. Swallowing it
            # would surface as a misleading "Stage not found"; fail closed with
            # the actual cause instead (NOVA-58).
            raise
        except Exception:
            return {}

    async def _load_stage_configs_filtered(
        self,
        database: str | None,
        schema: str | None,
    ) -> dict[str, StorageConfig]:
        """Load stages with optional database/schema filter."""
        sql = (
            "SELECT name, database_name, schema_name, storage_connection, base_prefix "
            "FROM NOVA_SYSTEM.CONFIG_STAGES"
        )
        params: list[str] = []
        filters = []
        if database:
            filters.append("database_name = %s")
            params.append(database)
        if schema:
            filters.append("schema_name = %s")
            params.append(schema)
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        result = await db.execute_system(sql, params or None)
        configs = {}
        for row in result["rows"]:
            name, db_name, schema_name, storage_conn, base_prefix = (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
            )
            conn = get_storage_connection(storage_conn)
            # Fallback base_prefix: {database_name}/{schema_name}/{stage_name}
            resolved_prefix = (base_prefix or "").strip("/")
            if not resolved_prefix:
                resolved_prefix = f"{db_name}/{schema_name}/{name}"
            # Resolve against the stage's *own* connection, not the workspace
            # default: a connection may carry its own secret reference, and
            # resolving the default would authenticate the stage as the wrong
            # principal. Fail-closed — a broken reference raises rather than
            # falling back (NOVA-58).
            access_key, secret_key = resolve_storage_credentials(storage_conn)
            configs[name] = StorageConfig(
                storage_type=conn.type,
                endpoint=to_docker_endpoint(conn.endpoint),
                bucket=conn.bucket,
                base_prefix=resolved_prefix,
                access_key=access_key,
                secret_key=secret_key,
                region=conn.region or "us-east-1",
            )
        return configs

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

            s3 = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT,  # host-side endpoint for boto3
                aws_access_key_id=config.access_key,
                aws_secret_access_key=config.secret_key,
                config=BotoConfig(signature_version="s3v4"),
                region_name=config.region or "us-east-1",
            )

            # Read first 8KB of the file
            resp = s3.get_object(Bucket=config.bucket, Key=s3_key, Range="bytes=0-8191")
            raw = resp["Body"].read().decode("utf-8", errors="replace")
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
            logger.exception(
                "CSV parameter detection failed for stage %r; falling back to defaults",
                ref.stage_name,
            )
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
    ) -> list[str]:
        result = await db.execute_system(
            """
            SELECT DISTINCT name
            FROM NOVA_SYSTEM.CONFIG_STAGES
            WHERE database_name = %s
            ORDER BY name
            """,
            [database],
        )
        return [row[0] for row in result["rows"]]

    async def _list_stage_files(
        self,
        stage_name: str,
        database: str,
        folder: str | None = None,
    ) -> list[dict]:
        from app.modules.stages.service import stage_service

        result = await db.execute_system(
            """
            SELECT id
            FROM NOVA_SYSTEM.CONFIG_STAGES
            WHERE name = %s AND database_name = %s
            ORDER BY schema_name
            LIMIT 1
            """,
            [stage_name, database],
        )
        if not result["rows"]:
            return []
        storage_prefix = folder.replace(".", "/") if folder else ""
        return await stage_service.list_files(result["rows"][0][0], prefix=storage_prefix)

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
        followed by a table name. Strings, comments and ``@stage`` paths are
        masked out first so nothing inside them is considered, and mask
        characters preserve offsets so the rewrite is exact.

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
        return out


query_service = QueryService()
