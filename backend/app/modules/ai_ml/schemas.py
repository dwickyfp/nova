"""AI Provider Management schemas — Pydantic models for provider and model CRUD."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ── AI Providers ──────────────────────────────────────────────


class AIProviderCreate(BaseModel):
    """Request body for creating a new AI provider."""

    name: str = Field(..., min_length=1, max_length=128)
    type: str = Field(..., min_length=1, max_length=32)
    endpoint: str = Field(..., min_length=1, max_length=512)
    api_key: str | None = Field(default=None, max_length=512)
    default_params: dict[str, Any] | None = None


class AIProviderResponse(BaseModel):
    """Response model for a single AI provider.

    Never carries the plaintext ``api_key`` (AGENTS.md: credential-invisible).
    Clients receive ``has_api_key`` plus a masked preview they can render.
    """

    id: str
    name: str
    type: str
    endpoint: str
    has_api_key: bool = False
    api_key_masked: str | None = None
    default_params: dict[str, Any] | None = None
    is_active: bool = True
    created_at: datetime | None = None
    created_by: str | None = None


class AIProviderListResponse(BaseModel):
    """Response model for listing AI providers."""

    providers: list[AIProviderResponse]
    count: int


class AIProviderUpdate(BaseModel):
    """Request body for updating an existing AI provider."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    type: str | None = Field(default=None, min_length=1, max_length=32)
    endpoint: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = Field(default=None, max_length=512)
    default_params: dict[str, Any] | None = None
    is_active: bool | None = None


# ── AI Models ─────────────────────────────────────────────────


class AIModelCreate(BaseModel):
    """Request body for creating a new AI model under a provider."""

    provider_id: str
    name: str = Field(..., min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=256)
    type: Literal["llm", "embedding"]
    max_tokens: int | None = Field(default=None, gt=0)
    logical_alias: str | None = Field(default=None, min_length=1, max_length=128)
    revision: str | None = Field(default=None, min_length=1, max_length=128)
    dimensions: int | None = Field(default=None, gt=0)
    modality: Literal["text"] | None = None
    metric: Literal["cosine", "l2"] | None = None
    default_params: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_embedding(self) -> "AIModelCreate":
        if self.type == "embedding":
            if not all((self.logical_alias, self.revision, self.dimensions)):
                raise ValueError("Embedding models require logical_alias, revision, and dimensions")
            if self.max_tokens is not None:
                raise ValueError("max_tokens applies only to LLM models")
        elif any((self.logical_alias, self.revision, self.dimensions, self.modality, self.metric)):
            raise ValueError("Embedding metadata applies only to embedding models")
        return self


class AIModelResponse(BaseModel):
    """Response model for a single AI model."""

    id: str
    provider_id: str
    name: str
    display_name: str | None = None
    type: Literal["llm", "embedding"]
    max_tokens: int | None = None
    logical_alias: str | None = None
    revision: str | None = None
    dimensions: int | None = None
    modality: str | None = None
    metric: str | None = None
    default_params: dict[str, Any] | None = None
    is_active: bool = True
    created_at: datetime | None = None
    created_by: str | None = None


class AIModelListResponse(BaseModel):
    """Response model for listing AI models."""

    models: list[AIModelResponse]
    count: int


class AIModelUpdate(BaseModel):
    """Request body for updating an existing AI model."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=256)
    type: Literal["llm", "embedding"] | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    logical_alias: str | None = Field(default=None, min_length=1, max_length=128)
    revision: str | None = Field(default=None, min_length=1, max_length=128)
    dimensions: int | None = Field(default=None, gt=0)
    modality: Literal["text"] | None = None
    metric: Literal["cosine", "l2"] | None = None
    default_params: dict[str, Any] | None = None
    is_active: bool | None = None


# ── Test Connection ─────────────────────────────────────────────


class TestConnectionRequest(BaseModel):
    """Request body for testing a provider connection."""

    type: str = Field(..., min_length=1, max_length=32)
    endpoint: str = Field(..., min_length=1, max_length=512)
    api_key: str | None = Field(default=None, max_length=512)


class TestConnectionResponse(BaseModel):
    """Response model for test connection result."""

    success: bool
    message: str
    models: list[str] = Field(default_factory=list)
