"""Pipe Manager schemas — request/response models for pipe CRUD and file tracking."""

from pydantic import BaseModel, Field


class PipeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256, pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")
    database: str = Field(default="", max_length=256, pattern=r"^(?:[A-Za-z_][A-Za-z0-9_$]*)?$")
    sql: str = Field(..., min_length=1)  # The INSERT INTO ... SELECT FROM FILES(...) statement
    auto_ingest: bool = True
    poll_interval: int = Field(300, ge=1, le=3600)
    batch_size: str = Field("1GB", pattern=r"^[1-9][0-9]*(?:B|KB|MB|GB|TB|PB)?$")
    batch_files: int = Field(256, ge=1)


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
