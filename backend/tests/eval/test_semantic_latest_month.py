from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.unit.test_semantic_guidance_fallback import USER, setup_tool


async def test_latest_month_trajectory_uses_consent_and_completes(monkeypatch):
    question = "What is the latest month with data for revenue?"
    tool, planner_provider, execute = setup_tool(monkeypatch)
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(
        script=[
            tool_call_frame(
                "latest-month", name="semantic_query", arguments={"question": question}
            ),
            text_frame("The latest month with revenue data is May 2025."),
        ]
    )
    consented = []

    async def consent(invocation, classification):
        consented.append((invocation.tool_name, classification))
        return True

    context = LoopContext(user_name="alice", user=USER.copy())
    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="latest-month", user_name="alice", title="Eval"),
            user_content=question,
            context=context,
            resolve_consent=consent,
        )
    ]
    assert consented == [("semantic_query", "read_only")], frames
    assert context.route["required_capabilities"] == ("semantic_query",)
    planner_provider.complete.assert_not_awaited()
    execute.assert_awaited_once()
    assert "ORDER BY `order_date` DESC" in execute.call_args.kwargs["sql"]
    assert "private-test-value" not in "".join(frames)
    assert any('"finish_reason":"stop"' in frame.replace(" ", "") for frame in frames)
