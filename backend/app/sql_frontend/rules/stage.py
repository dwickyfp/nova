from dataclasses import replace

from app.sql_frontend.context import PlanningContext
from app.sql_frontend.planning.execution import EngineSqlPlan, ExecutionPlan


class StageReferenceRule:
    name = "stage_references"

    def matches(self, plan: ExecutionPlan, context: PlanningContext) -> bool:
        return isinstance(plan, EngineSqlPlan) and bool(plan.stage_bindings)

    async def apply(self, plan: ExecutionPlan, context: PlanningContext) -> ExecutionPlan:
        assert isinstance(plan, EngineSqlPlan)
        return replace(plan, stage_aware=True)
