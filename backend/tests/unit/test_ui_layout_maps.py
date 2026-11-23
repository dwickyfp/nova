"""Maps and public images on pages: checked on the server, stored in the program."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.modules.assistant import ui_layout as ul
from app.modules.assistant import ui_public

CITIES = {"evidence_c": {
    "columns": ["city", "lat", "lon", "revenue", "api_token"],
    "rows": [["Jakarta", -6.2, 106.85, 900, "sk-live-abcdefghijklmnopqrstuvwxyz0123456789"],
             ["Bandung", -6.91, 107.61, 400, None],
             ["Nowhere", 120.0, 500.0, 1, None]],
    "sql": "SELECT city, lat, lon, revenue, api_token FROM cities",
}}


def _ctx() -> ul.LayoutContext:
    return ul.LayoutContext(tables=CITIES, question="Show our cities on a map")


async def _check(line: str) -> tuple[str, Any]:
    statement = await ul.check_statement(line, _ctx(), state_names=set(), defined=set())
    return ul.parse_statement(statement.text)


@pytest.fixture(autouse=True)
def _no_cache():
    ui_public._cache.clear()


def _route(lon_lat: list[list[float]], distance: float, duration: float) -> dict[str, Any]:
    return {"geometry": {"type": "LineString", "coordinates": lon_lat},
            "distance": distance, "duration": duration}


async def test_a_data_map_shows_verified_rows_without_secrets_or_bad_coordinates():
    _name, node = await _check(
        'm = DataMap("evidence_c", "lat", "lon", "city", "revenue", "Revenue by city")')
    assert node.name == "GeoMap"
    props = ul._plain(node.args[0])
    assert props["rows"] == [
        {"lat": -6.2, "lon": 106.85, "city": "Jakarta", "revenue": 900},
        {"lat": -6.91, "lon": 107.61, "city": "Bandung", "revenue": 400},
    ]
    assert props["options"] == {"popupFields": ["revenue"]}
    assert "api_token" not in str(props)


async def test_a_data_map_finds_coordinates_itself_and_refuses_unknown_columns():
    _name, node = await _check('m = DataMap("evidence_c")')
    assert node.name == "GeoMap"
    assert "api_token" not in ul._plain(node.args[0])["rows"][0]
    with pytest.raises(ul.LayoutError, match="no column"):
        await _check('m = DataMap("evidence_c", "latitude", "lon")')


def test_places_are_checked_against_the_city_they_name():
    assert ui_public.check_place("Jakarta", -6.21, 106.85)["check"] == "verified"
    moved = ui_public.check_place("Kota Bandung", -7.9, 112.6)
    assert (moved["check"], moved["lat"], moved["lon"]) == ("corrected", -6.9175, 107.6191)
    assert ui_public.check_place("Solo", 0, 0)["check"] == "corrected"
    assert ui_public.check_place("Borobudur", -7.6079, 110.2038)["check"] == "approximate"
    with pytest.raises(ui_public.PublicContentError):
        ui_public.check_place("Somewhere", 91, 10)


async def test_a_route_is_fetched_at_city_level_and_stored_with_its_alternatives(monkeypatch):
    calls: list[tuple[str, dict[str, Any] | None]] = []

    async def answer(url: str, params: dict[str, Any] | None = None) -> Any:
        calls.append((url, params))
        return {"code": "Ok", "routes": [
            _route([[106.85, -6.21], [107.0, -6.5], [107.62, -6.92]], 151_200.4, 10_800.6),
            _route([[106.85, -6.21], [107.62, -6.92]], 160_000, 12_000),
        ]}

    monkeypatch.setattr(ui_public, "_public_json", answer)
    _name, node = await _check(
        'r = RouteMap("Jakarta to Bandung", [{label: "Jakarta", lat: -6.208812, lon: 106.845623},'
        ' {label: "Bandung", lat: -6.917464, lon: 107.619123}], true)')
    assert node.name == "PlaceMap"
    title, places, routes, unavailable, optimized = (ul._plain(arg) for arg in node.args)
    assert title == "Jakarta to Bandung"
    assert [place["check"] for place in places] == ["verified", "verified"]
    assert [(r["distance_m"], r["duration_s"]) for r in routes] == [(151_200, 10_801),
                                                                       (160_000, 12_000)]
    assert unavailable is None and optimized is False
    # Only two decimals of each position leave Nova.
    url, params = calls[0]
    assert url.endswith("/route/v1/driving/106.85,-6.21;107.62,-6.92")
    assert params is not None and params["alternatives"] == "true"


async def test_a_sea_crossing_keeps_the_places_and_says_why(monkeypatch):
    async def never(url: str, params: dict[str, Any] | None = None) -> Any:
        raise AssertionError("places on different islands are not sent")

    monkeypatch.setattr(ui_public, "_public_json", never)
    _name, node = await _check(
        'r = RouteMap("Jakarta to Makassar", [{label: "Jakarta", lat: -6.2, lon: 106.85},'
        ' {label: "Makassar", lat: -5.15, lon: 119.43}], true)')
    _title, places, routes, unavailable, _optimized = (ul._plain(arg) for arg in node.args)
    assert len(places) == 2 and routes == []
    assert unavailable == "These places are on different islands; no road joins them."


async def test_a_place_with_no_road_is_unavailable(monkeypatch):
    async def no_route(url: str, params: dict[str, Any] | None = None) -> Any:
        return {"code": "NoRoute", "message": "Impossible route between points"}

    monkeypatch.setattr(ui_public, "_public_json", no_route)
    found = await ui_public.road_routes([ui_public.check_place("Borobudur", -7.6, 110.2),
                                         ui_public.check_place("Somewhere", -7.0, 111.0)])
    assert found == {"unavailable": "No road route connects these places."}


async def test_a_routing_timeout_is_unavailable_not_an_error(monkeypatch):
    async def slow(url: str, params: dict[str, Any] | None = None) -> Any:
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(ui_public, "_public_json", slow)
    found = await ui_public.road_routes([ui_public.check_place("Jakarta", -6.2, 106.85),
                                         ui_public.check_place("Bandung", -6.9, 107.6)])
    assert found == {"unavailable": "The road routing service did not answer."}


async def test_an_optimised_route_visits_the_places_in_the_shortest_order(monkeypatch):
    async def answer(url: str, params: dict[str, Any] | None = None) -> Any:
        if "/table/" in url:
            # Input order Jakarta, Surabaya, Bandung: J -> B -> S is shortest.
            return {"code": "Ok", "durations": [[0, 9, 2], [9, 0, 7], [2, 7, 0]]}
        assert url.endswith("106.85,-6.21;107.62,-6.92;112.75,-7.26")
        assert params is not None and params["alternatives"] == "false"
        return {"code": "Ok", "routes": [_route([[106.85, -6.21], [112.75, -7.26]], 7e5, 4e4)]}

    monkeypatch.setattr(ui_public, "_public_json", answer)
    _name, node = await _check(
        'r = RouteMap("Shortest visit", [{label: "Jakarta", lat: -6.2088, lon: 106.8456},'
        ' {label: "Surabaya", lat: -7.2575, lon: 112.7521},'
        ' {label: "Bandung", lat: -6.9175, lon: 107.6191}], true, true)')
    _title, places, routes, _unavailable, optimized = (ul._plain(arg) for arg in node.args)
    assert [place["label"] for place in places] == ["Jakarta", "Bandung", "Surabaya"]
    assert optimized is True and len(routes) == 1


def test_the_open_path_is_the_shortest_from_the_first_place():
    # A one-way visit: returning to the start does not count.
    cost = [[0, 1, 5, 9], [1, 0, 2, 8], [5, 2, 0, 3], [9, 8, 3, 0]]
    assert ui_public.open_path(cost) == [0, 1, 2, 3]
    big = [[abs(a - b) for b in range(15)] for a in range(15)]
    assert ui_public.open_path(big) == list(range(15))


def test_route_lines_decode_from_polyline6():
    # (38.5, -120.2), (40.7, -120.95), (43.252, -126.453) at six decimals.
    encoded = "_izlhA~rlgdF_{geC~ywl@_kwzCn`{nI"
    assert ui_public._line(encoded) == [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]]


def test_stored_route_lines_stay_within_the_position_budget():
    # A wiggling road: every point is a visible bend.
    line = [[106 + index / 1_000, -6.0 + (index % 2) / 100] for index in range(9_000)]
    routes = ui_public._routes([_route(line, 1, 1), _route(line, 2, 2), _route(line, 3, 3),
                                _route(line, 4, 4)])
    assert len(routes) == ui_public.MAX_ALTERNATIVES
    lines = [ui_public._decode(route["line"], 5) or [] for route in routes]
    assert sum(len(points) for points in lines) <= ui_public.ROUTE_POSITIONS
    assert lines[0][0] == [106.0, -6.0]


def test_a_straight_stretch_is_stored_as_its_ends():
    line = [[106 + index / 1_000, -6.0] for index in range(500)]
    [route] = ui_public._routes([_route(line, 1, 1)])
    assert ui_public._decode(route["line"], 5) == [[106.0, -6.0], [106.499, -6.0]]


async def test_images_come_only_from_wikimedia(monkeypatch):
    async def commons(url: str, params: dict[str, Any] | None = None) -> Any:
        assert url == ui_public.COMMONS
        return {"query": {"pages": {
            "1": {"index": 2, "imageinfo": [{"thumburl": "https://evil.test/x.jpg"}]},
            "2": {"index": 1, "imageinfo": [{
                "thumburl": "https://upload.wikimedia.org/a/b/960px-Borobudur.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Borobudur.jpg"}]},
        }}}

    monkeypatch.setattr(ui_public, "_public_json", commons)
    _name, node = await _check('p = SearchImage("Borobudur temple", "Borobudur temple")')
    assert node.name == "Picture"
    assert [ul._plain(arg) for arg in node.args] == [
        "https://upload.wikimedia.org/a/b/960px-Borobudur.jpg", "Borobudur temple",
        "https://commons.wikimedia.org/wiki/File:Borobudur.jpg"]
    _name, node = await _check(
        'p = Image("https://upload.wikimedia.org/x/y.jpg", "A temple")')
    assert ul._plain(node.args[0]) == "https://upload.wikimedia.org/x/y.jpg"
    for url in ("https://upload.wikimedia.org.evil.test/x.jpg", "http://upload.wikimedia.org/x.jpg",
                "https://user@upload.wikimedia.org/x.jpg"):
        with pytest.raises(ul.LayoutError, match="Wikimedia"):
            await _check(f'p = Image("{url}", "A temple")')


async def test_public_requests_reach_only_allowed_hosts():
    with pytest.raises(ui_public.PublicContentError):
        await ui_public._public_json("https://example.com/route/v1/driving/1,2;3,4")
    with pytest.raises(ui_public.PublicContentError):
        await ui_public._public_json("http://router.project-osrm.org/route/v1/driving/1,2;3,4")


async def test_the_model_cannot_write_a_checked_map_itself():
    for line in ['m = GeoMap({rows: []})',
                 'm = PlaceMap("x", [{label: "a", lat: 0, lon: 0, check: "verified"}], [])',
                 'p = Picture("https://upload.wikimedia.org/x.jpg", "x", "y")']:
        with pytest.raises(ul.LayoutError, match="Nova"):
            await _check(line)
