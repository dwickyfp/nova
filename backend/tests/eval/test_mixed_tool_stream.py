from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.streaming import StreamAccumulator
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import RecordingTool, ScriptedProvider, text_frame


async def test_mixed_stream_executes_required_query_before_answering():
    accumulator = StreamAccumulator()
    accumulator.feed(
        {
            "choices": [
                {
                    "delta": {
                        "content": "Checking databases.",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "mixed-query",
                                "function": {
                                    "name": "query_execute",
                                    "arguments": '{"sql":"SHOW DATABASES"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )
    provider = ScriptedProvider(script=[accumulator.message(), text_frame("One database.")])
    registry = ToolRegistry()
    tool = RecordingTool()
    registry.register(tool)
    approvals = []

    async def consent(invocation, classification):
        approvals.append((invocation.tool_name, classification))
        return True

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="mixed-stream", user_name="alice", title="Eval"),
            user_content="SHOW DATABASES",
            context=LoopContext(user_name="alice"),
            resolve_consent=consent,
        )
    ]
    assert tool.runs == 1
    assert approvals == [("query_execute", "read_only")]
    output = "".join(frames)
    assert '"finish_reason":"stop"' in output.replace(" ", "")
    assert "required_capability_incomplete" not in output
    assert "One database." in output
