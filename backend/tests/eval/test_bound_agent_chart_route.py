from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import EvalTool


async def test_bound_model_chart_uses_semantic_query_before_chart(monkeypatch):
    async def authorized(_context):
        return None

    monkeypatch.setattr("app.modules.agents.semantic.access.load_authorized_models", authorized)
    semantic = EvalTool(
        "semantic_query",
        parameters={
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
        data={"semantic_plan": {"metrics": ["recognized_revenue"]}, "sql": "SELECT 1"},
        table={"columns": ["month", "revenue"], "rows": [["2026-08", 1]]},
    )
    chart = EvalTool(
        "data_to_chart",
        parameters={
            "type": "object",
            "properties": {"intent": {"type": "string"}},
            "required": ["intent"],
        },
        chart={"chart_spec": '{"mark":"line"}'},
    )
    registry = ToolRegistry()
    registry.register(semantic)
    registry.register(chart)
    provider = ScriptedProvider(
        script=[
            tool_call_frame(
                "semantic-1", name="semantic_query", arguments={"question": "monthly channel trend"}
            ),
            tool_call_frame(
                "chart-1", name="data_to_chart", arguments={"intent": "monthly channel trend"}
            ),
            text_frame("Here is the trend."),
        ]
    )

    async def consent(_invocation, _classification):
        return True

    context = LoopContext(user_name="alice", semantic_model_ids=["sales-model"])
    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="chart", user_name="alice", title="Eval"),
            user_content="Buat chart tren bulanan Mobile App versus Store sejak 2023.",
            context=context,
            resolve_consent=consent,
        )
    ]
    assert context.route["required_capabilities"] == ("semantic_query", "data_to_chart")
    assert [step["name"] for step in context.steps if step["kind"] == "tool"] == [
        "semantic_query",
        "data_to_chart",
    ]
    assert len(semantic.runs) == len(chart.runs) == 1
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")
