"""Measure the warm native compatibility path against parse/build/plan, without I/O."""

import asyncio
import json
import statistics
import time

from app.modules.query.dialect.parser import parse_sql
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import EngineSqlPlan
from app.sql_frontend.planning.planner import SQLPlanner

CORPUS = (
    "SELECT 1",
    "SELECT 'CREATE TASK; @stage; ML_PREDICT' AS literal",
    "WITH c AS (SELECT 1 AS id) SELECT id FROM c",
    "INSERT INTO target SELECT 1",
    "UPDATE target SET value=2 WHERE id=1",
    "EXPLAIN SELECT * FROM target",
)


def metrics(samples: list[float]) -> dict[str, float]:
    return {
        "median_us": round(statistics.median(samples), 2),
        "p95_us": round(sorted(samples)[int(len(samples) * 0.95)], 2),
    }


async def report() -> dict:
    planner = SQLPlanner()
    baseline: list[float] = []
    frontend: list[float] = []
    select_one: list[float] = []
    for iteration in range(55):
        for sql in CORPUS:
            start = time.perf_counter_ns()
            legacy = parse_sql(sql)
            elapsed = (time.perf_counter_ns() - start) / 1000
            assert legacy.original_sql == sql and not legacy.errors
            if iteration >= 5:
                baseline.append(elapsed)
            start = time.perf_counter_ns()
            statement = ast_builders.build(parse_statement(sql))
            plan = await planner.plan(statement, PlanningContext(confirm_destructive=True))
            elapsed = (time.perf_counter_ns() - start) / 1000
            assert isinstance(plan, EngineSqlPlan) and plan.engine_sql == sql
            if iteration >= 5:
                frontend.append(elapsed)
                if sql == "SELECT 1":
                    select_one.append(elapsed)
    return {
        "samples_per_path": len(frontend),
        "corpus_statements": len(CORPUS),
        "baseline_native_compatibility": metrics(baseline),
        "frontend_parse_build_plan": metrics(frontend),
        "select_one_parse_build_plan": metrics(select_one),
        "median_added_us": round(statistics.median(frontend) - statistics.median(baseline), 2),
        "scope": "Warm CPU only; no binding, capability detection, secrets, audit or engine I/O",
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(report()), indent=2))
