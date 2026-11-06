"""Route optimization orders a driver's stops into a short visiting sequence."""

import httpx
import pyarrow as pa
import pytest

from app.modules.ml_engine.engines.routing import optimize_routes
from app.modules.ml_engine.spec import (
    InvalidMLSpec,
    MLExecutionSpec,
    MLSecurityContext,
    MLTask,
)


@pytest.fixture(autouse=True)
def _no_road_service(monkeypatch):
    """Unit tests never call the public routing server; road tests mock it."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "ROUTING_OSRM_URL", "")


def _spec(**overrides):
    values = {
        "task": MLTask.ROUTE_OPTIMIZATION,
        "input_sql": "SELECT outlet_name, latitude, longitude, driver FROM stops",
        "security": MLSecurityContext("analyst", "secret", role="sales_analyst"),
        "row_identifier": "outlet_name",
        "series_column": "driver",
        "parameters": {"latitude_column": "latitude", "longitude_column": "longitude"},
    }
    values.update(overrides)
    return MLExecutionSpec(**values)


def _stops():
    # Five outlets on one street, listed out of order, plus a repeat from a daily result.
    line = [("A", 0.00), ("D", 0.03), ("B", 0.01), ("E", 0.04), ("C", 0.02), ("D", 0.03)]
    rows = [{"outlet_name": name, "latitude": 3.58, "longitude": 98.66 + offset,
             "driver": "EMP-19"} for name, offset in line]
    rows += [{"outlet_name": "X", "latitude": 3.50, "longitude": 98.60, "driver": "EMP-20"},
             {"outlet_name": "Y", "latitude": 3.51, "longitude": 98.61, "driver": "EMP-20"}]
    return pa.Table.from_pylist(rows)


def _routes(output):
    routes = {}
    for row in output.results:
        routes.setdefault(row["route"], []).append(row)
    return {name: sorted(rows, key=lambda row: row["stop_seq"]) for name, rows in routes.items()}


def test_stops_are_visited_in_the_shortest_order_per_driver():
    output = optimize_routes(_stops(), _spec())
    routes = _routes(output)
    route = routes["EMP-19"]
    assert [row["stop"] for row in route] == ["A", "B", "C", "D", "E"]
    assert [row["stop_seq"] for row in route] == [1, 2, 3, 4, 5]
    assert route[0]["leg_km"] == 0 and route[-1]["cumulative_km"] == route[0]["route_km"]
    assert route[0]["route_km"] < route[0]["original_order_km"] and route[0]["saving_pct"] > 0
    assert route[0]["longitude"] == 98.66 and route[0]["latitude"] == 3.58
    assert len(routes["EMP-20"]) == 2
    assert "Counted 1 repeated stop row(s) once" in output.metrics["warnings"]


def test_a_depot_starts_every_route():
    spec = _spec(parameters={
        "latitude_column": "latitude", "longitude_column": "longitude",
        "start_latitude": 3.58, "start_longitude": 98.71,
    })
    route = _routes(optimize_routes(_stops(), spec))["EMP-19"]
    assert [row["stop"] for row in route] == ["Depot", "E", "D", "C", "B", "A"]


def test_route_optimization_needs_coordinates_and_is_never_saved():
    with pytest.raises(InvalidMLSpec, match="latitude and longitude"):
        _spec(parameters={}).validate()
    with pytest.raises(InvalidMLSpec, match="not a model"):
        _spec(persist=True, model_name="routes").validate()


def test_clusters_form_delivery_areas_from_locations_and_total_their_demand():
    west = [(f"W{i}", 3.58 + i * 0.001, 98.60 + i * 0.002, 10) for i in range(4)]
    east = [(f"E{i}", 3.58 + i * 0.001, 98.80 + i * 0.002, 20) for i in range(4)]
    table = pa.Table.from_pylist([
        {"outlet_name": name, "latitude": lat, "longitude": lon, "units": units}
        for name, lat, lon, units in west + east
    ])
    spec = _spec(series_column=None, target_column="units", parameters={
        "latitude_column": "latitude", "longitude_column": "longitude", "clusters": 2,
    })
    output = optimize_routes(table, spec)
    routes = _routes(output)
    assert set(routes) == {"Area 1", "Area 2"}
    assert all(row["stop"].startswith("W") for row in routes["Area 1"])
    assert sum(row["demand"] for row in routes["Area 1"]) == 40
    assert sum(row["demand"] for row in routes["Area 2"]) == 80
    assert output.algorithm.startswith("kmeans(k=2)")


def test_grouping_by_the_stop_itself_forms_delivery_areas_instead():
    rows = [{"outlet_name": f"T{i}", "latitude": 3.5 + (i % 2) * 0.1,
             "longitude": 98.6 + (i // 2) * 0.01} for i in range(12)]
    output = optimize_routes(pa.Table.from_pylist(rows), _spec(series_column="outlet_name"))
    routes = _routes(output)
    # Twelve "routes" of one stop each order nothing; areas by location do.
    assert len(routes) >= 2 and all(name.startswith("Area ") for name in routes)
    assert sum(len(stops) for stops in routes.values()) == 12
    assert any("names each stop" in warning for warning in output.metrics["warnings"])


def test_grouping_by_outlet_name_still_forms_areas_when_two_outlets_share_a_name():
    rows = [{"outlet_name": f"T{i}", "latitude": 3.5 + (i % 2) * 0.1,
             "longitude": 98.6 + (i // 2) * 0.01} for i in range(14)]
    rows += [{"outlet_name": "Warung Fajar", "latitude": 3.52, "longitude": 98.68},
             {"outlet_name": "Warung Fajar", "latitude": 3.55, "longitude": 98.69}]
    table = pa.Table.from_pylist(rows)
    for spec in (_spec(series_column="outlet_name"),
                 _spec(series_column="outlet_name", row_identifier=None)):
        routes = _routes(optimize_routes(table, spec))
        # One shared name made one two-stop group; the rest still ordered nothing.
        assert all(name.startswith("Area ") for name in routes)
        assert len(routes) < 8 and sum(len(stops) for stops in routes.values()) == 16


def test_road_distances_from_osrm_decide_the_order_and_the_reported_length(monkeypatch):
    from app.core.config import settings
    from app.modules.ml_engine.engines import routing

    monkeypatch.setattr(settings, "ROUTING_OSRM_URL", "https://osrm.example")
    monkeypatch.setattr("app.common.ssrf_guard.resolve_and_validate_url", lambda *a, **k: None)
    # Three stops in a row; by road, B is far from both (a river with no bridge).
    road = [[0, 9, 2], [9, 0, 9], [2, 9, 0]]
    calls = []

    def fake_get(url, params, timeout):
        calls.append(url)
        return httpx.Response(200, json={"code": "Ok", "distances": [
            [value * 1000 for value in row] for row in road
        ]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(routing.httpx, "get", fake_get)
    rows = [{"outlet_name": name, "latitude": 3.58, "longitude": 98.66 + i * 0.01,
             "driver": "EMP-19"} for i, name in enumerate("ABC")]
    output = optimize_routes(pa.Table.from_pylist(rows), _spec())
    route = sorted(output.results, key=lambda row: row["stop_seq"])
    # Straight lines would visit A, B, C; by road, A then C then B.
    assert [row["stop"] for row in route] == ["A", "C", "B"]
    assert route[0]["route_km"] == 11.0
    assert output.metrics["distance"] == "road_km_osrm"
    assert calls[0].startswith("https://osrm.example/table/v1/driving/98.660000,3.580000;")


def test_an_unanswering_road_service_falls_back_to_straight_lines(monkeypatch):
    from app.core.config import settings
    from app.modules.ml_engine.engines import routing

    monkeypatch.setattr(settings, "ROUTING_OSRM_URL", "https://osrm.example")
    monkeypatch.setattr("app.common.ssrf_guard.resolve_and_validate_url", lambda *a, **k: None)

    def down(url, params, timeout):
        raise httpx.ConnectTimeout("down")

    monkeypatch.setattr(routing.httpx, "get", down)
    output = optimize_routes(_stops(), _spec())
    assert output.metrics["distance"] == "great_circle_km"
    assert any("straight-line" in warning for warning in output.metrics["warnings"])


def test_two_outlets_that_share_a_name_are_two_stops():
    rows = [
        {"outlet_name": "Warung Fajar", "latitude": 3.55, "longitude": 98.66, "driver": "D"},
        {"outlet_name": "Warung Fajar", "latitude": 3.60, "longitude": 98.70, "driver": "D"},
        {"outlet_name": "Kios Siti", "latitude": 3.57, "longitude": 98.68, "driver": "D"},
    ]
    output = optimize_routes(pa.Table.from_pylist(rows), _spec())
    stops = {row["stop"] for row in output.results}
    assert stops == {"Warung Fajar (1)", "Warung Fajar (2)", "Kios Siti"}
