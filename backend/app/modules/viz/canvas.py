"""Canvas geometry and the deterministic, lossless v2 → v3 dashboard upcaster.

Version 2 pages were a 12-column GridStack grid with 72 px rows and a 6 px
margin around every widget. At a 1200 px frame a column is exactly 100 px, so a
v2 cell maps to integer canvas pixels with no rounding::

    x = col * 100 + 6      w = cols * 100 - 12
    y = row * 72 + 6       h = rows * 72 - 12

The upcast grid (12 columns, 12 px gutter, 6 px margin) reproduces the same
column lines, so ``cell_of`` inverts ``cell_rect`` exactly for untouched
elements. Immutable revisions are never rewritten: this runs on every read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from .contracts.dashboards import (
    CanvasGrid,
    DashboardDefinition,
    Element,
    ElementStyle,
    FilterElement,
    Geometry,
    ImageElement,
    LegacySpacer,
    Page,
    PageCanvas,
    SolidFill,
    Stroke,
    TextElement,
    TextRun,
    VisualElement,
)
from .contracts.dashboards_v2 import (
    ChartWidget,
    DashboardDefinitionV2,
    FilterWidget,
    ImageWidget,
    Layout,
    PageV2,
    SpacerWidget,
    TextWidget,
    Widget,
    WidgetAppearance,
)

LEGACY_WIDTH = 1200
LEGACY_ROW = 72
LEGACY_COLUMNS = 12
LEGACY_MARGIN = 6
LEGACY_GRID = CanvasGrid(columns=12, gutter=2 * LEGACY_MARGIN, margin=LEGACY_MARGIN, baseline=8)
_PADDING = {"none": 0, "compact": 12, "comfortable": 16}
_APPEARANCE_PADDING = {value: key for key, value in _PADDING.items()}


# ── Grid cells ↔ canvas pixels ──────────────────────────────────────────

def _pitch(grid: CanvasGrid, width: int) -> float:
    column = (width - 2 * grid.margin - (grid.columns - 1) * grid.gutter) / grid.columns
    return column + grid.gutter


def cell_rect(layout: Layout, grid: CanvasGrid, width: int) -> Geometry:
    """Canvas pixels for a legacy 12-column cell on a page's grid."""
    step = _pitch(grid, width) * grid.columns / LEGACY_COLUMNS
    return Geometry(
        x=round(grid.margin + layout.x * step), w=round(layout.w * step - grid.gutter),
        y=grid.margin + layout.y * LEGACY_ROW, h=layout.h * LEGACY_ROW - grid.gutter,
    )


def cell_of(geometry: Geometry, grid: CanvasGrid, width: int) -> Layout:
    """The nearest legacy 12-column cell; exact for anything ``cell_rect`` produced."""
    step = _pitch(grid, width) * grid.columns / LEGACY_COLUMNS
    w = min(LEGACY_COLUMNS, max(2, round((geometry.w + grid.gutter) / step)))
    x = min(LEGACY_COLUMNS - w, max(0, round((geometry.x - grid.margin) / step)))
    h = min(48, max(2, round((geometry.h + grid.gutter) / LEGACY_ROW)))
    y = min(10_000, max(0, round((geometry.y - grid.margin) / LEGACY_ROW)))
    return Layout(x=x, y=y, w=w, h=h)


def cells(page: Page) -> dict[UUID, Layout]:
    """The legacy cell of each top-level element and spacer, for grid-speaking clients."""
    grid, width = page.canvas.grid, page.canvas.width
    found = {item.element_id: cell_of(item.geometry, grid, width) for item in page.elements}
    found.update({item.element_id: cell_of(item.geometry, grid, width)
                  for item in page.legacy_spacers})
    return found


def legacy_widgets(page: Page) -> list[Widget]:
    """The page as v2 grid widgets, in stacking order, then spacers.

    This is the exact inverse of ``upcast_page`` for an untouched page, and the
    view a grid-speaking client (the Dataviz assistant tools until they use
    canvas operations) reads. Elements a grid cannot show (shapes, icons,
    connectors, annotations, groups) are left out; numbers bound in text are
    left out rather than written as literals.
    """
    grid, width = page.canvas.grid, page.canvas.width
    widgets: list[Widget] = []
    for element in page.elements:
        base: dict[str, Any] = {
            "widget_id": element.element_id, "layout": cell_of(element.geometry, grid, width),
            "locked": element.locked, "appearance": appearance_of(element.style),
        }
        if isinstance(element, VisualElement):
            widgets.append(ChartWidget(**base, title=element.title, chart_ref=element.chart_ref,
                                       style=element.overrides, filters=element.filters))
        elif isinstance(element, TextElement):
            text = "".join(run.text for run in element.text.runs if isinstance(run, TextRun))
            widgets.append(TextWidget(**base, title=element.title, text=text,
                                      style="title" if element.role == "title" else "body"))
        elif isinstance(element, ImageElement):
            widgets.append(ImageWidget(**base, title=element.title, asset_id=element.asset_id,
                                       alt=element.alt, fit=element.fit))
        elif isinstance(element, FilterElement):
            widgets.append(FilterWidget(**base, title=element.title, filter_id=element.filter_id))
    widgets.extend(SpacerWidget(
        widget_id=item.element_id, layout=cell_of(item.geometry, grid, width), title=item.title,
        locked=item.locked, appearance=appearance_of(item.style),
    ) for item in page.legacy_spacers)
    return widgets


