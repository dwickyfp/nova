"""Text-only providers retain the engine's consent and evidence boundaries."""

from dataclasses import replace

from app.modules.assistant.provider_capabilities import ProviderCapabilities
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import RecordingTool, ScriptedProvider, text_frame


class TextProvider(ScriptedProvider):
    async def resolve(self, **kwargs):
        config = await super().resolve(**kwargs)
        return replace(config, capabilities=ProviderCapabilities(supports_tools=False))

    async def stream(self, *, messages, tools=None, provider=None):
        assert tools is None
        assert all(message["role"] != "tool" for message in messages)
        assert all("tool_calls" not in message for message in messages)
        async for frame in super().stream(messages=messages, tools=tools, provider=provider):
            yield frame


async def test_text_only_action_executes_with_consent_and_composes_from_evidence():
    provider = TextProvider(
        script=[
            text_frame('{"action":"query_execute","arguments":{"sql":"SELECT 1"}}'),
            text_frame("The query returned one row."),
        ],
        turn_plan={
            "intent": "raw_sql_query",
            "tools": ["query_execute"],
            "required_tools": ["query_execute"],
            "ml_task": None,
        },
    )
    tool = RecordingTool()
    registry = ToolRegistry()
    registry.register(tool)
    context = LoopContext(user_name="eval")
    prompts = []

    async def consent(invocation, classification):
        prompts.append((invocation.tool_name, classification))
        return True

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="text-only", user_name="eval", title="Eval"),
            user_content="SELECT 1",
            context=context,
            resolve_consent=consent,
        )
    ]
    assert tool.runs == 1
    assert prompts == [("query_execute", "read_only")]
    assert context.harness_mode == "strict"
    assert any('"finish_reason":"stop"' in frame.replace(" ", "") for frame in frames)
    assert any("The query returned one row." in frame for frame in frames)


async def test_invalid_text_only_action_cannot_run_or_answer_with_data():
    provider = TextProvider(
        script=[
            text_frame('{"action":"query_execute","arguments":{"sql":17}}'),
            text_frame("Revenue rose significantly."),
        ],
        turn_plan={
            "intent": "raw_sql_query",
            "tools": ["query_execute"],
            "required_tools": ["query_execute"],
            "ml_task": None,
        },
    )
    tool = RecordingTool()
    registry = ToolRegistry()
    registry.register(tool)

    async def consent(invocation, classification):
        raise AssertionError("Invalid arguments must be rejected before consent")

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="invalid", user_name="eval", title="Eval"),
            user_content="SELECT 1",
            context=LoopContext(user_name="eval"),
            resolve_consent=consent,
        )
    ]
    assert tool.runs == 0
    assert not any(frame.startswith("event: text_delta") for frame in frames)
    assert any("required_capability_incomplete" in frame for frame in frames)
