"""Deterministic Nova UI for verified results that no other visual shows.

The model writes an OpenUI program only when an answer needs a composed view,
and Studio draws every tool chart with the same UI components. A located result
or a single metric in an otherwise plain answer is compiled here into a map or
KPI from verified evidence, without another provider call, and validated by the
same Node runtime as model programs.
"""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from typing import Any

from app.modules.assistant.intelligent_ui import UiValidationError, block_dict, parse_program

_LATITUDE = ("latitude", "lat", "lintang")
_LONGITUDE = ("longitude", "lon", "lng", "long", "bujur")
_GEOMETRY = re.compile(
    r"^\s*(?:SRID=\d+;\s*)?(?:MULTI)?(?:POINT|LINESTRING|POLYGON)\s*\(", re.IGNORECASE
)
_GEOJSON = re.compile(r'^\s*\{\s*"type"\s*:\s*"(?:Point|LineString|Polygon)"')
_LABEL = re.compile(r"name|nama|label|title|judul", re.IGNORECASE)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_MAX_KPIS = 4
_POPUP_FIELDS = 6


def _text(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _names(table: dict[str, Any]) -> list[str]:
    return [str(c.get("name", "")) if isinstance(c, dict) else str(c)
            for c in table.get("columns") or []]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, int | float | Decimal) and not (
        isinstance(value, str) and _NUMBER.fullmatch(value.strip())
    ):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _column(table: dict[str, Any], name: str) -> list[Any]:
    index = _names(table).index(name)
    return [row[index] if isinstance(row, list | tuple) and index < len(row)
            else row.get(name) if isinstance(row, dict) else None
            for row in table.get("rows") or []]


def _label(name: str) -> str:
    text = name.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else name


def _coordinate(names: list[str], aliases: tuple[str, ...]) -> str | None:
    lowered = {name.lower(): name for name in names}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    for alias in aliases:
        for low, name in lowered.items():
            if low.endswith("_" + alias) or low.startswith(alias + "_"):
                return name
    return None


def _located(latitudes: list[Any], longitudes: list[Any]) -> bool:
    for lat_value, lon_value in zip(latitudes, longitudes, strict=False):
        lat, lon = _number(lat_value), _number(lon_value)
        if (lat is not None and lon is not None and -90 <= lat <= 90
                and -180 <= lon <= 180 and (lat, lon) != (0, 0)):
            return True
    return False


def map_program(evidence_id: str, table: dict[str, Any], title: str) -> str | None:
    """A governed GeoMap for a result that carries coordinates or shapes."""
    names = _names(table)
    if not table.get("rows"):
        return None
    latitude, longitude = _coordinate(names, _LATITUDE), _coordinate(names, _LONGITUDE)
    geometry = None
    if latitude and longitude:
        if not _located(_column(table, latitude), _column(table, longitude)):
            return None
    else:
        latitude = longitude = None
        geometry = next((
            name for name in names
            if any(isinstance(value, str) and (_GEOMETRY.match(value) or _GEOJSON.match(value))
                   for value in _column(table, name)[:20])
        ), None)
        if geometry is None:
            return None
    located = {latitude, longitude, geometry}
    rest = [name for name in names if name not in located]
    label = next((name for name in rest if _LABEL.search(name)), None) or next(
        (name for name in rest
         if all(_number(value) is None for value in _column(table, name)[:20])), None,
    )
    popup = [name for name in rest if name != label][:_POPUP_FIELDS]
    options = "{popupFields: [" + ", ".join(_text(name) for name in popup) + "]}" if popup else "{}"
    args = [
        f"@Evidence({_text(evidence_id)})",
        _text(longitude) if longitude else "null",
        _text(latitude) if latitude else "null",
        _text(geometry) if geometry else "null",
        _text(label) if label else "null",
        _text(title or "Locations"),
        "$selected", options, "$viewport",
    ]
    return ("$selected = -1\n$viewport = null\n"
            f"root = Col([GeoMap({', '.join(args)})])")


def kpi_column(table: dict[str, Any]) -> str | None:
    """The single numeric column of a one-row, one-column result."""
    names, rows = _names(table), table.get("rows") or []
    if len(names) != 1 or len(rows) != 1:
        return None
    return names[0] if _number(_column(table, names[0])[0]) is not None else None


def kpi_program(metrics: list[tuple[str, str, dict[str, Any]]]) -> str:
    tiles = []
    for evidence_id, column, table in metrics:
        value = _number(_column(table, column)[0])
        digits = 0 if value is not None and value.is_integer() else 2
        tiles.append(
            f"Kpi(@Evidence({_text(evidence_id)}), {_text(column)}, "
            f"{_text(_label(column))}, {digits})"
        )
    if len(tiles) == 1:
        return f"root = Col([{tiles[0]}])"
    return f"root = Col([Grid([{', '.join(tiles)}], {len(tiles)})])"


async def _block(program: str, tables: dict[str, dict[str, Any]], fallback: str,
                 auto: str) -> dict[str, Any]:
    result = await parse_program(program, tables=tables, business=True)
    return block_dict(program, fallback, result, tables,
                      provenance={"composer": "deterministic", "auto": auto})


async def compose(
    tables: dict[str, dict[str, Any]],
    summaries: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """A map, or else KPI tiles, for an answer no other visual shows.

    ``tables`` are the verified results the answer displays. Returns the blocks
    and the reasons any were skipped.
    """
    blocks: list[dict[str, Any]] = []
    skipped: list[str] = []
    for evidence_id, table in tables.items():
        title = (summaries.get(evidence_id) or "Locations")[:256]
        program = map_program(evidence_id, table, title)
        if program is None:
            continue
        try:
            blocks.append(await _block(program, {evidence_id: table}, f"**{title}**", "map"))
        except UiValidationError:
            skipped.append("map_validation")
        return blocks, skipped

    metrics = [(evidence_id, column, table) for evidence_id, table in tables.items()
               if (column := kpi_column(table))][:_MAX_KPIS]
    if metrics:
        try:
            blocks.append(await _block(
                kpi_program(metrics), {evidence_id: table for evidence_id, _, table in metrics},
                "\n".join(f"**{_label(column)}**" for _, column, _ in metrics), "kpi",
            ))
        except UiValidationError:
            skipped.append("kpi_validation")
    return blocks, skipped
