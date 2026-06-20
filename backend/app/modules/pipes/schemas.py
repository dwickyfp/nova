"""Pipe Manager schemas — request/response models for pipe CRUD and file tracking."""

from pydantic import BaseModel, Field
from typing import Literal


class PipeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    database: str = Field(default='', max_length=256)
    sql: str = Field(..., min_length=1)  # The INSERT INTO ... SELECT FROM FILES(...) statement
    auto_ingest: bool = True
    poll_interval: int = 300  # seconds
    batch_size: str = '1GB'
    batch_files: int = 256


class PipeResponse(BaseModel):
    name: str
    database: str
    state: str  # e.g. 'RUNNING', 'SUSPENDED'
    sql: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    created_at: str | None = None


class PipeListResponse(BaseModel):
    pipes: list[PipeResponse]
    count: int


class PipeFileResponse(BaseModel):
    file_name: str
    state: str  # LOADED, LOADING, ERROR
    file_size: int | None = None
    error_message: str | None = None
    loaded_at: str | None = None


class PipeFileListResponse(BaseModel):
    files: list[PipeFileResponse]
    count: int
