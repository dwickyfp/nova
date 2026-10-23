"""Deterministic maps and KPIs for verified results no other visual shows."""

from __future__ import annotations

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


async def test_metrics_fill_an_answer_without_locations():
    composed, _ = await ac.compose({"evidence_1": TOTAL, "evidence_2": SALES}, {})
    assert [block["provenance"]["auto"] for block in composed] == ["kpi"]
    assert set(composed[0]["evidence"]) == {"evidence_1"}
    assert await ac.compose({"evidence_2": SALES}, {}) == ([], [])


async def test_validation_failure_is_reported_not_raised(monkeypatch):
    async def reject(*_args, **_kwargs):
        raise UiValidationError("rejected")

    monkeypatch.setattr(ac, "parse_program", reject)
    assert await ac.compose({"evidence_2": OUTLETS}, {}) == ([], ["map_validation"])
    assert await ac.compose({"evidence_1": TOTAL}, {}) == ([], ["kpi_validation"])
