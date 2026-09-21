"""Incremental parsing of an OpenAI-compatible chat-completions SSE stream.

The provider streams ``data: {json}`` chunks, each a partial delta. Text arrives
as ``choices[0].delta.content`` fragments; tool calls arrive as
``choices[0].delta.tool_calls[]`` whose ``function.arguments`` is a JSON string
fragmented across chunks and keyed by ``index`` (not id — the id only appears on
the first fragment). This module folds those chunks into the same assistant
message shape the non-streaming path returns, so the loop treats both alike.

Kept free of any HTTP or credential concern: it consumes already-decoded lines
and has no access to the API key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _ToolCallAccumulator:
    """One tool call being assembled across chunks, keyed by its stream index."""

    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class StreamAccumulator:
    """Folds streamed deltas into a final assistant message.

    ``feed`` consumes one ``data:`` payload and returns the text fragment to emit
    now (possibly empty). ``message`` returns the assembled OpenAI-style message
    once the stream is done.
    """

    content: str = ""
    finish_reason: str | None = None
    tool_calls: dict[int, _ToolCallAccumulator] = field(default_factory=dict)
    #: Token usage, when the provider reports it. OpenAI-compatible providers
    #: send a final chunk (often with an empty ``choices`` array) carrying
    #: ``usage``: ``{prompt_tokens, completion_tokens, total_tokens}``. Kept so
    #: Studio Observability can total tokens per turn without a second call.
    usage: dict[str, Any] | None = None

    def feed(self, payload: dict[str, Any]) -> str:
        # Usage may arrive on the final chunk, which sometimes carries no
        # choices. Capture it before the choices check.
        usage = payload.get("usage")
        if isinstance(usage, dict):
            self.usage = usage

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = choices[0]
        if not isinstance(choice, dict):
            return ""

        finish = choice.get("finish_reason")
        if isinstance(finish, str):
            self.finish_reason = finish

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return ""

        text = delta.get("content")
        if isinstance(text, str) and text:
            self.content += text
            return text

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            self._accumulate_tool_calls(tool_calls)
        return ""

    def _accumulate_tool_calls(self, tool_calls: list[Any]) -> None:
        for entry in tool_calls:
            if not isinstance(entry, dict):
                continue
            index = entry.get("index")
            if not isinstance(index, int):
                index = 0
            slot = self.tool_calls.get(index)
            if slot is None:
                slot = _ToolCallAccumulator()
                self.tool_calls[index] = slot

            call_id = entry.get("id")
            if isinstance(call_id, str) and call_id:
                slot.id = call_id

            function = entry.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name:
                slot.name = name
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                slot.arguments += arguments

    def message(self) -> dict[str, Any]:
        """The assembled assistant message, shaped like the non-streamed reply."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": slot.id,
                    "type": "function",
                    "function": {"name": slot.name, "arguments": slot.arguments},
                }
                for _, slot in sorted(self.tool_calls.items())
            ]
        if self.usage is not None:
            message["usage"] = self.usage
        return message


def parse_sse_data_line(line: str) -> dict[str, Any] | None:
    """Parse one SSE line into a chunk payload, or None for non-data/`[DONE]`."""
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
