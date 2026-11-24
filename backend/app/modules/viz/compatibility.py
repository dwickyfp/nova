"""Lossless v1 adapters. Unsupported semantics produce a conversion blocker."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast
from uuid import UUID, uuid5

from .contracts.charts import CartesianEncoding, ChartDefinition, ChartSort, Dimension, Measure
from .contracts.dashboards import ChartReference
from .contracts.dashboards_v2 import (
    ChartWidget,
    DashboardWidget,
    Layout,
    SpacerWidget,
    TextWidget,
)
from .contracts.dashboards_v2 import DashboardDefinitionV2 as DashboardDefinition
from .contracts.dashboards_v2 import PageV2 as Page
from .spec import DashboardSpec, WorksheetSpec


class ConversionBlocked(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def dashboard_v1_to_v2(
    legacy: DashboardSpec, *, dashboard_id: UUID,
    chart_mappings: Mapping[str, ChartReference],
) -> DashboardDefinition:
    if legacy.actions or any(zone.kind == "filter" for zone in legacy.zones):
        raise ConversionBlocked("FILTER_MAPPING_REVIEW_REQUIRED")
    if legacy.size.mode != "auto":
        raise ConversionBlocked("FIXED_CANVAS_REVIEW_REQUIRED")
    widgets: list[DashboardWidget] = []
    for zone in legacy.zones:
        rect = zone.rect
        if rect.x % 2 or rect.w % 2 or rect.w < 4 or rect.h < 2:
            raise ConversionBlocked("LAYOUT_REPACK_REVIEW_REQUIRED")
        layout = Layout(x=rect.x // 2, y=rect.y, w=rect.w // 2, h=rect.h)
        widget_id = uuid5(dashboard_id, "widget:" + zone.zone_id)
        if zone.kind == "sheet":
            reference = chart_mappings.get(zone.artifact_id or "")
            if reference is None:
                raise ConversionBlocked("CHART_MAPPING_REQUIRED")
            widgets.append(ChartWidget(widget_id=widget_id, layout=layout, chart_ref=reference))
        elif zone.kind == "text" and zone.text is not None:
            widgets.append(TextWidget(
                widget_id=widget_id, layout=layout, text=zone.text.content, style=zone.text.style,
            ))
        elif zone.kind == "spacer":
            widgets.append(SpacerWidget(widget_id=widget_id, layout=layout))
        else:
            raise ConversionBlocked("UNSUPPORTED_ZONE")
    return DashboardDefinition(
        dashboard_id=dashboard_id, title=legacy.title or "Untitled dashboard",
        pages=(Page(
            page_id=uuid5(dashboard_id, "page:1"), title="Page 1", widgets=tuple(widgets),
        ),),
    )


def worksheet_v1_to_v2(
    legacy: WorksheetSpec, *, chart_id: UUID, dataset_id: UUID,
    dataset_revision: int, field_mapping: Mapping[str, str],
) -> ChartDefinition:
    """Dataset bindings must come from a separately validated source conversion."""
    if legacy.filters or legacy.calculations or legacy.table_calcs:
        raise ConversionBlocked("CALCULATION_FILTER_REVIEW_REQUIRED")
    if legacy.limit is None:
        raise ConversionBlocked("RESULT_LIMIT_REVIEW_REQUIRED")
    if legacy.marks.type not in {"bar", "line", "area"}:
        raise ConversionBlocked("VISUAL_MAPPING_REVIEW_REQUIRED")
    if any((legacy.marks.size, legacy.marks.text, legacy.marks.detail, legacy.marks.angle)):
        raise ConversionBlocked("MARK_ENCODING_REVIEW_REQUIRED")
    vertical = all(not field.is_measure for field in legacy.columns)
    dimensions = legacy.columns if vertical else legacy.rows
    measures = legacy.rows if vertical else legacy.columns
    if (not dimensions or not measures or any(field.is_measure for field in dimensions)
            or any(not field.is_measure for field in measures)):
        raise ConversionBlocked("SHELF_MAPPING_REVIEW_REQUIRED")
    if legacy.marks.color:
        raise ConversionBlocked("COLOR_STACK_REVIEW_REQUIRED")

    def field_id(name: str) -> str:
        if name not in field_mapping:
            raise ConversionBlocked("FIELD_MAPPING_REQUIRED")
        return field_mapping[name]

    encoding = CartesianEncoding(
        dimensions=tuple(Dimension(
            field_id=field_id(field.field), discrete=field.discrete, date_grain=field.date_part,
            date_mode="part" if field.discrete else "truncate",
        ) for field in dimensions),
        measures=tuple(Measure.model_validate({
            "field_id": field_id(field.field),
            "aggregation": "count_distinct" if field.aggregation == "distinct_count"
            else field.aggregation,
            "series": legacy.marks.type,
        }) for field in measures),
        orientation="vertical" if vertical else "horizontal",
        color=(Dimension(field_id=field_id(legacy.marks.color.field))
               if legacy.marks.color else None),
    )
    return ChartDefinition(
        chart_id=chart_id, title=legacy.title or "Untitled chart", dataset_id=dataset_id,
        dataset_revision=dataset_revision,
        visual=cast(Literal["bar", "line", "area"], legacy.marks.type), encoding=encoding,
        row_limit=legacy.limit,
        sort=tuple(ChartSort(field_id=field_id(item.field), direction=item.direction)
                   for item in legacy.sort),
    )
