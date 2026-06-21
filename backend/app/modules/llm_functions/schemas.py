"""LLM Function Management schemas — Pydantic models for alias CRUD and UDF registration."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Function Types ────────────────────────────────────────────

VALID_FUNCTION_TYPES = {
    "complete",
    "sentiment",
    "classify",
    "summarize",
    "extract",
    "translate",
    "filter",
    "embed",
}


# ── Aliases ───────────────────────────────────────────────────


class AliasCreate(BaseModel):
    """Request body for creating a model alias."""

    alias_name: str = Field(..., min_length=1, max_length=128)
    function_type: str = Field(..., min_length=1, max_length=32)
    provider_id: str = Field(..., min_length=1, max_length=64)
    model_id: str | None = None
    system_prompt: str | None = None
    default_params: dict[str, Any] | None = None
    is_default: bool = True


class AliasUpdate(BaseModel):
    """Request body for updating a model alias."""

    alias_name: str | None = Field(default=None, min_length=1, max_length=128)
    function_type: str | None = Field(default=None, min_length=1, max_length=32)
    provider_id: str | None = Field(default=None, min_length=1, max_length=64)
    model_id: str | None = None
    system_prompt: str | None = None
    default_params: dict[str, Any] | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class AliasResponse(BaseModel):
    """Response model for a single alias."""

    id: str
    alias_name: str
    function_type: str
    provider_id: str
    provider_name: str | None = None
    model_id: str | None = None
    model_name: str | None = None
    system_prompt: str | None = None
    default_params: dict[str, Any] | None = None
    is_default: bool = True
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: str | None = None


class AliasListResponse(BaseModel):
    """Response model for listing aliases."""

    aliases: list[AliasResponse]
    count: int


# ── UDF Registration ──────────────────────────────────────────


class UDFStatusItem(BaseModel):
    """Status of a single LLM UDF."""

    function_name: str
    function_type: str
    alias_name: str | None = None
    provider_name: str | None = None
    model_name: str | None = None
    registered: bool
    error: str | None = None


class UDFStatusResponse(BaseModel):
    """Response model for UDF registration status."""

    functions: list[UDFStatusItem]
    total: int
    registered: int
    failed: int


class UDFRegisterResponse(BaseModel):
    """Response model for UDF registration action."""

    success: bool
    registered: int
    failed: int
    details: list[UDFStatusItem]
