"""Dropped turns: a model summary prepared in the background, used on the next turn."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.modules.assistant.context import (
    MODEL_SUMMARY_PREFIX,
    ContextManager,
    SummaryCache,
    summary_cache,
)
from app.modules.assistant.provider_capabilities import ProviderCapabilities
from app.modules.assistant.service import _summarize_in_background


def transcript(turns: int) -> list[dict]:
    messages = [{"role": "system", "content": "contract"}]
    for index in range(turns):
        messages.append({"role": "user", "content": f"question {index} " + "x" * 400})
        messages.append({"role": "assistant", "content": f"answer {index} " + "y" * 400})
    return messages


def test_summary_cache_is_scoped_by_thread_and_bounded():
    cache = SummaryCache(capacity=2)
    lines = ("User: omzet = revenue minus returns",)
    cache.put("t1", lines, "Omzet is revenue minus returns.")
    assert cache.get("t1", lines).startswith(MODEL_SUMMARY_PREFIX)
    assert cache.get("t2", lines) is None
    cache.put("t1", ("a",), "a")
    cache.put("t1", ("b",), "b")
    assert cache.get("t1", lines) is None


def test_curate_uses_a_model_summary_when_one_matches():
    messages = transcript(12)
    first = ContextManager(token_budget=1500).curate(messages)
    assert first.stats.dropped_turns and first.stats.dropped_lines
    assert not first.stats.model_summary
    cache = SummaryCache()
    cache.put("t1", first.stats.dropped_lines, "The user defined omzet.")
    second = ContextManager(
        token_budget=1500, summary_lookup=lambda lines: cache.get("t1", lines)
    ).curate(messages)
    assert second.stats.model_summary
    assert any("The user defined omzet." in str(item["content"]) for item in second.messages)


async def test_background_summary_fills_the_cache():
    calls = []

    async def complete(*, messages, provider):
        calls.append(messages)
        return {"content": "The user asked about revenue by city."}

    lines = ("User: revenue by city", "Assistant: Jakarta leads.")
    _summarize_in_background(SimpleNamespace(complete=complete), object(), "thread-x", lines)
    for _ in range(20):
        await asyncio.sleep(0)
        if summary_cache.get("thread-x", lines):
            break
    assert "revenue by city" in summary_cache.get("thread-x", lines)
    assert "Do not add facts or numbers" in calls[0][0]["content"]


def test_window_budget_never_shrinks_below_the_default():
    capabilities = ProviderCapabilities.from_mapping({"context_window": 200_000})
    assert int(capabilities.context_window * 0.6) == 120_000
    assert ProviderCapabilities().context_window * 0.6 < 24_000
