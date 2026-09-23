"""SSE event contract for the assistant stream (frozen in spec §4).

The event names and payload shapes are the contract Stage D consumes. They are
centralised here so the backend and its tests use one spelling and the frontend
has a single source to mirror.

Wire format: ``event: <name>\\ndata: <json>\\n\\n``. Payloads are JSON objects.

 * ``text_delta``  — ``{"text": "..."}``
 * ``thinking``    — ``{"phase", "text", "status"}`` live agentic step
 * ``plan``        — ``{"steps": [{"id", "text", "status"}]}`` the task plan
 * ``tool_call``   — a ``ToolCallView`` with ``status:"pending"``
 * ``tool_progress`` — a live, redacted stage inside one tool execution
 * ``tool_status`` — ``{"tool_call_id", "status"}``
 * ``content_block_done`` — closes one ordered response content block
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
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.modules.assistant.schemas import ToolCallView

#: Event names — the contract.
EVENT_TEXT_DELTA = "text_delta"
EVENT_THINKING = "thinking"
EVENT_PLAN = "plan"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_PROGRESS = "tool_progress"
EVENT_TOOL_STATUS = "tool_status"
EVENT_CONTENT_BLOCK_DONE = "content_block_done"
EVENT_DONE = "done"
EVENT_ERROR = "error"
EVENT_PING = "ping"
#: Agent Studio additions (Phase 12): structured content blocks a business answer
#: can carry (a result grid, a chart, a source citation), rendered by the chat.
EVENT_TABLE = "table"
EVENT_CHART = "chart"
EVENT_CITATION = "citation"
#: A visible tool result summary, sent after success. Skill bodies stay in the
#: model's tool result and are not emitted on this stream.
EVENT_TOOL_DETAIL = "tool_detail"

#: Media type the route must set on the response.
SSE_MEDIA_TYPE = "text/event-stream"

_run_id: ContextVar[str | None] = ContextVar("assistant_run_id", default=None)
_sequence: ContextVar[int] = ContextVar("assistant_event_sequence", default=0)


def begin_run(run_id: str) -> None:
    """Start a request-local event envelope for one agent turn."""
    _run_id.set(run_id)
    _sequence.set(0)


def format_sse(event: str, data: dict[str, Any]) -> str:
    """Serialise one SSE frame.

    ``data`` is JSON-encoded on one line. Values are already safe by the time
    they reach here (redacted SQL, generic errors); this function does not
    redact.

    Engine values are not natively JSON-serialisable: a ``DECIMAL`` column
    arrives as :class:`decimal.Decimal` and a date column as
    :class:`datetime.date`. A structured content block (a result grid, a chart)
    carries exactly those, so they are coerced here rather than at each tool.
    ``Decimal`` becomes a string to preserve exact precision (a float would lose
    it on a money column); dates and datetimes use ISO 8601.
    """
    payload = dict(data)
    current_run = _run_id.get()
    if current_run is not None:
        sequence = _sequence.get()
        payload.setdefault("run_id", current_run)
        payload.setdefault("sequence", sequence)
        _sequence.set(sequence + 1)
    return (
        f"event: {event}\ndata: "
        f"{json.dumps(payload, separators=(',', ':'), default=_json_default)}\n\n"
    )


def _json_default(value: Any) -> Any:
    """JSON fallback for engine-native types in structured content blocks."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray):
        return value.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def text_delta(
    text: str,
    *,
    content_index: int | None = None,
    content_id: str | None = None,
) -> str:
    """A text fragment in the ordered response stream.

    ``content_index`` is the authored position in the final response, not the
    time at which the bytes became available.  Older callers may omit the
    ordering fields; clients retain a compatibility path for those frames.
    """
    payload: dict[str, Any] = {"text": text}
    _with_content_position(payload, content_index, content_id)
    return format_sse(EVENT_TEXT_DELTA, payload)


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
    return format_sse(EVENT_TOOL_STATUS, {"tool_call_id": tool_call_id, "status": status})


