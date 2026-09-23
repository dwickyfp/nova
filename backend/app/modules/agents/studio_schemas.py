"""Nova Studio API schemas — the standalone chat surface's settings and catalogs.

Studio runs outside Nova's layout but uses the same session. These shapes cover
what its Settings panel and sidebar need: the caller's identity, the roles and
warehouses they can pick, and their Studio preferences.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.assistant.schemas import utc_datetime

StudioTheme = Literal["light", "dark", "system"]


class StudioIdentity(BaseModel):
    """Who the caller is, and what they can switch between."""

    username: str
    roles: list[str] = Field(default_factory=list)
    active_role: str | None = None
    warehouses: list[str] = Field(default_factory=list)
    active_warehouse: str | None = None


class StudioPreferences(BaseModel):
    """Studio-local UI settings. Stored in NOVA_SYSTEM.CONFIG_USER_PREFERENCES."""

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


# ── Query-backed artifacts ───────────────────────────────────

ArtifactType = Literal["chart", "table"]


class ArtifactCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    artifact_type: ArtifactType
    sql_text: str = Field(min_length=1, max_length=200_000)
    database_name: str | None = Field(default=None, max_length=128)
    schema_name: str | None = Field(default=None, max_length=128)
    chart_spec: dict[str, Any] | None = None
    agent_id: str | None = Field(default=None, max_length=64)
    thread_id: str | None = Field(default=None, max_length=64)


class ArtifactSummary(BaseModel):
    artifact_id: str
    title: str
    artifact_type: ArtifactType
    agent_id: str | None = None
    thread_id: str | None = None
    database_name: str | None = None
    schema_name: str | None = None
    created_at: datetime
    updated_at: datetime


class ArtifactView(ArtifactSummary):
    sql_text: str
    chart_spec: dict[str, Any] | None = None


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactSummary] = Field(default_factory=list)
    count: int = 0


class ArtifactRefreshResponse(BaseModel):
    artifact: ArtifactView
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    elapsed_ms: float = 0.0


class ArtifactDraft(BaseModel):
    sql_text: str = Field(min_length=1, max_length=200_000)
    artifact_type: ArtifactType
    chart_spec: dict[str, Any] | None = None


class ArtifactEditMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class ArtifactEditRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    draft: ArtifactDraft | None = None
    columns: list[Annotated[str, Field(max_length=128)]] = Field(
        default_factory=list, max_length=100
    )
    history: list[ArtifactEditMessage] = Field(default_factory=list, max_length=8)


class ArtifactEditResponse(BaseModel):
    message: str
    draft: ArtifactDraft | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    elapsed_ms: float = 0.0


class ArtifactApplyRequest(BaseModel):
    draft: ArtifactDraft
    expected_updated_at: datetime


# ── Studio dashboards ──────────────────────────────────────────

class DashboardTile(BaseModel):
    tile_id: str = Field(min_length=1, max_length=64)
    artifact_id: str = Field(min_length=1, max_length=64)
    x: int = Field(ge=0, le=5)
    y: int = Field(ge=0, le=3)
    w: int = Field(ge=1, le=6)
    h: int = Field(ge=1, le=4)

    @model_validator(mode="after")
    def within_grid(self) -> DashboardTile:
        if self.x + self.w > 6 or self.y + self.h > 4:
            raise ValueError("Dashboard tiles must fit inside the 6 × 4 grid.")
        return self


class DashboardLayout(BaseModel):
    tiles: list[DashboardTile] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def no_overlaps(self) -> DashboardLayout:
        seen_ids: set[str] = set()
        occupied: set[tuple[int, int]] = set()
        for tile in self.tiles:
            if tile.tile_id in seen_ids:
                raise ValueError("Dashboard tile IDs must be unique.")
            seen_ids.add(tile.tile_id)
            cells = {
                (x, y)
                for x in range(tile.x, tile.x + tile.w)
                for y in range(tile.y, tile.y + tile.h)
            }
            if occupied & cells:
                raise ValueError("Dashboard tiles cannot overlap.")
            occupied.update(cells)
        return self


class DashboardCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Dashboard name cannot be blank.")
        return value


class DashboardUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    layout: DashboardLayout
    expected_updated_at: datetime

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Dashboard name cannot be blank.")
        return value


class DashboardSummary(BaseModel):
    dashboard_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class DashboardView(DashboardSummary):
    layout: DashboardLayout


class DashboardListResponse(BaseModel):
    dashboards: list[DashboardSummary] = Field(default_factory=list)
    count: int = 0


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
