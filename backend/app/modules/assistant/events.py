"""SSE event contract for the assistant stream (frozen in spec §4).

The event names and payload shapes are the contract Stage D consumes. They are
centralised here so the backend and its tests use one spelling and the frontend
has a single source to mirror.

Wire format: ``event: <name>\\ndata: <json>\\n\\n``. Payloads are JSON objects.

 * ``text_delta``  — ``{"text": "..."}``
 * ``thinking``    — ``{"phase", "text", "status"}`` live agentic step
 * ``plan``        — ``{"steps": [{"id", "text", "status"}]}`` the task plan
 * ``tool_call``   — a ``ToolCallView`` with ``status:"pending"``
 * ``tool_status`` — ``{"tool_call_id", "status"}``
 * ``done``        — ``{"message_id", "finish_reason"}``
 * ``error``       — ``{"code", "message"}`` (message already redacted)
 * ``ping``        — ``{}``

``thinking`` and ``plan`` make the agentic work transparent: the client shows the
plan the assistant adopted, then a running commentary of the steps it takes
(loading a skill, running a query). They carry **no** model chain-of-thought —
only Nova's own phase labels and short, redacted status lines.

Consent is **not** carried on this stream: the client resolves a pending call
with a separate HTTP request, and the server then emits ``tool_status`` on the
still-open stream. That keeps the approval half-duplex out of SSE.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.modules.assistant.schemas import ToolCallView

#: Event names — the contract.
EVENT_TEXT_DELTA = "text_delta"
EVENT_THINKING = "thinking"
EVENT_PLAN = "plan"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_STATUS = "tool_status"
EVENT_DONE = "done"
EVENT_ERROR = "error"
EVENT_PING = "ping"

#: Media type the route must set on the response.
SSE_MEDIA_TYPE = "text/event-stream"


def format_sse(event: str, data: dict[str, Any]) -> str:
    """Serialise one SSE frame.

    ``data`` is JSON-encoded on one line. Values are already safe by the time
    they reach here (redacted SQL, generic errors); this function does not
    redact.
    """
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def text_delta(text: str) -> str:
    return format_sse(EVENT_TEXT_DELTA, {"text": text})


#: Phases a ``thinking`` frame can carry, in the order they occur in a turn.
PHASES = ("plan", "skill", "act", "observe", "answer")


def thinking(phase: str, text: str, status: str = "running") -> str:
    """One transparent agentic step.

    ``phase`` is one of ``PHASES``; ``status`` is ``running`` or ``done``. The
    text is a short, redacted status line Nova owns — never model chain-of-
    thought.
    """
    return format_sse(EVENT_THINKING, {"phase": phase, "text": text, "status": status})


def plan(steps: list[dict[str, Any]]) -> str:
    """The task plan the assistant adopted.

    Each step is ``{"id": str, "text": str, "status": "pending"|"running"|"done"}``.
    """
    return format_sse(EVENT_PLAN, {"steps": steps})


def tool_call(view: ToolCallView) -> str:
    return format_sse(EVENT_TOOL_CALL, view.model_dump(mode="json"))


def tool_status(tool_call_id: str, status: str) -> str:
    return format_sse(
        EVENT_TOOL_STATUS, {"tool_call_id": tool_call_id, "status": status}
    )


def done(message_id: str, finish_reason: str = "stop") -> str:
    return format_sse(
        EVENT_DONE, {"message_id": message_id, "finish_reason": finish_reason}
    )


def error(code: str, message: str) -> str:
    return format_sse(EVENT_ERROR, {"code": code, "message": message})


def ping() -> str:
    return format_sse(EVENT_PING, {})


async def stream_from_events(events: AsyncIterator[str]) -> AsyncIterator[str]:
    """Pass-through helper so the route can compose event generators uniformly.

    Present so tests can drive the route with a plain async iterator of frames
    without re-implementing the SSE encoding.
    """
    async for frame in events:
        yield frame
