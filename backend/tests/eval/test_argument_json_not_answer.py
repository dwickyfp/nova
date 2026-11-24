"""Tool arguments a model writes as a bare JSON object never become answer text.

Benchmark ``date_relative-002``: with tools withheld, the model wrote the
arguments of another semantic query (``{"question": "..."}``) and then the
answer; the JSON object was shown as the start of the answer.
"""

from app.modules.assistant.service import AssistantLoop, LoopContext, _without_argument_json
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import RecordingTool, ScriptedProvider, text_frame

_SCHEMAS = [{
    "type": "function",
    "function": {
        "name": "semantic_query",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}, "view": {"type": "string"}},
            "required": ["question"],
        },
    },
}]


def test_leading_argument_object_is_removed():
    text = (
        '{"question":"Berapa total revenue 2025-06-29 sampai 2025-07-05?"}'
        "Total pendapatan untuk pekan terakhir belum terverifikasi."
    )
    cleaned, removed = _without_argument_json(text, _SCHEMAS)
    assert removed
    assert cleaned == "Total pendapatan untuk pekan terakhir belum terverifikasi."


def test_an_empty_call_before_the_arguments_is_removed_too():
    # Re-bench date_relative-002: the answer began '{}{"question":"Gunakan Semantic View ..."}'.
    text = (
        '{}{"question":"Gunakan Semantic View demo_sales: pendapatan 29 Juni sampai '
        '5 Juli 2025"}Pendapatan 7 hari terakhir dalam data adalah Rp29.298.000.'
    )
    cleaned, removed = _without_argument_json(text, _SCHEMAS)
    assert removed
    assert cleaned == "Pendapatan 7 hari terakhir dalam data adalah Rp29.298.000."
    assert _without_argument_json("{}\nNo figures yet.", _SCHEMAS) == ("No figures yet.", True)


def test_other_json_and_inline_braces_stay():
    for text in (
        'Use {"question": "x"} inline.',  # not at a line start
        '{"metric": "revenue"}\nAn example object.',  # keys of no tool
        "```json\n{\"question\": \"x\"}\n```",  # fenced code the user asked for
        "Revenue {grew} in July.",
        "Pass {} when there are no options.",  # an empty object inside a sentence
        'Revenue {} {"question": "x"} inline.',  # not at a line start
    ):
        assert _without_argument_json(text, _SCHEMAS) == (text, False)


async def test_answer_never_starts_with_tool_arguments():
    registry = ToolRegistry()
    registry.register(RecordingTool())
    provider = ScriptedProvider(script=[
        text_frame(
            '{"sql": "SELECT SUM(total_amount) FROM orders"}\nThe total is not verified yet.'
        ),
    ])
    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="argument-json", user_name="alice", title="Eval"),
            user_content="Total revenue?",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda *_: None,
        )
    ]
    output = "".join(frames)
    assert "The total is not verified yet." in output
    assert "SELECT SUM" not in output
    answer = "".join(frame for frame in frames if frame.startswith("event: text_delta"))
    assert "The total is not verified yet." in answer
    assert '\\"sql\\"' not in answer


async def test_arguments_alone_are_repaired_not_shown():
    registry = ToolRegistry()
    registry.register(RecordingTool())
    provider = ScriptedProvider(script=[
        text_frame('{"sql": "SELECT 1"}'),
        text_frame("Nothing to report."),
    ])
    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="argument-json-2", user_name="alice", title="Eval"),
            user_content="Anything?",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda *_: None,
        )
    ]
    output = "".join(frames)
    assert "Nothing to report." in output
    assert "SELECT 1" not in output
