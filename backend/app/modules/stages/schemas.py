"""Stage Manager schemas — Pydantic models for stage CRUD + file operations."""

from datetime import datetime

from pydantic import BaseModel, Field, ValidationInfo, field_validator

# ── Stage CRUD ─────────────────────────────────────────────────


class StageCreate(BaseModel):
    """Create a new stage."""
    name: str = Field(..., min_length=1, max_length=128)
    database_name: str = Field(..., min_length=1, max_length=128)
    schema_name: str = Field(..., min_length=1, max_length=128)
    storage_connection: str = Field(..., min_length=1, max_length=128)
    base_prefix: str = Field(default="", max_length=512)

    @field_validator("name", "database_name", "schema_name", "storage_connection", "base_prefix")
    @classmethod
    def validate_storage_width(cls, value: str, info: ValidationInfo) -> str:
        limit = 512 if info.field_name == "base_prefix" else 128
        if len(value.encode("utf-8")) > limit:
            raise ValueError(f"Must fit within {limit} UTF-8 bytes")
        if info.field_name != "base_prefix" and not value.strip():
            raise ValueError("Must not be blank")
        if "\x00" in value:
            raise ValueError("NUL byte is not allowed")
        return value


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
