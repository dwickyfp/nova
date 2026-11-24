"""Commands accept intent and revision, never a client-supplied actor."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .common import Contract, Name, Permission, Revision
from .dashboards import (
    MAX_ELEMENTS_PER_PAGE,
    ChartOverrides,
    ChartReference,
    DatasetReference,
    DatumAnchor,
    Element,
    ElementStyle,
    Endpoint,
    Geometry,
    IconName,
    Interaction,
    MobileProjection,
    Page,
    PageCanvas,
    RichText,
)
from .dashboards_v2 import DashboardWidget, Layout, PageV2
from .filters import FilterDefinition, FilterTree


class Placement(Contract):
    widget_id: UUID
    layout: Layout


# ── Legacy grid operations (schema 2). Kept byte-for-byte so stored receipts
# replay; ``operations.apply_operation`` adapts them onto the canvas. ──────

class LayoutChange(Contract):
    op: Literal["layout.apply"] = "layout.apply"
    page_id: UUID
    placements: tuple[Placement, ...] = Field(min_length=1, max_length=100)


class WidgetAdd(Contract):
    op: Literal["widget.add"] = "widget.add"
    page_id: UUID
    widget: DashboardWidget


class WidgetReplace(Contract):
    op: Literal["widget.replace"] = "widget.replace"
    page_id: UUID
    widget: DashboardWidget


class WidgetRemove(Contract):
    op: Literal["widget.remove"] = "widget.remove"
    page_id: UUID
    widget_id: UUID


class PageAdd(Contract):
    op: Literal["page.add"] = "page.add"
    # A legacy grid page is tried first so an old request dumps exactly as it did.
    page: PageV2 | Page = Field(union_mode="left_to_right")


class PageRemove(Contract):
    op: Literal["page.remove"] = "page.remove"
    page_id: UUID


class PageRename(Contract):
    op: Literal["page.rename"] = "page.rename"
    page_id: UUID
    title: Name


class PageOrder(Contract):
    op: Literal["page.reorder"] = "page.reorder"
    page_ids: tuple[UUID, ...] = Field(min_length=1, max_length=15)


class Rename(Contract):
    op: Literal["dashboard.rename"] = "dashboard.rename"
    title: Name


class FiltersReplace(Contract):
    op: Literal["filters.replace"] = "filters.replace"
    filters: tuple[FilterDefinition, ...] = Field(max_length=64)


class ActionsReplace(Contract):
    op: Literal["actions.replace"] = "actions.replace"
    actions: tuple[Interaction, ...] = Field(max_length=64)


class DatasetsReplace(Contract):
    op: Literal["datasets.replace"] = "datasets.replace"
    datasets: tuple[DatasetReference, ...] = Field(max_length=128)


# ── Canvas operations (schema 3) ────────────────────────────────────────

class StackAbove(Contract):
    above: UUID


class ElementsAdd(Contract):
    """Add elements as one block: in front, at the back, or just above another."""

    op: Literal["elements.add"] = "elements.add"
    page_id: UUID
    elements: tuple[Element, ...] = Field(min_length=1, max_length=MAX_ELEMENTS_PER_PAGE)
    stack: Literal["front", "back"] | StackAbove = "front"


class ElementPatch(Contract):
    """Properties to set on one element; anything omitted is unchanged.

    The merged element is validated again, so a property its kind does not have
    is refused. ``element_id``, ``kind`` and group ``children`` cannot change here.
    """

    name: str | None = None
    geometry: Geometry | None = None
    locked: bool | None = None
    hidden: bool | None = None
    decorative: bool | None = None
    style: ElementStyle | None = None
    title: str | None = None
    chart_ref: ChartReference | None = None
    overrides: ChartOverrides | None = None
    filters: FilterTree | None = None
    filter_id: UUID | None = None
    role: Literal["title", "heading", "body", "caption"] | None = None
    text: RichText | None = None
    asset_id: UUID | None = None
    alt: str | None = None
    fit: Literal["contain", "cover"] | None = None
    shape: Literal["rect", "ellipse", "line", "pill", "triangle"] | None = None
    icon: IconName | None = None
    label: str | None = None
    start: Endpoint | None = None
    end: Endpoint | None = None
    head: Literal["none", "arrow", "dot"] | None = None
    curve: Literal["straight", "arc", "elbow"] | None = None
    target: DatumAnchor | None = None
    placement: Literal["auto", "n", "e", "s", "w"] | None = None

    @model_validator(mode="after")
    def not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("An element update must set at least one property")
        return self


class ElementUpdate(Contract):
    element_id: UUID
    set: ElementPatch


class ElementsUpdate(Contract):
    op: Literal["elements.update"] = "elements.update"
    page_id: UUID
    updates: tuple[ElementUpdate, ...] = Field(min_length=1, max_length=MAX_ELEMENTS_PER_PAGE)


class ElementsRemove(Contract):
    """Remove elements; connectors, annotations and interactions that depended on them go too."""

    op: Literal["elements.remove"] = "elements.remove"
    page_id: UUID
    element_ids: tuple[UUID, ...] = Field(min_length=1, max_length=MAX_ELEMENTS_PER_PAGE)


class ElementPlacement(Contract):
    element_id: UUID
    geometry: Geometry


class ElementsArrange(Contract):
    """New geometry for some elements of one page, applied together."""

    op: Literal["elements.arrange"] = "elements.arrange"
    page_id: UUID
    placements: tuple[ElementPlacement, ...] = Field(
        min_length=1, max_length=MAX_ELEMENTS_PER_PAGE,
    )


class ElementsReorder(Contract):
    """The complete back-to-front order of the page's top-level elements."""

    op: Literal["elements.reorder"] = "elements.reorder"
    page_id: UUID
    element_ids: tuple[UUID, ...] = Field(max_length=MAX_ELEMENTS_PER_PAGE)


