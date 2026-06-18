"""Stage Manager schemas — Pydantic models for stage CRUD + file operations."""

from datetime import datetime
from pydantic import BaseModel, Field


# ── Stage CRUD ─────────────────────────────────────────────────


class StageCreate(BaseModel):
    """Create a new stage."""
    name: str = Field(..., min_length=1, max_length=256)
    database_name: str = Field(..., min_length=1, max_length=256)
    schema_name: str = Field(..., min_length=1, max_length=256)
    storage_connection: str = Field(..., min_length=1, max_length=256)
    base_prefix: str = Field(default="", max_length=1024)


class StageResponse(BaseModel):
    """Stage detail."""
    id: str
    name: str
    database_name: str
    schema_name: str
    storage_connection: str
    base_prefix: str
    created_at: datetime | None = None
    created_by: str | None = None


class StageListResponse(BaseModel):
    """Paginated stage list."""
    stages: list[StageResponse]
    count: int


# ── Stage Files ─────────────────────────────────────────────────


class StageFile(BaseModel):
    """A single file or directory inside a stage's storage path."""
    name: str
    size: int = 0
    last_modified: datetime | None = None
    is_dir: bool = False


class StageFileList(BaseModel):
    """File listing for a stage."""
    files: list[StageFile]
    prefix: str = ""
