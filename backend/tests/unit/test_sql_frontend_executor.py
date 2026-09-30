from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ForbiddenSQLError
from app.modules.query.repository import QueryResult
from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.execution.executor import SQLExecutor
from app.sql_frontend.planning.execution import (
    ActionKind,
    Atomicity,
    CompositePlan,
    EngineSqlPlan,
    NovaActionPlan,
    SourcePayload,
)


def engine(sql="SELECT 1", **flags):
    return EngineSqlPlan(sql, sql, PlanEffects(**flags))


async def test_composite_is_ordered_and_returns_final_step():
    calls = []

    async def run(plan, context):
        calls.append(plan.engine_sql)
        return QueryResult(rows=[[len(calls)]])

    plan = CompositePlan(
        (engine(reads_data=True), engine("INSERT INTO t SELECT 1", writes_data=True)),
        Atomicity.BEST_EFFORT,
    )
    result = await SQLExecutor(run).execute(plan, ExecutionContext("alice"))
    assert calls == ["SELECT 1", "INSERT INTO t SELECT 1"]
    assert result.rows == [[2]]
    assert plan.effects == PlanEffects(reads_data=True, writes_data=True)


@pytest.mark.parametrize("exception", [False, True])
async def test_composite_stops_on_failure_without_rollback(exception):
    calls = []

    async def run(plan, context):
        calls.append(plan.engine_sql)
        if exception:
            raise RuntimeError("engine failed")
        return QueryResult(error="engine failed")

    executor = SQLExecutor(run)
    plan = CompositePlan((engine(), engine("SELECT 2")), Atomicity.BEST_EFFORT)
    if exception:
        with pytest.raises(RuntimeError):
            await executor.execute(plan, ExecutionContext("alice"))
    else:
        assert (await executor.execute(plan, ExecutionContext("alice"))).error
    assert calls == ["SELECT 1"]


async def test_confirmation_is_enforced_before_any_composite_step():
    handler = AsyncMock()
    deletion = EngineSqlPlan(
        "DELETE FROM t", "DELETE FROM t", PlanEffects(deletes_rows=True), requires_confirmation=True
    )
    plan = CompositePlan((engine(), deletion), Atomicity.BEST_EFFORT)
    with pytest.raises(ForbiddenSQLError):
        await SQLExecutor(handler).execute(plan, ExecutionContext("alice"))
    handler.assert_not_awaited()
    assert plan.requires_confirmation


async def test_typed_action_dispatch_rejects_unknown_and_duplicate_handlers():
    handler = AsyncMock(return_value=QueryResult(rows=[["done"]]))
    executor = SQLExecutor(AsyncMock())
    plan = NovaActionPlan(ActionKind.CREATE_TASK, SourcePayload(3), PlanEffects(writes_data=True))
    assert "encrypted_password" not in str(asdict(plan))
    with pytest.raises(SemanticError):
        await executor.execute(plan, ExecutionContext("alice"))
    executor.register(ActionKind.CREATE_TASK, handler)
    assert (await executor.execute(plan, ExecutionContext("alice"))).rows == [["done"]]
    with pytest.raises(ValueError):
        executor.register(ActionKind.CREATE_TASK, handler)


async def test_empty_composite_is_rejected():
    with pytest.raises(SemanticError):
        await SQLExecutor(AsyncMock()).execute(
            CompositePlan((), Atomicity.BEST_EFFORT), ExecutionContext("alice")
        )
