from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from app.core.exceptions import ForbiddenSQLError
from app.modules.query.repository import QueryResult
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.planning.execution import (
    ActionKind,
    CompositePlan,
    EngineSqlPlan,
    ExecutionPlan,
    NovaActionPlan,
)


class EngineHandler(Protocol):
    async def __call__(self, plan: EngineSqlPlan, context: ExecutionContext) -> QueryResult: ...


ActionHandler = Callable[[NovaActionPlan, ExecutionContext], Awaitable[QueryResult]]


class SQLExecutor:
    def __init__(self, engine: EngineHandler) -> None:
        self.engine = engine
        self._actions: dict[ActionKind, ActionHandler] = {}

    def register(self, action: ActionKind, handler: ActionHandler) -> None:
        if action in self._actions:
            raise ValueError(f"Action handler already registered: {action.value}")
        self._actions[action] = handler

    async def execute(self, plan: ExecutionPlan, context: ExecutionContext) -> QueryResult:
        if plan.requires_confirmation and not context.confirm_destructive:
            raise ForbiddenSQLError("Destructive SQL requires confirmation before execution.")
        if isinstance(plan, CompositePlan):
            if not plan.steps:
                raise SemanticError("Composite plan must contain at least one step")
            for step in plan.steps:
                result = await self.execute(step, context)
                if result.error:
                    return result
            return result
        if isinstance(plan, EngineSqlPlan):
            return await self.engine(plan, context)
        if isinstance(plan, NovaActionPlan):
            try:
                handler = self._actions[plan.action]
            except KeyError:
                raise SemanticError("No executor for Nova action") from None
            return await handler(plan, context)
        raise SemanticError("Unsupported execution plan")
