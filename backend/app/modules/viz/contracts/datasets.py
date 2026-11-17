"""Dataset definitions contain source identity and schema, never query rows."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import Contract, FieldId, LogicalType, Name, Revision
from .filters import RelativeWindow
from .parameters import Parameter as Parameter


class ObjectSource(Contract):
    kind: Literal["table", "view"]
    catalog: FieldId
    database: FieldId
    schema_name: FieldId | None = None
    object_name: FieldId
    physical_id: str = Field(min_length=1, max_length=256)


class SqlSource(Contract):
    kind: Literal["sql"] = "sql"
    sql: str = Field(min_length=1, max_length=200_000)
    ast_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    catalog: FieldId
    database: FieldId
    schema_name: FieldId | None = None
    dependencies: tuple[ObjectSource, ...] = Field(min_length=1, max_length=64)


class SemanticSelection(Contract):
    kind: Literal["semantic_model"] = "semantic_model"
    model_id: str = Field(min_length=1, max_length=128)
    model_revision: Revision
    model_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    dimensions: tuple[FieldId, ...] = Field(default=(), max_length=32)
    metrics: tuple[FieldId, ...] = Field(min_length=1, max_length=64)
    named_filters: tuple[FieldId, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def unique_selections(self) -> Self:
        outputs = (*self.dimensions, *self.metrics)
        if len({name.casefold() for name in outputs}) != len(outputs):
            raise ValueError("Semantic output names must be unique")
        if len(set(self.named_filters)) != len(self.named_filters):
            raise ValueError("Semantic filters must be unique")
        return self


class SemanticSource(SemanticSelection):
    catalog: Literal["default_catalog"] = "default_catalog"
    database: FieldId
    schema_name: Literal["default"] = "default"
    dependencies: tuple[ObjectSource, ...] = Field(min_length=1, max_length=64)


class LocalMetricSource(Contract):
    kind: Literal["dashboard_local_metric_view"] = "dashboard_local_metric_view"
    dataset_id: UUID
    dataset_revision: Revision


DatasetSource = Annotated[
    ObjectSource | SqlSource | SemanticSource | LocalMetricSource, Field(discriminator="kind")
]


class DatasetField(Contract):
    field_id: FieldId
    name: Name
    source_column: FieldId
    logical_type: LogicalType
    physical_type: str = Field(min_length=1, max_length=128)
    semantic_role: Literal["dimension", "measure"]
    nullable: bool
    timezone: str | None = Field(default=None, max_length=64)
    unit: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class Metric(Contract):
    metric_id: UUID
    revision: Revision
    name: Name
    formula: str = Field(min_length=1, max_length=8000)
    level: Literal["row", "aggregate", "post_aggregate"]
    grain: tuple[FieldId, ...] = Field(max_length=32)
    unit: str = Field(min_length=1, max_length=64)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    scope: Literal["dashboard_local", "shared"]
    addressing: tuple[FieldId, ...] = Field(default=(), max_length=8)
    period: RelativeWindow | None = None
    missing_periods: Literal["gap", "zero"] = "gap"
    leap_day: Literal["last_day", "null"] = "last_day"


class JoinKey(Contract):
    local_field_id: FieldId
    foreign_field_id: FieldId


class Relationship(Contract):
    relationship_id: UUID
    dataset_id: UUID
    dataset_revision: Revision
    keys: tuple[JoinKey, ...] = Field(min_length=1, max_length=16)
    cardinality: Literal["1:1", "many:1", "1:many", "many:many"]
    join: Literal["inner", "left", "semi"]


class DatasetDefinition(Contract):
    schema_version: Literal[2] = 2
    dataset_id: UUID
    title: Name
    source: DatasetSource
    fields: tuple[DatasetField, ...] = Field(min_length=1, max_length=512)
    parameters: tuple[Parameter, ...] = Field(default=(), max_length=32)
    grain: tuple[FieldId, ...] = Field(default=(), max_length=32)
    metrics: tuple[Metric, ...] = Field(default=(), max_length=64)
    relationships: tuple[Relationship, ...] = Field(default=(), max_length=32)
    schema_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def references(self) -> Self:
        if isinstance(self.source, LocalMetricSource) and self.source.dataset_id == self.dataset_id:
            raise ValueError("A local metric source must reference a distinct dataset")
        field_ids = {field.field_id for field in self.fields}
        if len(field_ids) != len(self.fields):
            raise ValueError("Dataset field IDs must be unique")
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("Dataset field names must be unambiguous")
        if len({item.parameter_id for item in self.parameters}) != len(self.parameters):
            raise ValueError("Parameter IDs must be unique")
        if len({item.metric_id for item in self.metrics}) != len(self.metrics):
            raise ValueError("Metric IDs must be unique")
        if len({item.name for item in self.metrics}) != len(self.metrics):
            raise ValueError("Metric names must be unique")
        if len({item.relationship_id for item in self.relationships}) != len(self.relationships):
            raise ValueError("Relationship IDs must be unique")
        references = set(self.grain)
        for metric in self.metrics:
            references.update(metric.grain)
            references.update(metric.addressing)
        for relationship in self.relationships:
            if relationship.dataset_id == self.dataset_id:
                raise ValueError("Self joins require a distinct governed source binding")
            references.update(key.local_field_id for key in relationship.keys)
        if not references <= field_ids:
            raise ValueError("Dataset references an unknown field")
        return self
