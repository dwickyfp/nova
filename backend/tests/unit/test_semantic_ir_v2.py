"""Semantic IR v2: metric conditions, window transforms, top-N per group, drill-across.

Execution equivalence against an independent oracle is covered by the engine
level of the Studio accuracy benchmark; these tests pin structure and refusal.
"""

from __future__ import annotations

import pytest

from app.modules.agents.semantic.compiler import MultiFactCompilationError, SemanticCompiler
from app.modules.agents.semantic.plan_contract import validate_generated_plan
from app.modules.agents.semantic.planning import (
    SemanticHaving,
    SemanticPlan,
    SemanticPlanError,
    SemanticTime,
    SemanticTopN,
    SemanticTransform,
    validate_plan,
)
from tests.benchmark.studio_accuracy.model import bench_model

MODEL = bench_model()


def compile_plan(**fields) -> str:
    return SemanticCompiler().compile(MODEL, SemanticPlan(**fields)).sql


def test_plain_plan_sql_is_unchanged_by_the_wrapper():
    sql = compile_plan(metrics=("total_revenue",), dimensions=("city",))
    assert "FROM (" not in sql and "OVER (" not in sql


def test_share_of_total_is_a_window_over_all_rows():
    sql = compile_plan(
        metrics=("total_revenue",), dimensions=("city",),
        transforms=(SemanticTransform("total_revenue", "share_of_total"),),
    )
    assert "100.0 * q.`total_revenue` / NULLIF(SUM(q.`total_revenue`) OVER (), 0)" in sql
    assert "AS `total_revenue_share_pct`" in sql


def test_having_filters_after_windows():
    sql = compile_plan(
        metrics=("total_revenue",), dimensions=("city",),
        transforms=(SemanticTransform("total_revenue", "share_of_total"),),
        having=(SemanticHaving("total_revenue", ">", 1000000000),),
    )
    # The share is computed in the inner layer, before the condition filters rows.
    assert sql.index("OVER ()") < sql.index("WHERE w.`total_revenue` > 1000000000")


def test_top_n_per_group_uses_row_number_partitioned_by_the_group():
    sql = compile_plan(
        metrics=("product_revenue",), dimensions=("city", "category"),
        top_n_per_group=SemanticTopN(2, ("city",), "product_revenue"),
    )
    assert "ROW_NUMBER() OVER (PARTITION BY q.`city` ORDER BY q.`product_revenue` DESC)" in sql
    assert "w.`_nova_row` <= 2" in sql
    assert "`_nova_row`" not in sql.split("FROM (", 1)[0]


def test_rank_and_running_total():
    sql = compile_plan(
        metrics=("total_revenue",), dimensions=("city",),
        time=SemanticTime("order_date", grain="month", range="current_year"),
        transforms=(
            SemanticTransform("total_revenue", "rank"),
            SemanticTransform("total_revenue", "running_total"),
        ),
    )
    assert "RANK() OVER (ORDER BY q.`total_revenue` DESC)" in sql
    assert ("SUM(q.`total_revenue`) OVER (PARTITION BY q.`city` ORDER BY q.`order_date` "
            "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)") in sql


def test_share_within_each_compared_period():
    sql = compile_plan(
        metrics=("total_revenue",), dimensions=("city",),
        time=SemanticTime("order_date", range="current_month", compare="previous_period"),
        transforms=(SemanticTransform("total_revenue", "share_of_total"),),
    )
    assert "OVER (PARTITION BY q.`comparison_period`)" in sql


def test_drill_across_joins_conformed_channel():
    sql = compile_plan(
        metrics=("total_revenue", "marketing_spend_total"), dimensions=("sales_channel",),
        time=SemanticTime("order_date", range="current_month"),
    )
    assert "`marketing_spend`.`channel` AS `sales_channel`" in sql
    assert "FULL OUTER JOIN f1 ON f0.`sales_channel` <=> f1.`sales_channel`" in sql
    # Each fact keeps its own time dimension.
    assert "`orders`.`order_date` >= DATE_TRUNC('month', CURRENT_DATE())" in sql
    assert "`marketing_spend`.`spend_date` >= DATE_TRUNC('month', CURRENT_DATE())" in sql


def test_drill_across_by_time_grain_joins_on_period():
    sql = compile_plan(
        metrics=("total_revenue", "marketing_spend_total"),
        time=SemanticTime("order_date", grain="month", range="current_year"),
    )
    assert "f0.`period` <=> f1.`period`" in sql


def test_drill_across_refuses_an_unshared_dimension_and_comparisons():
    with pytest.raises(MultiFactCompilationError, match="conformed_dimensions"):
        compile_plan(metrics=("total_revenue", "marketing_spend_total"), dimensions=("city",))
    with pytest.raises(MultiFactCompilationError):
        compile_plan(
            metrics=("total_revenue", "marketing_spend_total"),
            time=SemanticTime("order_date", range="current_month", compare="previous_period"),
        )


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"having": (SemanticHaving("order_count", ">", 5),)}, "not a selected metric"),
        ({"having": (SemanticHaving("total_revenue", "LIKE", 5),)}, "operator"),
        ({"having": (SemanticHaving("total_revenue", ">", "big"),)}, "number"),
        ({"transforms": (SemanticTransform("total_revenue", "median"),)}, "Unsupported transform"),
        ({"transforms": (SemanticTransform("total_revenue", "running_total"),)}, "time grain"),
        ({"top_n_per_group": SemanticTopN(2, ("city",), "total_revenue")}, "partitions"),
    ],
)
def test_invalid_v2_plans_are_rejected(fields, message):
    plan = SemanticPlan(metrics=("total_revenue",), dimensions=("city",), **fields)
    errors = validate_plan(MODEL, plan)
    assert any(message in error for error in errors), errors
    with pytest.raises(SemanticPlanError):
        SemanticCompiler().compile(MODEL, plan)


def test_contract_accepts_v2_fields_and_plans_without_them():
    base = {
        "metrics": ["total_revenue"], "dimensions": ["city"], "filters": [],
        "named_filters": [], "time": None, "order_by": [], "limit": None,
        "unresolved_concepts": [],
    }
    validate_generated_plan(dict(base))
    validate_generated_plan({
        **base,
        "having": [{"metric": "total_revenue", "operator": ">", "value": 10}],
        "transforms": [{"metric": "total_revenue", "kind": "share_of_total"}],
        "top_n_per_group": None,
    })
    with pytest.raises(SemanticPlanError):
        validate_generated_plan({**base, "transforms": [{"metric": "x", "kind": "median"}]})


def test_plan_round_trip_keeps_v2_fields():
    plan = SemanticPlan(
        metrics=("total_revenue",), dimensions=("city", "category"),
        having=(SemanticHaving("total_revenue", ">", 10),),
        transforms=(SemanticTransform("total_revenue", "rank"),),
        top_n_per_group=SemanticTopN(2, ("city",), "total_revenue"),
    )
    assert SemanticPlan.from_dict(plan.as_dict()) == plan
