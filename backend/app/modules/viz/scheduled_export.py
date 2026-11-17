"""Scheduled dashboard exports: one page's charts as an Excel workbook.

A schedule runs on the task scheduler as the role's scheduled-task account
(see ``export_schedules``). Each run reads the published dashboard, queries
every chart on the page through the governed dataset query (so Ranger row
filters and masks apply to that role), and saves one workbook in the
dashboard's shared workspace. Charts get the dashboard filters' default
values, as a person opening the page would see them; nothing is selected.

The query shape mirrors the editor's ``chart-query.ts`` and the filter
defaults mirror ``filter-model.ts``; their unit tests pin the same cases.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4
from xml.etree import ElementTree
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from .contracts.charts import (
    CartesianEncoding,
    CategoryEncoding,
    ChartDefinition,
    DistributionEncoding,
    FlowEncoding,
    HeatmapEncoding,
    HierarchyEncoding,
    MapEncoding,
    ScatterEncoding,
    SingleMeasureEncoding,
    TableEncoding,
)
from .contracts.dashboards import ChartWidget, DashboardDefinition, Page
from .contracts.filters import FieldBinding, FilterDefinition, FilterTree
from .contracts.queries import DatasetQueryRequest, DatasetQueryResult, QueryColumn

SUPPORTED = frozenset({"sum", "avg", "min", "max", "count", "count_distinct"})
MAX_CHARTS = 40
WORKBOOK = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExportUnavailable(Exception):
    """A chart or page cannot be exported; the message is safe to show."""


# ── Query shape ────────────────────────────────────────────────────────────


def _dimension(item: Any) -> dict[str, Any]:
    grain = getattr(item, "date_grain", None)
    field_id = item if isinstance(item, str) else item.field_id
    return {"field_id": field_id, **({"date_grain": grain} if grain else {})}


def query_shape(chart: ChartDefinition) -> dict[str, Any]:
    """Dimensions, measures, sort and limit for a chart, as the editor builds them."""
    encoding = chart.encoding
    dimensions: list[Any] = []
    measures: list[tuple[str, str]] = []
    if isinstance(encoding, CartesianEncoding):
        dimensions = list(encoding.dimensions)
        if encoding.color and all(item.field_id != encoding.color.field_id
                                  for item in encoding.dimensions):
            dimensions.append(encoding.color)
        measures = [(item.field_id, item.aggregation) for item in encoding.measures]
    elif isinstance(encoding, SingleMeasureEncoding):
        measures = [(encoding.measure.field_id, encoding.measure.aggregation)]
        if encoding.comparison:
            measures.append((encoding.comparison.field_id, encoding.comparison.aggregation))
    elif isinstance(encoding, CategoryEncoding):
        dimensions = [encoding.category]
        measures = [(encoding.measure.field_id, encoding.measure.aggregation)]
    elif isinstance(encoding, ScatterEncoding):
        dimensions = [encoding.x, encoding.y, *([encoding.size] if encoding.size else []),
                      *([encoding.color] if encoding.color else [])]
    elif isinstance(encoding, HeatmapEncoding):
        dimensions = [encoding.x, *([encoding.y] if encoding.y else [])]
        measures = [(encoding.value.field_id, encoding.value.aggregation)]
    elif isinstance(encoding, DistributionEncoding):
        dimensions = [encoding.field_id, *([encoding.group] if encoding.group else [])]
        measures = [(encoding.field_id, "count")]
    elif isinstance(encoding, TableEncoding):
        dimensions = [*encoding.dimensions, *encoding.pivot_columns]
        measures = [(item.field_id, item.aggregation) for item in encoding.measures]
    elif isinstance(encoding, HierarchyEncoding):
        dimensions = list(encoding.levels)
        measures = [(encoding.value.field_id, encoding.value.aggregation)]
    elif isinstance(encoding, FlowEncoding):
        dimensions = [encoding.source, encoding.target]
        measures = [(encoding.value.field_id, encoding.value.aggregation)]
    elif isinstance(encoding, MapEncoding):
        dimensions = [encoding.latitude, encoding.longitude,
                      *([encoding.group] if encoding.group else [])]
        measures = ([(encoding.value.field_id, encoding.value.aggregation)] if encoding.value
                    else [(encoding.latitude, "count")])
    if not dimensions and not measures:
        raise ExportUnavailable("A chart has no fields")
    if len(dimensions) > 8:
        raise ExportUnavailable("A chart groups by more than eight fields")
    if any(aggregation not in SUPPORTED for _, aggregation in measures):
        raise ExportUnavailable("A chart uses an aggregation exports do not support")
    dims = [_dimension(item) for item in dimensions]
    dim_ids = [item["field_id"] for item in dims]

    def key(field_id: str, aggregation: str | None) -> str | None:
        for index, (measure, kind) in enumerate(measures):
            if measure == field_id and (not aggregation or kind == aggregation):
                return f"m{index}"
        return f"d{dim_ids.index(field_id)}" if field_id in dim_ids else None

    sort = [{"column": column, "direction": item.direction or "asc"}
            for item in chart.sort
            if (column := key(item.field_id, item.aggregation))]
    category_limit = None
    if isinstance(encoding, CategoryEncoding):
        category_limit = encoding.max_categories or 12
        if not sort:
            sort.append({"column": "m0", "direction": "desc"})
    return {
        "dimensions": dims,
        "measures": [{"field_id": field_id, "aggregation": aggregation}
                     for field_id, aggregation in measures],
        "sort": sort[:8],
        "limit": min(category_limit or chart.row_limit or 1000, 10_000),
    }


# ── Dashboard filter defaults ──────────────────────────────────────────────


def _same(binding: FieldBinding, chart: ChartDefinition) -> bool:
    return (binding.dataset_id == chart.dataset_id
            and binding.dataset_revision == chart.dataset_revision)


def _target(item: FilterDefinition, chart: ChartDefinition) -> FieldBinding | None:
    if item.field and _same(item.field, chart):
        return item.field
    return next((mapping.target for mapping in item.mappings if _same(mapping.target, chart)), None)


def _in_scope(item: FilterDefinition, widget: UUID, page: UUID) -> bool:
    if item.scope.kind == "global":
        return True
    if item.scope.kind == "page":
        return item.scope.page_id == page
    return widget in item.scope.widget_ids


def _predicate(field: FieldBinding, op: str, values: Sequence[Any] = (),
               **extra: Any) -> dict[str, Any]:
    return {"type": "predicate", "field": field.model_dump(mode="json"), "op": op,
            "values": [value.model_dump(mode="json") for value in values],
            "source": "global", "stage": "row", **extra}


def _next_day(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def _default(item: FilterDefinition, field: FieldBinding, timezone: str) -> dict[str, Any] | None:
    defaults = list(item.defaults)
    if item.kind == "relative_date" and item.relative:
        return _predicate(field, "relative_date", relative={
            "unit": item.relative.unit, "count": item.relative.count or 1,
            "mode": item.relative.mode or "rolling", "timezone": timezone,
            "week_start": "iso", "fiscal_start_month": 1,
        })
    if not defaults:
        return None
    if item.kind in {"date_range", "number_range_slider"}:
        lower, upper = defaults[0], defaults[1] if len(defaults) > 1 else None
        parts = [_predicate(field, "gte", [lower])]
        if upper is not None:
            if upper.type == "date":
                parts.append(_predicate(field, "lt", [upper.model_copy(
                    update={"value": _next_day(str(upper.value))})]))
            else:
                parts.append(_predicate(field, "lte", [upper]))
        return parts[0] if len(parts) == 1 else {"type": "and", "children": parts}
    if item.kind == "text_entry" and defaults[0].type == "string":
        text = str(defaults[0].value).strip()
        return _predicate(field, "like_literal", [defaults[0].model_copy(
            update={"value": text})]) if text else None
    if item.kind == "boolean_switch" and defaults[0].type == "boolean":
        return _predicate(field, "eq", [defaults[0]])
    return _predicate(field, "eq" if len(defaults) == 1 else "in", defaults)


def default_context(dashboard: DashboardDefinition, chart: ChartDefinition, widget: UUID,
                    page: UUID, timezone: str) -> dict[str, Any] | None:
    """The dashboard filters' default values that apply to one chart."""
    parts: list[dict[str, Any]] = []
    for item in dashboard.filters:
        if not _in_scope(item, widget, page):
            continue
        field = _target(item, chart)
        expression = field and _default(item, field, timezone)
        if expression:
            parts.append(expression)
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {"type": "and", "children": parts}


