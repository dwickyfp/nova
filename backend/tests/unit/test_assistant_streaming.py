"""Unit tests for the OpenAI-compatible streaming accumulator.

Covers the two things that make streaming work and are easy to get wrong:
text fragments must be forwarded incrementally, and tool calls — whose id only
arrives on the first fragment and whose arguments are split across fragments —
must be reassembled into one complete call.
"""

from __future__ import annotations

import json

from app.modules.assistant.streaming import (
    MAX_REASONING_CHARS,
    StreamAccumulator,
    StreamDelta,
    parse_sse_data_line,
    reasoning_text,
)


def _chunk(delta: dict, finish_reason: str | None = None) -> dict:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}


def test_text_deltas_accumulate_and_are_returned_incrementally():
    acc = StreamAccumulator()

    assert acc.feed(_chunk({"content": "Hello"})) == StreamDelta(text="Hello")
    assert acc.feed(_chunk({"content": " world"})).text == " world"
    assert acc.feed(_chunk({}, finish_reason="stop")) == StreamDelta()

    assert acc.message()["content"] == "Hello world"


def test_ignore_non_text_and_malformed_chunks():
    acc = StreamAccumulator()

    assert acc.feed({"choices": []}) == StreamDelta()
    assert acc.feed({"choices": [{"delta": {}}]}) == StreamDelta()
    assert acc.feed({"no_choices": True}) == StreamDelta()


def test_tool_call_is_assembled_across_fragments():
    acc = StreamAccumulator()

    # First fragment carries id + name + the start of the arguments.
    acc.feed(
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "query_execute", "arguments": '{"sql":'},
                    }
                ]
            }
        )
    )
    # Subsequent fragments carry only argument pieces (no id, no name).
    acc.feed(_chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "SELECT 1"}'}}]}))
    acc.feed(_chunk({}, finish_reason="tool_calls"))

    message = acc.message()
    assert message["content"] == ""
    assert message["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "query_execute", "arguments": '{"sql": "SELECT 1"}'},
        }
    ]


def test_multiple_tool_calls_are_kept_by_index():
    acc = StreamAccumulator()
    acc.feed(
        _chunk(
            {
                "tool_calls": [
                    {"index": 0, "id": "a", "function": {"name": "one", "arguments": "{}"}},
                    {"index": 1, "id": "b", "function": {"name": "two", "arguments": "{}"}},
                ]
            }
        )
    )

    calls = acc.message()["tool_calls"]
    assert [c["id"] for c in calls] == ["a", "b"]


def test_text_and_tool_call_in_same_delta_are_both_preserved():
    acc = StreamAccumulator()
    text = acc.feed(
        _chunk(
            {
                "content": "Checking the data.",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "mixed-1",
                        "function": {"name": "query_execute", "arguments": '{"sql":'},
                    }
                ],
            }
        )
    )
    acc.feed(
        _chunk(
            {
                "tool_calls": [{"index": 0, "function": {"arguments": '"SELECT 1"}'}}],
            }
        )
    )
    assert text.text == "Checking the data."
    assert acc.message()["content"] == text.text
    assert acc.message()["tool_calls"] == [
        {
            "id": "mixed-1",
            "type": "function",
            "function": {"name": "query_execute", "arguments": '{"sql":"SELECT 1"}'},
        }
    ]


def test_parse_sse_data_line():
    assert parse_sse_data_line('data: {"a": 1}') == {"a": 1}
    assert parse_sse_data_line("data: [DONE]") is None
    assert parse_sse_data_line("data:") is None
    assert parse_sse_data_line("event: ping") is None
    assert parse_sse_data_line("data: not-json") is None


# Reasoning summaries, in the shapes providers were recorded sending.


def test_single_reasoning_content_chunk_before_the_text():
    # 9router ``cx/gpt-6.2-sol(xhigh)`` (recorded 2026-11-23): the whole summary
    # arrives as one ``reasoning_content`` delta, then the content deltas.
    acc = StreamAccumulator()
    first = acc.feed(_chunk({"reasoning_content": "**Calculating time difference**"}))
    assert first == StreamDelta(reasoning="**Calculating time difference**")
    assert acc.feed(_chunk({"content": "3 h 25 min"})) == StreamDelta(text="3 h 25 min")
    message = acc.message()
    assert message == {"role": "assistant", "content": "3 h 25 min"}


def test_many_small_reasoning_content_fragments():
    # DeepSeek / vLLM / Qwen: token-sized ``reasoning_content`` deltas.
    acc = StreamAccumulator()
    pieces = ["The user ", "asks for ", "the top ", "customers."]
    out = [acc.feed(_chunk({"reasoning_content": piece, "content": None})) for piece in pieces]
    assert "".join(delta.reasoning for delta in out) == "The user asks for the top customers."
    assert all(delta.text == "" for delta in out)


def test_openrouter_reasoning_and_details_are_not_doubled():
    # OpenRouter sends the same text as ``reasoning`` and ``reasoning_details``.
    delta = {
        "reasoning": "Weighing the views.",
        "reasoning_details": [{"type": "reasoning.text", "text": "Weighing the views."}],
    }
    assert reasoning_text(delta) == "Weighing the views."
    assert reasoning_text({"reasoning_details": [
        {"type": "reasoning.summary", "summary": "Summary one."},
        {"type": "reasoning.encrypted", "data": "opaque"},
    ]}) == "Summary one."


def test_litellm_anthropic_thinking_reads_reasoning_content_once():
    # LiteLLM's Anthropic adapter: ``reasoning_content`` plus ``thinking_blocks``.
    delta = {
        "reasoning_content": "Checking the date range.",
        "thinking_blocks": [{"type": "thinking", "thinking": "Checking the date range.",
                             "signature": "sig"}],
    }
    assert reasoning_text(delta) == "Checking the date range."
    blocks_only = {"thinking_blocks": [{"type": "thinking", "thinking": "Only blocks."}]}
    assert reasoning_text(blocks_only) == "Only blocks."


def test_provider_without_reasoning_yields_none():
    acc = StreamAccumulator()
    out = [acc.feed(_chunk({"content": part})) for part in ("The ", "answer.")]
    assert all(delta.reasoning == "" for delta in out)
    assert reasoning_text({"reasoning": None, "reasoning_content": ""}) == ""


def test_reasoning_is_bounded_per_call_and_never_in_the_message():
    acc = StreamAccumulator()
    sent = acc.feed(_chunk({"reasoning_content": "x" * (MAX_REASONING_CHARS + 10)}))
    assert len(sent.reasoning) == MAX_REASONING_CHARS
    assert acc.reasoning_truncated
    assert acc.feed(_chunk({"reasoning_content": "more"})).reasoning == ""
    assert "reasoning" not in json.dumps(acc.message())
