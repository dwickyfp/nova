"""Deterministic benchmarks for the bounded assistant loop (NOVA-92).

Run:

    cd backend
    uv run pytest tests/benchmark -q -s

The suite is offline and deterministic. It measures the loop's own overhead —
context assembly, the consent gate, tool round-trips, and the two termination
paths — with a fake provider and a fake tool, so the numbers do not depend on
an LLM or a StarRocks cluster. The report at
``docs/benchmarks/nova-61-assistant.md`` explains what the numbers mean and,
more importantly, what they do not cover.

Every assertion here is a **smoke** check: the path terminates, returns a
non-zero iteration count, and yields a well-formed measurement. It is not a
performance gate. A shared CI runner is not a benchmark machine, and an
absolute wall-clock threshold makes the *pass set* depend on host load — which
is exactly the non-determinism this suite exists to avoid. The measured number
lives in the report, never in a pass/fail wall.
"""

from __future__ import annotations

import json

import pytest

from app.modules.assistant.service import (
    DEFAULT_MAX_ITERATIONS,
    AssistantLoop,
)
from app.modules.assistant.skills import (
    DEFAULT_SKILL_PROMPT,
    _estimate_tokens,
    default_skill,
)
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import (
    RecordingTool,
    ScriptedProvider,
    drain,
    measure,
    text_frame,
    thread,
    tool_call_frame,
)

pytestmark = pytest.mark.benchmark


def _plain_loop(max_iterations: int = DEFAULT_MAX_ITERATIONS) -> AssistantLoop:
    return AssistantLoop(
        provider=ScriptedProvider([text_frame("final answer")]),
        registry=ToolRegistry(),
        system_prompt="bench system prompt",
        max_iterations=max_iterations,
    )


def _tool_loop(max_iterations: int = DEFAULT_MAX_ITERATIONS) -> AssistantLoop:
    class FreshProvider(ScriptedProvider):
        async def resolve(self, **kwargs):
            self.script = [tool_call_frame("c1"), text_frame("done after tool")]
            return await super().resolve(**kwargs)

    registry = ToolRegistry()
    registry.register(RecordingTool())
    return AssistantLoop(
        provider=FreshProvider(
            [tool_call_frame("c1"), text_frame("done after tool")]
        ),
        registry=registry,
        system_prompt="bench system prompt",
        max_iterations=max_iterations,
    )


# ── Loop overhead per turn ────────────────────────────────────────────────────


async def test_benchmark_text_only_turn():
    """One iteration, no tool: context → provider → text → done."""
    loop = _plain_loop()
    result = await measure(
        lambda: drain(loop, thread_=thread(), content="hello"),
        iterations=500,
    )
    _print("text_only_turn", result)
    _assert_measured(result)


async def test_benchmark_text_only_turn_with_history():
    """The same turn against a 20-message transcript — the context cost."""
    loop = _plain_loop()
    result = await measure(
        lambda: drain(loop, thread_=thread(history_turns=10), content="hello"),
        iterations=300,
    )
    _print("text_only_turn_history_10", result)
    _assert_measured(result)


async def test_benchmark_tool_round_trip():
    """propose → consent → run → fold → final text (the full multi-iteration path)."""
    loop = _tool_loop()
    result = await measure(
        lambda: drain(loop, thread_=thread(), content="run it"),
        iterations=500,
    )
    _print("tool_round_trip", result)
    assert loop._registry.get("query_execute").runs == 500
    _assert_measured(result)


# ── Consent gate ──────────────────────────────────────────────────────────────


async def test_benchmark_consent_auto_approve():
    """Read-only grant: the gate is skipped, no tool_call frame is emitted."""
    loop = _tool_loop()
    granted = thread(read_only_grant=True)
    result = await measure(
        lambda: drain(loop, thread_=granted, content="run it"),
        iterations=500,
    )
    _print("consent_auto_approve", result)
    assert loop._registry.get("query_execute").runs == 500
    _assert_measured(result)


async def test_benchmark_consent_explicit_per_call():
    """No grant: the loop awaits an explicit decision before running the tool."""
    loop = _tool_loop()
    result = await measure(
        lambda: drain(loop, thread_=thread(), content="run it"),
        iterations=500,
    )
    _print("consent_explicit_per_call", result)
    assert loop._registry.get("query_execute").runs == 500
    _assert_measured(result)


# ── Context assembly and skill prompt size ────────────────────────────────────


async def test_benchmark_context_assembly():
    """``_build_messages`` over a growing transcript, excluding the provider."""
    loop = _plain_loop()

    def build(turns: int):
        async def _run() -> int:
            messages = loop._build_messages(
                thread(history_turns=turns), "current turn"
            )
            return len(messages)

        return _run

    for turns in (0, 10, 50):
        result = await measure(build(turns), iterations=1000)
        _print(f"build_messages_history_{turns}", result)
        _assert_measured(result)


