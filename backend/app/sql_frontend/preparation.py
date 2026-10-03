from __future__ import annotations

from app.modules.ml_engine.spec import MLSecurityContext
from app.modules.query.sql_pipeline import (
    PreparedSQL,
    guard_user_statement,
    prepare_stage_sql,
    redact_for_output,
)
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.execution.stages import StageRuntime
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import EngineSqlPlan
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.runtime_sql import lower_private_sql
from app.sql_frontend.stages import planned_stages


async def prepare_stream_sql(sql: str, security: MLSecurityContext) -> PreparedSQL:
    guard_user_statement(sql)
    parsed = parse_statement(sql)
    statement = ast_builders.build(parsed)
    plan = await SQLPlanner().plan(statement, PlanningContext())
    if not isinstance(plan, EngineSqlPlan):
        raise SemanticError("Streaming input requires engine SQL")
    stages = planned_stages(plan)
    configs = None
    if stages.stage_refs:
        stages, configs = await StageRuntime()._resolve_stage_refs(
            stages,
            database=security.database,
            schema=security.schema,
            username=security.username,
            password=security.password,
            role=security.role,
        )
    prepared = await prepare_stage_sql(plan.engine_sql, parsed=stages, stage_configs_by_ref=configs)
    prepared.engine_sql = lower_private_sql(plan, prepared.engine_sql, parsed.normalized_sql)
    prepared.redacted_sql = redact_for_output(prepared.engine_sql)
    return prepared
