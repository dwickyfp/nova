"""Dashboard documents, schema 3: one free-form canvas per page.

Each page is a fixed-size frame. Elements carry absolute geometry in canvas
pixels and their array order is the z-order (first is at the back). The grid in
``canvas.grid`` only assists authoring (snap, guides, auto-arrange); it never
positions anything. Version 2 grid documents live in ``dashboards_v2`` and are
upcast on read by ``app.modules.viz.canvas``. See
``docs/architecture/native-dataviz/ADR-001-free-canvas.md``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import Contract, FieldId, Name, Revision, Scalar
from .filters import FieldMapping, FilterDefinition, FilterTree

MAX_ELEMENTS_PER_PAGE = 150
MAX_DOCUMENT_BYTES = 1_048_576
MAX_GROUP_DEPTH = 3
#: How far an element may extend past the page edge; it may never leave it.
OVERHANG = 64

class ChartReference(Contract):
    chart_id: UUID
    revision: Revision


class DatasetReference(Contract):
    dataset_id: UUID
    revision: Revision


class WidgetAppearance(Contract):
    surface: Literal["card", "transparent"] = "card"
    border: bool = True
    padding: Literal["none", "compact", "comfortable"] = "compact"


class ChartOverrides(Contract):
    show_title: bool | None = None
    show_legend: bool | None = None
    show_labels: bool | None = None
    palette: Literal["neutral", "categorical", "single_tone"] | None = None


class Interaction(Contract):
    action_id: UUID
    kind: Literal["filter", "highlight", "drill", "navigate"]
    source_widget_id: UUID
    target_widget_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    target_page_id: UUID | None = None
    #: Drill-through: open another dashboard, narrowed by ``mappings``.
    target_dashboard_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
    target_workspace_id: str | None = Field(
        default=None, min_length=1, max_length=128, exclude_if=lambda value: value is None,
    )
    label: str | None = Field(
        default=None, min_length=1, max_length=120, exclude_if=lambda value: value is None,
    )
    mappings: tuple[FieldMapping, ...] = Field(default=(), max_length=32)
    run_on: Literal["select", "menu"] = "select"

    @model_validator(mode="after")
    def drill_through(self) -> Self:
        if self.target_dashboard_id is None:
            if self.target_workspace_id is not None:
                raise ValueError("A target workspace needs a target dashboard")
            return self
        if self.kind != "navigate" or self.target_page_id is not None:
            raise ValueError("Opening another dashboard is a navigate action without a page")
        if self.target_workspace_id is None or not self.mappings:
            raise ValueError("A dashboard link needs its workspace and at least one field mapping")
        return self

    @model_validator(mode="after")
    def targets(self) -> Self:
        if len(set(self.target_widget_ids)) != len(self.target_widget_ids):
            raise ValueError("Interaction targets must be unique")
        if self.kind == "navigate" and self.target_dashboard_id is not None:
            if self.target_widget_ids:
                raise ValueError("A dashboard link targets the other dashboard, not widgets")
        elif self.kind == "navigate":
            if self.target_page_id is None or self.target_widget_ids or self.mappings:
                raise ValueError("Navigation requires only a target page")
        elif not self.target_widget_ids:
            raise ValueError("Interaction requires target widgets")
        elif self.target_page_id is not None:
            raise ValueError("Only navigation can target a page")
        if self.kind in {"filter", "highlight", "drill"} and not self.mappings:
            raise ValueError("Data interactions require explicit field mappings")
        return self




# ── Page frame ──────────────────────────────────────────────────────────

PageSize = Literal["16:9", "4:3", "a4-landscape", "letter-landscape", "custom"]
#: Logical canvas pixels for each preset; "custom" takes any bounded size.
PAGE_SIZES: dict[str, tuple[int, int]] = {
    "16:9": (1280, 720), "4:3": (1024, 768),
    "a4-landscape": (1123, 794), "letter-landscape": (1056, 816),
}

ThemeToken = Literal[
    "background", "card", "muted", "accent", "brand", "primary", "foreground",
    "muted-foreground", "border", "surface-border", "success", "warning", "destructive",
    "chart-1", "chart-2", "chart-3", "chart-4", "chart-5", "chart-6", "chart-7",
    "chart-8", "chart-9",
]


class SolidFill(Contract):
    kind: Literal["solid"] = "solid"
    token: ThemeToken
    opacity: int = Field(default=100, strict=True, ge=0, le=100)


class GradientFill(Contract):
    kind: Literal["gradient"] = "gradient"
    start: ThemeToken
    end: ThemeToken
    angle: int = Field(default=180, strict=True, ge=0, le=359)


class ImageFill(Contract):
    """A managed image asset; never an external URL."""

    kind: Literal["image"] = "image"
    asset_id: UUID
    fit: Literal["cover", "contain"] = "cover"
    #: Darkening scrim in percent, so text on top stays readable.
    dim: int = Field(default=0, strict=True, ge=0, le=90)


Fill = Annotated[SolidFill | GradientFill | ImageFill, Field(discriminator="kind")]


class CanvasGrid(Contract):
    """Authoring aid only: columns and baseline rows to snap to, shown or hidden."""

    columns: int = Field(default=12, strict=True, ge=1, le=24)
    gutter: int = Field(default=16, strict=True, ge=0, le=64)
    margin: int = Field(default=24, strict=True, ge=0, le=256)
    #: Row pitch in canvas pixels (8-pt rows by default).
    baseline: int = Field(default=8, strict=True, ge=2, le=64)
    show: bool = True
    snap: bool = True
    #: Snap to other elements' edges and centres as well as to the grid.
    snap_to_elements: bool = True


class PageCanvas(Contract):
    size: PageSize = "16:9"
    width: int = Field(default=1280, strict=True, ge=320, le=4096)
    #: Long pages scroll; v2 pages can be as tall as 10,048 grid rows.
    height: int = Field(default=720, strict=True, ge=200, le=750_000)
    #: How the frame is shown: the whole page, the page width, or actual pixels.
    fit: Literal["page", "width", "actual"] = "page"
    background: Fill | None = None
    grid: CanvasGrid = Field(default_factory=CanvasGrid)

    @model_validator(mode="after")
    def preset_size(self) -> Self:
        if self.size != "custom" and PAGE_SIZES[self.size] != (self.width, self.height):
            raise ValueError("A preset page size fixes the canvas width and height")
        grid = self.grid
        usable = self.width - 2 * grid.margin - (grid.columns - 1) * grid.gutter
        if usable < 8 * grid.columns:
            raise ValueError("Grid columns do not fit the page width")
        return self


# ── Elements ────────────────────────────────────────────────────────────

class Geometry(Contract):
    """Canvas pixels relative to the page, or to the group for group children."""

    x: int = Field(strict=True, ge=-OVERHANG, le=4096 + OVERHANG)
    y: int = Field(strict=True, ge=-OVERHANG, le=750_000 + OVERHANG)
    w: int = Field(strict=True, ge=1, le=4096 + 2 * OVERHANG)
    h: int = Field(strict=True, ge=1, le=750_000 + 2 * OVERHANG)
    rotation: Literal[0, 90, 180, 270] = 0

    def within(self, width: int, height: int) -> bool:
        return (self.x >= -OVERHANG and self.y >= -OVERHANG
                and self.x + self.w <= width + OVERHANG and self.y + self.h <= height + OVERHANG)


class Stroke(Contract):
    token: ThemeToken
    width: Literal[0, 1, 2, 3, 4] = 1
    dash: Literal["solid", "dash", "dot"] = "solid"


class ElementStyle(Contract):
    fill: Fill | None = None
    stroke: Stroke | None = None
    radius: Literal[0, 4, 8, 12, 16, 9999] = 0
    shadow: Literal["none", "sm", "md"] = "none"
    opacity: int = Field(default=100, strict=True, ge=0, le=100)
    padding: Literal[0, 4, 8, 12, 16, 24] = 0


class DatumAnchor(Contract):
    """A data point inside a visual, resolved from the viewer's own query result.

    A point the viewer cannot see (row filter, role, filter) hides whatever is
    anchored to it; it is never guessed or shown as a placeholder.
    """

    element_id: UUID
    match: tuple[DatumMatch, ...] = Field(default=(), max_length=8)
    rule: Literal["max", "min", "first", "last"] | None = None
    measure_id: FieldId | None = None
    missing: Literal["hide"] = "hide"

    @model_validator(mode="after")
    def one_locator(self) -> Self:
        if bool(self.match) == (self.rule is not None):
            raise ValueError("A data anchor needs either dimension values or a rule, not both")
        return self


class DatumMatch(Contract):
    field_id: FieldId
    value: Scalar


class ValueBinding(Contract):
    """A number in text, evaluated as the viewer; never a literal copied from a query."""

    element_id: UUID
    measure_id: FieldId
    match: tuple[DatumMatch, ...] = Field(default=(), max_length=8)
    rule: Literal["max", "min", "first", "last", "total"] | None = None
    format: Literal["number", "currency", "percent"] = "number"


class TextRun(Contract):
    kind: Literal["text"] = "text"
    text: str = Field(max_length=8000)
    weight: Literal[400, 500, 600] | None = None


class ValueRun(Contract):
    kind: Literal["value"] = "value"
    value: ValueBinding


class RichText(Contract):
    runs: tuple[Annotated[TextRun | ValueRun, Field(discriminator="kind")], ...] = Field(
        default=(), max_length=256,
    )

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if sum(len(run.text) for run in self.runs if isinstance(run, TextRun)) > 8000:
            raise ValueError("Text exceeds 8,000 characters")
        return self

    def bindings(self) -> Iterator[ValueBinding]:
        return (run.value for run in self.runs if isinstance(run, ValueRun))


class ElementBase(Contract):
    element_id: UUID
    #: Name in the selection pane.
    name: str = Field(default="", max_length=120)
    geometry: Geometry
    locked: bool = False
    hidden: bool = False
    #: Decorative elements are hidden from assistive technology and tab order.
    decorative: bool = False
    style: ElementStyle = Field(default_factory=ElementStyle)


class VisualElement(ElementBase):
    kind: Literal["visual"] = "visual"
    title: str = Field(default="", max_length=256)
    chart_ref: ChartReference
    overrides: ChartOverrides = Field(default_factory=ChartOverrides)
    filters: FilterTree | None = None


class FilterElement(ElementBase):
    kind: Literal["filter"] = "filter"
    title: str = Field(default="", max_length=256)
    filter_id: UUID


class TextElement(ElementBase):
    kind: Literal["text"] = "text"
    title: str = Field(default="", max_length=256)
    role: Literal["title", "heading", "body", "caption"] = "body"
    text: RichText = Field(default_factory=RichText)


class ImageElement(ElementBase):
    kind: Literal["image"] = "image"
    title: str = Field(default="", max_length=256)
    asset_id: UUID
    alt: str = Field(min_length=1, max_length=1000)
    fit: Literal["contain", "cover"] = "contain"


class ShapeElement(ElementBase):
    kind: Literal["shape"] = "shape"
    shape: Literal["rect", "ellipse", "line", "pill", "triangle"] = "rect"


#: Curated icon names (lucide); stored by name so no arbitrary SVG is ever rendered.
IconName = Literal[
    "activity", "arrow-down", "arrow-down-right", "arrow-left", "arrow-right", "arrow-up",
    "arrow-up-right", "award", "banknote", "briefcase", "building-2", "calendar", "car",
    "chart-bar", "chart-column", "chart-line", "chart-pie", "circle-alert", "circle-check",
    "circle-x", "clock", "cloud", "credit-card", "database", "dollar-sign", "droplet",
    "factory", "flag", "fuel", "gauge", "globe", "graduation-cap", "heart", "heart-pulse",
    "house", "info", "leaf", "lightbulb", "lock", "mail", "map", "map-pin", "message-square",
    "package", "percent", "phone", "plane", "server", "shield-check", "ship", "shopping-bag",
    "shopping-cart", "sparkles", "star", "store", "sun", "target", "thumbs-down", "thumbs-up",
    "timer", "trending-down", "trending-up", "triangle-alert", "trophy", "truck", "user",
    "user-check", "users", "wallet", "warehouse", "zap",
]


class IconElement(ElementBase):
    kind: Literal["icon"] = "icon"
    icon: IconName
    #: Accessible label; an icon without one should be decorative.
    label: str = Field(default="", max_length=256)


class ElementEndpoint(Contract):
    kind: Literal["element"] = "element"
    element_id: UUID
    side: Literal["auto", "n", "e", "s", "w"] = "auto"


class DatumEndpoint(Contract):
    kind: Literal["datum"] = "datum"
    anchor: DatumAnchor


class PointEndpoint(Contract):
    kind: Literal["point"] = "point"
    x: int = Field(strict=True, ge=-OVERHANG, le=4096 + OVERHANG)
    y: int = Field(strict=True, ge=-OVERHANG, le=750_000 + OVERHANG)


Endpoint = Annotated[
    ElementEndpoint | DatumEndpoint | PointEndpoint, Field(discriminator="kind"),
]


class ConnectorElement(ElementBase):
    """A line or arrow; its path is recomputed from the endpoints when they move."""

    kind: Literal["connector"] = "connector"
    start: Endpoint
    end: Endpoint
    head: Literal["none", "arrow", "dot"] = "arrow"
    curve: Literal["straight", "arc", "elbow"] = "straight"


class AnnotationElement(ElementBase):
    kind: Literal["annotation"] = "annotation"
    target: DatumAnchor
    text: RichText = Field(default_factory=RichText)
    placement: Literal["auto", "n", "e", "s", "w"] = "auto"


class GroupElement(ElementBase):
    kind: Literal["group"] = "group"
    children: tuple[Element, ...] = Field(min_length=1, max_length=MAX_ELEMENTS_PER_PAGE)


Element = Annotated[
    VisualElement | FilterElement | TextElement | ImageElement | ShapeElement | IconElement
    | ConnectorElement | AnnotationElement | GroupElement,
    Field(discriminator="kind"),
]
GroupElement.model_rebuild()


def walk(elements: tuple[Element, ...], depth: int = 1) -> Iterator[tuple[Element, int]]:
    """Every element with its group depth (1 = on the page), groups before children."""
    for element in elements:
        yield element, depth
        if isinstance(element, GroupElement):
            yield from walk(element.children, depth + 1)


class LegacySpacer(Contract):
    """A v2 spacer, kept so the upcast stays lossless; the canvas never draws it."""

    element_id: UUID
    geometry: Geometry
    title: str = Field(default="", max_length=256)
    locked: bool = False
    style: ElementStyle = Field(default_factory=ElementStyle)


class MobileProjection(Contract):
    """One-column phone reading order: a projection, not a second layout."""

    order: tuple[UUID, ...] = Field(default=(), max_length=MAX_ELEMENTS_PER_PAGE)
    hidden: tuple[UUID, ...] = Field(default=(), max_length=MAX_ELEMENTS_PER_PAGE)


class Page(Contract):
    page_id: UUID
    title: Name
    canvas: PageCanvas = Field(default_factory=PageCanvas)
    elements: tuple[Element, ...] = Field(default=(), max_length=MAX_ELEMENTS_PER_PAGE)
    #: Keyboard and screen-reader order; empty means the computed reading order.
    tab_order: tuple[UUID, ...] = Field(default=(), max_length=MAX_ELEMENTS_PER_PAGE)
    mobile: MobileProjection = Field(default_factory=MobileProjection)
    legacy_spacers: tuple[LegacySpacer, ...] = Field(
        default=(), max_length=100, exclude_if=lambda value: not value,
    )

    def all_elements(self) -> list[Element]:
        return [element for element, _ in walk(self.elements)]

    @model_validator(mode="after")
    def canvas_rules(self) -> Self:
        placed = list(walk(self.elements))
        if len(placed) > MAX_ELEMENTS_PER_PAGE:
            raise ValueError(f"A page holds at most {MAX_ELEMENTS_PER_PAGE} elements")
        if any(depth > MAX_GROUP_DEPTH for _, depth in placed):
            raise ValueError(f"Groups nest at most {MAX_GROUP_DEPTH} levels")
        by_id = {element.element_id: element for element, _ in placed}
        spacers = {spacer.element_id for spacer in self.legacy_spacers}
        if (len(by_id) != len(placed) or len(spacers) != len(self.legacy_spacers)
                or by_id.keys() & spacers):
            raise ValueError("Element IDs must be unique on a page")
        width, height = self.canvas.width, self.canvas.height
        for element in self.elements:
            if not element.geometry.within(width, height):
                raise ValueError("An element may overhang the page edge only slightly")
        for element, _ in placed:
            if isinstance(element, GroupElement):
                box = element.geometry
                if not all(child.geometry.within(box.w, box.h) for child in element.children):
                    raise ValueError("Group children must stay inside their group")
        visuals = {key for key, value in by_id.items() if isinstance(value, VisualElement)}
        for element, _ in placed:
            anchors: list[DatumAnchor] = []
            bindings: list[ValueBinding] = []
            if isinstance(element, ConnectorElement):
                for endpoint in (element.start, element.end):
                    if isinstance(endpoint, ElementEndpoint):
                        if (endpoint.element_id not in by_id
                                or endpoint.element_id == element.element_id):
                            raise ValueError(
                                "A connector must attach to another element on its page")
                    elif isinstance(endpoint, DatumEndpoint):
                        anchors.append(endpoint.anchor)
            if isinstance(element, AnnotationElement):
                anchors.append(element.target)
                bindings.extend(element.text.bindings())
            if isinstance(element, TextElement):
                bindings.extend(element.text.bindings())
            targets = [item.element_id for item in anchors] + [item.element_id for item in bindings]
            if not set(targets) <= visuals:
                raise ValueError("Data anchors and values must refer to a visual on the same page")
        top = {element.element_id for element in self.elements}
        focusable = {key for key, value in by_id.items() if not value.decorative}
        for name, ids, allowed in (("Tab order", self.tab_order, focusable),
                                   ("Mobile order", self.mobile.order, top),
                                   ("Mobile hidden", self.mobile.hidden, top)):
            if len(set(ids)) != len(ids) or not set(ids) <= allowed:
                raise ValueError(f"{name} must list distinct elements of this page")
        return self


class DashboardDefinition(Contract):
    schema_version: Literal[3] = 3
    dashboard_id: UUID
    title: Name
    description: str = Field(default="", max_length=4000)
    pages: tuple[Page, ...] = Field(min_length=1, max_length=15)
    datasets: tuple[DatasetReference, ...] = Field(default=(), max_length=128)
    filters: tuple[FilterDefinition, ...] = Field(default=(), max_length=64)
    actions: tuple[Interaction, ...] = Field(default=(), max_length=64)

    def visuals(self) -> Iterator[tuple[Page, VisualElement]]:
        for page in self.pages:
            for element in page.all_elements():
                if isinstance(element, VisualElement):
                    yield page, element

    @model_validator(mode="after")
    def references(self) -> Self:
        if len({item.dataset_id for item in self.datasets}) != len(self.datasets):
            raise ValueError("Dashboard dataset references must have unique dataset IDs")
        page_ids = {page.page_id for page in self.pages}
        elements = [element for page in self.pages for element in page.all_elements()]
        spacers = [spacer.element_id for page in self.pages for spacer in page.legacy_spacers]
        element_ids = {element.element_id for element in elements} | set(spacers)
        chart_ids = {element.element_id for element in elements
                     if isinstance(element, VisualElement)}
        filter_ids = {item.filter_id for item in self.filters}
        if len(page_ids) != len(self.pages) or len(element_ids) != len(elements) + len(spacers):
            raise ValueError("Page and element IDs must be unique across the dashboard")
        if len(filter_ids) != len(self.filters):
            raise ValueError("Filter IDs must be unique")
        if len({action.action_id for action in self.actions}) != len(self.actions):
            raise ValueError("Action IDs must be unique")
        for element in elements:
            if isinstance(element, FilterElement) and element.filter_id not in filter_ids:
                raise ValueError("Element references an unknown filter")
        for item in self.filters:
            if not set(item.depends_on) <= filter_ids:
                raise ValueError("Cascading filter references an unknown parent")
            if item.scope.page_id is not None and item.scope.page_id not in page_ids:
                raise ValueError("Filter references an unknown page")
            if not set(item.scope.widget_ids) <= chart_ids:
                raise ValueError("Filter target must be a visual")
        remaining = {item.filter_id: set(item.depends_on) for item in self.filters}
        while remaining:
            ready = {identity for identity, parents in remaining.items() if not parents}
            if not ready:
                raise ValueError("Cascading filters cannot contain a cycle")
            remaining = {identity: parents - ready for identity, parents in remaining.items()
                         if identity not in ready}
        for action in self.actions:
            if action.source_widget_id not in chart_ids:
                raise ValueError("Interaction source must be a visual")
            if not set(action.target_widget_ids) <= chart_ids:
                raise ValueError("Interaction target must be a visual")
            if action.target_page_id is not None and action.target_page_id not in page_ids:
                raise ValueError("Interaction references an unknown page")
        size = len(json.dumps(self.model_dump(mode="json"), separators=(",", ":")).encode())
        if size > MAX_DOCUMENT_BYTES:
            raise ValueError("Dashboard exceeds the one MiB document limit")
        return self
