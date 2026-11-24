"""Assistant API schemas — request/response and the frozen view contracts.

The client-facing shapes (``ToolCallView``, thread/message views) are the
contract Stage D builds against; they mirror §2.1 of
``docs/specs/nova-61-agentic-assistant-design.md``.

``ToolCallView.sql_preview`` is documented as **already redacted**: the backend
must never place a credential-bearing statement here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from app.modules.assistant.app_context import NevaAppContext
from app.modules.assistant.attachments import validate_attachments


def utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


ToolClassification = Literal["read_only", "session_change", "destructive", "denied"]
ToolStatus = Literal[
    "pending",
    "approved",
    "denied",
    "running",
    "done",
    "failed",
    "cancelled",
]
MessageRole = Literal["user", "assistant", "tool"]


class ToolCallView(BaseModel):
    """One tool call as the panel renders it.

    ``sql_preview`` is redacted before it is put here — see the module
    docstring. ``result_summary`` is a row/affected count or a short status
    line, never rows and never credential-shaped values.
    """

    tool_call_id: str
    tool_name: str
    skill_name: str | None = None
    #: A plain label for a tool whose identifier is opaque (an MCP connector tool).
    display_name: str | None = None
    sql_preview: str = ""
    classification: ToolClassification = "read_only"
    status: ToolStatus = "pending"
    result_summary: str | None = None
    error: str | None = None


class AttachmentView(BaseModel):
    name: str
    size_bytes: int
    media_type: str = "text/plain"


class MessageView(BaseModel):
    message_id: str
    role: MessageRole
    content: str = ""
    tool_call: ToolCallView | None = None
    created_at: datetime
    #: The assistant turn's recorded trace, so a reopened conversation can
    #: rebuild its process view. Redacted by the loop before it is stored; an
    #: empty list on a user turn or a turn that recorded nothing.
    steps: list[dict] = Field(default_factory=list)
    #: Provider token report for the assistant turn, if it reported one.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    model_name: str | None = None
    feedback: Literal["like", "dislike"] | None = None
    attachments: list[AttachmentView] = Field(default_factory=list)
    #: Stream protocol the turn was recorded with. ``2`` turns carry their
    #: delivered event journal in ``steps``; ``1`` turns replay from steps.
    protocol_version: int = 1

    @field_validator("created_at")
    @classmethod
    def mark_created_at_utc(cls, value: datetime) -> datetime:
        return utc_datetime(value)


class MessageFeedbackRequest(BaseModel):
    feedback: Literal["like", "dislike"] | None


class ThreadView(BaseModel):
    thread_id: str
    title: str
    workspace_file_id: str | None = None
    agent_id: str | None = None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    @field_validator("created_at", "updated_at")
    @classmethod
    def mark_timestamps_utc(cls, value: datetime) -> datetime:
        return utc_datetime(value)


class ThreadListResponse(BaseModel):
    threads: list[ThreadView]
    count: int
    next_cursor: str | None = None


class ThreadDetailResponse(BaseModel):
    thread: ThreadView
    messages: list[MessageView]
    next_cursor: str | None = None


class ThreadCreateRequest(BaseModel):
    """Create a thread, optionally bound to the active worksheet file."""

    title: str | None = Field(default=None, max_length=256)
    workspace_file_id: str | None = Field(default=None, max_length=64)
    learning_enabled: bool = True


class ThreadUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)


class AttachmentInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=2_800_000)
    media_type: str = Field(default="text/plain", max_length=64)

    model_config = {"extra": "forbid"}


class MessageRequest(BaseModel):
    """A user turn. The response is an SSE stream, not a JSON body.

    ``model`` optionally pins the model for this turn (the panel's model
    selector). It is a model *name* registered under the resolved provider; when
    omitted, the provider's first active model is used.
    """

    content: str = Field(default="", max_length=32_000)
    database: str | None = Field(default=None, max_length=128)
    schema_name: str | None = Field(default=None, alias="schema", max_length=128)
    role: str | None = Field(
        default=None,
        max_length=128,
        deprecated=True,
        description="Ignored for execution. The authenticated session selects the active role.",
    )
    model: str | None = Field(default=None, max_length=256)
    provider_id: str | None = Field(default=None, max_length=64)
    app_context: NevaAppContext | None = None
    #: Files the person attached: text is read as data, images by name only.
    attachments: list[AttachmentInput] = Field(default_factory=list, max_length=3)
    _prepared_attachments: list[dict] = PrivateAttr(default_factory=list)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_message(self) -> MessageRequest:
        if not self.content.strip() and not self.attachments:
            raise ValueError("Write a message or attach a file.")
        self._prepared_attachments = validate_attachments(
            [item.model_dump() for item in self.attachments]
        )
        return self

    @property
    def prepared_attachments(self) -> list[dict]:
        return self._prepared_attachments


class NativeViewFilterInput(BaseModel):
    """One filter or chart selection applied on screen: a hint for Neva's answers."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["filter", "selection"]
    label: str = Field(min_length=1, max_length=128)
    text: str = Field(default="", max_length=256)
    dataset_id: UUID | None = None
    field_id: str | None = Field(default=None, min_length=1, max_length=256)
    values: list[
        Annotated[str, Field(max_length=256)] | int | float | bool | None
    ] = Field(default_factory=list, max_length=20)


