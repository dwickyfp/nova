"""Public places, road routes and images for page layouts.

A page may show public content next to verified data: a map of named places,
the road route between them, a public image. Nova fetches that content while
it checks the statement, stores it in the checked program and never lets the
browser reach the outside service, so a replay works offline and shows what
the live answer showed.

* Places are typed by the model, so each coordinate is checked: it must be in
  range, and when its label names a known city it must lie near that city. A
  coordinate far from the city it names is replaced by the city's centre; a
  place Nova cannot check is marked approximate.
* Road routes come from the public OSRM server. Only city-level positions are
  sent (two decimals, about a kilometre), at most 25 of them. Places on
  different known islands get no route (the public server would cross by
  ferry); a route that is unavailable (no road, a timeout) leaves the places
  on the map with a note instead.
* Images come from Wikimedia Commons only.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import unicodedata
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

import httpx

OSRM = "https://router.project-osrm.org"
COMMONS = "https://commons.wikimedia.org/w/api.php"
_HOSTS = frozenset({"router.project-osrm.org", "commons.wikimedia.org"})
_IMAGE_HOSTS = frozenset({"upload.wikimedia.org"})
_TIMEOUT_S = 8.0
_RESPONSE_LIMIT = 1_000_000
#: Positions across every stored route line, so a page stays small.
ROUTE_POSITIONS = 5_000
MAX_ALTERNATIVES = 3
MAX_PLACES = 25
#: A named city's coordinate may lie this far from its centre.
_CITY_RADIUS_KM = 60.0
_CACHE_SIZE = 128
#: About 30 m: closer bends of a road are not visible on a page map.
_TOLERANCE_DEG = 0.0003

#: City centres a place label is checked against (lat, lon).
CITIES: dict[str, tuple[float, float]] = {
    "jakarta": (-6.2088, 106.8456),
    "bandung": (-6.9175, 107.6191),
    "yogyakarta": (-7.7956, 110.3695),
    "semarang": (-6.9667, 110.4167),
    "surabaya": (-7.2575, 112.7521),
    "malang": (-7.9666, 112.6326),
    "makassar": (-5.1477, 119.4327),
    "medan": (3.5952, 98.6722),
    "denpasar": (-8.6705, 115.2126),
    "palembang": (-2.9761, 104.7754),
    "bogor": (-6.5971, 106.8060),
    "bekasi": (-6.2383, 106.9756),
    "tangerang": (-6.1783, 106.6319),
    "depok": (-6.4025, 106.7942),
    "surakarta": (-7.5755, 110.8243),
    "cirebon": (-6.7320, 108.5523),
    "tasikmalaya": (-7.3274, 108.2207),
    "purwokerto": (-7.4245, 109.2302),
    "kediri": (-7.8480, 112.0178),
    "serang": (-6.1200, 106.1503),
    "balikpapan": (-1.2379, 116.8529),
    "samarinda": (-0.5022, 117.1536),
    "banjarmasin": (-3.3194, 114.5908),
    "pontianak": (-0.0263, 109.3425),
    "manado": (1.4748, 124.8421),
    "palu": (-0.8917, 119.8707),
    "kendari": (-3.9985, 122.5129),
    "padang": (-0.9471, 100.4172),
    "pekanbaru": (0.5071, 101.4478),
    "batam": (1.0456, 104.0305),
    "jambi": (-1.6101, 103.6131),
    "bengkulu": (-3.8004, 102.2655),
    "bandar lampung": (-5.3971, 105.2668),
    "banda aceh": (5.5483, 95.3238),
    "mataram": (-8.5833, 116.1167),
    "kupang": (-10.1772, 123.6070),
    "ambon": (-3.6954, 128.1814),
    "jayapura": (-2.5337, 140.7181),
    "sorong": (-0.8762, 131.2558),
    "singapore": (1.3521, 103.8198),
    "kuala lumpur": (3.1390, 101.6869),
}
#: The landmass of each city: no road joins two different ones.
LANDS: dict[str, str] = {
    "jakarta": "java",
    "bandung": "java",
    "yogyakarta": "java",
    "semarang": "java",
    "surabaya": "java",
    "malang": "java",
    "makassar": "sulawesi",
    "medan": "sumatra",
    "denpasar": "bali",
    "palembang": "sumatra",
    "bogor": "java",
    "bekasi": "java",
    "tangerang": "java",
    "depok": "java",
    "surakarta": "java",
    "cirebon": "java",
    "tasikmalaya": "java",
    "purwokerto": "java",
    "kediri": "java",
    "serang": "java",
    "balikpapan": "borneo",
    "samarinda": "borneo",
    "banjarmasin": "borneo",
    "pontianak": "borneo",
    "manado": "sulawesi",
    "palu": "sulawesi",
    "kendari": "sulawesi",
    "padang": "sumatra",
    "pekanbaru": "sumatra",
    "batam": "batam",
    "jambi": "sumatra",
    "bengkulu": "sumatra",
    "bandar lampung": "sumatra",
    "banda aceh": "sumatra",
    "mataram": "lombok",
    "kupang": "timor",
    "ambon": "ambon",
    "jayapura": "jayapura",
    "sorong": "sorong",
    "singapore": "malay peninsula",
    "kuala lumpur": "malay peninsula",
}
_ALIASES = {
    "solo": "surakarta", "jogja": "yogyakarta", "jogjakarta": "yogyakarta",
    "djokjakarta": "yogyakarta", "dki jakarta": "jakarta", "lampung": "bandar lampung",
    "ujung pandang": "makassar", "singapura": "singapore",
}


class PublicContentError(ValueError):
    """Public content that cannot be shown, with a reason safe to give the model."""


def _city_key(label: str) -> str | None:
    plain = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"[^a-z ]+", " ", plain)
    plain = re.sub(r"\b(kota|city|kabupaten|kab|of)\b", " ", plain)
    plain = " ".join(plain.split())
    if plain in CITIES:
        return plain
    if plain in _ALIASES:
        return _ALIASES[plain]
    # "Jakarta Selatan", "Bandung (HQ)": the first known city the label starts with.
    for name in sorted([*CITIES, *_ALIASES], key=len, reverse=True):
        if plain.startswith(name + " "):
            return _ALIASES.get(name, name)
    return None


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _coordinate(value: Any, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and low <= number <= high else None


def check_place(label: str, latitude: Any, longitude: Any) -> dict[str, Any]:
    """A place whose coordinate is in range and, when it names a known city, near it.

    ``check`` is ``verified`` (near the city it names), ``corrected`` (moved to
    that city's centre) or ``approximate`` (Nova knows no city by that name).
    A coordinate out of range is refused unless the label names a known city.
    """
    lat, lon = _coordinate(latitude, -90, 90), _coordinate(longitude, -180, 180)
    city = _city_key(label)
    if city is not None:
        centre = CITIES[city]
        if lat is not None and lon is not None and _km((lat, lon), centre) <= _CITY_RADIUS_KM:
            return {"label": label, "lat": lat, "lon": lon, "check": "verified"}
        return {"label": label, "lat": centre[0], "lon": centre[1], "check": "corrected"}
    if lat is None or lon is None:
        raise PublicContentError(f"The place {label!r} has no valid coordinate")
    return {"label": label, "lat": lat, "lon": lon, "check": "approximate"}


# ── Outside requests ───────────────────────────────────────────────────

_cache: OrderedDict[str, Any] = OrderedDict()


async def _public_json(url: str, params: dict[str, Any] | None = None) -> Any:
    """A bounded JSON answer from an allowed public host."""
    target = urlparse(url)
    if target.scheme != "https" or target.hostname not in _HOSTS or target.username:
        raise PublicContentError("Unsupported public source")
    key = json.dumps([url, params], sort_keys=True)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    async with (
        asyncio.timeout(_TIMEOUT_S),
        httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=False,
                          headers={"User-Agent": "NovaStudio/1 public-content"}) as client,
        client.stream("GET", url, params=params) as response,
    ):
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > _RESPONSE_LIMIT:
                raise PublicContentError("The public answer was too large")
        status = response.status_code
    try:
        result = json.loads(body)
    except ValueError as exc:
        raise PublicContentError("The public answer was not readable") from exc
    if status >= 500:
        raise PublicContentError("The public service is unavailable")
    _cache[key] = result
    if len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)
    return result


# ── Road routes ────────────────────────────────────────────────────────


def _decode(encoded: str, precision: int = 6) -> list[list[float]] | None:
    """A polyline (Google encoding, six decimals) as [lon, lat] positions."""
    positions: list[list[float]] = []
    index = lat = lon = 0
    factor = 10 ** precision
    try:
        while index < len(encoded):
            deltas = []
            for _ in range(2):
                shift = result = 0
                while True:
                    byte = ord(encoded[index]) - 63
                    index += 1
                    result |= (byte & 0x1F) << shift
                    shift += 5
                    if byte < 0x20:
                        break
                deltas.append(~(result >> 1) if result & 1 else result >> 1)
            lat += deltas[0]
            lon += deltas[1]
            positions.append([lon / factor, lat / factor])
    except IndexError:
        return None
    return positions


def _line(geometry: Any) -> list[list[float]] | None:
    if isinstance(geometry, str):
        positions: Any = _decode(geometry)
    elif isinstance(geometry, dict) and geometry.get("type") == "LineString":
        positions = geometry.get("coordinates")
    else:
        return None
    if not isinstance(positions, list) or len(positions) < 2:
        return None
    line = []
    for point in positions:
        if not (isinstance(point, list) and len(point) == 2):
            return None
        lon, lat = _coordinate(point[0], -180, 180), _coordinate(point[1], -90, 90)
        if lon is None or lat is None:
            return None
        line.append([round(lon, 5), round(lat, 5)])
    return line


def _simplify(line: list[list[float]], budget: int) -> list[list[float]]:
    """The line with points closer than about 30 m to it dropped, within ``budget``."""
    keep = {0, len(line) - 1}
    stack = [(0, len(line) - 1)]
    while stack:
        first, last = stack.pop()
        (x1, y1), (x2, y2) = line[first], line[last]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1e-12
        far, distance = -1, _TOLERANCE_DEG
        for index in range(first + 1, last):
            x, y = line[index]
            offset = abs(dy * (x - x1) - dx * (y - y1)) / norm
            if offset > distance:
                far, distance = index, offset
        if far >= 0:
            keep.add(far)
            stack += [(first, far), (far, last)]
    kept = [line[index] for index in sorted(keep)]
    if len(kept) <= budget:
        return kept
    step = (len(kept) - 1) / (budget - 1)
    return [kept[round(index * step)] for index in range(budget)]


def encode_line(line: list[list[float]], precision: int = 5) -> str:
    """[lon, lat] positions as a polyline (Google encoding), the way pages store them."""
    factor = 10 ** precision
    out: list[str] = []
    last = (0, 0)
    for lon, lat in line:
        point = (round(lat * factor), round(lon * factor))
        for value in (point[0] - last[0], point[1] - last[1]):
            value = ~(value << 1) if value < 0 else value << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        last = point
    return "".join(out)


def _routes(candidates: Any) -> list[dict[str, Any]]:
    found: list[tuple[list[list[float]], float, float]] = []
    for candidate in candidates[:MAX_ALTERNATIVES] if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        line = _line(candidate.get("geometry"))
        distance = _coordinate(candidate.get("distance"), 0, 1e8)
        duration = _coordinate(candidate.get("duration"), 0, 1e8)
        if line is not None and distance is not None and duration is not None:
            found.append((line, distance, duration))
    if not found:
        return []
    # Share the position budget across the routes; store each as a polyline.
    budget = ROUTE_POSITIONS // len(found)
    return [{"line": encode_line(_simplify(line, budget)), "distance_m": round(distance),
             "duration_s": round(duration)} for line, distance, duration in found]


def city_level(place: dict[str, Any]) -> str:
    """The position sent to the routing service: two decimals, about a kilometre."""
    return f"{round(place['lon'], 2):.2f},{round(place['lat'], 2):.2f}"


async def road_routes(places: list[dict[str, Any]], *, optimize: bool = False) -> dict[str, Any]:
    """Driving routes through the places, in order or in the shortest order.

    Returns ``{"routes": [...]}`` with up to three alternatives (only between
    two places), the visiting ``order`` when it was optimised, or
    ``{"unavailable": reason}``.
    """
    if len(places) < 2:
        return {"unavailable": "A route needs at least two places."}
    if len(places) > MAX_PLACES:
        return {"unavailable": f"A route takes at most {MAX_PLACES} places."}
    lands = {LANDS.get(_city_key(str(place["label"])) or "") for place in places} - {None}
    if len(lands) > 1:
        return {"unavailable": "These places are on different islands; no road joins them."}
    order: list[int] | None = None
    try:
        if optimize and len(places) > 2:
            order = await _shortest_order(places)
            if order is None:
                return {"unavailable": "No road route connects these places."}
            places = [places[index] for index in order]
        positions = ";".join(city_level(place) for place in places)
        answer = await _public_json(
            f"{OSRM}/route/v1/driving/{positions}",
            {"overview": "full", "geometries": "polyline6", "steps": "false",
             "alternatives": "true" if len(places) == 2 else "false"},
        )
    except (PublicContentError, httpx.HTTPError, TimeoutError, OSError):
        return {"unavailable": "The road routing service did not answer."}
    if not isinstance(answer, dict) or answer.get("code") != "Ok":
        return {"unavailable": "No road route connects these places."}
    routes = _routes(answer.get("routes"))
    if not routes:
        return {"unavailable": "No road route connects these places."}
    return {"routes": routes, **({"order": order} if order is not None else {})}


async def _shortest_order(places: list[dict[str, Any]]) -> list[int] | None:
    """The order that visits every place by the shortest driving time, from the first."""
    positions = ";".join(city_level(place) for place in places)
    answer = await _public_json(f"{OSRM}/table/v1/driving/{positions}",
                                {"annotations": "duration"})
    if not isinstance(answer, dict) or answer.get("code") != "Ok":
        return None
    matrix = answer.get("durations")
    size = len(places)
    if not (isinstance(matrix, list) and len(matrix) == size and all(
            isinstance(row, list) and len(row) == size
            and all(_coordinate(cell, 0, 1e9) is not None for cell in row) for row in matrix)):
        return None
    return open_path(matrix)


def open_path(cost: list[list[float]]) -> list[int]:
    """The cheapest path from place 0 through every place (exact up to 12 places)."""
    size = len(cost)
    if size <= 2:
        return list(range(size))
    if size <= 12:
        # Held-Karp over subsets of the places after the first.
        best: dict[tuple[int, int], tuple[float, int]] = {
            (1 << node, node): (cost[0][node], 0) for node in range(1, size)}
        for mask in range(1, 1 << size):
            if mask & 1:
                continue
            for last in range(1, size):
                if not mask & (1 << last) or (mask, last) not in best:
                    continue
                spent = best[(mask, last)][0]
                for nxt in range(1, size):
                    if mask & (1 << nxt):
                        continue
                    key = (mask | (1 << nxt), nxt)
                    total = spent + cost[last][nxt]
                    if key not in best or total < best[key][0]:
                        best[key] = (total, last)
        full = ((1 << size) - 1) & ~1
        last = min(range(1, size), key=lambda node: best[(full, node)][0])
        path, mask = [], full
        while last:
            path.append(last)
            mask, last = mask & ~(1 << last), best[(mask, last)][1]
        return [0, *reversed(path)]
    # Larger: nearest neighbour, then 2-opt.
    path, left = [0], set(range(1, size))
    while left:
        nxt = min(left, key=lambda node: cost[path[-1]][node])
        path.append(nxt)
        left.remove(nxt)

    def length(order: list[int]) -> float:
        return sum(cost[a][b] for a, b in zip(order, order[1:], strict=False))

    improved = True
    while improved:
        improved = False
        for i in range(1, size - 1):
            for j in range(i + 1, size):
                candidate = path[:i] + path[i:j + 1][::-1] + path[j + 1:]
                if length(candidate) < length(path) - 1e-9:
                    path, improved = candidate, True
    return path


# ── Images ─────────────────────────────────────────────────────────────


def _image_url(info: dict[str, Any]) -> str | None:
    for value in (info.get("thumburl"), info.get("url")):
        if not isinstance(value, str) or len(value) > 2048:
            continue
        target = urlparse(value)
        if target.scheme == "https" and target.hostname in _IMAGE_HOSTS and not target.username:
            return value
    return None


def _source(value: Any) -> str:
    if isinstance(value, str) and value.startswith("https://commons.wikimedia.org/") and len(
            value) <= 2048:
        return value
    return "https://commons.wikimedia.org/"


async def search_image(query: str) -> dict[str, str]:
    """The first Wikimedia Commons image for a public query."""
    try:
        answer = await _public_json(COMMONS, {
            "action": "query", "format": "json", "generator": "search", "gsrsearch": query,
            "gsrnamespace": 6, "gsrlimit": 3, "prop": "imageinfo",
            "iiprop": "url", "iiurlwidth": 960,
        })
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        raise PublicContentError("The public image service did not answer") from exc
    pages = answer.get("query", {}).get("pages", {}) if isinstance(answer, dict) else {}
    if not isinstance(pages, dict):
        raise PublicContentError("No public image found")
    ranked = sorted((page for page in pages.values() if isinstance(page, dict)),
                    key=lambda page: page.get("index", 0))
    for page in ranked:
        infos = page.get("imageinfo")
        info = infos[0] if isinstance(infos, list) and infos and isinstance(infos[0], dict) else {}
        if (url := _image_url(info)):
            return {"url": url, "source": _source(info.get("descriptionurl"))}
    raise PublicContentError("No public image found")


def commons_image(url: str) -> dict[str, str]:
    """A Wikimedia upload URL the model gave, checked."""
    if not (checked := _image_url({"url": url})):
        raise PublicContentError("Images come from Wikimedia Commons uploads only")
    return {"url": checked, "source": "https://commons.wikimedia.org/"}
