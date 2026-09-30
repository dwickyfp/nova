from dataclasses import replace

from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.analysis.semantics import confirmation_policy
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.planning.execution import EngineSqlPlan, ExecutionPlan
from app.sql_frontend.rules.base import PlanRule


class RuleRegistry:
    def __init__(self) -> None:
        self._rules: list[PlanRule] = []

    def register(self, rule: PlanRule) -> None:
        if any(existing.name == rule.name for existing in self._rules):
            raise ValueError(f"Rule already registered: {rule.name}")
        self._rules.append(rule)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(rule.name for rule in self._rules)

    def preflight(self, analysis):
        effects = analysis.effects
        requires = analysis.requires_confirmation
        for rule in self._rules:
            bound = getattr(rule, "effect_bound", PlanEffects())
            effects |= bound
            requires |= getattr(rule, "requires_confirmation_bound", confirmation_policy(bound))
        return replace(analysis, effects=effects, requires_confirmation=requires)

    async def apply(self, plan: ExecutionPlan, context: PlanningContext) -> ExecutionPlan:
        for rule in self._rules:
            if rule.matches(plan, context):
                previous = plan
                plan = await rule.apply(plan, context)
                if (
                    isinstance(plan, EngineSqlPlan)
                    and isinstance(previous, EngineSqlPlan)
                    and plan.engine_sql != previous.engine_sql
                    and plan.transaction_intent == previous.transaction_intent
                ):
                    plan = replace(plan, transaction_intent=None)
                bound = previous.effects | getattr(rule, "effect_bound", PlanEffects())
                if not bound.includes(plan.effects):
                    raise SemanticError(f"Rule {rule.name} exceeded its declared effects")
                added = PlanEffects(
                    **{
                        name: getattr(plan.effects, name) and not getattr(previous.effects, name)
                        for name in plan.effects.__dataclass_fields__
                    }
                )
                if confirmation_policy(added) and not plan.requires_confirmation:
                    raise SemanticError(f"Rule {rule.name} omitted mutation confirmation")
                if not plan.effects.includes(previous.effects):
                    raise SemanticError(f"Rule {rule.name} removed execution effects")
                if previous.requires_confirmation and not plan.requires_confirmation:
                    raise SemanticError(f"Rule {rule.name} removed required confirmation")
        return plan
