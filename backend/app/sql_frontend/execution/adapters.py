from __future__ import annotations

import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import asyncmy  # type: ignore[import-untyped]

from app.common.ml_intercept import (
    MLForecastCall,
    MLPredictCall,
    detect_ml_forecast,
    rewrite_ml_predict_projection,
)
from app.common.sql_guard import CredentialsRedactionError
from app.core.database import db
from app.core.exceptions import ForbiddenSQLError
from app.modules.query.dialect.force_password_change import parse_force_password_change
from app.modules.query.dialect.ml_model import parse_create_ml_model
from app.modules.query.dialect.parser import CommandType, ParsedSQL
from app.modules.query.repository import QueryResult
from app.modules.query.sql_pipeline import prepare_stage_sql, redact_for_output
from app.modules.task_orchestration.ddl import TaskDDLError, parse_create_task
from app.modules.task_orchestration.lowering import TaskLoweringError, persist_lowered_task
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.execution.executor import ActionHandler, SQLExecutor
from app.sql_frontend.execution.stages import StageRuntime
from app.sql_frontend.parser import ParsedStatement
from app.sql_frontend.planning.execution import (
    EngineSqlPlan,
    ForecastPayload,
    MaterializePayload,
    ModelPayload,
    NovaActionPlan,
    PasswordPolicyPayload,
    PredictionPayload,
    SecurityPayload,
    TaskPayload,
)
from app.sql_frontend.runtime_sql import lower_private_sql
from app.sql_frontend.stages import planned_stages
from app.storage.secrets import SecretResolutionError

logger = logging.getLogger(__name__)


def _redact_error_message(message: str) -> str:
    try:
        return redact_for_output(message)
    except CredentialsRedactionError:
        return "[redacted: unredactable error message]"


