from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.unit.test_semantic_guidance_fallback import QUESTION, USER, setup_tool


async def test_natural_guidance_runs_through_consent_and_returns_grounded_result(monkeypatch):
    tool, planner, execute = setup_tool(monkeypatch)
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(
        script=[
            tool_call_frame("semantic-1", name="semantic_query", arguments={"question": QUESTION}),
            text_frame("Jakarta revenue for 2025 is 100."),
        ]
    )
    context = LoopContext(
        user_name="alice",
        user=USER.copy(),
    )
    prompts = []

    async def consent(invocation, classification):
        prompts.append((invocation.tool_name, classification))
        return True

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="semantic-guidance", user_name="alice", title="Eval"),
            user_content=QUESTION,
            context=context,
            resolve_consent=consent,
        )
    ]
    assert prompts == [("semantic_query", "read_only")]
    planner.complete.assert_awaited_once()
    execute.assert_awaited_once()
    sql = execute.call_args.kwargs["sql"]
    assert ">= '2025-01-01'" in sql and "< '2026-01-01'" in sql
    assert "private-test-value" not in "".join(frames)
    assert any('"finish_reason":"stop"' in frame.replace(" ", "") for frame in frames)
    assert any("Jakarta revenue for 2025 is 100." in frame for frame in frames)
