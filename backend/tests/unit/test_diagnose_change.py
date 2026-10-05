from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.modules.agents.tools.diagnose_change import (
    decompose_change,
    diagnose_change_tool,
)
from app.modules.assistant.planning import validate_turn_plan
from app.modules.assistant.tools import ToolInvocation


def _comparison() -> dict:
    return {
        "columns": ["period", "gross_revenue", "units", "returns"],
        "rows": [["August", 100, 10, 5], ["September", 96, 12, 8]],
    }


def test_decomposition_reconciles_without_claiming_a_cause() -> None:
    result = decompose_change(
        _comparison(),
        prior_period="August",
        current_period="September",
        revenue_column="gross_revenue",
        units_column="units",
        returns_column="returns",
    )
    effects = {item["name"]: Decimal(item["change"]) for item in result["components"]}
    assert result["net_change"] == "-7"
    assert effects == {
        "volume": Decimal(20),
        "unit_value": Decimal(-20),
        "interaction": Decimal(-4),
        "returns": Decimal(-3),
    }
    assert sum(effects.values()) == Decimal(result["net_change"])
    assert result["reconciled"]


def test_missing_drivers_are_unassigned() -> None:
    result = decompose_change(
        {"columns": ["period", "revenue"], "rows": [["before", 100], ["after", 80]]},
        prior_period="before",
        current_period="after",
        revenue_column="revenue",
    )
    assert result["components"] == [{"name": "unassigned", "change": "-20"}]


@pytest.mark.asyncio
async def test_diagnostic_tool_uses_only_the_authorized_last_result() -> None:
    invocation = ToolInvocation(
        tool_call_id="call-1",
        tool_name="diagnose_change",
        arguments={
            "prior_period": "August",
            "current_period": "September",
            "revenue_column": "gross_revenue",
            "units_column": "units",
            "returns_column": "returns",
        },
    )
    missing = await diagnose_change_tool.run(invocation, SimpleNamespace(last_result=None))
    assert not missing.ok
    result = await diagnose_change_tool.run(invocation, SimpleNamespace(last_result=_comparison()))
    assert result.ok
    assert "not proven root causes" in result.summary
    assert result.table["rows"][-1] == ["net_change", "-7"]


def test_diagnostic_plan_keeps_data_evidence_requirement() -> None:
    plan = validate_turn_plan(
        {"intent": "compound_analytics", "tools": ["semantic_query", "diagnose_change"],
         "required_tools": ["semantic_query", "diagnose_change"], "ml_task": None},
        {"semantic_query", "diagnose_change", "query_execute"},
    )
    assert plan.route.needs_data and plan.route.needs_diagnosis
    assert plan.route.required_capabilities == ("semantic_query", "diagnose_change")


MONTHLY = {
    "columns": ["entry_date", "department", "total_expense"],
    "rows": [
        ["2026-04-01 00:00:00", "Sales", "100"], ["2026-04-01 00:00:00", "Finance", "50"],
        ["2026-05-01 00:00:00", "Sales", "180"], ["2026-05-01 00:00:00", "Finance", "40"],
    ],
}


def test_any_measure_is_explained_by_its_own_period_column_and_month_prefix():
    result = decompose_change(
        MONTHLY, prior_period="2026-04", current_period="2026-05",
        period_column="entry_date", revenue_column="total_expense",
        dimension_columns=["department"],
    )
    assert result["net_change"] == "70"
    assert result["components"][0] == {"name": '["Sales"]', "change": "80"}
    assert result["reconciled"]


@pytest.mark.asyncio
async def test_a_call_that_names_no_usable_column_can_be_repaired():
    outcome = await diagnose_change_tool.run(
        ToolInvocation(tool_call_id="d", tool_name="diagnose_change", arguments={
            "prior_period": "2026-04", "current_period": "2026-05", "revenue_column": "revenue",
        }),
        SimpleNamespace(last_result=MONTHLY),
    )
    assert not outcome.ok and outcome.recoverable
    assert "entry_date, department, total_expense" in outcome.safe_detail
