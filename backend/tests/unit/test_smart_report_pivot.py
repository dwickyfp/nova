from dataclasses import replace
from decimal import Decimal

import pytest

from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.smart_reports.pivot import pivot_result
from app.modules.smart_reports.schemas import Widget
from tests.unit.test_smart_reports import view


async def test_pivot_distinct_totals_are_engine_aggregates_not_sums():
    widget = Widget(
        id="pivot",
        title="Customers",
        kind="pivot",
        pivot_columns=["city"],
        query={"source": "sales", "metrics": ["customers"], "dimensions": ["region", "city"]},
    )
    evidence = {
        ("region", "city"): [["Java", "Jakarta", Decimal(37)], ["Java", "Bandung", Decimal(37)]],
        ("city",): [["Jakarta", Decimal(37)], ["Bandung", Decimal(37)]],
        ("region",): [["Java", Decimal(37)]],
        (): [[Decimal(37)]],
    }
    calls = []

    async def execute(source, plan):
        calls.append(plan)
        return {"columns": [*plan.dimensions, "customers"], "rows": evidence[plan.dimensions]}

    result = await pivot_result(
        widget,
        SemanticPlan(metrics=("customers",), dimensions=("region", "city"), limit=2),
        None,
        execute,
        view(),
    )
    assert result["rows"] == [["Java", Decimal(37), Decimal(37), Decimal(37)]]
    assert result["totals"]["total_customers"] == 37
    assert all(plan.limit == 1001 and plan.offset == 0 for plan in calls)
    assert result["row_kinds"] == ["detail"]

    async def truncated(source, plan):
        return {"columns": [], "rows": [], "truncated": True}

    with pytest.raises(ValueError, match="1,000"):
        await pivot_result(widget, replace(calls[0]), None, truncated, view())
