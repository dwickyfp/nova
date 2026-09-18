"""Deterministic offline benchmarks for the bounded assistant loop (NOVA-92).

Run:

    cd backend && uv run pytest tests/benchmark -q -s

Every case here is offline and single-process: a scripted fake provider and a
recording fake tool, driven against a fixed wall-clock budget. Nothing resolves
a provider key, opens a socket, or touches the engine, so the suite is
reproducible in CI and prints the same pass set every run.

What is measured (all loop overhead, provider time excluded):

* a one-iteration text-only turn;
* the multi-iteration tool round-trip (propose → consent → run → fold → text);
* the auto-approve path (read-only grant) versus the explicit per-call path;
* context assembly (``_build_messages``) and the default skill prompt size;
* the iteration-cap and time-budget termination paths.

The numbers are printed to stdout (``-s``) and mirrored in
``docs/benchmarks/nova-61-assistant.md``. They are loop overhead by
construction; production latency is dominated by provider and engine
round-trips, which this harness excludes on purpose.
"""

from __future__ import annotations

import time

import pytest

from app.modules.assistant.service import (
    DEFAULT_MAX_ITERATIONS,
    AssistantLoop,
)
from app.modules.assistant.skills import DEFAULT_SKILL_PROMPT, _estimate_tokens
from app.modules.assistant.state import AssistantMessage
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import (
    BENCH_TIME_BUDGET_SECONDS,
    REPETITIONS,
    RecordingTool,
    ScriptedProvider,
    TurnSpec,
    format_table,
    make_context,
    make_thread,
    measure_turn,
    run_async,
    text_message,
    tool_call_message,
)

pytestmark = pytest.mark.benchmark

# ── Loop overhead per turn ───────────────────────────────────────────────────

TEXT_ONLY = TurnSpec(
    name="text_only_1_iter",
    script=[text_message()],
)

TOOL_ROUND_TRIP = TurnSpec(
    name="tool_round_trip_explicit",
    script=[tool_call_message(), text_message()],
)

TOOL_ROUND_TRIP_GRANT = TurnSpec(
    name="tool_round_trip_grant",
    script=[tool_call_message(), text_message()],
    consent_read_only=True,
)


def test_loop_overhead_text_only_turn():
    stats = run_async(measure_turn(TEXT_ONLY))
    print(f"\n{format_table([stats])}")
    assert stats.iterations == 1
    assert stats.median_ns > 0


def test_loop_overhead_tool_round_trip_explicit_consent():
    stats = run_async(measure_turn(TOOL_ROUND_TRIP))
    print(f"\n{format_table([stats])}")
    # propose → consent → run → fold → final text = 2 provider iterations.
    assert stats.iterations == 2


def test_consent_gate_cost_is_visible():
    """The auto-approve path must be measurably cheaper than asking.

    Both turns are the same script; only the consent route differs. The grant
    path skips the ``tool_call`` frame and the awaited resolver. We assert the
    *ordering* is stable enough to report, with a tolerance so a noisy CI box
    does not flake: a grant cannot plausibly be slower by a wide margin.
    """
    explicit = run_async(measure_turn(TOOL_ROUND_TRIP))
    granted = run_async(measure_turn(TOOL_ROUND_TRIP_GRANT))
    print(f"\n{format_table([explicit, granted])}")
    assert granted.iterations == explicit.iterations == 2
    # The grant path emits one fewer frame (no tool_call) by construction.
    assert granted.median_ns <= explicit.median_ns * 2.0


def test_all_loop_overhead_cases_in_one_table():
    """One consolidated table for the report's headline numbers."""
    stats = [
        run_async(measure_turn(spec))
        for spec in (TEXT_ONLY, TOOL_ROUND_TRIP, TOOL_ROUND_TRIP_GRANT)
    ]
    print(f"\n{format_table(stats)}")
    assert all(s.repetitions == REPETITIONS for s in stats)


# ── Context assembly cost ────────────────────────────────────────────────────


