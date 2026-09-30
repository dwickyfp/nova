from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.planning.execution import ExecutionPlan
from app.sql_frontend.rules.base import PlanRule


class RuleRegistry:
    def __init__(self) -> None:
        self._rules: list[PlanRule] = []

    def register(self, rule: PlanRule) -> None:
        if any(existing.name == rule.name for existing in self._rules):
            raise ValueError(f"Rule already registered: {rule.name}")
        self._rules.append(rule)

    async def apply(self, plan: ExecutionPlan, context: PlanningContext) -> ExecutionPlan:
        for rule in self._rules:
            if rule.matches(plan, context):
                previous = plan
                plan = await rule.apply(plan, context)
                if not plan.effects.includes(previous.effects):
                    raise SemanticError(f"Rule {rule.name} removed execution effects")
                if previous.requires_confirmation and not plan.requires_confirmation:
                    raise SemanticError(f"Rule {rule.name} removed required confirmation")
        return plan