class NativeDashboardTurnInput(BaseModel):
    """The native Dataviz dashboard on screen: an untrusted hint.

    Native tools read the dashboard again as the caller before using it; the
    revision is what the page shows, so a change made meanwhile is detected.
    """

    model_config = ConfigDict(extra="forbid")

    dashboard_id: UUID
    revision: int = Field(ge=1, le=9_007_199_254_740_991, strict=True)
    page_id: UUID | None = None
    selected_widget_id: UUID | None = None
    #: Filters and chart selections applied on the page, so answers match the screen.
    view_filters: list[NativeViewFilterInput] = Field(default_factory=list, max_length=32)


class DatavizTurnInput(BaseModel):
    """The Dataviz page a Studio turn is asked from (docs/arch-19-studio-dataviz.md).

    Untrusted hints: native tools read the dashboard again as the caller before
    using it. ``dashboard_id``, ``native_filters_key``, ``edit_state`` and
    ``path`` described the retired BI dashboards; they are accepted from older
    clients and ignored.
    """

    model_config = ConfigDict(extra="forbid")

    dashboard_id: int | str | None = Field(default=None, union_mode="left_to_right")
    native_filters_key: str | None = Field(default=None, max_length=128)
    edit_state: Literal["clean", "dirty", "unknown"] = "unknown"
    path: str | None = Field(default=None, max_length=512)
    native_dashboard: NativeDashboardTurnInput | None = None

    @field_validator("dashboard_id")
    @classmethod
    def bounded(cls, value: int | str | None) -> int | str | None:
        if isinstance(value, str) and len(value) > 128:
            raise ValueError("dashboard_id is too long")
        return value

    def turn_hints(self) -> dict[str, Any]:
        """What a turn keeps: only the native dashboard on screen."""
        return {"native_dashboard": self.native_dashboard.model_dump(mode="json")
                if self.native_dashboard is not None else None}


class AgentMessageRequest(MessageRequest):
    dataviz: DatavizTurnInput | None = None
    work_intent: Literal["ANSWER", "ANALYZE", "INVESTIGATE", "PLAN", "RESEARCH", "ACT"] | None = (
        None
    )
    new_mission: bool = False
    continue_mission_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_turn(self) -> AgentMessageRequest:
        if self.new_mission and self.continue_mission_id:
            raise ValueError("New and continue Mission controls conflict")
        return self


class ConsentDecisionRequest(BaseModel):
    """Allow or deny one pending action. It covers that call only."""

    decision: Literal["allow_once", "deny"]
    secure_input: dict[str, str] | None = None


class HumanInputAnswerRequest(BaseModel):
    """The answer to one human-in-the-loop request.

    ``action`` is ``allow_once`` / ``deny`` for an approval, and ``answer`` /
    ``decline`` for a question, form or choice. ``answer`` is validated against
    the request.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["allow_once", "deny", "answer", "decline"]
    answer: dict[str, Any] | None = None


class ConsentDecisionResponse(BaseModel):
    tool_call_id: str
    status: ToolStatus
