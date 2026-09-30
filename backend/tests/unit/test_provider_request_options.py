"""Opt-in request options come only from an explicit capability profile."""

from __future__ import annotations

from app.modules.assistant.provider import AssistantProviderClient, ProviderConfig
from app.modules.assistant.provider_capabilities import ProviderCapabilities

MESSAGES = [{"role": "system", "content": "platform prompt"}, {"role": "user", "content": "q"}]


def body(capabilities: ProviderCapabilities) -> dict:
    config = ProviderConfig("p", "m", "https://example.test/v1", "unused", capabilities)
    return AssistantProviderClient._request_body(config, MESSAGES, None)


def test_defaults_send_neither_option():
    sent = body(ProviderCapabilities())
    assert "prompt_cache_key" not in sent and "reasoning_effort" not in sent


def test_cache_key_is_stable_for_the_same_system_prompt():
    capabilities = ProviderCapabilities.from_mapping({"supports_prompt_cache_key": True})
    first, second = body(capabilities), body(capabilities)
    assert first["prompt_cache_key"] == second["prompt_cache_key"]
    assert first["prompt_cache_key"].startswith("nova-")
    assert "platform prompt" not in first["prompt_cache_key"]


def test_reasoning_effort_accepts_only_known_levels():
    assert body(ProviderCapabilities.from_mapping({"reasoning_effort": "high"}))[
        "reasoning_effort"
    ] == "high"
    assert "reasoning_effort" not in body(
        ProviderCapabilities.from_mapping({"reasoning_effort": "maximum"})
    )
