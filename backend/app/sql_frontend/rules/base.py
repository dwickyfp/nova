from typing import Protocol

from app.sql_frontend.context import PlanningContext
from app.sql_frontend.planning.execution import ExecutionPlan


class PlanRule(Protocol):
    name: str

    def matches(self, plan: ExecutionPlan, context: PlanningContext) -> bool: ...
    async def apply(self, plan: ExecutionPlan, context: PlanningContext) -> ExecutionPlan: ...
