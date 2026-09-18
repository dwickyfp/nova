"""Measurement harness for the bounded assistant loop.

This module is the instrumentation half of the benchmark suite: it drives a
single assistant turn to completion and reports wall-clock statistics over many
repetitions using ``time.perf_counter_ns`` (monotonic, nanosecond resolution).

What it does **not** do: call a network, resolve a provider key, or touch
StarRocks. The provider is a scripted fake and the tool is a recording fake, so
the only cost measured is the loop's own work — message assembly, consent
resolution, control flow, and SSE frame formatting. Provider latency and engine
latency are deliberately excluded (they dominate in production and are not
something a single-process loop can represent).

Statistics are reported as min / median / p95 over a fixed number of
repetitions. A single run is not a measurement; the report includes the p95 so
the tail of the loop's overhead is visible rather than hidden behind a mean.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.modules.assistant.provider import ProviderConfig
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import (
    AssistantMessage,
    AssistantThread,
    ConsentPolicy,
)
from app.modules.assistant.tools import (
    ToolInvocation,
    ToolOutcome,
    ToolRegistry,
)

#: Fixed wall-clock budget for every benchmark loop. Large enough that the
#: budget never fires in the happy-path cases (so the measured number is the
#: loop, not an early termination), and the same constant everywhere so the
#: numbers are comparable.
BENCH_TIME_BUDGET_SECONDS = 60.0

#: Repetitions per case. 200 keeps the whole suite under a few seconds while
#: giving a stable median and a p95 that is not noise.
REPETITIONS = 200


def _provider_config() -> ProviderConfig:
    """A canned provider config — no key is ever read from the environment.

    ``api_key`` is a literal placeholder (``"bench-key"``), which is not a
    credential: the fake provider never transmits it. The string is shaped
    deliberately unlike a real key so a scanner does not flag this fixture.
    """
    return ProviderConfig(
        provider_id="bench",
        model="bench-model",
        endpoint="http://bench.invalid/v1/chat/completions",
        api_key="bench-key",
    )


class ScriptedProvider:
    """A fake ``AssistantProviderClient`` that replays a fixed script.

    ``complete`` returns the next message in the script and records the messages
    it was given (so a case can assert the loop assembled the expected context).
    It never resolves a provider through ``ai_service`` and never opens a socket.
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def resolve(self) -> ProviderConfig:
        return _provider_config()

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        provider: ProviderConfig | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"messages": messages, "tools": tools})
        if not self._script:
            return {"role": "assistant", "content": "done"}
        return self._script.pop(0)


class RecordingTool:
    """A fake tool that records invocations and returns a canned outcome.

    Registered on a private :class:`ToolRegistry`; the process-wide registry is
    never touched, so a benchmark cannot leak into another test's state.
    """

    name = "bench_tool"
    description = "a recording tool used only by the benchmark"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"sql": {"type": "string"}},
    }

    def __init__(
        self,
        *,
        classification: str = "read_only",
        summary: str = '{"columns":["n"],"row_count":1,"rows":[[1]]}',
    ) -> None:
        self._classification = classification
        self._summary = summary
        self.invocations: list[ToolInvocation] = []

    @property
    def classification(self) -> str:
        return self._classification

    def preview(self, invocation: ToolInvocation) -> str:
        return invocation.arguments.get("sql", "")

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        self.invocations.append(invocation)
        return ToolOutcome(ok=True, summary=self._summary)


