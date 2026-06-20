"""Pydantic schemas for the Database Explorer API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class CatalogInfo(BaseModel):
    """A single catalog (top-level namespace)."""

    name: str
    type: str = Field(default="Internal")
    comment: str | None = None
    databases: list[str] = Field(default_factory=list)


class CatalogsResponse(BaseModel):
    """Response for GET /explorer/catalogs."""

    catalogs: list[CatalogInfo]


# ---------------------------------------------------------------------------
# Database summary
# ---------------------------------------------------------------------------

class TableSummary(BaseModel):
    """Lightweight table info for tree view."""

    name: str
    table_model: str | None = None  # PRIMARY_KEYS, DUPLICATE_KEYS, etc.
    engine: str | None = None
    row_count: int | None = None
    data_size: int | None = None
    create_time: datetime | None = None


class ViewSummary(BaseModel):
    name: str
    definer: str | None = None
    is_updatable: str | None = None


class MaterializedViewSummary(BaseModel):
    name: str
    refresh_type: str | None = None
    is_active: bool | None = None
    last_refresh_state: str | None = None
    table_rows: int | None = None
    query_rewrite_status: str | None = None


class FunctionSummary(BaseModel):
    name: str
    routine_type: str | None = None
    definer: str | None = None
    created: datetime | None = None


class PipeSummary(BaseModel):
    name: str
    state: str | None = None
    target_table: str | None = None
    load_status: str | None = None
    last_error: str | None = None
    created_time: datetime | None = None


class StageSummary(BaseModel):
    name: str
    storage_connection: str | None = None
    base_prefix: str | None = None
    created_at: datetime | None = None


class DatabaseObjectsResponse(BaseModel):
    """All objects inside a database for the explorer tree."""

    database: str
    tables: list[TableSummary]
    views: list[ViewSummary]
    materialized_views: list[MaterializedViewSummary]
    functions: list[FunctionSummary]
    pipes: list[PipeSummary]
    stages: list[StageSummary]
    summary: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Detail views
# ---------------------------------------------------------------------------

class ColumnInfo(BaseModel):
    name: str
    ordinal_position: int
    data_type: str
    column_type: str | None = None
    is_nullable: str | None = None
    column_key: str | None = None
    column_default: str | None = None
    extra: str | None = None
    column_comment: str | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None
    character_maximum_length: int | None = None


class PartitionInfo(BaseModel):
    name: str
    partition_id: int | None = None
    partition_key: str | None = None
    partition_value: str | None = None
    row_count: int | None = None
    data_size: int | None = None
    storage_size: int | None = None
    buckets: int | None = None
    replication_num: int | None = None
    visible_version: int | None = None
    data_version: int | None = None


class TableProperties(BaseModel):
    table_model: str | None = None
    primary_key: str | None = None
    partition_key: str | None = None
    distribute_key: str | None = None
    distribute_type: str | None = None
    distribute_bucket: str | int | None = None
    sort_key: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    create_ddl: str | None = None


class TableDetailResponse(BaseModel):
    name: str
    database: str
    columns: list[ColumnInfo]
    properties: TableProperties
    partitions: list[PartitionInfo]
    row_count: int | None = None
    data_size: int | None = None


class ViewDetailResponse(BaseModel):
    name: str
    database: str
    definition: str | None = None
    definer: str | None = None
    security_type: str | None = None
    is_updatable: str | None = None
    create_ddl: str | None = None


class MaterializedViewDetailResponse(BaseModel):
    name: str
    database: str
    definition: str | None = None
    refresh_type: str | None = None
    is_active: bool | None = None
    inactive_reason: str | None = None
    task_name: str | None = None
    last_refresh_state: str | None = None
    last_refresh_error: str | None = None
    last_refresh_start_time: datetime | None = None
    last_refresh_finished_time: datetime | None = None
    last_refresh_duration: float | None = None
    table_rows: int | None = None
    query_rewrite_status: str | None = None
    creator: str | None = None


class FunctionDetailResponse(BaseModel):
    name: str
    database: str
    routine_type: str | None = None
    definition: str | None = None
    definer: str | None = None
    created: datetime | None = None
    last_altered: datetime | None = None
    is_deterministic: str | None = None
    sql_data_access: str | None = None
    comment: str | None = None


class PipeDetailResponse(BaseModel):
    name: str
    database: str
    pipe_id: int | None = None
    state: str | None = None
    target_table: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    load_status: str | None = None
    last_error: str | None = None
    created_time: datetime | None = None
