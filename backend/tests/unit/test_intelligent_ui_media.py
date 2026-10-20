"""Public responses remain bounded and malformed services degrade predictably."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
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


@pytest.mark.parametrize("url", [
    "http://commons.wikimedia.org/w/api.php",
    "https://commons.wikimedia.org.evil.test/",
    "https://user@commons.wikimedia.org/",
    "https://127.0.0.1/",
])
async def test_public_transport_refuses_unapproved_sources_before_connecting(monkeypatch, url):
    client = AsyncMock()
    monkeypatch.setattr(ui_media.httpx, "AsyncClient", client)
    with pytest.raises(HTTPException) as refusal:
        await ui_media._public_json(url)
    assert refusal.value.status_code == 422
    client.assert_not_called()


@pytest.mark.parametrize("body,status", [
    (b'{"ok":true}', 200),
    (b"[]", 200),
    (b"invalid JSON", 200),
    (b"x" * (ui_media._LIMIT + 1), 200),
    (b"", 302),
    (b"", 503),
])
async def test_public_transport_bounds_and_validates_response(monkeypatch, body, status):
    original = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, content=body, headers={"Location": "https://other.test/"})

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["timeout"] == 8
        return original(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(ui_media.httpx, "AsyncClient", client)
    if body == b'{"ok":true}':
        assert await ui_media._public_json(
            "https://commons.wikimedia.org/w/api.php", {"query": "public place"},
        ) == {"ok": True}
    else:
        with pytest.raises((HTTPException, httpx.HTTPStatusError, json.JSONDecodeError)):
            await ui_media._public_json("https://commons.wikimedia.org/w/api.php")
    assert len(requests) == 1
    assert requests[0].headers["User-Agent"] == "NovaStudio/1 public-media"


async def test_public_media_timeout_becomes_bounded_gateway_error(monkeypatch):
    monkeypatch.setattr(ui_media, "_public_json", AsyncMock(side_effect=httpx.ReadTimeout("slow")))
    with pytest.raises(HTTPException) as unavailable:
        await ui_media.public_media("SearchImage", {"query": "public place"})
    assert unavailable.value.status_code == 502


async def test_public_media_cache_reuses_static_response(monkeypatch):
    cache = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    monkeypatch.setattr(ui_media.session_store, "_redis", cache)
    props = {"url": "https://upload.wikimedia.org/example.png", "alt": "Public example"}
    first = await ui_media.public_media("Image", props)
    key, value = cache.set.call_args.args
    assert key.startswith("nova:studio:public-media:")
    assert "example.png" not in key
    assert cache.set.call_args.kwargs == {"ex": 3600}
    cache.get.return_value = value
    assert await ui_media.public_media("Image", props) == first
    assert cache.set.await_count == 1


async def test_public_route_retains_valid_geometry(monkeypatch):
    geometry = {"type": "LineString", "coordinates": [[106.8, -6.2], [106.9, -6.3]]}
    monkeypatch.setattr(ui_media, "_public_json", AsyncMock(return_value={
        "routes": [{"geometry": geometry}],
    }))
    result = await ui_media.public_media("Map", {"route": True, "markers": [
        {"props": {"longitude": lon, "latitude": lat}} for lon, lat in geometry["coordinates"]
    ]})
    assert result["geometry"] == geometry
    assert "route_unavailable" not in result
