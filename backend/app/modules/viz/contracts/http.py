"""Public document responses expose metadata, never execution credentials."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from .charts import ChartDefinition
from .common import Contract, FieldId, Name, Permission, Revision
from .dashboards import DashboardDefinition
from .dashboards_v2 import DashboardDefinitionV2
from .datasets import DatasetDefinition, DatasetField, Parameter


class ChartCreate(Contract):
    workspace_id: str = Field(min_length=1, max_length=128)
    definition: ChartDefinition


class DashboardCreate(Contract):
    workspace_id: str = Field(min_length=1, max_length=128)
    #: A schema-2 grid definition is still accepted and stored upcast to the canvas.
    definition: DashboardDefinitionV2 | DashboardDefinition = Field(union_mode="left_to_right")


class OperationReceipt(Contract):
    operation_id: UUID
    resource_id: UUID
    base_revision: int = Field(ge=0)
    new_revision: Revision
    event_sequence: Revision
    committed_at: AwareDatetime


class PublishedMetric(Contract):
    metric_id: UUID
    revision: Revision
    name: Name
    level: Literal["row", "aggregate", "post_aggregate"]
    unit: str
    currency: str | None


class DatasetPublication(Contract):
    schema_version: Literal[2] = 2
    definition_scope: Literal["publication"] = "publication"
    dataset_id: UUID
    title: Name
    source_kind: Literal["table", "view", "sql", "semantic_model", "dashboard_local_metric_view"]
    fields: tuple[DatasetField, ...] = Field(min_length=1, max_length=512)
    parameters: tuple[Parameter, ...] = Field(max_length=32)
    metrics: tuple[PublishedMetric, ...] = Field(max_length=64)
    grain: tuple[FieldId, ...] = Field(max_length=32)


def dataset_publication(document: DatasetDefinition) -> DatasetPublication:
    return DatasetPublication(
        dataset_id=document.dataset_id, title=document.title, source_kind=document.source.kind,
        fields=document.fields, parameters=document.parameters, grain=document.grain,
        metrics=tuple(PublishedMetric(
            metric_id=metric.metric_id, revision=metric.revision, name=metric.name,
            level=metric.level, unit=metric.unit, currency=metric.currency,
        ) for metric in document.metrics),
    )


class DocumentSnapshot(Contract):
    resource_id: UUID
    revision: Revision
    metadata_revision: Revision
    grants_revision: Revision
    publication_id: UUID | None
    definition: ChartDefinition | DashboardDefinition | DatasetDefinition | DatasetPublication


class DocumentSummary(Contract):
    resource_id: UUID
    kind: Literal["chart", "dashboard", "dataset"]
    owner_name: str
    workspace_id: str
    updated_at: AwareDatetime
    metadata_revision: Revision
    grants_revision: Revision
    current_publication_id: UUID | None
    revision: Revision
    title: str


class DatasetReferenceSummary(Contract):
    dataset_id: UUID
    revision: Revision
    title: Name | None


class DashboardDatasets(Contract):
    dashboard_id: UUID
    revision: Revision
    datasets: tuple[DatasetReferenceSummary, ...] = Field(max_length=128)


class RevisionSummary(Contract):
    revision: Revision
    schema_version: int
    digest: str
    author_name: str
    created_at: AwareDatetime


class GrantSummary(Contract):
    subject_type: Literal["user", "role"]
    subject_id: str
    permission: Permission
    expires_at: AwareDatetime | None
    granted_by: str
    granted_at: AwareDatetime


class GrantState(Contract):
    revision: Revision
    grants: list[GrantSummary]


class DocumentEvent(Contract):
    event_id: UUID
    operation_id: UUID
    sequence: Revision
    event_kind: Literal["create", "edit", "publish", "restore", "grants", "delete"]
    created_at: AwareDatetime


class EventPage(Contract):
    events: list[DocumentEvent]
    through: Revision
    next_sequence: int = Field(ge=0, le=9_007_199_254_740_991)
