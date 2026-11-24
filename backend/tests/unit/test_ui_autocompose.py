"""Deterministic data views for verified results no other visual shows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.assistant import ui_autocompose as ac
from app.modules.assistant import ui_layout

SALES = {"columns": ["salesperson", "region", "revenue"], "rows": [
    ["Budi", "Jakarta", Decimal("1200.50")], ["Rizky", "Bandung", "3200.00"],
    ["Budi", "Bandung", 400]]}
OUTLETS = {"columns": ["outlet_name", "latitude", "longitude", "district", "depot_latitude"],
           "rows": [["Toko A", -6.24, 106.79, "Cilandak", -6.2]]}
ROUTES = {"columns": ["route", "geom"], "rows": [["R1", "LINESTRING(106.79 -6.24, 106.8 -6.25)"]]}
TOTAL = {"columns": ["total_revenue"], "rows": [[Decimal("356805054.00")]]}


def _props(component):
    assert component is not None and component["name"] == "GeoMap"
    return component["props"]


def test_maps_prefer_exact_coordinates_then_geometry():
    outlets = _props(ac.map_component("evidence_2", OUTLETS, "Outlets"))
    assert (outlets["longitude"], outlets["latitude"], outlets["label"], outlets["title"]) == (
        "longitude", "latitude", "outlet_name", "Outlets")
    assert outlets["options"] == {"popupFields": ["district", "depot_latitude"]}
    suffixed = {"columns": ["store", "store_lat", "store_lng"], "rows": [["A", "-6.2", "106.8"]]}
    stores = _props(ac.map_component("e", suffixed, "Stores"))
    assert (stores["longitude"], stores["latitude"]) == ("store_lng", "store_lat")
    routes = _props(ac.map_component("e", ROUTES, "Routes"))
    assert (routes["geometry"], routes["label"]) == ("geom", "route")
    assert "longitude" not in routes
    # Lines are an ordered route: drawn as one, not as shapes.
    assert routes["options"]["layer"] == "route"


@pytest.mark.parametrize("table", [
    SALES,
    {"columns": ["name", "latitude", "longitude"], "rows": [["Null island", 0, 0]]},
    {"columns": ["name", "latitude", "longitude"], "rows": [["Bad", 120, 400]]},
    {"columns": ["name", "latitude", "longitude"], "rows": []},
])
def test_results_without_valid_locations_get_no_map(table):
    assert ac.map_component("e", table, "Places") is None


def test_single_metric_is_a_kpi_tile_read_from_its_row():
    assert ac.kpi_column(TOTAL) == "total_revenue"
    assert ac.kpi_column(SALES) is None
    assert ac.kpi_column({"columns": ["name"], "rows": [["Budi"]]}) is None
    tiles = ac.kpi_component([("e1", "total_revenue", TOTAL), ("e2", "total_revenue", TOTAL)])
    assert tiles is not None and tiles["name"] == "KpiTiles"
    first = tiles["props"]["items"][0]
    assert first == {"label": "Total revenue", "value": 356805054.0, "format": "number",
                     "decimals": 0, "aggregation": "value", "evidence_id": "e1",
                     "column": "total_revenue"}


def _kinds(composed):
    return [payload["name"] if kind == "component" else payload["provenance"]["auto"]
            for kind, payload in composed]


async def _fake_chart(evidence_id, table, plan, title):
    return {"name": "DatavizChart", "props": {"title": title, "chart": {"visual": plan["visual"]}},
            "source": {"evidence_id": evidence_id}, "plan": plan}


async def test_maps_come_before_metrics_and_only_one_is_drawn():
    composed, skipped = await ac.compose(
        {"evidence_1": TOTAL, "evidence_2": OUTLETS}, {"evidence_2": "Outlets"},
    )
    assert skipped == []
    assert _kinds(composed) == ["GeoMap"]
    props = composed[0][1]["props"]
    assert props["title"] == "Outlets" and props["evidence_id"] == "evidence_2"
    assert (props["longitude"], props["latitude"], props["label"]) == (
        "longitude", "latitude", "outlet_name")
    assert props["rows"][0]["outlet_name"] == "Toko A"


async def test_planned_routes_are_the_map_before_their_stops():
    composed, _ = await ac.compose(
        {"evidence_2": OUTLETS, "evidence_3": ROUTES},
        {"evidence_2": "Outlets", "evidence_3": "Optimized routes"},
    )
    assert _kinds(composed) == ["GeoMap"]
    assert composed[0][1]["props"]["title"] == "Optimized routes"


async def test_metrics_come_first_and_other_results_get_their_own_view():
    composed, skipped = await ac.compose({"evidence_1": TOTAL, "evidence_2": SALES}, {},
                                         chart=_fake_chart)
    assert skipped == []
    assert _kinds(composed) == ["KpiTiles", "grid"]
    assert composed[0][1]["props"]["items"][0]["evidence_id"] == "evidence_1"
    grid = composed[1][1]
    # A grid is a deterministic page holding the verified rows.
    assert grid["catalog_version"] == ui_layout.CATALOG
    assert grid["provenance"]["evidence_ids"] == ["evidence_2"]
    assert grid["program"].startswith("root = Page([grid])\ngrid = ResultTable(")
    assert '["salesperson", "region", "revenue"]' in grid["program"]
    assert '["Budi", "Jakarta", 1200.5]' in grid["program"]


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


@pytest.mark.parametrize(("table", "visual"), [
    (WEEKLY, "line"), (YEARLY, "line"), (REGION, "bar"), (OUTLET_SALES, "bar"),
])
def test_trends_and_rankings_get_a_native_chart_plan(table, visual):
    chosen = ac.chart_plan(table)
    assert chosen is not None and chosen[0]["visual"] == visual
    assert not ac.is_grid(table)


@pytest.mark.parametrize("table", [CROSSTAB, SALES, VISITS])
def test_detail_results_are_grids(table):
    assert ac.chart_plan(table) is None
    assert ac.is_grid(table)


@pytest.mark.parametrize("table", [
    WIDE,
    {"columns": ["region", "revenue"], "rows": []},
    {"columns": ["region", "revenue"], "rows": [["Jakarta", 10]]},
    {"columns": [f"c{index}" for index in range(51)], "rows": [list(range(51))]},
    {"columns": ["note"], "rows": [[None], [""]]},
])
def test_unrecognised_or_empty_shapes_get_no_grid(table):
    assert not ac.is_grid(table)


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
    assert _kinds(composed) == ["KpiTiles"]
    items = composed[0][1]["props"]["items"]
    assert [(item["column"], item["value"], item["decimals"]) for item in items] == [
        ("visits", 8.0, 0), ("km", 25.17, 2)]


async def test_trends_and_rankings_are_native_dataviz_charts():
    composed, skipped = await ac.compose({"e1": REGION}, {"e1": "Revenue by region"},
                                         chart=_fake_chart)
    assert skipped == [] and _kinds(composed) == ["DatavizChart"]
    payload = composed[0][1]
    assert payload["props"]["title"] == "Revenue by region"
    assert payload["plan"] == {"visual": "bar", "dimensions": [{"field": "region"}],
                               "measures": [{"field": "revenue_total", "aggregation": "sum"}],
                               "sort": {"field": "revenue_total", "direction": "desc"}}
    composed, _ = await ac.compose({"e1": WEEKLY}, {}, chart=_fake_chart)
    assert composed[0][1]["plan"]["visual"] == "line"
    assert composed[0][1]["props"]["title"] == "Revenue"
    composed, _ = await ac.compose({"e1": OUTLET_SALES}, {}, chart=_fake_chart)
    assert composed[0][1]["plan"]["top_n"] == 10


async def test_a_chart_without_a_governed_query_is_not_drawn_by_another_engine():
    async def no_chart(*_args):
        return None

    composed, skipped = await ac.compose({"e1": REGION}, {}, chart=no_chart)
    assert composed == [] and skipped == ["ranking_native"]


async def test_at_most_two_results_get_a_view_and_charts_leave_only_maps():
    tables = {"e1": REGION, "e2": WEEKLY, "e3": CROSSTAB}
    composed, _ = await ac.compose(tables, {}, chart=_fake_chart)
    assert [payload["plan"]["visual"] for _, payload in composed] == ["bar", "line"]
    assert await ac.compose(tables, {}, maps_only=True) == ([], [])


async def test_validation_failure_is_reported_not_raised(monkeypatch):
    async def reject(*_args, **_kwargs):
        raise ui_layout.LayoutError("rejected")

    monkeypatch.setattr(ui_layout, "check_statement", reject)
    assert await ac.compose({"evidence_1": SALES}, {}) == ([], ["grid_validation"])


STOPS = {"columns": ["route", "stop_seq", "stop", "latitude", "longitude", "demand",
                     "leg_km", "route_km", "saving_pct"],
         "rows": [["Area 1", 1, "Toko A", 3.58, 98.66, 120, 0, 4.2, 18.5],
                  ["Area 1", 2, "Toko B", 3.59, 98.67, 80, 1.5, 4.2, 18.5],
                  ["Area 2", 1, "Toko C", 3.52, 98.70, 60, 0, 2.1, 9.0]]}


async def test_ordered_stops_are_drawn_as_numbered_routes():
    stops = _props(ac.map_component("e", STOPS, "Delivery routes"))
    assert (stops["longitude"], stops["latitude"], stops["label"]) == (
        "longitude", "latitude", "stop")
    assert stops["options"] == {
        "layer": "route", "sequence": "stop_seq", "group": "route",
        "routeFields": ["route_km", "saving_pct"], "popupFields": ["demand", "leg_km"],
    }
    composed, skipped = await ac.compose(
        {"evidence_2": OUTLETS, "evidence_4": STOPS},
        {"evidence_2": "Outlets", "evidence_4": "Delivery routes"},
    )
    assert skipped == []
    assert _kinds(composed) == ["GeoMap"]
    assert composed[0][1]["props"]["title"] == "Delivery routes"
