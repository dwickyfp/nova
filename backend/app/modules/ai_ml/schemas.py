"""AI Provider Management schemas — Pydantic models for provider and model CRUD."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── AI Providers ──────────────────────────────────────────────


class AIProviderCreate(BaseModel):
    """Request body for creating a new AI provider."""

    name: str = Field(..., min_length=1, max_length=128)
    type: str = Field(..., min_length=1, max_length=32)
    endpoint: str = Field(..., min_length=1, max_length=512)
    api_key_env: str | None = Field(default=None, max_length=128)
    default_params: dict[str, Any] | None = None


class AIProviderResponse(BaseModel):
    """Response model for a single AI provider."""

    id: str
    name: str
    type: str
    endpoint: str
    api_key_env: str | None = None
    default_params: dict[str, Any] | None = None
    is_active: bool = True
    created_at: datetime | None = None
    created_by: str | None = None


class AIProviderListResponse(BaseModel):
    """Response model for listing AI providers."""

    providers: list[AIProviderResponse]
    count: int


# ── AI Models ─────────────────────────────────────────────────


class AIModelCreate(BaseModel):
    """Request body for creating a new AI model under a provider."""

    provider_id: str
    name: str = Field(..., min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=256)
    type: str = Field(..., min_length=1, max_length=32)
    max_tokens: int | None = None
    default_params: dict[str, Any] | None = None


class AIModelResponse(BaseModel):
    """Response model for a single AI model."""

    id: str
    provider_id: str
    name: str
    display_name: str | None = None
    type: str
    max_tokens: int | None = None
    default_params: dict[str, Any] | None = None
    is_active: bool = True
    created_at: datetime | None = None
    created_by: str | None = None


class AIModelListResponse(BaseModel):
    """Response model for listing AI models."""

    models: list[AIModelResponse]
    count: int