# ── Appearance ↔ style ──────────────────────────────────────────────────

def style_of(appearance: WidgetAppearance) -> ElementStyle:
    return ElementStyle(
        fill=SolidFill(token="card") if appearance.surface == "card" else None,
        stroke=Stroke(token="surface-border") if appearance.border else None,
        radius=8, padding=_PADDING[appearance.padding],  # type: ignore[arg-type]
    )


def appearance_of(style: ElementStyle) -> WidgetAppearance:
    """Inverse of ``style_of`` for the legacy projection and its proof."""
    return WidgetAppearance(
        surface="card" if style.fill is not None else "transparent",
        border=style.stroke is not None,
        padding=_APPEARANCE_PADDING.get(style.padding, "compact"),  # type: ignore[arg-type]
    )


# ── Upcast ──────────────────────────────────────────────────────────────

def _base(widget: Widget, grid: CanvasGrid, width: int) -> dict[str, Any]:
    return {
        "element_id": widget.widget_id, "geometry": cell_rect(widget.layout, grid, width),
        "locked": widget.locked, "style": style_of(widget.appearance),
    }


def upcast_widget(widget: Widget, grid: CanvasGrid = LEGACY_GRID,
                  width: int = LEGACY_WIDTH) -> dict[str, Any]:
    """A v2 widget as v3 element data (spacers are not elements)."""
    base = _base(widget, grid, width)
    if isinstance(widget, ChartWidget):
        return {**base, "kind": "visual", "title": widget.title, "chart_ref": widget.chart_ref,
                "overrides": widget.style, "filters": widget.filters}
    if isinstance(widget, TextWidget):
        runs = [{"kind": "text", "text": widget.text}]
        return {**base, "kind": "text", "title": widget.title, "role": widget.style,
                "text": {"runs": runs}}
    if isinstance(widget, ImageWidget):
        return {**base, "kind": "image", "title": widget.title, "asset_id": widget.asset_id,
                "alt": widget.alt, "fit": widget.fit}
    if isinstance(widget, FilterWidget):
        return {**base, "kind": "filter", "title": widget.title, "filter_id": widget.filter_id}
    raise ValueError("Spacers are not canvas elements")


def upcast_spacer(widget: SpacerWidget, grid: CanvasGrid = LEGACY_GRID,
                  width: int = LEGACY_WIDTH) -> LegacySpacer:
    return LegacySpacer(element_id=widget.widget_id, title=widget.title, locked=widget.locked,
                        geometry=cell_rect(widget.layout, grid, width),
                        style=style_of(widget.appearance))


def upcast_page(page: PageV2) -> Page:
    bottom = max((w.layout.y + w.layout.h for w in page.widgets), default=0)
    canvas = PageCanvas(size="custom", width=LEGACY_WIDTH, fit="width", grid=LEGACY_GRID,
                        height=max(720, bottom * LEGACY_ROW))
    return Page.model_validate({
        "page_id": page.page_id, "title": page.title, "canvas": canvas,
        "elements": [upcast_widget(w) for w in page.widgets if not isinstance(w, SpacerWidget)],
        "legacy_spacers": [upcast_spacer(w) for w in page.widgets if isinstance(w, SpacerWidget)],
    })


def upcast_dashboard(document: DashboardDefinitionV2) -> DashboardDefinition:
    return DashboardDefinition.model_validate({
        "dashboard_id": document.dashboard_id, "title": document.title,
        "description": document.description,
        "pages": [upcast_page(page) for page in document.pages],
        "datasets": document.datasets, "filters": document.filters, "actions": document.actions,
    })


def dashboard_definition(payload: Mapping[str, Any]) -> DashboardDefinition:
    """Parse any stored or submitted dashboard, upcasting schema 2 on read."""
    if payload.get("schema_version", 2) == 2:
        return upcast_dashboard(DashboardDefinitionV2.model_validate(payload))
    return DashboardDefinition.model_validate(payload)


def current(document: DashboardDefinition | DashboardDefinitionV2) -> DashboardDefinition:
    return upcast_dashboard(document) if isinstance(document, DashboardDefinitionV2) else document


# ── Reading order ───────────────────────────────────────────────────────

#: Elements whose tops are this close read as one row, left to right.
ROW_TOLERANCE = 24


def reading_order(elements: Iterable[Element]) -> list[Element]:
    """Top-to-bottom, then left-to-right: the default tab and phone order."""
    remaining = sorted(elements, key=lambda item: (item.geometry.y, item.geometry.x))
    ordered: list[Element] = []
    while remaining:
        top = remaining[0].geometry.y
        row = [item for item in remaining if item.geometry.y - top <= ROW_TOLERANCE]
        ordered.extend(sorted(row, key=lambda item: (item.geometry.x, item.geometry.y)))
        remaining = [item for item in remaining if item not in row]
    return ordered


def tab_order(page: Page) -> list[Element]:
    """The page's explicit tab order, else its computed reading order (no decoratives)."""
    focusable = [item for item in page.elements if not item.decorative and not item.hidden]
    if page.tab_order:
        by_id = {item.element_id: item for item in focusable}
        listed = [by_id[identity] for identity in page.tab_order if identity in by_id]
        rest = [item for item in reading_order(focusable) if item.element_id not in page.tab_order]
        return listed + rest
    return reading_order(focusable)
