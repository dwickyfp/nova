"""Assistant API schemas — request/response and the frozen view contracts.

The client-facing shapes (``ToolCallView``, thread/message views) are the
contract Stage D builds against; they mirror §2.1 of
``docs/specs/nova-61-agentic-assistant-design.md``.

``ToolCallView.sql_preview`` is documented as **already redacted**: the backend
must never place a credential-bearing statement here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from app.modules.assistant.app_context import NoveAppContext
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


class ThreadDetailResponse(BaseModel):
    thread: ThreadView
    messages: list[MessageView]


class ThreadCreateRequest(BaseModel):
    """Create a thread, optionally bound to the active worksheet file."""

    title: str | None = Field(default=None, max_length=256)
    workspace_file_id: str | None = Field(default=None, max_length=64)


class ThreadUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)


class MessageRequest(BaseModel):
    """A user turn. The response is an SSE stream, not a JSON body.

    ``model`` optionally pins the model for this turn (the panel's model
    selector). It is a model *name* registered under the resolved provider; when
    omitted, the provider's first active model is used.
    """

    content: str = Field(..., min_length=1, max_length=32_000)
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
    app_context: NoveAppContext | None = None

    model_config = {"populate_by_name": True}


class AttachmentInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=2_800_000)
    media_type: str = Field(default="text/plain", max_length=64)

    model_config = {"extra": "forbid"}


class AgentMessageRequest(MessageRequest):
    content: str = Field(default="", max_length=32_000)
    attachments: list[AttachmentInput] = Field(default_factory=list, max_length=3)
    _prepared_attachments: list[dict] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def validate_turn(self) -> AgentMessageRequest:
        if not self.content.strip() and not self.attachments:
            raise ValueError("Write a message or attach a file.")
        self._prepared_attachments = validate_attachments(
            [item.model_dump() for item in self.attachments]
        )
        return self

    @property
    def prepared_attachments(self) -> list[dict]:
        return self._prepared_attachments


class ConsentDecisionRequest(BaseModel):
    """Resolve a pending tool call.

    ``decision`` is one of ``allow_once`` / ``allow_session`` / ``deny``.
    ``allow_session`` grants a read-only, conversation-scoped policy (E2b).
    """

    decision: Literal["allow_once", "allow_session", "deny"]
    secure_input: dict[str, str] | None = None


class ConsentDecisionResponse(BaseModel):
    tool_call_id: str
    status: ToolStatus
    grant_active: bool = False


class GrantRequest(BaseModel):
    """Set or clear the conversation's read-only always-allow grant.

    The composer's approval-mode selector sets this before a turn runs, so a
    read-only query does not need a per-call approval card. Only the
    read-only grant exists, so the value is a bounded boolean rather than a
    policy string; a future grant type adds a field, not a free-form policy.
    """

    always_allow_read_only: bool
