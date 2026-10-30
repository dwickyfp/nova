"""A simple result's shape picks its chart without a model call."""

from __future__ import annotations

import pytest

from app.modules.agents.tools.data_to_chart import _shape_decides
from app.modules.viz.showme import classify_fields

BY_CITY = classify_fields(["city", "outlets"], [["Jakarta Barat", 4], ["Jakarta Timur", 5]])
BY_DAY = classify_fields(["day", "visits"], [["2026-10-01", 4], ["2026-10-02", 5]])


@pytest.mark.parametrize("intent, pills, decided", [
    ("chart jumlah outlet per kota", BY_CITY, True),
    ("bar chart of outlets per city", BY_CITY, True),
    ("pie chart outlet per kota", BY_CITY, False),
    ("tren kunjungan harian", BY_DAY, True),
    ("bar chart kunjungan per hari", BY_DAY, False),
])
def test_one_dimension_and_one_measure_decide_unless_another_type_is_named(
    intent, pills, decided,
) -> None:
    assert _shape_decides(intent, pills) is decided


def test_wider_results_still_ask_the_model() -> None:
    pills = classify_fields(
        ["city", "outlets", "visits"], [["Jakarta Barat", 4, 9], ["Jakarta Timur", 5, 7]]
    )
    assert _shape_decides("chart outlet dan kunjungan per kota", pills) is False
