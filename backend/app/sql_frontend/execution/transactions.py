from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Any, cast

from app.common.identifiers import check_identifier
from app.modules.query.repository import QueryResult
from app.sql_frontend.binding.models import TableName
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.planning.execution import CompositePlan, EngineSqlPlan

logger = logging.getLogger(__name__)
ConnectionFactory = Callable[[ExecutionContext], AbstractAsyncContextManager[Any]]
AuditCallback = Callable[[ExecutionContext, str, list[int], int | None], Awaitable[Any]]


def failure_metadata(
    plan: CompositePlan,
    completed: list[int],
    failed: int | None,
    *,
    outcome: str,
    partial: bool,
    rollback_attempted: bool = False,
) -> dict[str, Any]:
    return {
        "atomicity": plan.atomicity.value,
        "completed_steps": completed,
        "failed_step": failed,
        "outcome": outcome,
        "partial_effects_possible": partial,
        "rollback_attempted": rollback_attempted,
        "rollback_succeeded": outcome == "rolled_back",
    }


async def validate_transaction(plan: CompositePlan, context: ExecutionContext) -> str:
    caps = context.capabilities
    if context.transaction_active or caps is None or not caps.sql_transactions:
        raise SemanticError("Engine transaction is unavailable or nested")
    version = caps.identity.version
    if version is None or version.prerelease or version.release < (3, 5, 0):
        raise SemanticError("Engine transaction support cannot be proven for this release")
    modified: set[tuple[str, ...]] = set()
    inserted: set[tuple[str, ...]] = set()
    mutated: set[tuple[str, ...]] = set()
    targets: set[tuple[str, str]] = set()
    for step in plan.steps:
        if not isinstance(step, EngineSqlPlan) or step.transaction_intent is None:
            raise SemanticError("Transaction requires proven engine DML steps")
        intent = step.transaction_intent
        if not intent.proven or intent.kind not in {"insert", "update", "delete"}:
            raise SemanticError("Transaction dependencies are not eligible")
        catalog, database, table = intent.target
        if catalog != "default_catalog" or not database:
            raise SemanticError("Transaction requires one internal database")
        identity = tuple(value.casefold() for value in intent.target)
        targets.add((catalog, database))
        if {tuple(value.casefold() for value in read) for read in intent.reads} & modified:
            raise SemanticError("Transaction cannot read a previously modified table")
        for read_catalog, read_database, read_table in intent.reads:
            if context.binder is None:
                raise SemanticError("Transaction source dependencies cannot be proven")
            bound = await context.binder.resolve_table(
                TableName(read_table, read_database, catalog=read_catalog)
            )
            if bound.table_type != "BASE TABLE":
                raise SemanticError("Transaction view dependencies cannot be proven")
        shared = caps.identity.deployment_mode == "shared_data" and version.release >= (4, 0, 0)
        if intent.kind in {"update", "delete"}:
            if not shared or not caps.transaction_update_delete or identity in inserted | mutated:
                raise SemanticError("Transaction UPDATE/DELETE order or deployment is unsupported")
            if context.binder is None:
                raise SemanticError("Transaction UPDATE/DELETE table eligibility is unknown")
            details = await context.binder.get_details(TableName(table, database, catalog=catalog))
            if details.table_type != "BASE TABLE" or details.key_type != "PRIMARY":
                raise SemanticError("Transactional UPDATE/DELETE requires a primary-key table")
            mutated.add(identity)
        if intent.kind == "insert":
            if identity in inserted and (not shared or not caps.transaction_repeated_insert):
                raise SemanticError("Repeated transactional INSERT requires shared-data")
            if identity in modified and intent.columns is not None:
                if context.binder is None:
                    raise SemanticError("Partial-column transaction eligibility is unknown")
                columns = await context.binder.get_columns(
                    TableName(table, database, catalog=catalog)
                )
                expected = {column.name.casefold() for column in columns}
                supplied = [column.casefold() for column in intent.columns]
                if len(set(supplied)) != len(supplied) or set(supplied) != expected:
                    raise SemanticError("Partial-column INSERT after a write is unsupported")
            inserted.add(identity)
        modified.add(identity)
    if len(targets) != 1:
        raise SemanticError("Transaction targets must use one database")
    return next(iter(targets))[1]


class TransactionRunner:
    def __init__(self, open_connection: ConnectionFactory, audit: AuditCallback) -> None:
        self.open_connection = open_connection
        self.audit = audit

    async def execute(
        self,
        plan: CompositePlan,
        context: ExecutionContext,
        execute_step: Callable[..., Awaitable[QueryResult]],
    ) -> QueryResult:
        database = await validate_transaction(plan, context)
        completed: list[int] = []
        failed: int | None = None
        outcome = "not_started"
        rollback_attempted = False
        result = QueryResult()
        async with self.open_connection(context) as connection:
            prepared = replace(
                context,
                connection=connection,
                database=database,
                engine_session_prepared=True,
                transaction_active=True,
            )
            try:
                async with connection.cursor() as cursor:
                    if context.role:
                        await cursor.execute(
                            f"SET ROLE {check_identifier(context.role, field='role')}"
                        )
                    await connection.select_db(database)
                    await cursor.execute("BEGIN")
                outcome = "active"
                for index, step in enumerate(plan.steps):
                    failed = index
                    result = await execute_step(step, prepared)
                    if result.error:
                        raise _StepFailure(result)
                    completed.append(index)
                failed = None
                # Once COMMIT is sent, a lost response cannot prove a rollback.
                outcome = "unknown"
                async with connection.cursor() as cursor:
                    await cursor.execute("COMMIT")
                outcome = "committed"
                result.execution_failure = None
                result.warnings.append("Composite transaction committed")
                return result
            except BaseException as exc:
                if outcome == "active":
                    rollback_attempted = True
                    try:

                        async def rollback() -> None:
                            async with connection.cursor() as cursor:
                                await cursor.execute("ROLLBACK")

                        await asyncio.wait_for(rollback(), timeout=2)
                        outcome = "rolled_back"
                    except BaseException:
                        outcome = "unknown"
                metadata = failure_metadata(
                    plan,
                    completed,
                    failed,
                    outcome=outcome,
                    partial=outcome == "unknown",
                    rollback_attempted=rollback_attempted,
                )
                if outcome == "unknown":
                    connection.close()
                if isinstance(exc, _StepFailure):
                    exc.result.execution_failure = metadata
                    return exc.result
                cast(Any, exc).execution_failure = metadata
                raise
            finally:
                try:
                    await self.audit(context, outcome, completed, failed)
                except Exception:
                    logger.exception("Could not audit composite transaction outcome=%s", outcome)


class _StepFailure(Exception):
    def __init__(self, result: QueryResult) -> None:
        self.result = result
