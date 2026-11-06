"""Deterministic Nova UI for verified results that no other visual shows.

When the model writes no OpenUI program, a data answer still gets a data view.
Each verified result is classified by the shape of its columns and compiled
here into a map, KPI tiles, a trend, a ranking or a grid bound to its evidence,
without another provider call, and validated by the same Node runtime as model
programs. Results with no recognisable shape get no block.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
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
# Numeric columns that identify or order rows rather than measure them:
# "outlet_id", "visit_order", "rank", but not "order_count".
_KEY = re.compile(
    r"(?:^|_)(?:id|no|nomor|seq|sequence|order|urutan|urut|rank|peringkat|kode|code)$",
    re.IGNORECASE,
)
# A time column names its period first or last: "month", "order_date", "week_start".
_PERIOD = (r"date|day|week|month|quarter|year|period|time|tanggal|tgl|hari|minggu|bulan|"
           r"kuartal|tahun|periode|waktu")
_TEMPORAL_NAME = re.compile(rf"^(?:{_PERIOD})(?:_|$)|_(?:{_PERIOD})$", re.IGNORECASE)
_TEMPORAL_VALUE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?(?:[ T][\d:.+Z-]*)?$")
_SAMPLE = 50
_TREND_MIN_ROWS = 3
_RANKING_MAX_ROWS = 30
_TOP = 10
_MAX_GRID_MEASURES = 4
_MAX_COLUMNS = 50
_MAX_DATA_BLOCKS = 2


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


def kpi_columns(table: dict[str, Any]) -> list[str]:
    """The metrics of a one-row result whose every column is a measure."""
    if (column := kpi_column(table)):
        return [column]
    if len(table.get("rows") or []) != 1:
        return []
    kinds = shape(table)
    measures = kinds["measure"]
    if not measures or len(measures) > _MAX_KPIS or len(measures) != len(_names(table)):
        return []
    return measures


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


def _kind(name: str, values: list[Any]) -> str:
    """temporal, measure, key, category, or empty for a column's sampled values."""
    sample = [value for value in values[:_SAMPLE] if value is not None and value != ""]
    if not sample:
        return "empty"
    if all(isinstance(value, date) or (
        isinstance(value, str) and _TEMPORAL_VALUE.match(value.strip())
    ) for value in sample):
        return "temporal"
    numbers = [_number(value) for value in sample]
    if all(number is not None for number in numbers):
        if _TEMPORAL_NAME.search(name) and all(
            number is not None and number.is_integer() for number in numbers
        ):
            return "temporal"
        return "key" if _KEY.search(name) else "measure"
    return "category"


def shape(table: dict[str, Any]) -> dict[str, list[str]]:
    """Column names grouped by kind, in result order."""
    groups: dict[str, list[str]] = {
        "temporal": [], "measure": [], "key": [], "category": [], "empty": [],
    }
    for name in _names(table):
        groups[_kind(name, _column(table, name))].append(name)
    return groups


def data_program(evidence_id: str, table: dict[str, Any],
                 title: str | None) -> tuple[str, str] | None:
    """A trend, ranking or grid for a verified result, with the reason chosen.

    Returns None for a result with fewer than two rows, too many columns or
    measures, or a shape this composer does not recognise.
    """
    names, rows = _names(table), table.get("rows") or []
    if not rows or not names or len(names) > _MAX_COLUMNS:
        return None
    kinds = shape(table)
    temporal, measures, categories = kinds["temporal"], kinds["measure"], kinds["category"]
    source = f"@Evidence({_text(evidence_id)})"
    if (len(temporal) == 1 and len(measures) == 1 and not categories
            and len(rows) >= _TREND_MIN_ROWS):
        x, y = temporal[0], measures[0]
        heading = _text(title or _label(y))
        return (f"root = Col([Chart(@Sort({source}, {_text(x)}, \"asc\"), {_text(x)}, "
                f"{_text(y)}, \"line\", {heading})])", "trend")
    if len(categories) == 1 and len(measures) == 1 and not temporal and len(rows) >= 2:
        x, y = categories[0], measures[0]
        heading = _text(title or _label(y))
        if len(rows) <= _RANKING_MAX_ROWS:
            return (f"root = Col([Chart(@Sort({source}, {_text(y)}, \"desc\"), {_text(x)}, "
                    f"{_text(y)}, \"bar\", {heading})])", "ranking")
        return (f"root = Col([Chart(@Top({source}, {_text(y)}, {_TOP}), {_text(x)}, "
                f"{_text(y)}, \"bar\", {heading}), "
                f"Caption(\"Highest values within these result rows\"), "
                f"Table({source}, null, {heading})])", "ranking")
    # One row reads better as prose than as a one-line grid.
    if (len(rows) >= 2 and len(measures) <= _MAX_GRID_MEASURES
            and (categories or temporal or kinds["key"])):
        return f"root = Col([Table({source}, null, {_text(title or 'Result')})])", "grid"
    return None


async def _block(program: str, tables: dict[str, dict[str, Any]], fallback: str,
                 auto: str) -> dict[str, Any]:
    result = await parse_program(program, tables=tables, business=True)
    return block_dict(program, fallback, result, tables,
                      provenance={"composer": "deterministic", "auto": auto})


async def compose(
    tables: dict[str, dict[str, Any]],
    summaries: dict[str, str],
    *,
    maps_only: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Data views for an answer no other visual shows.

    ``tables`` are the verified results the answer displays. A located result
    gets a map and nothing else. Otherwise single metrics share KPI tiles, and
    up to two other results each get a trend, ranking or grid. A tool chart
    does not show locations, so ``maps_only`` still draws a located result but
    nothing else. Returns the blocks and the reasons any were skipped.
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
    if maps_only:
        return blocks, skipped

    metrics = [(evidence_id, column, table) for evidence_id, table in tables.items()
               for column in kpi_columns(table)][:_MAX_KPIS]
    if metrics:
        try:
            blocks.append(await _block(
                kpi_program(metrics), {evidence_id: table for evidence_id, _, table in metrics},
                "\n".join(f"**{_label(column)}**" for _, column, _ in metrics), "kpi",
            ))
        except UiValidationError:
            skipped.append("kpi_validation")
    shown_metrics = {evidence_id for evidence_id, _, _ in metrics}
    drawn = 0
    for evidence_id, table in tables.items():
        if evidence_id in shown_metrics or kpi_columns(table) or drawn >= _MAX_DATA_BLOCKS:
            continue
        summary = (summaries.get(evidence_id) or "")[:256] or None
        chosen = data_program(evidence_id, table, summary)
        if chosen is None:
            continue
        program, auto = chosen
        drawn += 1
        fallback = f"**{summary or _label(next(iter(shape(table)['measure']), 'Result'))}**"
        try:
            blocks.append(await _block(program, {evidence_id: table}, fallback, auto))
        except UiValidationError:
            skipped.append(f"{auto}_validation")
    return blocks, skipped
