"""A comparison with a period that has no data is settled, not failed.

"This year vs last year" when the data holds no rows for last year: the
computation cannot exist, and the answer says so. Only a dated, complete
result whose range spans the period decides; anything else is still an error.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agents.tools.compute_metrics import ComputeMetricsTool, PeriodWithoutData, compute
from app.modules.assistant.tools import ToolInvocation


def yearly(period, rows=None, **extra):
    return {"columns": ["order_date", "net_revenue"],
            "rows": rows or [["2026-01-01", "9011367749.03"]], "period": period, **extra}


GROWTH = {"operation": "pct_change", "value_column": "net_revenue",
          "label_column": "order_date", "from_label": "2025", "to_label": "2026"}


@pytest.mark.parametrize("period", [
    # Asked for 2025 and 2026; 2025 came back empty.
    {"first": "2025-01-01", "last": "2026-11-10"},
    {"first": "2026-01-01", "last": "2026-11-10",
     "compared_first": "2025-01-01", "compared_last": "2025-11-10"},
    # The whole data lies in 2026, so 2025 has none.
    {"first": "2026-05-01", "last": "2026-11-04", "scope": "data"},
])
@pytest.mark.parametrize("label", ["2025", "2025-01-01", "2025-01-01 00:00:00"])
def test_a_spanned_period_without_rows_has_no_data(period, label):
    with pytest.raises(PeriodWithoutData) as raised:
        compute(yearly(period), {**GROWTH, "from_label": label})
    assert raised.value.period == {"first": "2025-01-01", "last": "2025-12-31"}


@pytest.mark.parametrize(("table", "label"), [
    # Not asked for: the query covered 2026 only.
    (yearly({"first": "2026-01-01", "last": "2026-11-10"}), "2025"),
    # Inside the data's own range: the data may hold it, the result does not say.
    (yearly({"first": "2024-01-01", "last": "2026-11-04", "scope": "data"}), "2025"),
    # No dates at all, or a cut-off result.
    (yearly(None), "2025"),
    (yearly({"first": "2025-01-01", "last": "2026-11-10"}, truncated=True), "2025"),
    ({"columns": ["city", "net_revenue"], "rows": [["Jakarta", "1"]],
      "period": {"first": "2025-01-01", "last": "2026-11-10"}}, "2025"),
    # Not a period, and a month inside a yearly row is that row's data.
    (yearly({"first": "2025-01-01", "last": "2026-11-10"}), "Jakarta"),
])
def test_anything_else_is_still_an_error(table, label):
    with pytest.raises(Exception) as raised:
        compute(table, {**GROWTH, "from_label": label})
    assert not isinstance(raised.value, PeriodWithoutData)


def test_a_month_inside_a_yearly_row_is_not_missing():
    table = yearly({"first": "2025-01-01", "last": "2026-11-10"})
    with pytest.raises(Exception) as raised:
        compute(table, {**GROWTH, "from_label": "2026-03-01"})
    assert not isinstance(raised.value, PeriodWithoutData)


def test_monthly_rows_find_the_missing_month():
    table = {"columns": ["month", "net_revenue"],
             "rows": [["2026-05-01", "1"], ["2026-06-01", "2"]],
             "period": {"first": "2026-01-01", "last": "2026-06-30"}}
    with pytest.raises(PeriodWithoutData) as raised:
        compute(table, {"operation": "difference", "value_column": "net_revenue",
                        "label_column": "month", "from_label": "2026-04-01",
                        "to_label": "2026-05-01"})
    assert raised.value.period == {"first": "2026-04-01", "last": "2026-04-30"}


async def test_the_tool_reports_the_missing_period_and_settles():
    context = SimpleNamespace(evidence_tables={"evidence_2": yearly(
        {"first": "2025-01-01", "last": "2026-11-10"})}, last_result=None)
    outcome = await ComputeMetricsTool().run(
        ToolInvocation(tool_call_id="c", tool_name="compute_metrics",
                       arguments={**GROWTH, "evidence_id": "evidence_2"}),
        context,
    )
    assert outcome.ok and outcome.table is None
    assert "No data for 2025" in outcome.summary
    assert "comparison is not possible" in outcome.summary
    assert outcome.metadata["period_without_data"] == {"first": "2025-01-01",
                                                       "last": "2025-12-31"}


@pytest.mark.parametrize("given", [
    "order_date 2026 sales; prior-year comparison unavailable when 2025 absent",
    "order_date...",
])
def test_a_note_after_the_label_column_name_still_names_it(given):
    table = yearly({"first": "2025-01-01", "last": "2026-11-10"})
    with pytest.raises(PeriodWithoutData):
        compute(table, {**GROWTH, "label_column": given})


@pytest.mark.parametrize("given", ["order", "order_dates", "the order_date"])
def test_a_label_column_that_is_not_a_column_is_still_refused(given):
    table = yearly({"first": "2025-01-01", "last": "2026-11-10"})
    with pytest.raises(Exception, match="is not in the result"):
        compute(table, {**GROWTH, "label_column": given})
