"""Run the Studio Smart worker and record the size and usage of every model call.

    cd backend && NOVA_TOKEN_PROBE_FILE=/tmp/calls.jsonl \\
        uv run python -m tests.benchmark.smart_tokens.probe

It is ``python -m app.agent_worker`` with the provider client wrapped: each
streamed or one-shot call appends one JSON line holding the participant path
(``/root``, ``/root/visits``), what the prompt is made of in estimated tokens,
the tool schemas offered, and the usage the provider reported. No message
content is written, only sizes and tool names.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from app.modules.assistant import provider as provider_module
from app.modules.assistant.context import estimate_message_tokens

_PARTICIPANT = re.compile(r"You are (/root\S*) in a governed Smart collaboration")
_COORDINATION = ("<AGENT_COORDINATION_MESSAGE>", "Collaboration update before synthesis")


def _participant(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "system":
            found = _PARTICIPANT.search(str(message.get("content") or ""))
            if found:
                return found.group(1).rstrip(".")
    return "internal"


def composition(messages: list[dict[str, Any]], tools: list[dict] | None) -> dict[str, int]:
    """Estimated prompt tokens by part, with Nova's own estimator."""
    parts: dict[str, int] = {}
    names: dict[str, str] = {}
    first_user = True

    def add(key: str, value: int) -> None:
        parts[key] = parts.get(key, 0) + value

    for message in messages:
        role = message.get("role")
        size = estimate_message_tokens(message)
        content = str(message.get("content") or "")
        if role == "system":
            add("system", size)
        elif role == "assistant":
            for call in message.get("tool_calls") or []:
                names[str(call.get("id"))] = str((call.get("function") or {}).get("name"))
            add("assistant", size)
        elif role == "tool":
            add(f"tool:{names.get(str(message.get('tool_call_id')), 'unknown')}", size)
        elif content.startswith(_COORDINATION):
            add("coordination", size)
        elif first_user:
            first_user = False
            add("task", size)
        else:
            add("user", size)
    if tools:
        add("tool_schemas", len(json.dumps(tools, separators=(",", ":"), default=str)) // 4)
    return parts


def _usage(message: Any) -> dict[str, int | None]:
    usage = message.get("usage") if isinstance(message, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    return {key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}


def _write(record: dict[str, Any]) -> None:
    path = Path(os.environ["NOVA_TOKEN_PROBE_FILE"])
    with path.open("a") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def _record(method: str, messages, tools, response_format, message, started: float) -> None:
    schema = (response_format or {}).get("json_schema") or {}
    _write({
        "at": round(time.time(), 3),
        "seconds": round(time.time() - started, 3),
        "method": method,
        "participant": _participant(messages),
        "purpose": "tools" if tools else (schema.get("name") or "answer"),
        "tools": sorted(str((tool.get("function") or {}).get("name")) for tool in tools or []),
        "tool_calls": [str((call.get("function") or {}).get("name"))
                       for call in (message or {}).get("tool_calls") or []],
        "parts": composition(messages, tools),
        "usage": _usage(message),
    })


def install() -> None:
    client = provider_module.AssistantProviderClient
    stream, complete = client.stream, client.complete

    async def recorded_stream(self, *, messages, tools=None, **kwargs):
        started, final = time.time(), None
        async for kind, payload in stream(self, messages=messages, tools=tools, **kwargs):
            if kind == "message":
                final = payload
            yield kind, payload
        _record("stream", messages, tools, None, final, started)

    async def recorded_complete(self, *, messages, tools=None, response_format=None, **kwargs):
        started = time.time()
        message = await complete(
            self, messages=messages, tools=tools, response_format=response_format, **kwargs
        )
        _record("complete", messages, tools, response_format, message, started)
        return message

    client.stream = recorded_stream
    client.complete = recorded_complete


def main() -> None:
    if not os.environ.get("NOVA_TOKEN_PROBE_FILE"):
        raise SystemExit("Set NOVA_TOKEN_PROBE_FILE to the JSONL file to append to.")
    install()
    from app.agent_worker.__main__ import main as worker_main

    worker_main()


if __name__ == "__main__":
    main()
