"""Public responses remain bounded and malformed services degrade predictably."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.assistant import ui_media


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(ui_media.session_store, "_redis", None)


async def test_wikimedia_thumbnail_host_is_supported_without_accepting_other_hosts(monkeypatch):
    thumbnail = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a1/photo.jpg/960px.jpg"
    request = AsyncMock(
        return_value={
            "query": {
                "pages": {
                    "1": {
                        "imageinfo": [
                            {
                                "thumburl": thumbnail,
                                "url": "https://upload.wikimedia.org/wikipedia/commons/a/a1/photo.jpg",
                            }
                        ]
                    }
                }
            }
        }
    )
    monkeypatch.setattr(ui_media, "_public_json", request)
    result = await ui_media.public_media("SearchImage", {"query": "Landmark"})
    assert result["url"] == thumbnail
    assert ui_media._image_url({"thumburl": "https://thumb.wikimedia.org.evil.test/x"}) is None
    assert ui_media._image_url({"thumburl": "https://user@thumb.wikimedia.org/x"}) is None


@pytest.mark.parametrize(
    "response",
    [
        {"query": []},
        {"query": {"pages": []}},
        {"query": {"pages": {"1": None}}},
        {"query": {"pages": {"1": {"imageinfo": "invalid"}}}},
        {"query": {"pages": {"1": {"imageinfo": [None]}}}},
    ],
)
async def test_malformed_wikimedia_is_a_bounded_gateway_error(monkeypatch, response):
    monkeypatch.setattr(ui_media, "_public_json", AsyncMock(return_value=response))
    with pytest.raises(HTTPException) as error:
        await ui_media.public_media("SearchImage", {"query": "London", "alt": "Landmark"})
    assert error.value.status_code == 502


@pytest.mark.parametrize(
    "geometry",
    [
        None,
        [],
        {"type": "LineString", "coordinates": []},
        {"type": "LineString", "coordinates": [[True, 2], [1, 2]]},
        {"type": "LineString", "coordinates": [[float("nan"), 2], [1, 2]]},
    ],
)
async def test_bad_osrm_route_preserves_the_public_places(monkeypatch, geometry):
    monkeypatch.setattr(
        ui_media,
        "_public_json",
        AsyncMock(
            return_value={
                "routes": [{"geometry": geometry}],
            }
        ),
    )
    result = await ui_media.public_media(
        "Map",
        {
            "route": True,
            "markers": [
                {"props": {"latitude": 51.5, "longitude": -0.1, "label": "A"}},
                {"props": {"latitude": 51.6, "longitude": -0.2, "label": "B"}},
            ],
        },
    )
    assert len(result["markers"]) == 2
    assert result["route_unavailable"] is True
    assert "geometry" not in result


@pytest.mark.parametrize("latitude", [True, float("nan"), float("inf"), 91])
async def test_invalid_coordinates_do_not_leave_nova(monkeypatch, latitude):
    request = AsyncMock()
    monkeypatch.setattr(ui_media, "_public_json", request)
    with pytest.raises(HTTPException) as error:
        await ui_media.public_media(
            "Map",
            {
                "markers": [
                    {"props": {"latitude": latitude, "longitude": 0}},
                ]
            },
        )
    assert error.value.status_code == 422
    request.assert_not_awaited()
