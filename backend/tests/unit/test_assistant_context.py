"""Context-window management for the assistant loop (NOVA-124) — unit tests.

Coverage of ``app.modules.assistant.context``:

* the token estimator and per-message accounting;
* tool-result clearing: old bodies replaced, recent ones kept, marker stable;
* recency windowing: oldest turns dropped first, the current turn never dropped;
* the summary note is bounded and is charged against the budget;
* ``fits`` is false only when the pinned window alone exceeds the budget;
* the manager is pure (input list is not mutated) and deterministic;
* the loop wires curation in: a long transcript is shrunk before it is sent, and
  an over-budget pinned window terminates with ``context_overflow`` rather than
  a provider error.

No network, no provider, no StarRocks.
"""

from __future__ import annotations

from app.modules.assistant.context import (
    CHARS_PER_TOKEN,
    ContextManager,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
)
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import (
    RecordingTool,
    ScriptedProvider,
    allow,
    text_frame,
)


def _turn(user: str, assistant: str) -> list[dict]:
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


def _long_history(turns: int, *, chars: int = 400) -> list[dict]:
    messages = [{"role": "system", "content": "system contract"}]
    for i in range(turns):
        messages.extend(_turn(f"question {i} " + "u" * chars, f"answer {i} " + "a" * chars))
    messages.append({"role": "user", "content": "current question"})
    return messages


# ── the estimator ────────────────────────────────────────────────────────────


def test_estimate_tokens_is_chars_over_four():
    assert CHARS_PER_TOKEN == 4
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_estimate_message_tokens_counts_content_and_tool_calls():
    plain = {"role": "assistant", "content": "a" * 40}
    assert estimate_message_tokens(plain) == 10
    with_call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "query_execute",
                    "arguments": "a" * 40,
                }
            }
        ],
    }
    # name (13 chars -> 3) + arguments (40 -> 10)
    assert estimate_message_tokens(with_call) == 3 + 10


def test_estimate_messages_tokens_sums():
    messages = _turn("a" * 8, "b" * 8)
    assert estimate_messages_tokens(messages) == 2 + 2


# ── pure and deterministic ───────────────────────────────────────────────────


def test_curate_does_not_mutate_input():
    messages = _long_history(20)
    snapshot = [dict(m) for m in messages]
    ContextManager(token_budget=100).curate(messages)
    assert messages == snapshot


def test_curate_is_deterministic():
    messages = _long_history(20)
    first = ContextManager(token_budget=200).curate(messages)
    second = ContextManager(token_budget=200).curate(messages)
    assert first.messages == second.messages
    assert first.stats.as_dict() == second.stats.as_dict()


def test_constructor_rejects_bad_budget():
    import pytest

    with pytest.raises(ValueError):
        ContextManager(token_budget=0)
    with pytest.raises(ValueError):
        ContextManager(keep_recent=-1)
    with pytest.raises(ValueError):
        ContextManager(tool_result_ttl=-1)


# ── system prompt and recent window are preserved ────────────────────────────


def test_system_prompt_always_survives():
    messages = _long_history(40)
    curated = ContextManager(token_budget=100, keep_recent=2).curate(messages)
    assert curated.messages[0] == {"role": "system", "content": "system contract"}


def test_recent_window_is_kept_verbatim():
    messages = _long_history(40)
    curated = ContextManager(token_budget=200, keep_recent=4).curate(messages)
    assert curated.messages[-4:] == messages[-4:]


def test_current_turn_is_never_dropped():
    messages = _long_history(40)
    curated = ContextManager(token_budget=50, keep_recent=2).curate(messages)
    assert curated.messages[-1] == {"role": "user", "content": "current question"}


# ── tool-result clearing ─────────────────────────────────────────────────────


def test_old_tool_results_are_cleared_and_recent_kept():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q1"},
        {"role": "user", "content": "[tool result] old rows " + "x" * 400},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "user", "content": "[tool result] recent rows " + "y" * 400},
    ]
    curated = ContextManager(token_budget=100_000, tool_result_ttl=2, keep_recent=0).curate(
        messages
    )
    cleared = [
        m["content"] for m in curated.messages if str(m.get("content")).startswith("[tool result")
    ]
    assert any("cleared" in c for c in cleared)
    # The most recent tool result (inside the TTL) keeps its body.
    assert any("recent rows" in c for c in cleared)
    assert curated.stats.cleared_tool_results == 1


def test_clearing_marks_but_keeps_message_position():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "[tool result] " + "x" * 400},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "current"},
    ]
    curated = ContextManager(token_budget=100_000, tool_result_ttl=0, keep_recent=0).curate(
        messages
    )
    assert len(curated.messages) == len(messages)
    assert "cleared" in curated.messages[1]["content"]


def test_tool_result_ttl_zero_clears_all():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "[tool result] one"},
        {"role": "user", "content": "[tool result] two"},
        {"role": "user", "content": "current"},
    ]
    curated = ContextManager(token_budget=100_000, tool_result_ttl=0, keep_recent=0).curate(
        messages
    )
    cleared = [m for m in curated.messages if "cleared" in str(m.get("content"))]
    assert len(cleared) == 2


