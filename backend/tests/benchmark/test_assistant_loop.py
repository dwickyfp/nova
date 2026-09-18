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

Every assertion here is a *smoke* bound (the path terminates and stays within a
generous ceiling), not a performance gate: a shared CI runner is not a
benchmark machine, and a flaky threshold is worse than no threshold.
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
    registry = ToolRegistry()
    registry.register(RecordingTool())
    return AssistantLoop(
        provider=ScriptedProvider(
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
    assert result["p95_us"] < 50_000  # generous smoke bound, not a gate


async def test_benchmark_text_only_turn_with_history():
    """The same turn against a 20-message transcript — the context cost."""
    loop = _plain_loop()
    result = await measure(
        lambda: drain(loop, thread_=thread(history_turns=10), content="hello"),
        iterations=300,
    )
    _print("text_only_turn_history_10", result)
    assert result["p95_us"] < 100_000


async def test_benchmark_tool_round_trip():
    """propose → consent → run → fold → final text (the full multi-iteration path)."""
    loop = _tool_loop()
    result = await measure(
        lambda: drain(loop, thread_=thread(), content="run it"),
        iterations=500,
    )
    _print("tool_round_trip", result)
    assert result["p95_us"] < 100_000


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
    assert result["p95_us"] < 100_000


async def test_benchmark_consent_explicit_per_call():
    """No grant: the loop awaits an explicit decision before running the tool."""
    loop = _tool_loop()
    result = await measure(
        lambda: drain(loop, thread_=thread(), content="run it"),
        iterations=500,
    )
    _print("consent_explicit_per_call", result)
    assert result["p95_us"] < 100_000


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
        assert result["p95_us"] < 20_000


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
    assert result["p95_us"] < 100_000


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
    assert result["p95_us"] < 50_000


async def test_benchmark_denied_call_termination():
    """A denied call ends the iteration without running the tool."""
    loop = _tool_loop()
    result = await measure(
        lambda: drain(loop, thread_=thread(), content="run it", resolver=_deny),
        iterations=300,
    )
    _print("denied_call_termination", result)
    assert result["p95_us"] < 100_000


async def _deny(_inv, _cls) -> bool:
    return False


# ── output helper ─────────────────────────────────────────────────────────────


def _print(name: str, result: dict[str, float], extra: dict | None = None) -> None:
    """Emit one machine-readable line the report generator can parse."""
    payload = {"benchmark": name, **result}
    if extra:
        payload.update(extra)
    print("BENCHMARK " + json.dumps(payload, sort_keys=True))