def test_skill_prompt_size():
    """The assembled default skill is sent on every turn — measure its cost."""
    chars = len(DEFAULT_SKILL_PROMPT)
    result = {
        "iterations": 1.0,
        "min_us": 0.0,
        "median_us": 0.0,
        "p95_us": 0.0,
        "max_us": 0.0,
        "mean_us": 0.0,
    }
    _print(
        "skill_prompt_size",
        result,
        extra={
            "characters": chars,
            "estimated_tokens": _estimate_tokens(DEFAULT_SKILL_PROMPT),
            "budget_tokens": default_skill.metadata.token_budget,
            "sections": len(default_skill.sections),
            "revision": default_skill.metadata.revision[:12],
        },
    )
    assert chars > 0


# ── Termination paths ─────────────────────────────────────────────────────────


async def test_benchmark_iteration_cap_termination():
    """A model that never stops calling tools is stopped at the cap."""
    registry = ToolRegistry()
    registry.register(RecordingTool())
    loop = AssistantLoop(
        provider=ScriptedProvider([tool_call_frame(f"c{i}") for i in range(64)]),
        registry=registry,
        system_prompt="bench system prompt",
        max_iterations=8,
    )
    result = await measure(
        lambda: drain(loop, thread_=thread(read_only_grant=True), content="loop"),
        iterations=300,
    )
    _print("iteration_cap_termination", result)
    _assert_measured(result)


async def test_benchmark_time_budget_termination():
    """A zero-second budget terminates on the first guard check."""
    registry = ToolRegistry()
    registry.register(RecordingTool())
    loop = AssistantLoop(
        provider=ScriptedProvider([tool_call_frame("c1")]),
        registry=registry,
        system_prompt="bench system prompt",
        max_iterations=8,
        time_budget_seconds=0.0,
    )
    result = await measure(
        lambda: drain(loop, thread_=thread(), content="go"),
        iterations=500,
    )
    _print("time_budget_termination", result)
    _assert_measured(result)


async def test_benchmark_denied_call_termination():
    """A denied call ends the iteration without running the tool."""
    loop = _tool_loop()
    result = await measure(
        lambda: drain(loop, thread_=thread(), content="run it", resolver=_deny),
        iterations=300,
    )
    _print("denied_call_termination", result)
    _assert_measured(result)


async def _deny(_inv, _cls) -> bool:
    return False


# ── context management (NOVA-124) ────────────────────────────────────────────


async def test_benchmark_context_curation_overhead():
    """The added cost of curating a transcript, above assembling it.

    Two cases on the same 50-turn transcript: a budget so large curation is a
    no-op walk, and the default budget where it must drop turns. The difference
    is the price of the context-management feature; it must stay negligible next
    to the provider and engine round-trips it protects.
    """
    from app.modules.assistant.context import ContextManager

    def build(manager: ContextManager):
        loop = AssistantLoop(
            provider=ScriptedProvider([text_frame("x")]),
            registry=ToolRegistry(),
            system_prompt="bench system prompt",
            context_manager=manager,
        )
        history = thread(history_turns=50)

        async def _run() -> int:
            return len(loop._build_messages(history, "current turn"))

        return _run

    noop = await measure(build(ContextManager(token_budget=10_000_000)), iterations=1000)
    _print("context_curate_noop_history_50", noop)
    _assert_measured(noop)

    active = await measure(build(ContextManager(token_budget=2_000)), iterations=1000)
    _print("context_curate_active_history_50", active)
    _assert_measured(active)
    # The active case must actually have pruned; assert the path is exercised by
    # checking a fresh call's stats rather than timing it.
    manager = ContextManager(token_budget=2_000)
    transcript = [{"role": "system", "content": "s"}]
    for i in range(50):
        transcript.append({"role": "user", "content": f"q{i} " + "x" * 200})
        transcript.append({"role": "assistant", "content": f"a{i} " + "y" * 200})
    stats = manager.curate(transcript).stats
    assert stats.dropped_turns > 0


# ── output helper ─────────────────────────────────────────────────────────────


def _print(name: str, result: dict[str, float], extra: dict | None = None) -> None:
    """Emit one machine-readable line the report generator can parse."""
    payload = {"benchmark": name, **result}
    if extra:
        payload.update(extra)
    print("BENCHMARK " + json.dumps(payload, sort_keys=True))


def _assert_measured(result: dict[str, float]) -> None:
    """Smoke check: the case ran and produced a well-formed measurement.

    Deliberately **not** an absolute threshold. Under load a shared runner can
    inflate any microsecond bound, so a wall-clock assertion is a flake source,
    not a correctness signal. What must hold is that the path terminated, ran at
    least one iteration, and reported finite non-negative samples.
    """
    assert result["iterations"] >= 1
    for key in ("min_us", "median_us", "p95_us", "max_us"):
        assert result[key] >= 0
        assert result[key] != float("inf")
