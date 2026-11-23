"""Live reasoning and narration in a real loop trajectory (Studio chat F2).

A provider that streams a reasoning summary has it shown while the model call
runs, redacted fragment by fragment, before the tool it leads to. Narration
before a tool call is shown without figures. The answer is sent only after the
loop finished with it. Reasoning never goes back to the model: not on the next
call of the same turn and not on a later turn.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import RecordingTool, ScriptedProvider, text_frame, tool_call_frame

SECRET = "sk-" + "Zq9Xw8Vu7Ts6Rq5Po4Nm3Lk2"
REASONING_ONE = (
    f"The person wants the database list; the stored key {SECRET} is not needed. "
    "Listing the databases answers it."
)
REASONING_TWO = "**Reading the result**"


@dataclass
class ReasoningProvider(ScriptedProvider):
    """Streams scripted reasoning fragments before each scripted message."""

    reasoning: list[list[str]] = field(default_factory=list)
    received: list[list[dict[str, Any]]] = field(default_factory=list)

    async def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        provider: Any = None,
        **_: Any,
    ) -> AsyncIterator[tuple[str, Any]]:
        self.received.append(json.loads(json.dumps(messages, default=str)))
        fragments = self.reasoning.pop(0) if self.reasoning else []
        for fragment in fragments:
            yield ("reasoning", fragment)
        async for item in super().stream(messages=messages, tools=tools, provider=provider):
            yield item


def _split(text: str, size: int) -> list[str]:
    return [text[index:index + size] for index in range(0, len(text), size)]


def _frames_named(frames: list[str], name: str) -> list[dict[str, Any]]:
    return [
        json.loads(frame.split("data: ", 1)[1]) for frame in frames
        if frame.startswith(f"event: {name}\n")
    ]


def _index(frames: list[str], name: str) -> int:
    return next(i for i, frame in enumerate(frames) if frame.startswith(f"event: {name}\n"))


async def _run(provider: ScriptedProvider, thread: AssistantThread) -> list[str]:
    registry = ToolRegistry()
    registry.register(RecordingTool())

    async def consent(_invocation: Any, _classification: str) -> bool:
        return True

    return [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=thread,
            user_content="SHOW DATABASES",
            context=LoopContext(user_name="alice", steps=[]),
            resolve_consent=consent,
        )
    ]


def _first_call(sql: str = "SHOW DATABASES") -> dict[str, Any]:
    message = tool_call_frame("call-1", sql=sql)
    message["content"] = (
        "There are probably 3 databases. I will list the databases first."
    )
    return message


async def test_reasoning_streams_before_the_tool_and_is_redacted_across_fragments() -> None:
    provider = ReasoningProvider(
        script=[_first_call(), text_frame("One database is available.")],
        reasoning=[_split(REASONING_ONE, 3), [REASONING_TWO]],
    )
    frames = await _run(provider, AssistantThread(thread_id="t1", user_name="alice", title="Eval"))
    output = "".join(frames)

    reasoning = _frames_named(frames, "model_reasoning")
    shown = "".join(frame["text"] for frame in reasoning)
    # Both model calls showed their summary; each part was closed.
    assert REASONING_TWO in shown
    assert [frame["status"] for frame in reasoning].count("done") == 2
    # The credential never reached the stream, not even split across fragments.
    assert SECRET not in output and SECRET[:8] not in shown
    assert "[redacted credential]" in shown
    assert any(
        frame["action"] == "redacted" for frame in _frames_named(frames, "guard")
    )
    # Reasoning is gathered into readable pieces, not one event per fragment.
    assert len(reasoning) < len(_split(REASONING_ONE, 3)) / 3

    # Interleaved by production order: reasoning, narration, tool, reasoning, answer.
    first_reasoning = _index(frames, "model_reasoning")
    narration = _index(frames, "narration")
    tool = _index(frames, "tool_call")
    answer = _index(frames, "text_delta")
    assert first_reasoning < narration < tool < answer
    second = [
        i for i, frame in enumerate(frames)
        if frame.startswith("event: model_reasoning\n") and REASONING_TWO in frame
    ]
    assert tool < second[0] < answer

    # Narration states no figure; the figure is only for a verified answer.
    narrated = _frames_named(frames, "narration")
    assert [frame["text"] for frame in narrated] == ["I will list the databases first."]

    # The provider's second call saw the narration as the assistant turn, but no
    # reasoning text at all.
    second_call = json.dumps(provider.received[1])
    assert "Listing the databases answers it" not in second_call
    assert "reasoning" not in second_call.lower()


async def test_reasoning_from_an_earlier_turn_is_not_sent_back() -> None:
    earlier_journal = {
        "kind": "event_journal",
        "protocol_version": 2,
        "events": [
            {"event": "part.start", "data": {"part_id": "r#0", "type": "reasoning",
                                             "source": "model", "text": ""}},
            {"event": "part.delta", "data": {"part_id": "r#0",
                                             "text": "PRIVATE-REASONING-MARKER"}},
        ],
    }
    thread = AssistantThread(
        thread_id="t2", user_name="alice", title="Eval",
        messages=[
            AssistantMessage(message_id="u1", role="user", content="List databases"),
            AssistantMessage(
                message_id="a1", role="assistant", content="One database is available.",
                steps=[
                    {"kind": "model_reasoning", "text": "PRIVATE-REASONING-MARKER"},
                    {"kind": "narration", "text": "I will list the databases first."},
                    earlier_journal,
                ],
            ),
        ],
    )
    provider = ReasoningProvider(script=[text_frame("Still one database.")])
    await _run(provider, thread)
    sent = json.dumps(provider.received)
    assert "One database is available." in sent
    assert "PRIVATE-REASONING-MARKER" not in sent


async def test_a_provider_without_reasoning_streams_no_reasoning_part() -> None:
    provider = ReasoningProvider(
        script=[_first_call(), text_frame("One database is available.")],
    )
    frames = await _run(provider, AssistantThread(thread_id="t3", user_name="alice", title="Eval"))
    assert _frames_named(frames, "model_reasoning") == []
    assert _index(frames, "narration") < _index(frames, "tool_call") < _index(frames, "text_delta")
    assert "One database is available." in "".join(frames)