class ElementsGroup(Contract):
    """Put top-level elements into a new group, stacked where the topmost of them was.

    The group's box is the smallest box around its members; their positions on the
    page do not change.
    """

    op: Literal["elements.group"] = "elements.group"
    page_id: UUID
    group_id: UUID
    element_ids: tuple[UUID, ...] = Field(min_length=1, max_length=MAX_ELEMENTS_PER_PAGE)
    name: str = Field(default="", max_length=120)


class ElementsUngroup(Contract):
    """Release a top-level group's children onto the page, stacked where the group was."""

    op: Literal["elements.ungroup"] = "elements.ungroup"
    page_id: UUID
    group_id: UUID


class PageCanvasSet(Contract):
    op: Literal["page.canvas.set"] = "page.canvas.set"
    page_id: UUID
    canvas: PageCanvas


class PageTabOrderSet(Contract):
    op: Literal["page.tab_order.set"] = "page.tab_order.set"
    page_id: UUID
    element_ids: tuple[UUID, ...] = Field(max_length=MAX_ELEMENTS_PER_PAGE)


class PageMobileSet(Contract):
    op: Literal["page.mobile.set"] = "page.mobile.set"
    page_id: UUID
    mobile: MobileProjection


Change = Annotated[
    LayoutChange | WidgetAdd | WidgetReplace | WidgetRemove | PageAdd | PageRemove
    | PageRename | PageOrder | Rename | FiltersReplace | ActionsReplace | DatasetsReplace
    | ElementsAdd | ElementsUpdate | ElementsRemove | ElementsArrange | ElementsReorder
    | ElementsGroup | ElementsUngroup | PageCanvasSet | PageTabOrderSet | PageMobileSet,
    Field(discriminator="op"),
]


class Operation(Contract):
    operation_id: UUID
    expected_revision: Revision
    changes: tuple[Change, ...] = Field(min_length=1, max_length=100)


class Grant(Contract):
    subject_type: Literal["user", "role"]
    subject_id: str = Field(min_length=1, max_length=128)
    permissions: tuple[Permission, ...] = Field(min_length=1, max_length=7)
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def distinct_permissions(self) -> Self:
        if len(set(self.permissions)) != len(self.permissions):
            raise ValueError("Grant permissions must be unique")
        if "publish" in self.permissions and not {"edit", "manage"} & set(self.permissions):
            raise ValueError("Publish requires edit or manage permission")
        if self.subject_id != self.subject_id.strip():
            raise ValueError("Grant subject must be a normalized directory identifier")
        return self


class GrantsReplace(Contract):
    operation_id: UUID
    expected_revision: Revision
    grants: tuple[Grant, ...] = Field(max_length=1000)

    @model_validator(mode="after")
    def distinct_subjects(self) -> Self:
        subjects = {(grant.subject_type, grant.subject_id) for grant in self.grants}
        if len(subjects) != len(self.grants):
            raise ValueError("Combine permissions for each grant subject")
        return self


class PublicationRequest(Contract):
    operation_id: UUID
    expected_revision: Revision
    release_notes: str = Field(default="", max_length=2000)


class RestoreRequest(Contract):
    operation_id: UUID
    expected_revision: Revision
    publication_id: UUID


class DeleteRequest(Contract):
    operation_id: UUID
    expected_revision: Revision
