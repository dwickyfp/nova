"""Unit tests for the OpenAI-compatible streaming accumulator.

Covers the two things that make streaming work and are easy to get wrong:
text fragments must be forwarded incrementally, and tool calls — whose id only
arrives on the first fragment and whose arguments are split across fragments —
must be reassembled into one complete call.
"""

from __future__ import annotations

from app.modules.assistant.streaming import StreamAccumulator, parse_sse_data_line


def _chunk(delta: dict, finish_reason: str | None = None) -> dict:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}


def test_text_deltas_accumulate_and_are_returned_incrementally():
    acc = StreamAccumulator()

    assert acc.feed(_chunk({"content": "Hello"})) == "Hello"
    assert acc.feed(_chunk({"content": " world"})) == " world"
    assert acc.feed(_chunk({}, finish_reason="stop")) == ""

    assert acc.message()["content"] == "Hello world"


def test_ignore_non_text_and_malformed_chunks():
    acc = StreamAccumulator()

    assert acc.feed({"choices": []}) == ""
    assert acc.feed({"choices": [{"delta": {}}]}) == ""
    assert acc.feed({"no_choices": True}) == ""


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
    acc.feed(
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "SELECT 1"}'}}]})
    )
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


def test_parse_sse_data_line():
    assert parse_sse_data_line("data: {\"a\": 1}") == {"a": 1}
    assert parse_sse_data_line("data: [DONE]") is None
    assert parse_sse_data_line("data:") is None
    assert parse_sse_data_line("event: ping") is None
    assert parse_sse_data_line("data: not-json") is None
