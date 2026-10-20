"""Bounded adapters for static public media declared in a verified UI program."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from app.core.redis import session_store

_HOSTS = frozenset({"commons.wikimedia.org", "router.project-osrm.org"})
_LIMIT = 256_000


def _image_url(info: dict) -> str | None:
    for value in (info.get("thumburl"), info.get("url")):
        if not isinstance(value, str) or len(value) > 2048:
            continue
        target = urlparse(value)
        if target.scheme == "https" and target.netloc in {
            "upload.wikimedia.org",
            "thumb.wikimedia.org",
        }:
            return value
    return None


async def _public_json(url: str, params: dict | None = None) -> dict:
    target = urlparse(url)
    if target.scheme != "https" or target.hostname not in _HOSTS or target.username:
        raise HTTPException(422, "Unsupported public media source")
    async with (
        asyncio.timeout(8),
        httpx.AsyncClient(
            timeout=8, follow_redirects=False, headers={"User-Agent": "NovaStudio/1 public-media"}
        ) as client,
        client.stream("GET", url, params=params) as response,
    ):
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > _LIMIT:
                raise HTTPException(502, "Public media response exceeded its limit")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise HTTPException(502, "Invalid public media response")
        return result


def _route(candidate: object) -> dict | None:
    if not isinstance(candidate, dict):
        return None
    geometry = candidate.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not 2 <= len(coordinates) <= 5000:
        return None
    for point in coordinates:
        if not isinstance(point, list) or len(point) != 2:
            return None
        if any(
            isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n)
            for n in point
        ):
            return None
        if not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90:
            return None
    result: dict = {"geometry": {"type": "LineString", "coordinates": coordinates}}
    for key in ("distance", "duration"):
        value = candidate.get(key)
        if value is not None:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                return None
            result[key] = value
    return result


async def public_media(kind: str, props: dict) -> dict:
    digest = hashlib.sha256(json.dumps([kind, props], sort_keys=True).encode()).hexdigest()
    client = session_store._redis
    cache_key = f"nova:studio:public-media:v3:{digest}"
    if client:
        cached = await client.get(cache_key)
        if cached:
            return json.loads(cached)
    try:
        if kind == "SearchImage":
            query = props.get("query")
            if not isinstance(query, str) or not 1 <= len(query) <= 256:
                raise HTTPException(422, "Invalid public image search")
            response = await _public_json(
                "https://commons.wikimedia.org/w/api.php",
                {
                    "action": "query",
                    "format": "json",
                    "generator": "search",
                    "gsrsearch": query,
                    "gsrnamespace": 6,
                    "gsrlimit": 3,
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "iiurlwidth": 960,
                },
            )
            payload = response.get("query", {})
            if not isinstance(payload, dict) or not isinstance(payload.get("pages", {}), dict):
                raise HTTPException(502, "Invalid public media response")
            pages = list(payload.get("pages", {}).values())[:3]
            if any(
                not isinstance(page, dict)
                or not isinstance(page.get("imageinfo", []), list)
                or any(not isinstance(info, dict) for info in page.get("imageinfo", [])[:1])
                for page in pages
            ):
                raise HTTPException(502, "Invalid public media response")
            photos = [
                info for page in pages for info in page.get("imageinfo", [])[:1] if _image_url(info)
            ]
            if not photos:
                raise HTTPException(404, "No public image found")
            info = photos[0]
            source = str(info.get("descriptionurl", "https://commons.wikimedia.org/"))
            if not source.startswith("https://commons.wikimedia.org/"):
                source = "https://commons.wikimedia.org/"
            result = {
                "url": _image_url(info),
                "source": source,
                "description": props.get("alt", query),
            }
        elif kind == "Image":
            if not str(props.get("url", "")).startswith("https://upload.wikimedia.org/"):
                raise HTTPException(422, "Unsupported public image")
            result = {
                "url": props["url"],
                "source": "https://commons.wikimedia.org/",
                "description": props.get("alt", ""),
            }
        elif kind == "Map":
            markers = props.get("markers")
            if not isinstance(markers, list) or not 1 <= len(markers) <= 25:
                raise HTTPException(422, "A public map needs 1..25 places")
            places = []
            for marker in markers:
                p = marker.get("props", {}) if isinstance(marker, dict) else {}
                lat, lon = p.get("latitude"), p.get("longitude")
                if (
                    not isinstance(lat, (int, float))
                    or isinstance(lat, bool)
                    or not math.isfinite(lat)
                    or not -90 <= lat <= 90
                    or (
                        not isinstance(lon, (int, float))
                        or isinstance(lon, bool)
                        or not math.isfinite(lon)
                        or not -180 <= lon <= 180
                    )
                ):
                    raise HTTPException(422, "Invalid public map coordinate")
                places.append({"latitude": lat, "longitude": lon, "label": p.get("label", "Place")})
            result = {"markers": places}
            if props.get("route") and len(places) > 1:
                coordinates = ";".join(f"{p['longitude']},{p['latitude']}" for p in places)
                try:
                    route = await _public_json(
                        "https://router.project-osrm.org/route/v1/driving/" + coordinates,
                        {
                            "overview": "simplified",
                            "geometries": "geojson",
                            "steps": "false",
                            "alternatives": "true",
                        },
                    )
                    routes = []
                    total_positions = 0
                    for candidate in route.get("routes", [])[:3]:
                        checked = _route(candidate)
                        if checked is None:
                            continue
                        total_positions += len(checked["geometry"]["coordinates"])
                        if total_positions > 5000:
                            break
                        routes.append(checked)
                    if routes:
                        result["geometry"] = routes[0]["geometry"]
                        result["routes"] = routes
                    else:
                        result["route_unavailable"] = True
                except (
                    httpx.HTTPError,
                    HTTPException,
                    KeyError,
                    IndexError,
                    ValueError,
                    TypeError,
                    AttributeError,
                    TimeoutError,
                ):
                    result["route_unavailable"] = True
        else:
            raise HTTPException(422, "Unsupported public media component")
    except (httpx.HTTPError, ValueError, KeyError, TimeoutError) as exc:
        raise HTTPException(502, "Public media is unavailable") from exc
    if len(json.dumps(result).encode()) > _LIMIT:
        raise HTTPException(502, "Public media response exceeded its limit")
    if client:
        await client.set(cache_key, json.dumps(result), ex=3600)
    return result
