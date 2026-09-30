from __future__ import annotations

from typing import Protocol

from app.sql_frontend.ast.statements import Statement
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.planning.execution import ExecutionPlan
from app.sql_frontend.planning.logical import LogicalPlan


class StatementPlanner(Protocol):
    async def plan(self, logical: LogicalPlan, context: PlanningContext) -> ExecutionPlan: ...


class PlannerRegistry:
    def __init__(self) -> None:
        self._planners: dict[type[Statement], StatementPlanner] = {}

    def register(self, node: type[Statement], planner: StatementPlanner) -> None:
        if node in self._planners:
            raise ValueError(f"Planner already registered for {node.__name__}")
        self._planners[node] = planner

    def resolve(self, statement: Statement) -> StatementPlanner:
        try:
            return self._planners[type(statement)]
        except KeyError:
            raise SemanticError(f"No planner for {type(statement).__name__}") from None