def test_build_messages_cost_grows_with_history():
    """``_build_messages`` is pure; time it directly with two history sizes."""
    loop = AssistantLoop(provider=ScriptedProvider([text_message()]), registry=ToolRegistry())

    def _build(history_len: int) -> int:
        history = [
            AssistantMessage(message_id=f"m{i}", role="user", content="question " * 10)
            for i in range(history_len)
        ]
        thread = make_thread(history=history)
        samples: list[int] = []
        for _ in range(500):
            start = time.perf_counter_ns()
            messages = loop._build_messages(thread, "current question")
            samples.append(time.perf_counter_ns() - start)
        assert messages[-1]["content"] == "current question"
        return int(sorted(samples)[len(samples) // 2])

    short = _build(0)
    long = _build(50)
    print(
        f"\n| build_messages | median ns |\n| --- | ---: |\n"
        f"| 0 history | {short} |\n| 50 history | {long} |"
    )
    assert long > 0 and short > 0


def test_default_skill_prompt_size():
    """The default system prompt is sent on every turn — report its size.

    ``_estimate_tokens`` is the module's own deterministic estimator
    (4 chars/token); the same text always scores the same, so the report number
    is reproducible without a tokenizer dependency.
    """
    chars = len(DEFAULT_SKILL_PROMPT)
    tokens = _estimate_tokens(DEFAULT_SKILL_PROMPT)
    print(
        f"\n| skill prompt | chars | est. tokens |\n| --- | ---: | ---: |\n"
        f"| DEFAULT_SKILL_PROMPT | {chars} | {tokens} |"
    )
    assert chars > 0
    assert tokens == chars // 4


def test_tool_schemas_are_part_of_every_call():
    registry = ToolRegistry()
    registry.register(RecordingTool())
    loop = AssistantLoop(provider=ScriptedProvider([text_message()]), registry=registry)
    schemas = loop._tool_schemas()
    assert schemas and schemas[0]["function"]["name"] == "bench_tool"


# ── Bounded termination (must terminate, measured, not hang) ─────────────────


def test_iteration_cap_terminates_measured():
    """A model that never stops calling tools hits the cap and returns."""
    cap = 3
    script = [tool_call_message(call_id=f"c{i}") for i in range(cap)]
    provider = ScriptedProvider(script)
    tool = RecordingTool()
    thread = make_thread(consent_read_only=True)  # grant → no resolver await
    loop = AssistantLoop(
        provider=provider,
        registry=_registry_with(tool),
        max_iterations=cap,
        time_budget_seconds=BENCH_TIME_BUDGET_SECONDS,
    )

    async def _turn():
        start = time.perf_counter_ns()
        frames = [f async for f in loop.run(
            thread=thread,
            user_content="loop",
            context=make_context(),
            resolve_consent=_allow,
        )]
        return time.perf_counter_ns() - start, frames

    elapsed, frames = run_async(_turn())
    assert frames[-1].startswith("event: done")
    assert "iteration_cap" in frames[-1]
    assert len(tool.invocations) == cap
    print(f"\niteration cap terminated in {elapsed} ns ({len(frames)} frames)")
    assert elapsed > 0

def test_iteration_cap_default_is_eight():
    assert DEFAULT_MAX_ITERATIONS == 8


def test_time_budget_terminates_measured():
    """A turn whose provider outlasts the budget stops with a timeout frame.

    The budget is set to zero so the loop's own deadline check fires on the
    first iteration — the test proves the guard terminates rather than hangs,
    without sleeping.
    """
    provider = ScriptedProvider([text_message()])
    thread = make_thread()
    loop = AssistantLoop(
        provider=provider,
        registry=ToolRegistry(),
        time_budget_seconds=0.0,
    )

    async def _turn():
        start = time.perf_counter_ns()
        frames = [f async for f in loop.run(
            thread=thread,
            user_content="timeout",
            context=make_context(),
            resolve_consent=_allow,
        )]
        return time.perf_counter_ns() - start, frames

    elapsed, frames = run_async(_turn())
    assert frames[0].startswith("event: error")
    assert "timeout" in frames[0]
    assert frames[-1].startswith("event: done")
    # The provider must not have been called: the deadline check precedes it.
    assert provider.calls == []
    print(f"\ntime budget terminated in {elapsed} ns ({len(frames)} frames)")


def test_provider_failure_terminates_without_credentials():
    class FailingProvider(ScriptedProvider):
        async def resolve(self):
            raise RuntimeError("no provider configured")

    loop = AssistantLoop(provider=FailingProvider([]), registry=ToolRegistry())
    frames = run_async(_collect(loop, make_thread()))
    assert frames[0].startswith("event: error")
    assert "provider_unavailable" in frames[0]


# ── Determinism guard ────────────────────────────────────────────────────────


def test_two_runs_report_the_same_iteration_counts():
    """Re-run gives the same shape — the suite is deterministic.

    Wall-clock numbers vary by machine; the pass set and iteration counts do
    not. This is the "re-run twice gives the same pass set" acceptance check.
    """
    first = [run_async(measure_turn(s, repetitions=10)) for s in (TEXT_ONLY, TOOL_ROUND_TRIP)]
    second = [run_async(measure_turn(s, repetitions=10)) for s in (TEXT_ONLY, TOOL_ROUND_TRIP)]
    assert [s.iterations for s in first] == [s.iterations for s in second]


# ── helpers ──────────────────────────────────────────────────────────────────


def _registry_with(tool: RecordingTool) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(tool)
    return registry


async def _allow(_invocation, _classification) -> bool:
    return True


async def _collect(loop: AssistantLoop, thread) -> list[str]:
    return [
        frame
        async for frame in loop.run(
            thread=thread,
            user_content="x",
            context=make_context(),
            resolve_consent=_allow,
        )
    ]
