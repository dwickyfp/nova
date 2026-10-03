from __future__ import annotations

import logging
from dataclasses import asdict
from uuid import uuid4

from app.common.sql_guard import redact_sql_credentials
from app.sql_frontend.analysis.semantics import StatementSemanticsRegistry, semantics_registry
from app.sql_frontend.ast import statements as ast
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.planning.execution import (
    ActionKind,
    EngineSqlPlan,
    ExecutionPlan,
    NovaActionPlan,
    PrivateSqlBinding,
    StageBinding,
)
from app.sql_frontend.planning.logical import LogicalPlan
from app.sql_frontend.planning.payloads import action_payload
from app.sql_frontend.planning.registry import PlannerRegistry
from app.sql_frontend.planning.transactions import transaction_intent
from app.sql_frontend.rules.registry import RuleRegistry
from app.sql_frontend.runtime_sql import private_sql_bindings

logger = logging.getLogger(__name__)


class NativePlanner:
    async def plan(self, logical: LogicalPlan, context: PlanningContext) -> EngineSqlPlan:
        sql = logical.statement.parsed.normalized_sql
        from app.sql_frontend.stages import stage_view

        bindings: tuple[StageBinding, ...] = ()
        command = "regular"
        private = private_sql_bindings(logical.statement.parsed)
        if isinstance(logical.statement, ast.StageAwareStatement):
            stages = stage_view(logical.statement.parsed)
            command = stages.command_type.value
            nonce = uuid4().hex
            bindings = tuple(
                StageBinding(
                    ref.stage_name,
                    tuple(ref.path_parts + ([ref.file_name] if ref.file_name else [])),
                    ref.start,
                    ref.end,
                    f"`__nova_stage_{nonce}_{index}`",
                    ref.file_name,
                    ref.is_directory,
                    "write" if command == "stage_export" and index == 0 else "read",
                    (context.database, context.schema),
                )
                for index, ref in enumerate(stages.stage_refs)
            )
        replacements: list[StageBinding | PrivateSqlBinding] = [*bindings, *private]
        replacements.sort(key=lambda binding: binding.start)
        if any(a.end > b.start for a, b in zip(replacements, replacements[1:], strict=False)):
            raise ValueError("Overlapping SQL template slots")
        for binding in reversed(replacements):
            sql = sql[: binding.start] + binding.slot + sql[binding.end :]
        sql = redact_sql_credentials(sql)
        return EngineSqlPlan(
            sql,
            sql,
            logical.analysis.effects,
            logical.source_key,
            requires_confirmation=logical.analysis.requires_confirmation,
            stage_bindings=bindings,
            stage_command=command,
            transaction_intent=transaction_intent(logical.statement, context),
            private_bindings=private,
        )


class ActionPlanner:
    def __init__(self, action: ActionKind) -> None:
        self.action = action

    async def plan(self, logical: LogicalPlan, context: PlanningContext) -> ExecutionPlan:
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
        if context.validated[logical.source_key] is None:
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
        self,
        registry: PlannerRegistry | None = None,
        rules: RuleRegistry | None = None,
        semantics: StatementSemanticsRegistry | None = None,
    ) -> None:
        self.registry = registry or default_registry()
        self.semantics = semantics if semantics is not None else semantics_registry
        self.rules = rules if rules is not None else RuleRegistry()
        if rules is None:
            from app.sql_frontend.rules.stage import StageReferenceRule

            self.rules.register(StageReferenceRule())

    async def plan(
        self, statement: ast.Statement, context: PlanningContext, *, source_key: int = 0
    ) -> ExecutionPlan:
        context.semantics = self.semantics
        context.validated[source_key] = self.semantics.validate(statement, context)
        logical = LogicalPlan(statement, self.semantics.analyze(statement), source_key)
        plan = await self.registry.resolve(statement).plan(logical, context)
        plan = await self.rules.apply(plan, context)
        logger.debug(
            "SQL planning metadata: %s",
            {
                "statement_kind": type(statement).__name__,
                "planner": type(self.registry.resolve(statement)).__name__,
                "rules": self.rules.names,
                "effects": asdict(plan.effects),
                "binding_requests": getattr(context.binder, "requests", {}),
                "capability_source": getattr(context.capabilities, "source", "conservative"),
                "atomicity": getattr(getattr(plan, "atomicity", None), "value", None),
            },
        )
        return plan

    def preflight(self, statement: ast.Statement):
        analysis = self.semantics.analyze(statement)
        return self.rules.preflight(analysis)
