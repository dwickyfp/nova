import asyncio

from app.modules.agents.tools.ml_execute import MLExecuteTool
from app.modules.assistant.intelligence import TurnIntent, TurnRoute, validate_json_arguments
from app.modules.assistant.service import AssistantLoop, LoopContext, _ml_parameters_for_task
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import EvalTool


def test_task_schemas_preserve_runtime_inputs():
    anomaly = _ml_parameters_for_task(MLExecuteTool.parameters, "anomaly_detection")
    forecast = _ml_parameters_for_task(MLExecuteTool.parameters, "forecast")

    assert validate_json_arguments(
        anomaly,
        {
            "task": "anomaly_detection",
            "input_sql": "SELECT id, value FROM metrics",
            "row_identifier": "id",
        },
    ) == []
    assert validate_json_arguments(
        forecast,
        {
            "task": "forecast",
            "input_sql": "SELECT day, total FROM daily_orders",
            "target": "total",
            "timestamp": "day",
            "horizon": 7,
            "frequency": "D",
        },
    ) == []


def test_semantic_clustering_accepts_row_identifier_after_skill_load(monkeypatch):
    asyncio.run(_run_semantic_clustering_scenario(monkeypatch))


async def _run_semantic_clustering_scenario(monkeypatch):
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
        data={"semantic_plan": {"metrics": ["order_count"]}, "sql": "SELECT 1"},
    )
    skill = EvalTool(
        "load_skill",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )
    ml = EvalTool(
        "ml_execute",
        parameters=MLExecuteTool.parameters,
        trace_detail={"run_id": "ml-run-1"},
    )
    registry = ToolRegistry()
    for tool in (semantic, skill, ml):
        registry.register(tool)

    arguments = {
        "task": "clustering",
        "input_sql": "SELECT region, monthly_order_count FROM monthly_orders",
        "feature_columns": ["monthly_order_count"],
        "row_identifier": "region",
        "persist": False,
    }
    provider = ScriptedProvider(
        script=[
            tool_call_frame(
                "semantic-1",
                name="semantic_query",
                arguments={"question": "monthly order count by region"},
            ),
            tool_call_frame("skill-1", name="load_skill", arguments={"name": "native-ml"}),
            tool_call_frame("ml-1", name="ml_execute", arguments=arguments),
            text_frame("Clustering completed."),
        ]
    )

    async def consent(_invocation, _classification):
        return True

    context = LoopContext(user_name="alice", semantic_model_ids=["sales-model"])
    loop = AssistantLoop(provider=provider, registry=registry)
    frames = [
        frame
        async for frame in loop.run(
            thread=AssistantThread(thread_id="ml", user_name="alice", title="ML"),
            user_content="Cluster monthly order count per region",
            context=context,
            resolve_consent=consent,
        )
    ]

    schema = loop._tool_schemas(
        ("ml_execute",),
        route=TurnRoute(TurnIntent.MACHINE_LEARNING, needs_ml=True, ml_task="clustering"),
    )[0]["function"]["parameters"]
    assert validate_json_arguments(schema, arguments) == []
    assert [step["name"] for step in context.steps if step["kind"] == "tool"] == [
        "semantic_query", "load_skill", "ml_execute"
    ]
    assert len(ml.runs) == 1
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")
