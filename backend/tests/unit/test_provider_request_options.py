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


def test_every_system_instruction_reaches_the_model_as_one_leading_message():
    # Some gateways read only the first system message; page context and the
    # loop's later corrections must not be dropped.
    config = ProviderConfig("p", "m", "https://example.test/v1", "unused", ProviderCapabilities())
    messages = [
        {"role": "system", "content": "platform prompt"},
        {"role": "system",
         "content": "<NOVA_APPLICATION_CONTEXT>editor</NOVA_APPLICATION_CONTEXT>"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "rows"},
        {"role": "system", "content": "Compose from recorded evidence only."},
    ]
    sent = AssistantProviderClient._request_body(config, messages, None)["messages"]
    assert [message["role"] for message in sent] == ["system", "user", "assistant", "tool"]
    assert sent[0]["content"] == (
        "platform prompt\n\n<NOVA_APPLICATION_CONTEXT>editor</NOVA_APPLICATION_CONTEXT>"
        "\n\nCompose from recorded evidence only."
    )
    assert sent[2]["tool_calls"] == [{"id": "c1"}]
    assert messages[1]["role"] == "system"  # the caller's list is unchanged


def test_the_cache_key_follows_the_platform_prompt_not_the_turn_context():
    capabilities = ProviderCapabilities.from_mapping({"supports_prompt_cache_key": True})
    config = ProviderConfig("p", "m", "https://example.test/v1", "unused", capabilities)
    keys = {
        AssistantProviderClient._request_body(
            config, [*MESSAGES[:1], {"role": "system", "content": context}, MESSAGES[1]], None,
        )["prompt_cache_key"]
        for context in ("page A", "page B")
    }
    assert len(keys) == 1
