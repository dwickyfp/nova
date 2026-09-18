"""SSE event contract for the assistant stream (frozen in spec §4).

The event names and payload shapes are the contract Stage D consumes. They are
centralised here so the backend and its tests use one spelling and the frontend
has a single source to mirror.

Wire format: ``event: <name>\\ndata: <json>\\n\\n``. Payloads are JSON objects.

* ``text_delta``  — ``{"text": "..."}``
* ``tool_call``   — a ``ToolCallView`` with ``status:"pending"``
* ``tool_status`` — ``{"tool_call_id", "status"}``
* ``done``        — ``{"message_id", "finish_reason"}``
* ``error``       — ``{"code", "message"}`` (message already redacted)
* ``ping``        — ``{}``

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
