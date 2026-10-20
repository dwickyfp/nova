"""Catalog history, routed public responses, and bounded map observations."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.modules.assistant import intelligent_ui as ui
from app.modules.assistant import ui_media
from app.modules.assistant.schemas import UiMapMetricInput


async def test_v2_and_v3_catalogs_remain_independently_validated():
    program = 'root=Col([Text("A saved answer")])'
    assert (await ui.parse_program(program, catalog_version="nova-openui-2"))[
        "catalog_version"
    ] == "nova-openui-2"
    assert (await ui.parse_program(program))["catalog_version"] == "nova-openui-3"
    with pytest.raises(ui.UiValidationError):
        await ui.parse_program(program, catalog_version="unknown")


@pytest.mark.parametrize("bad", [-1, float("inf"), True, "20"])
def test_invalid_route_metrics_are_not_business_facts(bad):
    assert (
        ui_media._route(
            {"geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, "duration": bad}
        )
        is None
    )


async def test_routes_are_bounded_and_original_geometry_is_compatible(monkeypatch):
    monkeypatch.setattr(ui_media.session_store, "_redis", None)
    route = {
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        "distance": 100,
        "duration": 20,
    }
    request = AsyncMock(return_value={"routes": [route] * 5})
    monkeypatch.setattr(ui_media, "_public_json", request)
    result = await ui_media.public_media(
        "Map",
        {
            "route": True,
            "markers": [
                {"props": {"latitude": 0, "longitude": 0}},
                {"props": {"latitude": 1, "longitude": 1}},
            ],
        },
    )
    assert len(result["routes"]) == 3
    assert result["geometry"] == route["geometry"]
    assert result["routes"][0]["distance"] == 100
    assert request.call_args.args[1]["alternatives"] == "true"


@pytest.mark.parametrize(
    "payload",
    [
        {"event": "ready", "elapsed_ms": 2, "coordinates": [1, 2]},
        {"event": "user-defined", "elapsed_ms": 2},
        {"event": "ready", "elapsed_ms": float("nan")},
        {"event": "ready", "elapsed_ms": 300001},
    ],
)
def test_observations_cannot_contain_location_payloads_or_unbounded_labels(payload):
    with pytest.raises(ValidationError):
        UiMapMetricInput(**payload)


async def test_saved_camera_is_validated_against_the_persisted_program():
    source = {
        "program": '$v=null\nroot=Col([Map([MapMarker(0,0,"Public")],false,"Map",{},$v)])',
        "catalog_version": "nova-openui-3",
        "state_defaults": {"$v": None},
        "evidence": {},
    }
    await ui.validate_source_state(source, {
        "$v": {"center": [1, 2], "zoom": 10, "bearing": 0, "pitch": 0},
    })
    with pytest.raises(ui.UiValidationError):
        await ui.validate_source_state(source, {
            "$v": {"center": [999, 2], "zoom": 10, "bearing": 0, "pitch": 0},
        })


async def test_observation_endpoint_revalidates_source_before_recording(monkeypatch):
    import importlib

    from fastapi import HTTPException

    router = importlib.import_module("app.modules.agents.router")
    denied = AsyncMock(side_effect=HTTPException(404, "Unavailable"))
    monkeypatch.setattr(router, "_ui_source", denied)
    with pytest.raises(HTTPException):
        await router.record_agent_map_metric(
            "a", "t", "m", "c", UiMapMetricInput(event="ready", elapsed_ms=20),
            {"username": "alice"},
        )
    denied.assert_awaited_once()
