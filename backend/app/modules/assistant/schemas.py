"""Assistant API schemas — request/response and the frozen view contracts.

The client-facing shapes (``ToolCallView``, thread/message views) are the
contract Stage D builds against; they mirror §2.1 of
``docs/specs/nova-61-agentic-assistant-design.md``.

``ToolCallView.sql_preview`` is documented as **already redacted**: the backend
must never place a credential-bearing statement here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ToolClassification = Literal["read_only", "destructive", "denied"]
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
    sql_preview: str = ""
    classification: ToolClassification = "read_only"
    status: ToolStatus = "pending"
    result_summary: str | None = None
    error: str | None = None


class MessageView(BaseModel):
    message_id: str
    role: MessageRole
    content: str = ""
    tool_call: ToolCallView | None = None
    created_at: datetime


class ThreadView(BaseModel):
    thread_id: str
    title: str
    workspace_file_id: str | None = None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


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
    """A user turn. The response is an SSE stream, not a JSON body."""

    content: str = Field(..., min_length=1, max_length=32_000)
    database: str | None = Field(default=None, max_length=128)
    schema_name: str | None = Field(default=None, alias="schema", max_length=128)
    role: str | None = Field(default=None, max_length=128)

    model_config = {"populate_by_name": True}


class ConsentDecisionRequest(BaseModel):
    """Resolve a pending tool call.

    ``decision`` is one of ``allow_once`` / ``allow_session`` / ``deny``.
    ``allow_session`` grants a read-only, conversation-scoped policy (E2b).
    """

    decision: Literal["allow_once", "allow_session", "deny"]


class ConsentDecisionResponse(BaseModel):
    tool_call_id: str
    status: ToolStatus
    grant_active: bool = False
