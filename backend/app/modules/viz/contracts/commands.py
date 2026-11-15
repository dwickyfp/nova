"""Commands accept intent and revision, never a client-supplied actor."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .common import Contract, Name, Permission, Revision
from .dashboards import DashboardWidget, DatasetReference, Interaction, Layout, Page
from .filters import FilterDefinition


class Placement(Contract):
    widget_id: UUID
    layout: Layout


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
    page: Page


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


Change = Annotated[
    LayoutChange | WidgetAdd | WidgetReplace | WidgetRemove | PageAdd | PageRemove
    | PageRename | PageOrder | Rename | FiltersReplace | ActionsReplace | DatasetsReplace,
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
