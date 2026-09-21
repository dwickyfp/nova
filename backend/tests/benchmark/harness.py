"""Deterministic microbenchmark harness for the assistant loop (NOVA-92).

Everything here is offline and reproducible: an in-process fake provider and
fake tool drive ``AssistantLoop.run`` while the harness measures wall-clock
cost with ``time.perf_counter_ns``. No network, no provider key, no StarRocks.

The harness deliberately excludes the two costs that dominate production —
the LLM round-trip and the StarRocks round-trip — so what it reports is the
*loop's own* overhead. The report states this limit; the numbers must not be
read as end-to-end latency.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import (
    ToolInvocation,
    ToolOutcome,
)


@dataclass
class ScriptedProvider:
    """A provider seam that returns a fixed script of messages.

    It implements the two methods the loop actually calls — ``resolve`` and
    ``stream`` — so a benchmark measures a real loop iteration, not a
    provider-resolution failure. (The previous version predated the streaming
    loop and silently exercised the error path, which made every ``*_turn``
    number meaningless; this is the fix.)

    ``stream`` yields each scripted message as one ``delta`` for its text, then
    a single ``message`` frame, mirroring the real provider's contract. When the
    script is exhausted it repeats the last entry, so a loop that calls the
    provider more than the script expects still terminates at its own cap
    instead of raising.
    """

    script: list[dict[str, Any]]
    calls: int = 0

    async def resolve(self, **_: Any) -> Any:
        from app.modules.assistant.provider import ProviderConfig

        return ProviderConfig(
            provider_id="bench",
            model="bench-model",
            endpoint="http://bench.invalid/v1/chat/completions",
            api_key="not-a-real-key",
        )

    async def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        provider: Any = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield ``("delta", text)`` frames then one ``("message", dict)``.

        This is the same shape ``AssistantProviderClient.stream`` produces, so
        the loop's buffering, tool-call handling, and termination paths are all
        exercised.
        """
        self.calls += 1
        message = self._next_message()
        content = str(message.get("content") or "")
        if content:
            yield ("delta", content)
        yield ("message", message)

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        provider: Any = None,
    ) -> dict[str, Any]:
        self.calls += 1
        return self._next_message()

    def _next_message(self) -> dict[str, Any]:
        if not self.script:
            return {"role": "assistant", "content": "ok"}
        if len(self.script) == 1:
            return self.script[0]
        return self.script.pop(0)


@dataclass
class RecordingTool:
    """A fake tool at the registry seam; records how often it ran."""

    name: str = "query_execute"
    classification: str = "read_only"
    ok: bool = True
    description: str = "run a read-only statement"
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        }
    )
    runs: int = 0

    def preview(self, invocation: ToolInvocation) -> str:
        return str(invocation.arguments.get("sql", "SELECT 1"))

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        self.runs += 1
        if self.ok:
            return ToolOutcome(ok=True, summary="1 row (benchmark)")
        return ToolOutcome(ok=False, summary="", error="benchmark failure")


def tool_call_frame(
    call_id: str,
    *,
    name: str = "query_execute",
    sql: str = "SELECT 1",
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One OpenAI-shaped assistant message proposing a tool call.

    ``arguments`` overrides the default ``{"sql": ...}``, for a tool that takes
    a different shape (``load_skill`` takes a skill name).
    """
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments or {"sql": sql}),
                },
            }
        ],
    }


def text_frame(text: str = "final answer") -> dict[str, Any]:
    return {"role": "assistant", "content": text}


async def allow(_inv: ToolInvocation, _cls: str) -> bool:
    return True


async def deny(_inv: ToolInvocation, _cls: str) -> bool:
    return False


def thread(
    *,
    read_only_grant: bool = False,
    history_turns: int = 0,
) -> AssistantThread:
    """A thread with optional pre-existing history.

    ``history_turns`` adds complete user/assistant pairs so context assembly is
    measured against a non-trivial transcript.
    """
    from app.modules.assistant.state import AssistantMessage

    t = AssistantThread(thread_id="bench-thread", user_name="bench", title="Bench")
    for i in range(history_turns):
        t.messages.append(
            AssistantMessage(message_id=f"u{i}", role="user", content=f"question {i}")
        )
        t.messages.append(
            AssistantMessage(message_id=f"a{i}", role="assistant", content=f"answer {i}")
        )
    t.consent.always_allow_read_only = read_only_grant
    return t


async def drain(
    loop: AssistantLoop,
    *,
    thread_: AssistantThread,
    content: str = "go",
    resolver: Callable[[ToolInvocation, str], Awaitable[bool | None]] = allow,
) -> int:
    """Run one full turn to completion, returning the frame count."""
    frames = 0
    async for _frame in loop.run(
        thread=thread_,
        user_content=content,
        context=LoopContext(user_name="bench", thread_id=thread_.thread_id),
        resolve_consent=resolver,
    ):
        frames += 1
    return frames


async def measure(
    func: Callable[[], Awaitable[Any]],
    *,
    iterations: int = 200,
) -> dict[str, float]:
    """Time an async callable ``iterations`` times and summarise.

    ``func`` is a zero-arg factory for the awaitable, invoked fresh each
    iteration so per-run setup is included in exactly the same way every time.
    Returns min/median/p95/max in microseconds plus the iteration count.
    """
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        await func()
        samples.append((time.perf_counter_ns() - start) / 1_000)
    ordered = sorted(samples)
    p95_index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return {
        "iterations": float(len(samples)),
        "min_us": ordered[0],
        "median_us": statistics.median(ordered),
        "p95_us": ordered[p95_index],
        "max_us": ordered[-1],
        "mean_us": statistics.fmean(ordered),
    }
