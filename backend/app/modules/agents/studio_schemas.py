"""Nova Studio API schemas — the standalone chat surface's settings and catalogs.

Studio runs outside Nova's layout but uses the same session. These shapes cover
what its Settings panel and sidebar need: the caller's identity, the roles and
warehouses they can pick, and their Studio preferences.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.studio_capabilities import StudioRuntimeCapabilities
from app.modules.assistant.schemas import utc_datetime

StudioTheme = Literal["light", "dark", "system"]


class StudioIdentity(BaseModel):
    """Who the caller is, and what they can switch between."""

    username: str
    roles: list[str] = Field(default_factory=list)
    active_role: str | None = None
    warehouses: list[str] = Field(default_factory=list)
    active_warehouse: str | None = None
    #: Administrator-enabled access to Studio News; the News routes enforce it.
    news_enabled: bool = False


class StudioPreferences(BaseModel):
    """Studio-local UI settings. Stored in the preference metadata (PostgreSQL)."""

    theme: StudioTheme = "system"
    language: str = "en"
    preferred_name: str | None = None
    #: Role pinned for Studio queries; ``None`` uses the session's default role.
    role: str | None = None
    #: Warehouse (resource group) pinned for Studio queries.
    warehouse: str | None = None
    #: Whether the thinking/plan trace is shown by default in a new chat.
    extended_thinking: bool = True


class StudioSettingsResponse(BaseModel):
    identity: StudioIdentity
    preferences: StudioPreferences


class StudioPreferencesUpdate(BaseModel):
    """PATCH semantics: only the provided fields change."""

    theme: StudioTheme | None = None
    language: str | None = Field(default=None, max_length=16)
    preferred_name: str | None = Field(default=None, max_length=128)
    role: str | None = Field(default=None, max_length=128)
    warehouse: str | None = Field(default=None, max_length=128)
    extended_thinking: bool | None = None


class StudioCapabilities(BaseModel):
    """What a Studio agent can use, for the Capabilities view."""

    agents: list[dict] = Field(default_factory=list)
    skills: list[dict] = Field(default_factory=list)
    tools: list[dict] = Field(default_factory=list)
    connectors: list[dict] = Field(default_factory=list)
    runtime: StudioRuntimeCapabilities | None = None


# ── Shared legacy dashboards ─────────────────────────────────


class DashboardComputeRequest(BaseModel):
    spec: dict[str, Any]
    filter_values: dict[str, dict[str, Any]] = Field(default_factory=dict)
    zone_filters: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


# ── Observability ──────────────────────────────────────────────


class UsageSeriesPoint(BaseModel):
    date: str
    sessions: int = 0
    tokens: int = 0


class UsageSummary(BaseModel):
    total_sessions: int = 0
    total_tokens: int = 0
    total_active_users: int = 0
    series: list[UsageSeriesPoint] = Field(default_factory=list)


class SessionView(BaseModel):
    thread_id: str
    title: str
    user_name: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    total_tokens: int = 0
    first_input: str = ""

    @field_validator("created_at", "updated_at")
    @classmethod
    def mark_timestamps_utc(cls, value: datetime) -> datetime:
        return utc_datetime(value)


class SessionListResponse(BaseModel):
    sessions: list[SessionView]


class TurnView(BaseModel):
    message_id: str
    seq: int
    role: str
    content: str = ""
    model_name: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    created_at: datetime
    #: The assistant turn's trace: ordered reasoning/tool/answer steps.
    steps: list[dict] = Field(default_factory=list)
    #: The system prompt sent to the model for this turn.
    instructions: str | None = None

    @field_validator("created_at")
    @classmethod
    def mark_created_at_utc(cls, value: datetime) -> datetime:
        return utc_datetime(value)


class ThreadTraceResponse(BaseModel):
    thread_id: str
    title: str
    user_name: str
    agent_id: str | None = None
    created_at: datetime
    updated_at: datetime
    total_tokens: int = 0
    turns: list[TurnView] = Field(default_factory=list)

    @field_validator("created_at", "updated_at")
    @classmethod
    def mark_timestamps_utc(cls, value: datetime) -> datetime:
        return utc_datetime(value)


class AccessCheckRequest(BaseModel):
    role_name: str = Field(min_length=1, max_length=128)


class RoleListResponse(BaseModel):
    roles: list[str] = Field(default_factory=list)
    count: int = 0
