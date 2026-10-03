"""Dimensional contributions retain exact reconciliation and bounded inputs."""

from decimal import Decimal

import pytest

from app.modules.agents.tools.diagnose_change import decompose_change


def test_ranked_segments_include_unreported_residual_and_new_segments():
    rows = [
        [period, f"city-{i}", str(Decimal(i) / 100)]
        for period in ("before", "after")
        for i in range(15)
    ]
    rows.append(["after", "Jakarta", "-23.451"])
    result = decompose_change(
        {"columns": ["period", "city", "revenue"], "rows": rows},
        prior_period="before",
        current_period="after",
        revenue_column="revenue",
        dimension_columns=["city"],
    )
    assert result["components"][0] == {"name": '["Jakarta"]', "change": "-23.451"}
    assert result["causal_status"] == "arithmetic"
    assert result["reconciled"]
    assert sum(Decimal(row["change"]) for row in result["components"]) == Decimal("-23.451")


@pytest.mark.parametrize(
    "rows,message",
    [
        ([["before", "Jakarta", 1], ["before", "Jakarta", 2], ["after", "Jakarta", 2]], "once"),
        ([["before", "Jakarta", 1]] * 201, "limit"),
        ([["before", "Jakarta", 1], ["after", "Jakarta", None]], "missing"),
    ],
)
def test_ambiguous_truncated_and_missing_comparisons_are_rejected(rows, message):
    with pytest.raises(ValueError, match=message):
        decompose_change(
            {"columns": ["period", "city", "revenue"], "rows": rows},
            prior_period="before",
            current_period="after",
            revenue_column="revenue",
            dimension_columns=["city"],
        )