# ── dropping and the summary note ────────────────────────────────────────────


def test_oldest_turns_dropped_first():
    messages = _long_history(30, chars=200)
    curated = ContextManager(token_budget=500, keep_recent=2).curate(messages)
    joined = " ".join(str(m.get("content")) for m in curated.messages)
    # The oldest explicit content is gone, the newest is present.
    assert "question 0 " not in joined
    assert "current question" in joined


def test_dropped_turns_produce_a_bounded_summary():
    messages = _long_history(30, chars=200)
    curated = ContextManager(token_budget=500, keep_recent=2).curate(messages)
    assert curated.stats.dropped_turns > 0
    assert curated.stats.summarized is True
    summary = curated.messages[1]["content"]
    assert summary.startswith("[earlier conversation")
    assert len(summary) <= 800 + len(
        "[earlier conversation, summarized because it exceeded the context budget]\n"
    )


def test_curated_output_is_within_budget_when_history_is_droppable():
    messages = _long_history(50, chars=300)
    curated = ContextManager(token_budget=1000, keep_recent=2).curate(messages)
    assert curated.stats.output_tokens <= 1000
    assert curated.stats.fits is True


def test_no_curation_when_already_within_budget():
    messages = _long_history(2, chars=20)
    curated = ContextManager(token_budget=100_000).curate(messages)
    assert curated.stats.dropped_turns == 0
    assert curated.stats.cleared_tool_results == 0
    assert curated.messages == messages


def test_fits_false_when_pinned_window_alone_exceeds_budget():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "a" * 4000},
    ]
    curated = ContextManager(token_budget=100, keep_recent=1).curate(messages)
    assert curated.stats.fits is False


def test_empty_and_system_only_inputs():
    empty = ContextManager().curate([])
    assert empty.messages == []
    sys_only = ContextManager().curate([{"role": "system", "content": "s"}])
    assert sys_only.messages == [{"role": "system", "content": "s"}]


# ── loop integration ─────────────────────────────────────────────────────────


def _loop(provider: ScriptedProvider, *, manager: ContextManager) -> AssistantLoop:
    registry = ToolRegistry()
    registry.register(RecordingTool())
    return AssistantLoop(
        provider=provider,
        registry=registry,
        system_prompt="system",
        context_manager=manager,
    )


async def test_loop_curates_a_long_transcript_before_sending():
    provider = ScriptedProvider(script=[text_frame("done")])
    manager = ContextManager(token_budget=1000, keep_recent=2)
    loop = _loop(provider, manager=manager)

    thread = AssistantThread(thread_id="t", user_name="u", title="T")
    for i in range(30):
        thread.messages.append(AssistantMessage(message_id=f"u{i}", role="user", content="q" * 300))
        thread.messages.append(
            AssistantMessage(message_id=f"a{i}", role="assistant", content="a" * 300)
        )
    thread.messages.append(AssistantMessage(message_id="cur", role="user", content="now"))

    context = LoopContext(user_name="u", thread_id="t")
    frames = [
        f
        async for f in loop.run(
            thread=thread, user_content="now", context=context, resolve_consent=allow
        )
    ]
    stats = context.context_stats
    assert stats is not None
    assert stats["dropped_turns"] > 0
    assert stats["fits"] is True
    assert any("event: done" in f for f in frames)


async def test_loop_stops_with_context_overflow_when_undroppable():
    provider = ScriptedProvider(script=[text_frame("should not run")])
    # A tiny budget with a huge system prompt: nothing droppable fits.
    manager = ContextManager(token_budget=10, keep_recent=1)
    loop = AssistantLoop(
        provider=provider,
        registry=ToolRegistry(),
        system_prompt="s" * 4000,
        context_manager=manager,
    )

    thread = AssistantThread(thread_id="t", user_name="u", title="T")
    thread.messages.append(AssistantMessage(message_id="cur", role="user", content="now"))
    context = LoopContext(user_name="u", thread_id="t")
    frames = [
        f
        async for f in loop.run(
            thread=thread, user_content="now", context=context, resolve_consent=allow
        )
    ]
    assert any("context_overflow" in f for f in frames)
    assert provider.calls == 0


async def test_loop_curation_note_is_surfaced():
    provider = ScriptedProvider(script=[text_frame("done")])
    manager = ContextManager(token_budget=1000, keep_recent=2)
    loop = _loop(provider, manager=manager)

    thread = AssistantThread(thread_id="t", user_name="u", title="T")
    for i in range(20):
        thread.messages.append(AssistantMessage(message_id=f"u{i}", role="user", content="q" * 300))
        thread.messages.append(
            AssistantMessage(message_id=f"a{i}", role="assistant", content="a" * 300)
        )
    thread.messages.append(AssistantMessage(message_id="cur", role="user", content="now"))

    context = LoopContext(user_name="u", thread_id="t")
    frames = [
        f
        async for f in loop.run(
            thread=thread, user_content="now", context=context, resolve_consent=allow
        )
    ]
    assert any("Fitting context budget" in f for f in frames)