def chart_request(dashboard: DashboardDefinition, chart: ChartDefinition, widget: UUID,
                  page: UUID, timezone: str) -> DatasetQueryRequest:
    shape = query_shape(chart)
    trees = [tree for tree in (
        chart.filters.expression.model_dump(mode="json") if chart.filters else None,
        default_context(dashboard, chart, widget, page, timezone),
    ) if tree]
    filters = None
    if trees:
        filters = FilterTree.model_validate(
            {"expression": trees[0] if len(trees) == 1 else {"type": "and", "children": trees}})
    return DatasetQueryRequest(revision=chart.dataset_revision, draft=False, filters=filters,
                               **shape)


# ── Workbook ───────────────────────────────────────────────────────────────

_WORDS = {"sum": "Sum of", "avg": "Average", "min": "Minimum", "max": "Maximum",
          "count": "Count of", "count_distinct": "Distinct"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def humanize(name: str) -> str:
    if re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+", name):
        name = name.rsplit(".", 1)[1]
    if not re.fullmatch(r"[a-z0-9_]+", name):
        return name
    words = " ".join(part for part in name.split("_") if part)
    return words[:1].upper() + words[1:] if words else name


def column_label(column: QueryColumn) -> str:
    if column.aggregation == "count" and not column.field_id:
        return "Rows"
    name = humanize(column.name)
    word = _WORDS.get(column.aggregation or "")
    return f"{word} {name[:1].lower()}{name[1:]}" if word else name


def cell_value(cell: Any, column: QueryColumn) -> str | float | bool | None:
    """Numbers stay numbers; dates keep the chart's grain label; nothing else is coerced."""
    if cell is None:
        return None
    kind, value = cell.type, cell.value
    if kind == "boolean":
        return bool(value)
    if kind in {"integer", "decimal", "float"}:
        number = float(value)
        # Beyond 15 significant digits a spreadsheet would round; keep the text.
        if kind != "float" and len(str(value).replace("-", "").replace(".", "").lstrip("0")) > 15:
            return str(value)
        return number
    if kind in {"date", "timestamp"} and column.date_grain:
        year, month = int(value[:4]), int(value[5:7])
        day = int(value[8:10])
        if column.date_grain == "year":
            return str(year)
        if column.date_grain == "quarter":
            return f"Q{(month - 1) // 3 + 1} {year}"
        if column.date_grain == "month":
            return f"{_MONTHS[month - 1]} {year}"
        return f"{_MONTHS[month - 1]} {day}, {year}"
    return str(value)


def sheet_names(titles: Sequence[str]) -> list[str]:
    """At most 31 characters, none of []:*?/\\ and unique in the workbook."""
    used: set[str] = set()
    names: list[str] = []
    for title in titles:
        base = re.sub(r"[\[\]:*?/\\]", " ", title).strip().strip("'")[:31].rstrip() or "Chart"
        name, number = base, 2
        while name.casefold() in used:
            suffix = f" ({number})"
            name = base[:31 - len(suffix)].rstrip() + suffix
            number += 1
        used.add(name.casefold())
        names.append(name)
    return names


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _text(value: str) -> str:
    # Text a spreadsheet would run as a formula stays text (OWASP CSV injection).
    if value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        value = "'" + value
    return escape(_CONTROL.sub("", value))


def _sheet(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = []
    for r, row in enumerate([list(headers), *rows], start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{_column(c)}{r}"
            if value is None:
                continue
            if isinstance(value, bool):
                cells.append(f'<c r="{ref}" t="b"><v>{int(value)}</v></c>')
            elif isinstance(value, float | int):
                cells.append(f'<c r="{ref}"><v>{value!r}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                             f"{_text(str(value))}</t></is></c>")
        lines.append(f'<row r="{r}">{"".join(cells)}</row>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(lines)}</sheetData></worksheet>')


RECEIPT_PART = "docProps/custom.xml"
_PROPERTIES = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VALUE_TYPES = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
#: Custom document properties: the run that saved the file and a digest of its content.
RUN_PROPERTY = "Nova scheduled run"
DIGEST_PROPERTY = "Nova content SHA-256"


def content_digest(parts: Mapping[str, bytes]) -> str:
    """SHA-256 over every part but the receipt, by part name, so any change shows."""
    digest = hashlib.sha256()
    for name in sorted(parts):
        if name == RECEIPT_PART:
            continue
        for chunk in (name.encode(), parts[name]):
            digest.update(len(chunk).to_bytes(8, "big"))
            digest.update(chunk)
    return digest.hexdigest()


def _receipt(run: str, digest: str) -> str:
    properties = "".join(
        f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{pid}" '
        f'name="{name}"><vt:lpwstr>{escape(value)}</vt:lpwstr></property>'
        for pid, (name, value) in enumerate(((RUN_PROPERTY, run), (DIGEST_PROPERTY, digest)),
                                            start=2))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Properties xmlns="{_PROPERTIES}" xmlns:vt="{_VALUE_TYPES}">{properties}'
            "</Properties>")


def read_receipt(data: bytes) -> tuple[dict[str, str], str] | None:
    """A saved workbook's receipt properties and its recomputed content digest.

    ``None`` when the file is not a readable workbook: a truncated or damaged
    archive, or a part whose checksum no longer matches.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.testzip() is not None:
                return None
            parts = {name: archive.read(name) for name in archive.namelist()}
        if "xl/workbook.xml" not in parts:
            return None
        properties: dict[str, str] = {}
        if RECEIPT_PART in parts:
            root = ElementTree.fromstring(parts[RECEIPT_PART])
            for item in root.findall(f"{{{_PROPERTIES}}}property"):
                value = item.find(f"{{{_VALUE_TYPES}}}lpwstr")
                properties[item.get("name", "")] = (value.text or "") if value is not None else ""
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ElementTree.ParseError, ValueError,
            EOFError, OSError):
        return None
    return properties, content_digest(parts)


def workbook(sheets: Sequence[tuple[str, Sequence[str], Sequence[Sequence[Any]]]], *,
             run: str | None = None) -> bytes:
    """A minimal Office Open XML workbook: one sheet per (name, headers, rows).

    ``run`` records which scheduled run saved the file, with a digest of the
    content, as custom document properties, so a retry can recognise its file.
    """
    if not sheets:
        raise ExportUnavailable("The page has no charts to export")
    names = sheet_names([name for name, _, _ in sheets])
    main = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    parts: dict[str, str] = {}
    parts["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1))
        + ('<Override PartName="/docProps/custom.xml" ContentType="application/'
           'vnd.openxmlformats-officedocument.custom-properties+xml"/>' if run else "")
        + "</Types>")
    parts["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{main}/officeDocument" Target="xl/workbook.xml"/>'
        + (f'<Relationship Id="rId2" Type="{main}/custom-properties" '
           'Target="docProps/custom.xml"/>' if run else "")
        + "</Relationships>")
    parts["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'xmlns:r="{main}"><sheets>'
        + "".join(f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i}" '
                  f'r:id="rId{i}"/>' for i, name in enumerate(names, start=1))
        + "</sheets></workbook>")
    parts["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i}" Type="{main}/worksheet" '
                  f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
        + "</Relationships>")
    for i, (_, headers, rows) in enumerate(sheets, start=1):
        parts[f"xl/worksheets/sheet{i}.xml"] = _sheet(headers, rows)
    encoded = {name: text.encode("utf-8") for name, text in parts.items()}
    if run:
        encoded[RECEIPT_PART] = _receipt(run, content_digest(encoded)).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in encoded.items():
            archive.writestr(name, body)
    return buffer.getvalue()


_CALCULATION_LABELS = {
    "running_total": "Running total", "percent_of_total": "Percent of total",
    "difference": "Difference from previous",
    "percent_difference": "Percent difference from previous",
    "rank": "Rank", "dense_rank": "Dense rank", "moving_average": "Moving average",
}
_PERCENT = {"percent_of_total", "percent_difference"}


def _number(cell: Any) -> float | None:
    if cell is None or cell.type not in {"integer", "decimal", "float"}:
        return None
    return float(cell.value)


def _order_key(cell: Any) -> tuple[int, Any]:
    if cell is None:
        return (1, "")
    number = _number(cell)
    return (0, number) if number is not None else (0, str(cell.value))


def apply_calculations(chart: ChartDefinition, result: DatasetQueryResult) -> list[list[Any]]:
    """The chart's table calculations over its rows, as the editor shows them.

    Mirrors ``calculations.ts``: along the addressing fields, per partition;
    ties share a rank; a moving average uses previous rows in order. Returns
    the cells with calculated values as plain numbers, so the workbook and the
    screen agree. Column names change in ``calculated_labels``.
    """
    rows: list[list[Any]] = [list(row) for row in result.rows]
    columns = result.columns
    for calculation in chart.table_calculations:
        target = next((i for i, column in enumerate(columns) if column.role == "measure"
                       and column.field_id == calculation.measure.field_id
                       and column.aggregation == calculation.measure.aggregation), None)

        def at(field: str) -> int | None:
            return next((i for i, column in enumerate(columns)
                         if column.role == "dimension" and column.field_id == field), None)

        order = [(at(item.field_id), item.direction == "desc") for item in calculation.order]
        partition = [at(field) for field in calculation.partition_by]
        if target is None or any(i is None for i, _ in order) or any(i is None for i in partition):
            continue
        raw: list[float | None] = []
        for row in rows:
            cell = row[target]
            number = float(cell) if isinstance(cell, int | float) else _number(cell)
            raw.append(0.0 if number is None and calculation.nulls == "zero" else number)
        groups: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            key = json.dumps([row[i].model_dump(mode="json") if row[i] is not None else None
                              for i in partition if i is not None])
            groups.setdefault(key, []).append(index)
        for members in groups.values():
            ordered = list(members)
            for column_index, desc in reversed(order):
                if column_index is None:
                    continue

                def sort_key(member: int, i: int = column_index) -> tuple[int, Any]:
                    return _order_key(rows[member][i])

                ordered.sort(key=sort_key, reverse=desc)
            present: list[float] = [n for n in (raw[i] for i in ordered) if n is not None]
            total = sum(present)
            ranked = sorted(set(present), reverse=True)
            running = 0.0
            for position, index in enumerate(ordered):
                own = raw[index]
                previous = raw[ordered[position - 1]] if position else None
                value: float | None = None
                kind = calculation.kind
                if kind == "running_total":
                    if own is not None:
                        running += own
                    value = None if own is None else running
                elif kind == "percent_of_total":
                    value = None if own is None or total == 0 else own / total * 100
                elif kind == "difference":
                    value = None if own is None or previous is None else own - previous
                elif kind == "percent_difference":
                    value = (None if own is None or previous is None or previous == 0
                             else (own - previous) / abs(previous) * 100)
                elif kind == "rank":
                    value = None if own is None else 1.0 + len([n for n in present if n > own])
                elif kind == "dense_rank":
                    value = None if own is None else 1 + ranked.index(own)
                elif kind == "moving_average":
                    size = calculation.window_size or 3
                    span = ordered[max(0, position - size + 1):position + 1]
                    window = [n for n in (raw[m] for m in span) if n is not None]
                    value = None if own is None or not window else sum(window) / len(window)
                rows[index][target] = None if value is None else round(value, 9)
    return rows


def calculated_label(chart: ChartDefinition, column: QueryColumn) -> str | None:
    for calculation in chart.table_calculations:
        if (column.role == "measure" and column.field_id == calculation.measure.field_id
                and column.aggregation == calculation.measure.aggregation):
            window = f" ({calculation.window_size})" if calculation.kind == "moving_average" else ""
            percent = " (%)" if calculation.kind in _PERCENT else ""
            name, field = _CALCULATION_LABELS[calculation.kind], humanize(column.name)
            return f"{name}{window} of {field[:1].lower()}{field[1:]}{percent}"
    return None


def result_sheet(title: str, result: DatasetQueryResult,
                 chart: ChartDefinition | None = None) -> tuple[str, list[str], list[list[Any]]]:
    columns = result.columns
    rows = apply_calculations(chart, result) if chart and chart.table_calculations else result.rows
    headers = [(calculated_label(chart, column) if chart else None) or column_label(column)
               for column in columns]
    return (title, headers,
            [[cell if isinstance(cell, int | float) and not isinstance(cell, bool)
              else cell_value(cell, columns[i]) for i, cell in enumerate(row)] for row in rows])


# ── One run ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExportTarget:
    dashboard_id: UUID
    page_id: UUID

    @property
    def record_id(self) -> str:
        return f"{self.dashboard_id}:{self.page_id}:xlsx"

    @classmethod
    def parse(cls, record_id: str) -> ExportTarget:
        try:
            dashboard, page, file_format = record_id.split(":")
            if file_format != "xlsx":
                raise ValueError(file_format)
            return cls(UUID(dashboard), UUID(page))
        except ValueError:
            raise ExportUnavailable("Unknown export schedule") from None


def record_for(dashboard: UUID, page: UUID, timezone: str) -> str:
    """The schedule's record: dashboard, page, format and the time zone it runs in."""
    return f"{ExportTarget(dashboard, page).record_id}:{timezone}"


def parse_record(record_id: str) -> tuple[ExportTarget, str]:
    head, _, timezone = record_id.rpartition(":")
    try:
        ZoneInfo(timezone)
    except (ValueError, KeyError):
        raise ExportUnavailable("Unknown export schedule") from None
    return ExportTarget.parse(head), timezone


def file_name(title: str, page: str, when: datetime, tag: str = "") -> str:
    base = re.sub(r"[^\w \-]+", " ", f"{title} {page}".strip(), flags=re.UNICODE)
    base = re.sub(r"\s+", " ", base).strip()[:80] or "Dashboard"
    return f"{base} {when:%Y-%m-%d %H%M}{f' {tag}' if tag else ''}.xlsx"


FOLDER = "Scheduled exports"


@dataclass(frozen=True)
class Occurrence:
    """One scheduled run, as the task scheduler persisted it before the run started.

    ``key`` is the durable run identity every retry keeps; ``due`` is the time the
    scheduler recorded for it. The file is named from ``due`` and carries ``key``.
    """

    key: str
    due: datetime

    @property
    def tag(self) -> str:
        """A short form of the key, to tell apart two runs due in the same minute."""
        return re.sub(r"[^0-9a-z]", "", self.key.lower())[:8] or "run"

    def names(self, timezone: str) -> tuple[str, str]:
        """The date stamp in file names, and its form with the run's tag."""
        stamp = f"{self.due.astimezone(ZoneInfo(timezone)):%Y-%m-%d %H%M}"
        return f" {stamp}.xlsx", f" {stamp} {self.tag}.xlsx"


Check = Literal["saved", "other", "damaged"]


def check_saved(entry: Mapping[str, Any], data: bytes, occurrence: Occurrence) -> Check:
    """Whether a file already in the folder is this run's complete workbook.

    ``saved``: its size matches what the workspace recorded, every part passes
    its checksum, it names this run and its content digest matches the one it
    carries. ``other``: an intact workbook saved by another run or by hand.
    ``damaged``: anything else, which is never reported as this run's file.
    """
    if not data or int(entry.get("size_bytes") or 0) != len(data):
        return "damaged"
    receipt = read_receipt(data)
    if receipt is None:
        return "damaged"
    properties, digest = receipt
    if properties.get(RUN_PROPERTY) != occurrence.key:
        return "other"
    return "saved" if properties.get(DIGEST_PROPERTY) == digest else "damaged"


async def find_saved(namespace: str, occurrence: Occurrence,
                     timezone: str) -> tuple[dict[str, Any], Check] | None:
    """This run's file in the export folder, if a previous attempt saved one.

    Only files dated with this run's due time can be its own. The run's file
    wins; otherwise a damaged candidate is reported so the run stops.
    """
    from app.modules.workspaces.service import workspace_service

    endings = occurrence.names(timezone)
    damaged: dict[str, Any] | None = None
    for entry in await workspace_service.list_entries(namespace):
        name = str(entry.get("name") or "")
        if (entry.get("entry_type") != "file" or entry.get("parent_path") != FOLDER
                or not name.endswith(endings)):
            continue
        _, data = await workspace_service.read_entry_bytes(namespace, str(entry["id"]))
        verdict = check_saved(entry, data, occurrence)
        if verdict == "saved":
            return entry, verdict
        if verdict == "damaged":
            damaged = damaged or entry
    return (damaged, "damaged") if damaged is not None else None


def _saved(entry: Mapping[str, Any], *, existing: bool) -> dict[str, Any]:
    return {"status": "saved", "entry_id": str(entry.get("id", "")),
            "bytes": int(entry.get("size_bytes") or 0), "existing": existing}


def _damaged(entry: Mapping[str, Any]) -> ExportUnavailable:
    return ExportUnavailable(
        f"The file {entry.get('name')} in {FOLDER} has this run's date but is incomplete or "
        "was changed, so the run was not marked done. The next scheduled run saves its own "
        "file.")


async def page_workbook(user: Mapping[str, Any], target: ExportTarget, *,
                        timezone: str,
                        run: str | None = None) -> tuple[DashboardDefinition, Page, bytes]:
    """Query every chart on the page as ``user`` and build the workbook."""
    from .dataset_service import native_dataset_service as datasets
    from .document_service import native_document_service as documents

    snapshot = await documents.read(user, target.dashboard_id, draft=False)
    dashboard = snapshot["definition"]
    if not isinstance(dashboard, DashboardDefinition):
        raise ExportUnavailable("Unknown export schedule")
    page = next((item for item in dashboard.pages if item.page_id == target.page_id), None)
    if page is None:
        raise ExportUnavailable("The scheduled page no longer exists")
    sheets = []
    for widget in [item for item in page.widgets if isinstance(item, ChartWidget)][:MAX_CHARTS]:
        chart_snapshot = await documents.read(user, widget.chart_ref.chart_id, draft=False,
                                              revision=widget.chart_ref.revision)
        chart = chart_snapshot["definition"]
        if not isinstance(chart, ChartDefinition):
            continue
        request = chart_request(dashboard, chart, widget.widget_id, page.page_id, timezone)
        result = await datasets.query(user, chart.dataset_id, request)
        sheets.append(result_sheet(widget.title or chart.title, result, chart))
    return dashboard, page, workbook(sheets, run=run)


async def run(record_id: str, user: Mapping[str, Any], *,
              occurrence: Occurrence | None = None,
              now: datetime | None = None) -> dict[str, Any]:
    """``_run``, counted by bounded outcome for operators."""
    from app.observability.metrics import dataviz_export

    try:
        result = await _run(record_id, user, occurrence=occurrence, now=now)
    except ExportUnavailable:
        dataviz_export("unavailable")
        raise
    except Exception:
        dataviz_export("error")
        raise
    dataviz_export("existing" if result.get("existing") else "saved")
    return result


async def _run(record_id: str, user: Mapping[str, Any], *,
               occurrence: Occurrence | None = None,
               now: datetime | None = None) -> dict[str, Any]:
    """One scheduled run: save the page's workbook once for its occurrence.

    The occurrence is persisted by the task scheduler before the run starts, and
    the file is named from it and carries its key. A retry after a crash (before
    or after the save, in the same minute or later) first looks for that file:
    a verified one is reported as saved without writing again, and a damaged one
    fails the run instead of being reported. ``occurrence`` is absent only for a
    direct call, which is then its own one-off occurrence.
    """
    from app.core.exceptions import StorageError
    from app.modules.workspaces.actor import acting_as
    from app.modules.workspaces.service import workspace_service
    from app.modules.workspaces.spaces import space_service

    from .document_service import native_document_service as documents

    target, timezone = parse_record(record_id)
    occurrence = occurrence or Occurrence(key=str(uuid4()), due=now or datetime.now(UTC))
    # Access can change after the schedule was saved: every run needs Export again.
    if "export" not in await documents.permissions(user, target.dashboard_id):
        raise ExportUnavailable("This role's access to the dashboard no longer includes Export")
    workspace = await documents.workspace_of(user, target.dashboard_id)
    if workspace == "personal":
        raise ExportUnavailable("Scheduled exports need a dashboard in a shared workspace")
    space = await space_service.resolve(dict(user), workspace, edit=True)
    with acting_as(dict(user)):
        found = await find_saved(space.namespace, occurrence, timezone)
    if found is not None:
        entry, verdict = found
        if verdict != "saved":
            raise _damaged(entry)
        return _saved(entry, existing=True)
    dashboard, page, data = await page_workbook(user, target, timezone=timezone,
                                                run=occurrence.key)
    when = occurrence.due.astimezone(ZoneInfo(timezone))
    with acting_as(dict(user)):
        try:
            await workspace_service.create_folder(space.namespace, "", FOLDER)
        except StorageError as exc:
            if exc.status_code != 409:
                raise
        for tag in ("", occurrence.tag):
            try:
                entry = await workspace_service.create_file_bytes(
                    space.namespace, FOLDER, file_name(dashboard.title, page.title, when, tag),
                    data)
            except StorageError as exc:
                if exc.status_code != 409:
                    raise
                # The name is taken: by this run's own concurrent attempt, by a
                # damaged file, or by another run's file (then use the tagged name).
                found = await find_saved(space.namespace, occurrence, timezone)
                if found is not None:
                    entry, verdict = found
                    if verdict != "saved":
                        raise _damaged(entry) from None
                    return _saved(entry, existing=True)
                continue
            return {"status": "saved", "entry_id": str(entry.get("id", "")),
                    "bytes": len(data), "existing": False}
    raise ExportUnavailable(
        f"Files with this run's names are already in {FOLDER} and belong to other runs, "
        "so nothing was saved. Rename or remove them before the next scheduled run.")
