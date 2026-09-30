from __future__ import annotations

from app.common.sql_guard import redact_sql_credentials
from app.core.exceptions import ForbiddenSQLError
from app.sql_frontend.analysis.analyzer import analyze
from app.sql_frontend.ast import statements as ast
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.planning.execution import (
    ActionKind,
    EngineSqlPlan,
    ExecutionPlan,
    NovaActionPlan,
    StageBinding,
)
from app.sql_frontend.planning.logical import LogicalPlan
from app.sql_frontend.planning.payloads import action_payload
from app.sql_frontend.planning.registry import PlannerRegistry
from app.sql_frontend.rules.registry import RuleRegistry


class NativePlanner:
    async def plan(self, logical: LogicalPlan, context: PlanningContext) -> EngineSqlPlan:
        sql = redact_sql_credentials(logical.statement.parsed.normalized_sql)
        from app.sql_frontend.stages import stage_view

        bindings: tuple[StageBinding, ...] = ()
        if isinstance(logical.statement, ast.StageAwareStatement):
            bindings = tuple(
                StageBinding(
                    ref.stage_name,
                    tuple(ref.path_parts + ([ref.file_name] if ref.file_name else [])),
                    ref.start,
                    ref.end,
                )
                for ref in stage_view(logical.statement.parsed).stage_refs
            )
        return EngineSqlPlan(
            sql,
            sql,
            logical.analysis.effects,
            logical.source_key,
            requires_confirmation=logical.analysis.requires_confirmation,
            stage_bindings=bindings,
        )


class ActionPlanner:
    def __init__(self, action: ActionKind) -> None:
        self.action = action

    async def plan(self, logical: LogicalPlan, context: PlanningContext) -> ExecutionPlan:
        from app.sql_frontend.analysis.validation import validate_action

        context.validated[logical.source_key] = validate_action(logical.statement, context)
        return NovaActionPlan(
            self.action,
            action_payload(self.action, logical.source_key, context.validated[logical.source_key]),
            logical.analysis.effects,
            logical.analysis.requires_confirmation,
        )


class SecurityPlanner(ActionPlanner):
    async def plan(self, logical: LogicalPlan, context: PlanningContext) -> ExecutionPlan:
        if not context.ranger_enabled or type(
            logical.statement.parsed.statement_context
        ).__name__ in {
            "CreateUserStatementContext",
            "AlterUserStatementContext",
            "DropUserStatementContext",
            "SetDefaultRoleStatementContext",
        }:
            return await NativePlanner().plan(logical, context)
        from app.sql_frontend.security import decode_security

        if decode_security(logical.statement.parsed) is None:
            return await NativePlanner().plan(logical, context)
        return await super().plan(logical, context)


def default_registry() -> PlannerRegistry:
    registry = PlannerRegistry()
    registry.register(ast.NativeStatement, NativePlanner())
    registry.register(ast.StageAwareStatement, NativePlanner())
    registry.register(ast.SecurityStatement, SecurityPlanner(ActionKind.SECURITY))
    for node, action in {
        ast.CreateTaskStatement: ActionKind.CREATE_TASK,
        ast.CreateMLModelStatement: ActionKind.CREATE_ML_MODEL,
        ast.MLPredictStatement: ActionKind.ML_PREDICT,
        ast.MLMaterializeStatement: ActionKind.ML_MATERIALIZE,
        ast.MLForecastStatement: ActionKind.ML_FORECAST,
        ast.ForcePasswordChangeStatement: ActionKind.FORCE_PASSWORD_CHANGE,
    }.items():
        registry.register(node, ActionPlanner(action))
    return registry


class SQLPlanner:
    def __init__(
        self, registry: PlannerRegistry | None = None, rules: RuleRegistry | None = None
    ) -> None:
        self.registry = registry or default_registry()
        self.rules = rules if rules is not None else RuleRegistry()
        if rules is None:
            from app.sql_frontend.rules.stage import StageReferenceRule

            self.rules.register(StageReferenceRule())

    async def plan(
        self, statement: ast.Statement, context: PlanningContext, *, source_key: int = 0
    ) -> ExecutionPlan:
        logical = LogicalPlan(statement, analyze(statement), source_key)
        plan = await self.registry.resolve(statement).plan(logical, context)
        plan = await self.rules.apply(plan, context)
        if plan.requires_confirmation and not context.confirm_destructive:
            raise ForbiddenSQLError("Destructive SQL requires confirmation before execution.")
        return plan
