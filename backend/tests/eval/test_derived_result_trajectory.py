"""Derived figures: one calculation call, its own table, the data result kept.

Scripted from the benchmark what-if and multi-step turns (what_if-001,
multi_step-002, session-016.t2): the model filled ``factor`` and ``percent``
together and every scale call was refused, and a city's figure could not be
compared with an overall figure held in another result.
"""

from __future__ import annotations

import json

from app.modules.agents.tools.compute_metrics import compute_metrics_tool
from app.modules.assistant.tools import ToolOutcome
from tests.benchmark.harness import text_frame, tool_call_frame
from tests.eval.harness import EvalTool
from tests.eval.test_iterative_analysis_trajectory import SEMANTIC_PARAMETERS, run, semantic_call


def semantic_tool(*tables):
    return EvalTool(
        "semantic_query", classification="read_only", parameters=SEMANTIC_PARAMETERS,
        outcomes=[
            ToolOutcome(ok=True, summary="rows", table=table,
                        data={"semantic_plan": {"metrics": ["revenue"]},
                              "sql": "SELECT SUM(total_amount) AS revenue FROM orders"})
            for table in tables
        ],
    )


def tables_of(joined: str) -> list[dict]:
    return [json.loads(frame.split("data: ", 1)[1])
            for frame in joined.split("\n\n") if frame.startswith("event: table")]


def tool_failures(joined: str) -> int:
    return sum(1 for frame in joined.split("\n\n")
               if frame.startswith("event: tool_status") and '"failed"' in frame)


async def test_a_what_if_with_factor_and_percent_is_computed_once():
    """what_if-001: "if revenue rose 10%": factor 1.1 and percent 10 together."""
    total = {"title": "Revenue", "columns": ["revenue"], "rows": [["175184000.00"]]}
    finish, text, joined, _ = await run(
        [
            semantic_call("s1", "total revenue"),
            tool_call_frame("c1", name="compute_metrics", arguments={
                "operation": "scale", "value_column": "revenue", "factor": 1.1, "percent": 10,
            }),
            text_frame("Jika pendapatan naik 10%, totalnya menjadi 192.702.400."),
        ],
        [semantic_tool(total), compute_metrics_tool],
        question="Kalau revenue naik 10%, totalnya jadi berapa?",
    )
    assert finish == "stop"
    assert tool_failures(joined) == 0
    assert "192.702.400" in text
    # The calculation is its own table part, tied to its tool call.
    derived = [table for table in tables_of(joined) if table["title"] == "scale of revenue"]
    assert len(derived) == 1 and derived[0]["tool_call_id"]
    assert derived[0]["rows"][0][2] == "192702400.0000"


async def test_the_top_city_is_compared_with_the_overall_figure():
    """multi_step-002: AOV of the top city against the overall AOV."""
    by_city = {"title": "AOV per city", "columns": ["shipping_city", "average_order_value"],
               "rows": [["Semarang", "24999000.00"], ["Medan", "3999000.00"]]}
    overall = {"title": "AOV", "columns": ["average_order_value"], "rows": [["11678933.33"]]}
    finish, text, joined, _ = await run(
        [
            semantic_call("s1", "AOV per kota"),
            semantic_call("s2", "AOV keseluruhan"),
            tool_call_frame("c1", name="compute_metrics", arguments={
                "operation": "difference", "value_column": "average_order_value",
                "evidence_id": "evidence_1", "with_evidence_id": "evidence_2",
                "to_label": "Semarang",
            }),
            text_frame("Semarang punya AOV tertinggi, 24.999.000, atau 13.320.066,67 di atas "
                       "rata-rata keseluruhan."),
        ],
        [semantic_tool(by_city, overall), compute_metrics_tool],
        question="Kota dengan AOV tertinggi apa, dan berapa selisihnya dengan AOV rata-rata?",
    )
    assert finish == "stop"
    assert tool_failures(joined) == 0
    assert "13.320.066,67" in text
    derived = [table for table in tables_of(joined)
               if table["title"] == "difference of average_order_value"]
    assert derived and derived[0]["rows"] == [
        ["Semarang", "24999000.0000", "11678933.3300", "13320066.6700"],
    ]