def tool_call_message(
    *,
    call_id: str = "call-1",
    name: str = "bench_tool",
    sql: str = "SELECT 1",
) -> dict[str, Any]:
    """A raw OpenAI-style assistant message carrying exactly one tool call."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps({"sql": sql})},
            }
        ],
    }


def text_message(text: str = "Here is your answer.") -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def make_thread(
    *,
    consent_read_only: bool = False,
    history: list[AssistantMessage] | None = None,
) -> AssistantThread:
    return AssistantThread(
        thread_id="bench-thread",
        user_name="bench-user",
        title="benchmark",
        messages=list(history or []),
        consent=ConsentPolicy(always_allow_read_only=consent_read_only),
    )


def make_context() -> LoopContext:
    """A context with no real credential.

    ``user`` is ``None`` on purpose: the recording tool never reads it, and the
    benchmark must never carry an ``encrypted_password``. If a future change
    makes the loop read the credential, this benchmark fails loudly instead of
    silently exercising a credential path.
    """
    return LoopContext(
        user_name="bench-user",
        database="bench_db",
        schema_name="bench_schema",
        role="bench_role",
        session_id="bench-session",
        thread_id="bench-thread",
        user=None,
    )


async def _drain(loop: AssistantLoop, thread: AssistantThread, content: str, resolve) -> int:
    frames = 0
    async for _frame in loop.run(
        thread=thread, user_content=content, context=make_context(), resolve_consent=resolve
    ):
        frames += 1
    return frames


def drive_turn(
    *,
    provider: ScriptedProvider,
    tool: RecordingTool,
    thread: AssistantThread,
    user_content: str = "answer the question",
    resolve_consent: Callable[[ToolInvocation, str], Awaitable[bool | None]] | None = None,
) -> Callable[[], Awaitable[int]]:
    """Return an async callable that runs one full turn and returns frame count.

    The loop is rebuilt per invocation because ``ScriptedProvider`` consumes its
    script; rebuilding is cheap and keeps every repetition identical.
    """
    registry = ToolRegistry()
    registry.register(tool)

    async def _resolve(_invocation: ToolInvocation, _classification: str) -> bool | None:
        return True

    resolver = resolve_consent or _resolve

    async def _turn() -> int:
        loop = AssistantLoop(
            provider=provider,
            registry=registry,
            time_budget_seconds=BENCH_TIME_BUDGET_SECONDS,
        )
        return await _drain(loop, thread, user_content, resolver)

    return _turn


@dataclass(frozen=True)
class TurnSpec:
    """One benchmark case: a scripted turn plus how to build its thread."""

    name: str
    script: list[dict[str, Any]]
    consent_read_only: bool = False
    tool: RecordingTool = field(default_factory=RecordingTool)
    user_content: str = "answer the question"


@dataclass(frozen=True)
class Stats:
    """min / median / p95 in nanoseconds plus the observed iteration count."""

    name: str
    repetitions: int
    iterations: int
    minimum_ns: int
    median_ns: int
    p95_ns: int

    @property
    def median_ms(self) -> float:
        return self.median_ns / 1_000_000


def _p95(samples: list[int]) -> int:
    """Nearest-rank p95 — no interpolation, so the tail is a real sample."""
    ordered = sorted(samples)
    rank = max(1, round(0.95 * len(ordered)))
    return ordered[rank - 1]


async def measure_turn(spec: TurnSpec, *, repetitions: int = REPETITIONS) -> Stats:
    """Time ``repetitions`` identical turns and summarise the samples.

    Warm-up is explicit: the first two samples are measured after one discarded
    call so import/allocation costs do not skew the min. Each case rebuilds its
    provider from ``spec.script`` (the fake is stateful) so repetitions are
    truly identical.
    """
    provider = ScriptedProvider(spec.script)
    tool = spec.tool
    thread = make_thread(consent_read_only=spec.consent_read_only)

    samples: list[int] = []
    iterations = 0
    total = repetitions + 1  # one discarded warm-up
    for index in range(total):
        turn = drive_turn(
            provider=provider,
            tool=tool,
            thread=thread,
            user_content=spec.user_content,
        )
        provider._script = list(spec.script)
        provider.calls.clear()
        start = time.perf_counter_ns()
        await turn()
        elapsed = time.perf_counter_ns() - start
        if index == 0:
            continue
        # Provider calls == loop iterations: one ``complete`` per iteration.
        iterations = len(provider.calls)
        samples.append(elapsed)

    return Stats(
        name=spec.name,
        repetitions=len(samples),
        iterations=iterations,
        minimum_ns=min(samples),
        median_ns=int(statistics.median(samples)),
        p95_ns=_p95(samples),
    )


def format_table(stats: list[Stats]) -> str:
    """Render stats as a Markdown table for the report/console."""
    lines = [
        "| case | iters | min (ns) | median (ns) | p95 (ns) | median (µs) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in stats:
        lines.append(
            f"| {s.name} | {s.iterations} | {s.minimum_ns} | {s.median_ns} "
            f"| {s.p95_ns} | {s.median_ms * 1000:.1f} |"
        )
    return "\n".join(lines)


def run_async(coro: Awaitable[Any]) -> Any:
    """Run a coroutine without requiring a pytest-asyncio mode."""
    return asyncio.run(coro)