def tool_progress(
    tool_call_id: str,
    *,
    stage: str,
    text: str,
    sql_preview: str | None = None,
) -> str:
    """A factual progress update emitted while a tool is still running.

    This is deliberately not model chain-of-thought.  It reports observable
    lifecycle states such as SQL generation, validation, and execution.  SQL
    must already be credential-redacted by the tool boundary.
    """
    payload: dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "stage": stage,
        "text": text,
    }
    if sql_preview is not None:
        payload["sql_preview"] = sql_preview
    return format_sse(EVENT_TOOL_PROGRESS, payload)


def table(
    payload: dict[str, Any],
    *,
    content_index: int | None = None,
    content_id: str | None = None,
    tool_call_id: str | None = None,
) -> str:
    """A result grid: ``{"title", "columns", "rows"}``. Rows are already redacted."""
    ordered = dict(payload)
    _with_content_position(ordered, content_index, content_id)
    if tool_call_id is not None:
        ordered["tool_call_id"] = tool_call_id
    return format_sse(EVENT_TABLE, ordered)


def chart(
    tool_call_id: str,
    chart_spec: str,
    *,
    content_index: int | None = None,
    content_id: str | None = None,
) -> str:
    """A chart: ``chart_spec`` is a Vega-Lite v5 JSON string.

    The shape mirrors Snowflake Cortex Agents' ``response.chart`` so a client
    that understands one understands the other.
    """
    payload: dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "chart_spec": chart_spec,
    }
    _with_content_position(payload, content_index, content_id)
    return format_sse(EVENT_CHART, payload)


def citation(
    payload: dict[str, Any],
    *,
    content_index: int | None = None,
    content_id: str | None = None,
) -> str:
    """A source reference: ``{"title", "source", "snippet"}``."""
    ordered = dict(payload)
    _with_content_position(ordered, content_index, content_id)
    return format_sse(EVENT_CITATION, ordered)


def content_block_done(content_index: int, content_id: str) -> str:
    """Seal an ordered block so a later block may become visible.

    Clients render a block immediately, but hold every higher index until all
    preceding blocks are sealed.  A table at index 1 therefore cannot jump
    ahead of streaming text at index 0, while a table authored at index 0 is
    free to appear before later prose.
    """
    return format_sse(
        EVENT_CONTENT_BLOCK_DONE,
        {"content_index": content_index, "content_id": content_id},
    )


def tool_detail(tool_call_id: str, text: str) -> str:
    """What a tool did, so an opened step has something to show.

    ``text`` is the tool's own already-redacted output: a loaded skill's body,
    or a short result summary. Sent once per successful call, and recorded on
    the trace so a reopened conversation shows the same thing.
    """
    return format_sse(EVENT_TOOL_DETAIL, {"tool_call_id": tool_call_id, "text": text})


def done(
    message_id: str,
    finish_reason: str = "stop",
    *,
    usage: dict[str, Any] | None = None,
) -> str:
    """The terminal frame.

    ``usage`` is the provider's token report for the whole turn, summed across
    its model calls. It rides the terminal frame so a live turn can show its
    cost without a second request, and it matches what is stored for a reload.
    Omitted when the provider reported nothing, never sent as zero.
    """
    payload: dict[str, Any] = {
        "message_id": message_id,
        "finish_reason": finish_reason,
    }
    if usage:
        payload["usage"] = usage
    return format_sse(EVENT_DONE, payload)


def error(code: str, message: str) -> str:
    return format_sse(EVENT_ERROR, {"code": code, "message": message})


def ping() -> str:
    return format_sse(EVENT_PING, {})


def _with_content_position(
    payload: dict[str, Any], content_index: int | None, content_id: str | None
) -> None:
    if content_index is not None:
        payload["content_index"] = content_index
    if content_id is not None:
        payload["content_id"] = content_id


async def stream_from_events(events: AsyncIterator[str]) -> AsyncIterator[str]:
    """Pass-through helper so the route can compose event generators uniformly.

    Present so tests can drive the route with a plain async iterator of frames
    without re-implementing the SSE encoding.
    """
    async for frame in events:
        yield frame
