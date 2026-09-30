from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from app.modules.query.repository import QueryResult
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.errors import ConfirmationRequiredError, SemanticError
from app.sql_frontend.planning.execution import (
    ActionKind,
    Atomicity,
    CompositePlan,
    EngineSqlPlan,
    ExecutionPlan,
    NovaActionPlan,
)


class EngineHandler(Protocol):
    async def __call__(self, plan: EngineSqlPlan, context: ExecutionContext) -> QueryResult: ...


ActionHandler = Callable[[NovaActionPlan, ExecutionContext], Awaitable[QueryResult]]


class ActionHandlerRegistry:
    def __init__(self) -> None:
        self.handlers: dict[type, ActionHandler] = {}

    def register(self, payload_type: type, handler: ActionHandler) -> None:
        if payload_type in self.handlers:
            raise ValueError("Action payload handler already registered")
        self.handlers[payload_type] = handler

    def resolve(self, payload: object) -> ActionHandler | None:
        return self.handlers.get(type(payload))


action_handler_registry = ActionHandlerRegistry()


class SQLExecutor:
    def __init__(
        self,
        engine: EngineHandler,
        *,
        transactions=None,
        extensions: ActionHandlerRegistry | None = None,
    ) -> None:
        self.engine = engine
        self.transactions = transactions
        self.extensions = extensions if extensions is not None else action_handler_registry
        self._actions: dict[ActionKind | str | type, ActionHandler] = {}

    def register(self, action: ActionKind | str | type, handler: ActionHandler) -> None:
        if action in self._actions or action in self.extensions.handlers:
            raise ValueError("Action handler already registered")
        self._actions[action] = handler

    async def execute(self, plan: ExecutionPlan, context: ExecutionContext) -> QueryResult:
        if plan.requires_confirmation and not context.confirm_destructive:
            raise ConfirmationRequiredError(plan.effects)
        if isinstance(plan, CompositePlan):
            if not plan.steps:
                raise SemanticError("Composite plan must contain at least one step")
            if plan.atomicity == Atomicity.SINGLE_ENGINE_TRANSACTION:
                if self.transactions is None:
                    raise SemanticError("Dedicated engine transaction connection is unavailable")
                return await self.transactions.execute(plan, context, self.execute)
            from app.sql_frontend.execution.transactions import failure_metadata

            completed: list[int] = []
            for index, step in enumerate(plan.steps):
                partial = any(item.effects.mutates for item in plan.steps[: index + 1])
                try:
                    result = await self.execute(step, context)
                except BaseException as exc:
                    cast(Any, exc).execution_failure = failure_metadata(
                        plan, completed, index, outcome="partial", partial=partial
                    )
                    raise
                if result.error:
                    result.execution_failure = failure_metadata(
                        plan, completed, index, outcome="partial", partial=partial
                    )
                    return result
                completed.append(index)
            return result
        if isinstance(plan, EngineSqlPlan):
            return await self.engine(plan, context)
        if isinstance(plan, NovaActionPlan):
            try:
                handler = (
                    self._actions.get(type(plan.payload))
                    or self.extensions.resolve(plan.payload)
                    or self._actions[plan.action]
                )
            except KeyError:
                raise SemanticError("No executor for Nova action") from None
            return await handler(plan, context)
        raise SemanticError("Unsupported execution plan")
