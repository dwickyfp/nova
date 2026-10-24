"""Show Me — chart-type requirements, ranking and field placement.

Each chart template declares the pills it needs; the ranker orders the
templates a set of classified fields can satisfy, and the placement engine
resolves a chart intent (from the assistant or a user's pick) into a full
worksheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.modules.viz.spec import (
    ChartIntent,
    FieldRef,
    FieldType,
    MarksCard,
    MarkType,
    WorksheetSpec,
)

PillKind = Literal["dimension", "measure", "date"]


@dataclass(frozen=True)
class Requirement:
    """One ShowMe instruction row: a pill kind, optionally satisfied."""

    kind: PillKind
    optional: bool = False

    def satisfied_by(self, pill: FieldRef) -> bool:
        if self.kind == "measure":
            return pill.is_measure
        if self.kind == "date":
            return not pill.is_measure and pill.field_type == "temporal"
        return not pill.is_measure and pill.field_type != "temporal"


@dataclass(frozen=True)
class ChartTemplate:
    """One ShowMe tile: its id, mark, and the fields it needs."""

    id: str
    label: str
    mark: MarkType
    requirements: tuple[Requirement, ...]

    def unmet(self, fields: list[FieldRef]) -> list[Requirement]:
        available = list(fields)
        unmet: list[Requirement] = []
        for requirement in self.requirements:
            for index, pill in enumerate(available):
                if requirement.satisfied_by(pill):
                    available.pop(index)
                    break
            else:
                if not requirement.optional:
                    unmet.append(requirement)
        return unmet


#: The chart catalog. Ordering matters: it is the preference order for
#: equally-valid suggestions (tables first when only dimensions exist, then
#: heatmaps, bars, lines, ...). Types Nova cannot render yet are omitted.
SHOWME_TYPES: tuple[ChartTemplate, ...] = (
    ChartTemplate(
        id="text_table",
        label="Text table",
        mark="text",
        requirements=(Requirement("dimension"), Requirement("measure")),
    ),
    ChartTemplate(
        id="heatmap",
        label="Highlight table",
        mark="text",
        requirements=(
            Requirement("dimension"),
            Requirement("dimension"),
            Requirement("measure"),
        ),
    ),
    ChartTemplate(
        id="bar",
        label="Bars",
        mark="bar",
        requirements=(Requirement("dimension"), Requirement("measure")),
    ),
    ChartTemplate(
        id="bar_stacked",
        label="Stacked bars",
        mark="bar",
        requirements=(
            Requirement("dimension"),
            Requirement("dimension", optional=True),
            Requirement("measure"),
        ),
    ),
    ChartTemplate(
        id="bar_side",
        label="Side-by-side bars",
        mark="bar",
        requirements=(
            Requirement("dimension"),
            Requirement("dimension"),
            Requirement("measure"),
        ),
    ),
    ChartTemplate(
        id="line",
        label="Lines",
        mark="line",
        requirements=(Requirement("date"), Requirement("measure")),
    ),
    ChartTemplate(
        id="area",
        label="Area",
        mark="area",
        requirements=(Requirement("date"), Requirement("measure")),
    ),
    ChartTemplate(
        id="scatter",
        label="Scatter",
        mark="circle",
        requirements=(Requirement("measure"), Requirement("measure")),
    ),
    ChartTemplate(
        id="pie",
        label="Pie",
        mark="pie",
        requirements=(Requirement("dimension"), Requirement("measure")),
    ),
    ChartTemplate(
        id="bubble",
        label="Circles",
        mark="circle",
        requirements=(
            Requirement("dimension"),
            Requirement("measure"),
            Requirement("measure"),
        ),
    ),
)

SHOWME_BY_ID = {item.id: item for item in SHOWME_TYPES}


def classify_fields(columns: list[str], rows: list[list[Any]]) -> list[FieldRef]:
    """Classify result columns into field pills.

    Numbers become measures with
    their default aggregation, everything else becomes dimensions; date-shaped
    text or date values become temporal dimensions. The first non-null sample
    decides; an all-null column is a dimension.
    """
    pills: list[FieldRef] = []
    for index, column in enumerate(columns):
        values = [row[index] for row in rows[:50] if index < len(row) and row[index] is not None]
        field_type = _classify(values)
        if field_type == "quantitative":
            pills.append(
                FieldRef(
                    field=column,
                    field_type="quantitative",
                    aggregation="sum",
                    discrete=False,
                )
            )
        else:
            pills.append(FieldRef(field=column, field_type=field_type))
    return pills


def _classify(values: list[Any]) -> FieldType:
    if not values:
        return "nominal"
    if all(_is_number(value) for value in values):
        return "quantitative"
    if all(_is_date_like(value) for value in values):
        return "temporal"
    return "nominal"


def _is_number(value: Any) -> bool:
    from decimal import Decimal

    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return False
        try:
            float(stripped)
        except ValueError:
            return False
        return True
    return False


_DATE_PREFIXES = ("20", "19")


def _is_date_like(value: Any) -> bool:
    import re

    if isinstance(value, __import__("datetime").date):
        return True
    if not isinstance(value, str):
        return False
    return bool(
        re.match(r"^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2})?)?$", value.strip())
        and value.strip()[:2] in _DATE_PREFIXES
    )


def rank(
    pills: list[FieldRef],
) -> list[tuple[ChartTemplate, list[Requirement]]]:
    """Rank the chart types these pills can build, best first.

    Deterministic: catalog order among types with no unmet requirements;
    types with unmet requirements follow, ordered by how many pills are
    missing (the ShowMe greyed-tile behaviour).
    """
    satisfied: list[tuple[ChartTemplate, list[Requirement]]] = []
    partial: list[tuple[int, int, ChartTemplate, list[Requirement]]] = []
    for position, item in enumerate(SHOWME_TYPES):
        unmet = item.unmet(pills)
        if not unmet:
            satisfied.append((item, []))
        else:
            partial.append((len(unmet), position, item, unmet))
    satisfied.extend(
        (item, unmet) for _, _, item, unmet in sorted(partial, key=lambda t: (t[0], t[1]))
    )
    return satisfied


def suggest(pills: list[FieldRef], *, top: int = 6) -> list[dict[str, Any]]:
    """The ShowMe panel payload: ranked tiles with their unmet requirements."""
    ranked = rank(pills)
    return [
        {
            "id": item.id,
            "label": item.label,
            "mark": item.mark,
            "enabled": not unmet,
            "unmet": [
                {"kind": requirement.kind, "optional": requirement.optional}
                for requirement in unmet
            ],
        }
        for item, unmet in ranked[:top]
    ]


def default_measure(pills: list[FieldRef]) -> FieldRef | None:
    measures = [pill for pill in pills if pill.field_type == "quantitative"]
    if measures:
        return FieldRef(
            field=measures[0].field,
            field_type="quantitative",
            aggregation="sum",
            discrete=False,
        )
    return FieldRef(field="*", field_type="quantitative", aggregation="count", discrete=False)


def place(
    chart_id: str,
    pills: list[FieldRef],
    *,
    source: Any,
    title: str = "",
) -> WorksheetSpec:
    """Resolve one ShowMe pick into a worksheet (field placement).

    Dimensions flow to the column shelf in order, measures to the row shelf,
    and the chart type's signature encoding (stack color, pie angle, bubble
    size, heatmap color) picks up the remaining pills.
    """
    known = {pill.field for pill in pills}
    dims = [p for p in pills if not p.is_measure and p.field_type != "temporal"]
    dates = [p for p in pills if not p.is_measure and p.field_type == "temporal"]
    measures = [
        FieldRef(field=p.field, field_type="quantitative", aggregation="sum", discrete=False)
        for p in pills
        if p.field_type == "quantitative"
    ]
    if not measures:
        fallback = default_measure(pills)
        if fallback is not None:
            measures = [fallback]

    item = SHOWME_BY_ID.get(chart_id, SHOWME_BY_ID["bar"])
    mark: MarkType = item.mark
    columns: list[FieldRef] = []
    rows: list[FieldRef] = []
    marks = MarksCard(type=mark)  # type: ignore[arg-type]

    if item.id == "text_table":
        columns = dims[:1] + dates[:1]
        rows = (dims[1:3] or dates[1:2]) + measures[:1]
        if not columns:
            columns, rows = rows[:1], rows[1:] or measures[:1]
        marks = MarksCard(type="text", text=measures[0] if measures else None)
    elif item.id == "heatmap":
        columns = dates[:1] or dims[:1]
        remaining_dims = [d for d in dims if d not in columns]
        rows = remaining_dims[:1]
        marks = MarksCard(
            type="text",
            text=measures[0] if measures else None,
            color=measures[0] if measures else None,
        )
    elif item.id in {"bar", "bar_stacked", "bar_side"}:
        axis = dates[:1] or dims[:1]
        columns = axis
        color_dims = [d for d in dims if d not in axis][:1]
        shows_color = item.id != "bar" and bool(color_dims)
        marks = MarksCard(type="bar", color=color_dims[0] if shows_color else None)
        # Two measures share one canvas as length: each becomes its own
        # series instead of one being lost.
        rows = measures[:2] if len(measures) > 1 else measures[:1]
    elif item.id in {"line", "area"}:
        columns = dates[:1] or dims[:1]
        color_dims = [d for d in dims if d not in columns][:1]
        rows = measures[:1]
        colored = bool(color_dims) or len(measures) > 1
        marks = MarksCard(type=mark, color=color_dims[0] if colored and color_dims else None)
    elif item.id == "pie":
        columns = dims[:1] or dates[:1]
        rows = measures[:1]
        marks = MarksCard(
            type="pie",
            angle=measures[0] if measures else None,
            color=columns[0] if columns else None,
        )
    elif item.id == "scatter":
        columns = measures[:1]
        rows = measures[1:2] or measures[:1]
        marks = MarksCard(type="circle", detail=dims[0] if dims else None)
    elif item.id == "bubble":
        columns = measures[:1]
        rows = measures[1:2]
        marks = MarksCard(
            type="circle", size=measures[0] if measures else None, detail=dims[0] if dims else None
        )

    if not rows and measures:
        rows = measures[:1]
    if not columns and dims:
        columns = dims[:1]
    if not columns and not rows:
        raise ValueError("Not enough fields to build this chart.")

    used = {p.field for p in [*columns, *rows]}
    if marks.color is not None:
        used.add(marks.color.field)
    if marks.size is not None:
        used.add(marks.size.field)
    if marks.text is not None:
        used.add(marks.text.field)

    spec = WorksheetSpec(
        title=title,
        source=source,
        columns=columns,
        rows=rows,
        marks=marks,
        field_types={pill.field: pill.field_type for pill in pills if pill.field in known},
    )
    return spec


def default_chart_id(pills: list[FieldRef]) -> str:
    """The chart ShowMe would highlight first for these pills.

    A temporal dimension with a measure reads as a line; any dimension with a
    measure reads as bars; dimensions alone read as a text table.
    """
    has_measure = any(pill.is_measure for pill in pills)
    has_date = any(not pill.is_measure and pill.field_type == "temporal" for pill in pills)
    if has_measure and has_date:
        return "line"
    if has_measure and any(not pill.is_measure for pill in pills):
        return "bar"
    if has_measure:
        return "scatter"
    return "text_table"


def from_intent(
    chart_intent: ChartIntent,
    columns: list[str],
    rows: list[list[Any]],
    *,
    source: Any,
    title: str = "",
) -> WorksheetSpec:
    """Resolve a chart intent (from the assistant) through the Show Me engine.

    Named fields are honored when they exist; anything else is placed by the
    ShowMe rules for the requested chart type. ``auto`` picks the type ShowMe
    would highlight for these fields.
    """
    pills = classify_fields(columns, rows)
    by_name = {pill.field: pill for pill in pills}
    y_fields = [chart_intent.y] if isinstance(chart_intent.y, str) else list(chart_intent.y or [])
    named = [
        by_name[name]
        for name in [chart_intent.x, *y_fields, chart_intent.color, chart_intent.size]
        if name and name in by_name
    ]
    chosen = named or pills

    chart_id: str = chart_intent.chart_type
    if chart_id == "auto":
        chart_id = default_chart_id(chosen if named else pills)
    spec = place(chart_id, pills, source=source, title=title)

    if named:
        spec = _apply_named(spec, chart_intent, by_name, chart_id)
    if chart_intent.aggregation:
        spec = spec.model_copy(
            update={
                "rows": [
                    pill.model_copy(update={"aggregation": chart_intent.aggregation})
                    if pill.is_measure
                    else pill
                    for pill in spec.rows
                ],
            }
        )
    return spec


def _as_measure(pill: FieldRef) -> FieldRef:
    return FieldRef(
        field=pill.field,
        field_type="quantitative",
        aggregation="sum",
        discrete=False,
    )


def _apply_named(
    spec: WorksheetSpec,
    chart_intent: ChartIntent,
    by_name: dict[str, FieldRef],
    chart_id: str,
) -> WorksheetSpec:
    """Override placed pills with the fields the request actually named."""
    x = by_name.get(chart_intent.x) if chart_intent.x else None
    y_names = [chart_intent.y] if isinstance(chart_intent.y, str) else list(chart_intent.y or [])
    ys = [by_name[name] for name in y_names if name in by_name]
    color = by_name.get(chart_intent.color) if chart_intent.color else None
    size = by_name.get(chart_intent.size) if chart_intent.size else None

    columns = list(spec.columns)
    rows = list(spec.rows)
    marks = spec.marks

    if x is not None:
        columns = [x]
    if ys:
        rows = [
            FieldRef(field=p.field, field_type="quantitative", aggregation="sum", discrete=False)
            if p.field_type == "quantitative"
            else p
            for p in ys
        ]
    # A request that names its fields speaks for itself: encodings it did not
    # name (an auto-placed color, say) are dropped rather than guessed.
    color_pill = (
        _as_measure(color) if color is not None and color.field_type == "quantitative" else color
    )
    size_pill = (
        _as_measure(size) if size is not None and size.field_type == "quantitative" else size
    )
    marks = marks.model_copy(update={"color": color_pill, "size": size_pill})
    if chart_id == "pie" and marks.type == "pie":
        angle = next((p for p in rows if p.is_measure), None)
        marks = marks.model_copy(update={"angle": angle})
    return spec.model_copy(update={"columns": columns, "rows": rows, "marks": marks})
