"""Version 2 dashboards: the 12-column grid layout before the free canvas.

Immutable revisions and operation receipts written before schema 3 keep this
shape. Reads upcast it with ``app.modules.viz.canvas.upcast_dashboard``; the
legacy grid operations (``layout.apply``, ``widget.*``) still carry these
widgets and are adapted onto the canvas. Never write a new v2 revision.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import Contract, Name
from .dashboards import (
    ChartOverrides,
    ChartReference,
    DatasetReference,
    Interaction,
    WidgetAppearance,
)
from .filters import FilterDefinition, FilterTree


class Layout(Contract):
    x: int = Field(strict=True, ge=0, le=11)
    y: int = Field(strict=True, ge=0, le=10_000)
    w: int = Field(strict=True, ge=2, le=12)
    h: int = Field(strict=True, ge=2, le=48)

    @model_validator(mode="after")
    def fits(self) -> Self:
        if self.x + self.w > 12:
            raise ValueError("Widget extends beyond the 12-column grid")
        return self

    def overlaps(self, other: Layout) -> bool:
        return (
            self.x < other.x + other.w and other.x < self.x + self.w
            and self.y < other.y + other.h and other.y < self.y + self.h
        )


class Widget(Contract):
    widget_id: UUID
    layout: Layout
    title: str = Field(default="", max_length=256)
    locked: bool = False
    appearance: WidgetAppearance = Field(default_factory=WidgetAppearance)


class ChartWidget(Widget):
    kind: Literal["chart"] = "chart"
    chart_ref: ChartReference
    style: ChartOverrides = Field(default_factory=ChartOverrides)
    filters: FilterTree | None = None


class TextWidget(Widget):
    kind: Literal["text"] = "text"
    text: str = Field(max_length=8000)
    style: Literal["title", "body"] = "body"


class ImageWidget(Widget):
    kind: Literal["image"] = "image"
    asset_id: UUID
    alt: str = Field(min_length=1, max_length=1000)
    fit: Literal["contain", "cover"] = "contain"


class FilterWidget(Widget):
    kind: Literal["filter"] = "filter"
    filter_id: UUID


class SpacerWidget(Widget):
    kind: Literal["spacer"] = "spacer"


DashboardWidget = Annotated[
    ChartWidget | TextWidget | ImageWidget | FilterWidget | SpacerWidget,
    Field(discriminator="kind"),
]


class PageV2(Contract):
    page_id: UUID
    title: Name
    widgets: tuple[DashboardWidget, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def nonoverlapping(self) -> Self:
        for index, widget in enumerate(self.widgets):
            if any(widget.layout.overlaps(other.layout) for other in self.widgets[:index]):
                raise ValueError("Dashboard widgets cannot overlap")
        return self


class DashboardDefinitionV2(Contract):
    schema_version: Literal[2] = 2
    dashboard_id: UUID
    title: Name
    description: str = Field(default="", max_length=4000)
    columns: Literal[12] = 12
    pages: tuple[PageV2, ...] = Field(min_length=1, max_length=15)
    # Omit the empty addition so existing v2 operation digests still replay.
    datasets: tuple[DatasetReference, ...] = Field(
        default=(), max_length=128, exclude_if=lambda value: not value,
    )
    filters: tuple[FilterDefinition, ...] = Field(default=(), max_length=64)
    actions: tuple[Interaction, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def references(self) -> Self:
        if len({item.dataset_id for item in self.datasets}) != len(self.datasets):
            raise ValueError("Dashboard dataset references must have unique dataset IDs")
        page_ids = {page.page_id for page in self.pages}
        widgets = [widget for page in self.pages for widget in page.widgets]
        widget_ids = {widget.widget_id for widget in widgets}
        chart_ids = {widget.widget_id for widget in widgets if isinstance(widget, ChartWidget)}
        filter_ids = {item.filter_id for item in self.filters}
        if len(page_ids) != len(self.pages) or len(widget_ids) != len(widgets):
            raise ValueError("Page and widget IDs must be unique across the dashboard")
        if len(filter_ids) != len(self.filters):
            raise ValueError("Filter IDs must be unique")
        if len({action.action_id for action in self.actions}) != len(self.actions):
            raise ValueError("Action IDs must be unique")
        for widget in widgets:
            if isinstance(widget, FilterWidget) and widget.filter_id not in filter_ids:
                raise ValueError("Widget references an unknown filter")
        for item in self.filters:
            if not set(item.depends_on) <= filter_ids:
                raise ValueError("Cascading filter references an unknown parent")
            if item.scope.page_id is not None and item.scope.page_id not in page_ids:
                raise ValueError("Filter references an unknown page")
            if not set(item.scope.widget_ids) <= chart_ids:
                raise ValueError("Filter target must be a chart widget")
        remaining = {item.filter_id: set(item.depends_on) for item in self.filters}
        while remaining:
            ready = {identity for identity, parents in remaining.items() if not parents}
            if not ready:
                raise ValueError("Cascading filters cannot contain a cycle")
            remaining = {identity: parents - ready for identity, parents in remaining.items()
                         if identity not in ready}
        for action in self.actions:
            if action.source_widget_id not in chart_ids:
                raise ValueError("Interaction source must be a chart widget")
            if not set(action.target_widget_ids) <= chart_ids:
                raise ValueError("Interaction target must be a chart widget")
            if action.target_page_id is not None and action.target_page_id not in page_ids:
                raise ValueError("Interaction references an unknown page")
        return self
