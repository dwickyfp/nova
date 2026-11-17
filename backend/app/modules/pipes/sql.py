"""Run pipe statements and pipe views for the SQL frontend's executor."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.modules.query.repository import QueryResult
from app.sql_frontend.analysis.effects import PlanEffects

logger = logging.getLogger(__name__)

_zone: list[str | None] = []


async def _engine_zone() -> str | None:
    """The engine's time zone, in which StarRocks prints pipe times."""
    if not _zone:
        try:
            from app.modules.task_orchestration.repository import task_orchestration_repository

            _zone.append(await task_orchestration_repository.get_engine_timezone())
        except Exception:  # noqa: BLE001 - times print in UTC instead
            return None
    return _zone[0]


def _caller(context: Any):
    from app.modules.pipes.control import PipeCaller

    return PipeCaller(context.username, context.role, context.session_id,
                      int(context.security_context_version or 1))


async def _as_caller(adapters: Any, context: Any, sql: str) -> QueryResult:
    """One statement on the engine as the caller; the SQL may hold stage credentials."""
    from app.common.sql_guard import redact_sql_credentials

    password = (
        "" if context.connection is not None else adapters._decrypt(context.encrypted_password)
    )
    try:
        return await adapters._repo.execute_as_user(
            sql=sql, username=context.username, password=password,
            database=context.database, role=context.role, max_rows=1,
            connected=context.connection,
            **({"session_prepared": True} if context.engine_session_prepared else {}),
        )
    except Exception as exc:  # noqa: BLE001 - returned as the statement's error
        return QueryResult(error=redact_sql_credentials(str(exc))[:2000])


def _plan(plan: Any):
    from app.sql_frontend.planning.execution import EngineSqlPlan

    return plan if isinstance(plan, EngineSqlPlan) else None


async def _rows_query(adapters: Any, plan: Any, context: Any, sql: str) -> QueryResult:
    """Run Nova-rendered rows on the engine as the caller; the audit names the statement."""
    from app.sql_frontend.planning.execution import EngineSqlPlan

    parsed = context.statements[plan.payload.source_key].parsed
    engine_plan = EngineSqlPlan(parsed.normalized_sql, parsed.normalized_sql,
                                PlanEffects(reads_data=True), plan.payload.source_key)
    return await adapters.execute_engine_sql(engine_plan, context, sql_override=sql)


def _result(parsed: Any, columns: list[str], rows: list[list[Any]], start: float,
            warnings: list[str] | None = None, affected: int = 0) -> QueryResult:
    return QueryResult(
        columns=columns, rows=rows, row_count=len(rows), affected_rows=affected,
        elapsed_ms=round((time.monotonic() - start) * 1000, 2),
        original_sql=parsed.original_sql, executed_sql=parsed.original_sql,
        warnings=warnings or [],
    )


