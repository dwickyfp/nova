"""An empty authorized result must not turn into an invented city amount."""

import json

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import RecordingTool, ScriptedProvider, text_frame, tool_call_frame


class EmptyCityTool(RecordingTool):
    name = "semantic_query"

    async def run(self, invocation, context):
        self.runs += 1
        return ToolOutcome(
            ok=True,
            summary="0 rows",
            table={"title": "City total", "columns": ["city", "total_amount"], "rows": []},
            data={
                "semantic_plan": {"metrics": ["total_amount"], "dimensions": ["city"]},
                "sql": (
                    "SELECT city, SUM(amount) FROM city_sales "
                    "WHERE city='Bandung' GROUP BY city"
                ),
            },
        )


async def test_city_rbac_empty_result_overrides_unsupported_number() -> None:
    tool = EmptyCityTool(name="semantic_query")
    tool.parameters = {
        "type": "object", "properties": {"question": {"type": "string"}},
        "required": ["question"],
    }
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider([
        tool_call_frame(
            "city-1", name="semantic_query",
            arguments={"question": "Berapa penjualan di Bandung?"},
        ),
        text_frame("Bandung has sales of 600."),
    ])

    async def allow(_invocation, _classification):
        return True

    frames = [
        frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread("city-rbac", "rbac_jakarta", "Eval"),
            user_content="Berapa penjualan di Bandung?",
            context=LoopContext(user_name="rbac_jakarta"),
            resolve_consent=allow,
        )
    ]
    assert tool.runs == 1
    assert any(frame.startswith("event: table\n") and '"rows":[]' in frame for frame in frames)
    answer = "".join(
        json.loads(frame.split("data: ", 1)[1])["text"]
        for frame in frames if frame.startswith("event: text_delta\n")
    )
    assert answer == "The authorized query returned no rows for this request."
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")
