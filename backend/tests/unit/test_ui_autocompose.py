"""Deterministic data views for verified results no other visual shows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.assistant import ui_autocompose as ac
from app.modules.assistant.intelligent_ui import UiValidationError

SALES = {"columns": ["salesperson", "region", "revenue"], "rows": [
    ["Budi", "Jakarta", Decimal("1200.50")], ["Rizky", "Bandung", "3200.00"],
    ["Budi", "Bandung", 400]]}
OUTLETS = {"columns": ["outlet_name", "latitude", "longitude", "district", "depot_latitude"],
           "rows": [["Toko A", -6.24, 106.79, "Cilandak", -6.2]]}
ROUTES = {"columns": ["route", "geom"], "rows": [["R1", "LINESTRING(106.79 -6.24, 106.8 -6.25)"]]}
TOTAL = {"columns": ["total_revenue"], "rows": [[Decimal("356805054.00")]]}


def test_maps_prefer_exact_coordinates_then_geometry():
    program = ac.map_program("evidence_2", OUTLETS, "Outlets") or ""
    assert '"longitude", "latitude", null, "outlet_name", "Outlets"' in program
    assert 'popupFields: ["district", "depot_latitude"]' in program
    suffixed = {"columns": ["store", "store_lat", "store_lng"], "rows": [["A", "-6.2", "106.8"]]}
    assert '"store_lng", "store_lat"' in (ac.map_program("e", suffixed, "Stores") or "")
    assert 'null, null, "geom", "route"' in (ac.map_program("e", ROUTES, "Routes") or "")


@pytest.mark.parametrize("table", [
    SALES,
    {"columns": ["name", "latitude", "longitude"], "rows": [["Null island", 0, 0]]},
    {"columns": ["name", "latitude", "longitude"], "rows": [["Bad", 120, 400]]},
    {"columns": ["name", "latitude", "longitude"], "rows": []},
])
def test_results_without_valid_locations_get_no_map(table):
    assert ac.map_program("e", table, "Places") is None


def test_single_metric_is_a_kpi_and_several_share_a_grid():
    assert ac.kpi_column(TOTAL) == "total_revenue"
    assert ac.kpi_column(SALES) is None
    assert ac.kpi_column({"columns": ["name"], "rows": [["Budi"]]}) is None
    assert ac.kpi_program([("e1", "total_revenue", TOTAL)]) == (
        'root = Col([Kpi(@Evidence("e1"), "total_revenue", "Total revenue", 0)])'
    )
    assert "Grid([" in ac.kpi_program([("e1", "total_revenue", TOTAL),
                                       ("e2", "total_revenue", TOTAL)])


async def test_maps_come_before_metrics_and_only_one_is_drawn():
    composed, skipped = await ac.compose(
        {"evidence_1": TOTAL, "evidence_2": OUTLETS, "evidence_3": ROUTES},
        {"evidence_2": "Outlets"},
    )
    assert skipped == []
    assert [block["provenance"]["auto"] for block in composed] == ["map"]
    block = composed[0]
    assert block["fallback"] == "**Outlets**"
    assert block["provenance"]["composer"] == "deterministic"
    assert set(block["evidence"]) == {"evidence_2"}


async def test_metrics_come_first_and_other_results_get_their_own_view():
    composed, skipped = await ac.compose({"evidence_1": TOTAL, "evidence_2": SALES}, {})
    assert skipped == []
    assert [block["provenance"]["auto"] for block in composed] == ["kpi", "grid"]
    assert set(composed[0]["evidence"]) == {"evidence_1"}
    assert set(composed[1]["evidence"]) == {"evidence_2"}
    assert all(block["provenance"]["composer"] == "deterministic" for block in composed)


REGION = {"columns": ["region", "revenue_total"], "rows": [
    ["Surabaya", "803480000.00"], ["Makassar", "482675000.00"], ["Bandung", "603050000.00"],
    ["Jakarta", "979755000.00"], ["Medan", "507710000.00"]]}
WEEKLY = {"columns": ["week_start", "revenue"], "rows": [
    ["2026-04-13", "286753000.00"], ["2026-04-06", "353586500.00"], ["2026-04-20", "333024000.00"]]}
OUTLET_SALES = {"columns": ["outlet_name", "revenue"],
                "rows": [[f"Toko {index}", 1000 + index] for index in range(45)]}
VISITS = {"columns": ["visit_order", "outlet_name"],
          "rows": [["1", "Minimarket Citra 2"], ["2", "Apotek Waras"]]}
DAY = {"columns": ["visits", "km"], "rows": [["8", "25.17"]]}
CROSSTAB = {"columns": ["region", "channel", "revenue"],
            "rows": [["Jakarta", "Retail", 10], ["Jakarta", "Online", 20]]}
WIDE = {"columns": ["region", "a", "b", "c", "d", "e"], "rows": [["Jakarta", 1, 2, 3, 4, 5]] * 2}
YEARLY = {"columns": ["year", "orders_count"], "rows": [[2024, 10], [2025, 12], [2026, 15]]}


@pytest.mark.parametrize(("table", "auto", "fragment"), [
    (WEEKLY, "trend", 'Chart(@Sort(@Evidence("e"), "week_start", "asc"), "week_start", '
                      '"revenue", "line"'),
    (YEARLY, "trend", '"year", "orders_count", "line"'),
    (REGION, "ranking", 'Chart(@Sort(@Evidence("e"), "revenue_total", "desc"), "region", '
                        '"revenue_total", "bar"'),
    (OUTLET_SALES, "ranking", 'Chart(@Top(@Evidence("e"), "revenue", 10)'),
    (CROSSTAB, "grid", 'Table(@Evidence("e"), null'),
    (SALES, "grid", 'Table(@Evidence("e"), null'),
    (VISITS, "grid", 'Table(@Evidence("e"), null'),
])
def test_result_shape_chooses_the_data_view(table, auto, fragment):
    program, chosen = ac.data_program("e", table, None) or ("", "")
    assert chosen == auto
    assert fragment in program


def test_long_rankings_say_they_cover_only_these_rows():
    program, _ = ac.data_program("e", OUTLET_SALES, "Sales by outlet") or ("", "")
    assert 'Caption("Highest values within these result rows")' in program
    assert 'Table(@Evidence("e"), null, "Sales by outlet")' in program


@pytest.mark.parametrize("table", [
    WIDE,
    {"columns": ["region", "revenue"], "rows": []},
    {"columns": ["region", "revenue"], "rows": [["Jakarta", 10]]},
    {"columns": [f"c{index}" for index in range(51)], "rows": [list(range(51))]},
    {"columns": ["note"], "rows": [[None], [""]]},
])
def test_unrecognised_or_empty_shapes_get_no_view(table):
    assert ac.data_program("e", table, None) is None


def test_columns_are_classified_by_values_and_names():
    assert ac.shape(VISITS)["key"] == ["visit_order"]
    assert ac.shape({"columns": ["order_count"], "rows": [[3]]})["measure"] == ["order_count"]
    assert ac.shape({"columns": ["revenue_month_total"], "rows": [[3]]})["measure"] == [
        "revenue_month_total"]
    assert ac.shape({"columns": ["order_date"], "rows": [[date(2026, 4, 6)]]})["temporal"] == [
        "order_date"]
    assert ac.shape(DAY)["measure"] == ["visits", "km"]


async def test_one_row_of_measures_becomes_kpi_tiles():
    assert ac.kpi_columns(DAY) == ["visits", "km"]
    assert ac.kpi_columns(REGION) == []
    composed, _ = await ac.compose({"e1": DAY}, {})
    assert [block["provenance"]["auto"] for block in composed] == ["kpi"]
    assert composed[0]["program"].count("Kpi(") == 2


async def test_the_field_sales_shapes_all_get_a_validated_view():
    composed, skipped = await ac.compose({"e1": REGION}, {"e1": "Revenue by region"})
    assert skipped == []
    assert [block["provenance"]["auto"] for block in composed] == ["ranking"]
    assert composed[0]["fallback"] == "**Revenue by region**"
    composed, skipped = await ac.compose({"e1": WEEKLY}, {})
    assert skipped == [] and composed[0]["provenance"]["auto"] == "trend"
    assert composed[0]["fallback"] == "**Revenue**"


async def test_at_most_two_results_get_a_view_and_charts_leave_only_maps():
    tables = {"e1": REGION, "e2": WEEKLY, "e3": CROSSTAB}
    composed, _ = await ac.compose(tables, {})
    assert [block["provenance"]["auto"] for block in composed] == ["ranking", "trend"]
    assert await ac.compose(tables, {}, maps_only=True) == ([], [])


async def test_validation_failure_is_reported_not_raised(monkeypatch):
    async def reject(*_args, **_kwargs):
        raise UiValidationError("rejected")

    monkeypatch.setattr(ac, "parse_program", reject)
    assert await ac.compose({"evidence_2": OUTLETS}, {}) == ([], ["map_validation"])
    assert await ac.compose({"evidence_1": TOTAL}, {}) == ([], ["kpi_validation"])
    assert await ac.compose({"evidence_1": REGION}, {}) == ([], ["ranking_validation"])