async def handle(adapters: Any, plan: Any, context: Any) -> QueryResult:
    from app.core.metadata_db import MetadataConflict
    from app.modules.pipes import views
    from app.modules.pipes.control import PipeError, pipe_control
    from app.modules.pipes.definition import PipeDefinitionError
    from app.sql_frontend.pipes import PipePayload

    payload = plan.payload
    if not isinstance(payload, PipePayload):
        raise TypeError("A pipe statement needs a pipe payload")
    parsed = context.statements[payload.source_key].parsed
    caller = _caller(context)
    start = time.monotonic()
    try:
        if payload.operation == "create":
            async def engine_check(sql: str) -> str | None:
                return (await _as_caller(adapters, context, sql)).error

            row, warnings = await pipe_control.create(
                payload.definition, caller, database=payload.database, schema=payload.schema,
                engine_check=engine_check,
            )
            if not warnings:
                warnings = ["Pipe created. It loads the files already in its path now, "
                            "and new files as they arrive while it is RUNNING."]
            return _result(parsed, ["pipe", "table", "state", "owner_role"],
                           [[f"{row['database_name']}.{row['name']}", row["target_table"],
                             row["state"], row["owner_role"]]], start, warnings, affected=1)
        if payload.operation == "drop":
            dropped = await pipe_control.drop(payload.name, caller, database=payload.database,
                                              if_exists=payload.if_exists)
            if dropped is None:
                return _result(parsed, ["pipe", "dropped"],
                               [[".".join(payload.name), False]], start,
                               ["The pipe does not exist; nothing changed"])
            return _result(parsed, ["pipe", "dropped"],
                           [[f"{dropped['database_name']}.{dropped['name']}", True]], start,
                           ["Pipe dropped. Rows it loaded stay in the table."], affected=1)
        if payload.operation == "alter":
            row, message = await pipe_control.alter(
                payload.name, caller, database=payload.database, action=str(payload.action),
                properties=payload.properties, file_name=payload.file_name,
            )
            return _result(parsed, ["pipe", "state"],
                           [[f"{row['database_name']}.{row['name']}", row["state"]]], start,
                           [message], affected=1)
        if payload.operation == "describe":
            row = await pipe_control.describe(payload.name, caller, payload.database)
            return _result(parsed, list(views.DESC_COLUMNS), [views.describe_row(row)], start)
        database = payload.show_database or payload.database
        rows, status, _ = await pipe_control.visible(caller, database)
        sql = views.show_sql(
            views.pipe_rows(rows, status, await _engine_zone()),
            like=payload.show_like, where=payload.show_where,
            order=payload.show_order, limit=payload.show_limit,
        )
        return await _rows_query(adapters, plan, context, sql)
    except (PipeError, PipeDefinitionError, MetadataConflict) as exc:
        message = str(exc)
        if isinstance(exc, MetadataConflict):
            message = ("The pipe changed at the same time, or another role already has a pipe "
                       "with this name; check SHOW PIPES and try again")
        return QueryResult(error=message, original_sql=parsed.original_sql,
                           executed_sql=parsed.original_sql,
                           elapsed_ms=round((time.monotonic() - start) * 1000, 2))


async def intercept(adapters: Any, plan: Any, context: Any) -> QueryResult | None:
    """A query over ``information_schema.pipes`` or ``pipe_files`` reads Nova's pipes."""
    from app.modules.pipes import views
    from app.modules.pipes.control import PipeError, pipe_control

    engine_plan = _plan(plan)
    if engine_plan is None or engine_plan.stage_bindings or engine_plan.private_bindings:
        return None
    parsed = context.statements[engine_plan.source_key].parsed
    if "pipe" not in parsed.normalized_sql.lower():
        return None
    refs = views.information_schema_refs(parsed)
    if not refs:
        return None
    try:
        pipes, status, files = await pipe_control.visible(
            _caller(context), files=any(view == "pipe_files" for view, _ in refs),
        )
    except PipeError as exc:
        return QueryResult(error=str(exc), original_sql=parsed.original_sql,
                           executed_sql=parsed.original_sql)
    zone = await _engine_zone()
    rendered = {
        "pipes": views.relation_sql(views.PIPES_COLUMNS, views.pipe_rows(pipes, status, zone)),
        "pipe_files": views.relation_sql(views.FILES_COLUMNS,
                                         views.file_rows(pipes, files, zone)),
    }
    replacements = []
    for view, node in refs:
        name = node.qualifiedName()
        text = rendered[view]
        if node.alias is None:
            text += f" AS `{view}`"
        replacements.append((name.start.start, name.stop.stop + 1, text))
    from app.sql_frontend.literals import typed_literal_rewrites

    for rewrite in typed_literal_rewrites(parsed):
        replacements.append((rewrite.start, rewrite.end, rewrite.slot))
    sql = parsed.normalized_sql
    for start, end, text in sorted(replacements, reverse=True):
        sql = sql[:start] + text + sql[end:]
    return await adapters.execute_engine_sql(engine_plan, context, sql_override=sql)