class FeatureAdapters:
    def __init__(
        self,
        host: Any,
        *,
        audit: Callable[..., Awaitable[Any]],
        decrypt: Callable[[str], str],
        task_repository: Any,
        set_flag: Callable[..., Awaitable[Any]],
        check_stage_access: Callable[..., Awaitable[Any]],
        get_storage_connection: Callable[..., Any],
        resolve_storage_credentials: Callable[..., tuple[str, str]],
    ) -> None:
        self._host = host
        self._repo = host._repo
        self._audit_sink = audit
        self._decrypt = decrypt
        self._task_repository = task_repository
        self._set_flag = set_flag
        self._check_stage_access = check_stage_access
        self._storage_connection = get_storage_connection
        self._resolve_credentials = resolve_storage_credentials

    async def _audit(self, **kwargs: Any) -> Any:
        for key in ("sql_text", "rewritten_sql", "error_message"):
            if kwargs.get(key):
                kwargs[key] = _redact_error_message(str(kwargs[key]))
        return await self._audit_sink(**kwargs)

    def executor(self) -> SQLExecutor:
        from app.sql_frontend.execution.transactions import TransactionRunner

        executor = SQLExecutor(
            self.execute_engine,
            transactions=TransactionRunner(self._transaction_connection, self._transaction_audit),
        )
        handlers: dict[type, Callable[..., Awaitable[QueryResult]]] = {
            TaskPayload: self._execute_create_task,
            ModelPayload: self._execute_create_ml_model,
            PredictionPayload: self._execute_ml_predict,
            ForecastPayload: self._execute_ml_forecast,
            PasswordPolicyPayload: self._execute_force_password_change,
        }
        for action, method in handlers.items():
            executor.register(action, self._handler(method))
        executor.register(MaterializePayload, self.materialize)
        executor.register(SecurityPayload, self.security)
        return executor

    @asynccontextmanager
    async def _transaction_connection(self, context):
        if not context.encrypted_password:
            raise SemanticError("Dedicated authenticated transaction connection is unavailable")
        async with db.user_conn(
            context.username, self._decrypt(context.encrypted_password)
        ) as conn:
            yield conn

    async def _transaction_audit(self, context, outcome, completed, failed):
        await self._audit(
            event_type="query",
            user_name=context.username,
            action="transaction_" + outcome,
            object_type="sql",
            object_name=context.database or "workspace",
            status="SUCCESS" if outcome == "committed" else "ERROR",
            error_message=json.dumps(
                {"outcome": outcome, "completed_steps": completed, "failed_step": failed}
            ),
            active_role=context.role,
            session_id=context.session_id,
            file_id=context.file_id,
            database_name=context.database,
            schema_name=context.schema,
        )

    def stage_schema_provider(self, context):
        async def schema(reference):
            from app.sql_frontend.binding.models import BoundOutputColumn, BoundRelation
            from app.sql_frontend.binding.types import parse_sql_type
            from app.sql_frontend.errors import BindingError

            if not context.capabilities or not context.capabilities.describe_files:
                raise BindingError("Engine cannot describe a stage relation")
            source = "SELECT * FROM " + reference.full_match
            from dataclasses import replace as replace_ref

            ref = replace_ref(reference, start=len("SELECT * FROM "), end=len(source))
            parsed = ParsedSQL(
                command_type=CommandType.STAGE_QUERY,
                original_sql=source,
                stage_refs=[ref],
                base_sql=source,
                errors=[],
            )
            parsed, configs = await self._host._resolve_stage_refs(
                parsed,
                database=context.database,
                schema=context.schema,
                username=context.username,
                role=context.role,
                connection=context.connection,
                password="" if context.connection else self._decrypt(context.encrypted_password),
            )
            params, columns = await self._host._detect_csv_params(
                parsed, {parsed.stage_refs[0].stage_name: configs[ref.start]}
            )
            prepared = await prepare_stage_sql(
                source,
                parsed=parsed,
                stage_configs_by_ref=configs,
                csv_params_by_ref={ref.start: params},
                csv_columns=columns,
            )
            # Credentials exist only in this execution helper, never the binder.
            sql = "DESC " + prepared.engine_sql[len("SELECT * FROM ") :]
            try:
                result = await self._repo.execute_as_user(
                    sql=sql,
                    username=context.username,
                    password=""
                    if context.connection
                    else self._decrypt(context.encrypted_password),
                    database=context.database,
                    role=context.role,
                    connected=context.connection,
                )
                if result.error:
                    raise BindingError("Stage metadata is unavailable")
                return BoundRelation(
                    tuple(
                        BoundOutputColumn(
                            columns[index] if columns and index < len(columns) else str(row[0]),
                            parse_sql_type(str(row[1])),
                            str(row[2]).upper() == "YES" if len(row) > 2 else None,
                        )
                        for index, row in enumerate(result.rows)
                    )
                )
            except Exception:
                raise BindingError("Stage metadata is unavailable or not accessible") from None
            finally:
                await self._host._audit_secret_resolutions(username=context.username)

        return schema

    def _handler(self, method: Callable[..., Awaitable[QueryResult]]) -> ActionHandler:
        parameters = inspect.signature(method).parameters

        async def handle(plan: NovaActionPlan, context: ExecutionContext) -> QueryResult:
            if hasattr(plan.payload, "definition") and plan.payload.definition is None:
                raise SemanticError("Action payload requires a validated definition")
            statement = context.statements[plan.payload.source_key]
            parsed = statement.parsed
            kwargs = vars_context(context)
            kwargs.update(
                sql=parsed.original_sql,
                normalized_sql=parsed.normalized_sql,
                validated_frontend=getattr(plan.payload, "definition", plan.payload),
                match=None,
                call=getattr(plan.payload, "definition", None),
            )
            return await method(
                **{key: value for key, value in kwargs.items() if key in parameters}
            )

        return handle

    async def execute_engine(self, plan: EngineSqlPlan, context: ExecutionContext) -> QueryResult:
        statement = context.statements[plan.source_key]
        source = statement.parsed
        sql = source.original_sql
        normalized_sql = (
            source.normalized_sql
            if not plan.private_bindings
            and plan.engine_sql == redact_for_output(source.normalized_sql)
            else plan.engine_sql
        )
        parsed = planned_stages(plan)
        if parsed.stage_refs and not plan.stage_aware:
            raise ValueError("Stage references require a stage lowering rule")
        username, encrypted_password = context.username, context.encrypted_password
        database, schema, role = context.database, context.schema, context.role
        max_rows, connection = context.max_rows, context.connection
        session_id, file_id = context.session_id, context.file_id
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
                parsed, stage_configs_by_ref = await self._host._resolve_stage_refs(
                    parsed,
                    database=database,
                    schema=schema,
                    username=username,
                    password="" if connection is not None else self._decrypt(encrypted_password),
                    role=role,
                    connection=connection,
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
                    params, columns = await self._host._detect_csv_params(
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
                await self._host._audit_secret_resolutions(username=username)
                await self._host._audit_engine_result(
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
            await self._host._audit_secret_resolutions(username=username)
        else:
            prepared = await prepare_stage_sql(normalized_sql, parsed=parsed)
            executed_sql = prepared.engine_sql
            warnings = prepared.warnings

        executed_sql = lower_private_sql(plan, executed_sql, source.normalized_sql)

        # 5. Execute
        #
        # ``connection`` is an already-authenticated engine session supplied by
        # the MySQL proxy, which relays StarRocks' own challenge and therefore
        # never holds a password (see ``app/proxy/auth.py``). Only the
        # connection-opening path needs the plaintext, so the decrypt is skipped
        # when one was injected — otherwise an empty ``encrypted_password``
        # would raise ``InvalidToken`` before the statement ever ran.
        password = "" if connection is not None else self._decrypt(encrypted_password)

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
                **({"session_prepared": True} if context.engine_session_prepared else {}),
            )

            result.original_sql = redact_for_output(sql)
            result.executed_sql = redacted_sql
            result.warnings = warnings

            # Rename $1, $2 columns with CSV header names if detected
            if csv_column_names and result.columns:
                for i, col_name in enumerate(csv_column_names):
                    if i < len(result.columns):
                        result.columns[i] = col_name
            await self._audit(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="sql",
                object_name=(database or "") if database else "workspace",
                status="ERROR"
                if result.error
                else ("EXECUTED" if context.transaction_active else "SUCCESS"),
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
            await self._audit(
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
        parsed_frontend=None,
        validated_frontend=None,
    ) -> QueryResult:
        """Execute Nova CREATE ML_MODEL DDL through the ML engine."""
        start = time.monotonic()
        try:
            statement = validated_frontend or parse_create_ml_model(
                normalized_sql, tokens=parsed_frontend.visible_tokens if parsed_frontend else None
            )
            password = self._decrypt(encrypted_password)

            from app.modules.ml_engine.service import ml_engine_service

            result = await ml_engine_service.train_model(
                **tenant_arguments(tenant),
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
            await self._audit(
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
            await self._audit(
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
        parsed_frontend=None,
        validated_frontend=None,
    ) -> QueryResult:
        """Execute Nova ``ML_PREDICT`` as one columnar, vectorized batch."""
        start = time.monotonic()
        alias = match.group(1)
        feature_sql = ""
        password = "" if connection is not None else self._decrypt(encrypted_password)
        from app.modules.ml_engine.service import ml_engine_service

        try:
            rewrite = validated_frontend or rewrite_ml_predict_projection(
                normalized_sql, match, tree=parsed_frontend.parse_tree if parsed_frontend else None
            )
            alias = rewrite.alias
            feature_sql = rewrite.feature_sql
            metadata, result_table = await ml_engine_service.batch_predict_projected(
                **tenant_arguments(tenant),
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
                **prediction_arguments(rewrite.predictions),
            )
            del metadata
            columns = result_table.column_names
            rows: list[list[Any]] = []
            for batch in result_table.to_batches(max_chunksize=4096):
                values = [column.to_pylist() for column in batch.columns]
                rows.extend([list(row) for row in zip(*values, strict=True)])
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            await self._audit(
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
            await self._audit(
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
            await self._audit(
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
            await self._audit(
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
        parsed_frontend=None,
        validated_frontend=None,
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
            if not role:
                raise TaskLoweringError("CREATE TASK requires an explicit execution role")
            timezone = await self._task_repository.get_engine_timezone()
            if not timezone:
                raise TaskLoweringError(
                    "cannot determine the engine timezone; CREATE TASK stores an "
                    "explicit IANA zone and will not assume UTC"
                )
            task = validated_frontend or parse_create_task(
                normalized_sql,
                database=database,
                schema=schema,
                timezone=timezone,
                parsed=parsed_frontend,
            )
            task = replace(
                task,
                database_name=task.database_name or database,
                schema_name=task.schema_name or schema or "default",
                timezone=task.timezone or timezone,
            )
            persisted = await persist_lowered_task(task, created_by=username, owner_role=role)
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            task_row = persisted.task
            edge_count = len(persisted.edges)

            await self._audit(
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
            await self._audit(
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
        role: str | None,
        parsed_frontend=None,
        validated_frontend=None,
    ) -> QueryResult:
        """Record the first-login password-change flag for a user.

        The statement is Nova metadata: StarRocks has no such attribute, so it is
        parsed here and written to ``NOVA_SYSTEM.CONFIG_USER_PREFERENCES``; the
        engine never sees it. Only the flag is stored — no password.
        """
        start = time.monotonic()
        try:
            parsed = validated_frontend or parse_force_password_change(
                normalized_sql, parsed=parsed_frontend
            )
            from app.modules.users.service import user_service

            if role not in {"ACCOUNTADMIN", "SECURITYADMIN", "user_admin", "security_admin"}:
                raise ForbiddenSQLError(
                    "An active security-admin role is required for password-change policy."
                )
            if parsed.username.casefold() == "root":
                raise ForbiddenSQLError("The root account is protected.")
            if not await user_service.user_exists(parsed.username):
                raise ValueError("The target user does not exist.")
            await self._set_flag(parsed.username, required=parsed.required)
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            await self._audit(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="user",
                object_name=username,
                status="ERROR",
                sql_text=sql,
                rewritten_sql=normalized_sql,
                error_message=_redact_error_message(str(exc)),
                duration_ms=int(elapsed_ms),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            return QueryResult(
                error=_redact_error_message(str(exc)),
                elapsed_ms=elapsed_ms,
                original_sql=sql,
                executed_sql=normalized_sql,
            )

        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        await self._audit(
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

    def stage_runtime(self) -> StageRuntime:
        from app.sql_frontend.execution.stages import StageRuntime

        return StageRuntime(
            access_check=self._check_stage_access,
            storage_connection=self._storage_connection,
            credentials=self._resolve_credentials,
        )

    async def _resolve_stage_refs(self, parsed: ParsedSQL, **kwargs: Any) -> tuple[ParsedSQL, dict]:
        return await self.stage_runtime()._resolve_stage_refs(parsed, **kwargs)

    async def _detect_csv_params(
        self, parsed: ParsedSQL, stage_configs: dict
    ) -> tuple[dict[str, str], list[str] | None]:
        return await self.stage_runtime()._detect_csv_params(parsed, stage_configs)

    async def materialize(self, plan: NovaActionPlan, context: ExecutionContext) -> QueryResult:
        parsed_frontend = context.statements[plan.payload.source_key].parsed
        sql, normalized_sql = parsed_frontend.original_sql, parsed_frontend.normalized_sql
        username, encrypted_password = context.username, context.encrypted_password
        database, schema, role = context.database, context.schema, context.role
        connection, tenant = context.connection, context.tenant
        security_context_version = context.security_context_version
        from app.modules.ml_engine.service import ml_engine_service
        from app.modules.ml_engine.spec import MLSecurityContext

        if not isinstance(plan.payload, MaterializePayload) or not plan.payload.input_sql:
            raise SemanticError("Materialization requires validated prediction input")
        alias, input_sql = plan.payload.model_alias, plan.payload.input_sql
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
                    password=self._decrypt(encrypted_password),
                    database=database,
                    schema=schema,
                    role=role,
                    tenant=tenant,
                    security_context_version=security_context_version,
                ),
            )
            await self._audit(
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
            await self._audit(
                event_type="query",
                user_name=username,
                action="ml_predict_materialize",
                object_type="ml_model",
                object_name=alias,
                status="ERROR",
                error_message=_redact_error_message(str(exc)),
            )
            raise

    async def security(self, plan: NovaActionPlan, context: ExecutionContext) -> QueryResult:
        from app.modules.access_control.security_context import require_security_context
        from app.sql_frontend.security import SecurityOperation, execute_security_operation

        if not isinstance(plan.payload, SecurityPayload):
            raise SemanticError("Security action requires a typed security payload")
        parsed = context.statements[plan.payload.source_key].parsed
        security = require_security_context(
            principal=context.username,
            active_role=context.role,
            database=context.database,
            session_id=context.session_id,
            security_context_version=context.security_context_version,
        )
        try:
            result = await execute_security_operation(
                SecurityOperation(plan.payload.operation, plan.payload.identifiers),
                security,
                parsed.original_sql,
            )
        except Exception as exc:
            await self._audit(
                event_type="query",
                user_name=context.username,
                action="execute",
                object_type="security",
                object_name=context.role or "",
                status="ERROR",
                sql_text=parsed.normalized_sql,
                error_message=str(exc),
                active_role=context.role,
                database_name=context.database,
                schema_name=context.schema,
                session_id=context.session_id,
            )
            raise
        await self._audit(
            event_type="query",
            user_name=context.username,
            action="execute",
            object_type="security",
            object_name=context.role or "",
            status="SUCCESS",
            sql_text=parsed.normalized_sql,
            active_role=context.role,
            database_name=context.database,
            schema_name=context.schema,
            session_id=context.session_id,
        )
        return result


def vars_context(context: ExecutionContext) -> dict[str, Any]:
    return {
        name: getattr(context, name)
        for name in (
            "username",
            "encrypted_password",
            "database",
            "schema",
            "role",
            "max_rows",
            "session_id",
            "file_id",
            "tenant",
            "security_context_version",
            "connection",
        )
    }


def tenant_arguments(tenant: str) -> dict[str, Any]:
    return {"tenant": tenant} if tenant != "default" else {}


def prediction_arguments(predictions: tuple) -> dict[str, Any]:
    return {"predictions": predictions} if len(predictions) > 1 else {}


def forecast_call(parsed: ParsedStatement) -> MLForecastCall | None:
    return detect_ml_forecast(parsed.normalized_sql, parsed=parsed)
