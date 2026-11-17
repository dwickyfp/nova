"""Dataset requests select sources; the server owns their identity and schema."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .common import Contract, FieldId, Name, Revision, Scalar
from .datasets import DatasetField, ObjectSource, SemanticSelection, SemanticSource, SqlSource
from .parameters import Parameter, ParameterInput


class ObjectSelection(Contract):
    kind: Literal["table", "view"]
    catalog: FieldId = "default_catalog"
    database: FieldId
    schema_name: FieldId = "default"
    object_name: FieldId
    columns: tuple[FieldId, ...] | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def unique_columns(self) -> "ObjectSelection":
        if self.columns and len({name.casefold() for name in self.columns}) != len(self.columns):
            raise ValueError("Selected columns must be unique")
        return self


class ObjectInspect(Contract):
    dataset_id: UUID
    source: ObjectSelection


class ObjectInspection(Contract):
    dataset_id: UUID
    source: ObjectSource
    fields: tuple[DatasetField, ...] = Field(min_length=1, max_length=512)
    schema_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ObjectDatasetUpdate(ObjectInspect):
    title: Name
    expected_physical_id: str = Field(min_length=1, max_length=256)
    expected_schema_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ObjectDatasetCreate(ObjectDatasetUpdate):
    workspace_id: str = Field(min_length=1, max_length=128)


class SqlSelection(Contract):
    kind: Literal["sql"] = "sql"
    catalog: Literal["default_catalog"] = "default_catalog"
    database: FieldId
    schema_name: Literal["default"] = "default"
    sql: str = Field(min_length=1, max_length=200_000)


class SqlInspect(Contract):
    dataset_id: UUID
    source: SqlSelection
    parameters: tuple[Parameter, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def unique_parameters(self) -> "SqlInspect":
        if len({item.parameter_id for item in self.parameters}) != len(self.parameters):
            raise ValueError("Parameter IDs must be unique")
        return self


class SqlInspection(Contract):
    dataset_id: UUID
    source: SqlSource
    fields: tuple[DatasetField, ...] = Field(min_length=1, max_length=512)
    schema_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    parameters: tuple[Parameter, ...] = Field(default=(), max_length=32)


class SqlDatasetUpdate(SqlInspect):
    title: Name
    expected_schema_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class SqlDatasetCreate(SqlDatasetUpdate):
    workspace_id: str = Field(min_length=1, max_length=128)


class SemanticInspect(Contract):
    dataset_id: UUID
    source: SemanticSelection


class SemanticInspection(Contract):
    dataset_id: UUID
    source: SemanticSource
    fields: tuple[DatasetField, ...] = Field(min_length=1, max_length=512)
    schema_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class SemanticDatasetUpdate(SemanticInspect):
    title: Name
    expected_schema_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class SemanticDatasetCreate(SemanticDatasetUpdate):
    workspace_id: str = Field(min_length=1, max_length=128)


DatasetInspect = ObjectInspect | SqlInspect | SemanticInspect
DatasetInspection = ObjectInspection | SqlInspection | SemanticInspection
DatasetCreate = ObjectDatasetCreate | SqlDatasetCreate | SemanticDatasetCreate
DatasetUpdate = ObjectDatasetUpdate | SqlDatasetUpdate | SemanticDatasetUpdate


class DashboardDatasetCreate(Contract):
    dataset: DatasetUpdate


class DatasetPreviewRequest(Contract):
    revision: Revision
    draft: bool = Field(default=True, strict=True)
    limit: int = Field(default=100, strict=True, ge=1, le=200)
    offset: int = Field(default=0, strict=True, ge=0, le=10_000)
    parameters: tuple[ParameterInput, ...] = Field(default=(), max_length=32, repr=False)

    @model_validator(mode="after")
    def unique_parameters(self) -> "DatasetPreviewRequest":
        if len({item.parameter_id for item in self.parameters}) != len(self.parameters):
            raise ValueError("Parameter inputs must be unique")
        return self


class DatasetPreview(Contract):
    dataset_id: UUID
    revision: Revision
    fields: tuple[DatasetField, ...] = Field(min_length=1, max_length=512)
    rows: tuple[tuple[Scalar | None, ...], ...] = Field(max_length=200)
    offset: int = Field(strict=True, ge=0, le=10_000)
    has_more: bool
    next_offset: int | None = Field(default=None, strict=True, ge=1, le=10_000)
