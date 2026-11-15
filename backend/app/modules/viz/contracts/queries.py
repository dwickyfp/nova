"""Aggregate dataset queries used by charts, filter options and dashboards.

A query names dataset fields by ID; it never carries SQL, expressions or
renderer options. Filter values are typed scalars bound as engine parameters.
"""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import Contract, FieldId, LogicalType, Name, Revision, Scalar
from .filters import FilterTree
from .parameters import ParameterInput

QueryAggregation = Literal["sum", "avg", "min", "max", "count", "count_distinct"]
DateGrain = Literal["day", "week", "month", "quarter", "year"]


class QueryDimension(Contract):
    field_id: FieldId
    date_grain: DateGrain | None = None


class QueryMeasure(Contract):
    # A count without a field counts rows.
    field_id: FieldId | None = None
    aggregation: QueryAggregation

    @model_validator(mode="after")
    def field_required(self) -> Self:
        if self.field_id is None and self.aggregation != "count":
            raise ValueError("Only a row count may omit its field")
        return self


class QuerySort(Contract):
    column: str = Field(pattern=r"^[dm][0-9]{1,2}$")
    direction: Literal["asc", "desc"] = "asc"


class DatasetQueryRequest(Contract):
    revision: Revision
    draft: bool = Field(default=True, strict=True)
    dimensions: tuple[QueryDimension, ...] = Field(default=(), max_length=8)
    measures: tuple[QueryMeasure, ...] = Field(default=(), max_length=16)
    filters: FilterTree | None = None
    sort: tuple[QuerySort, ...] = Field(default=(), max_length=8)
    limit: int = Field(default=1000, strict=True, ge=1, le=10_000)
    parameters: tuple[ParameterInput, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def shape(self) -> Self:
        if not self.dimensions and not self.measures:
            raise ValueError("A query needs a dimension or a measure")
        columns = {f"d{i}" for i in range(len(self.dimensions))} | {
            f"m{i}" for i in range(len(self.measures))
        }
        keys = [item.column for item in self.sort]
        if not set(keys) <= columns or len(set(keys)) != len(keys):
            raise ValueError("Sort keys must name distinct result columns")
        return self


class QueryColumn(Contract):
    key: str
    name: Name
    logical_type: LogicalType
    role: Literal["dimension", "measure"]
    field_id: FieldId | None = None
    aggregation: QueryAggregation | None = None
    date_grain: DateGrain | None = None
    timezone: str | None = None


class DatasetQueryResult(Contract):
    dataset_id: UUID
    revision: Revision
    columns: tuple[QueryColumn, ...]
    rows: tuple[tuple[Scalar | None, ...], ...]
    truncated: bool
