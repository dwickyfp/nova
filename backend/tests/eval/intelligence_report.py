"""Deterministic agent and semantic intelligence scorecard.

Run with::

    cd backend && uv run python -m tests.eval.intelligence_report

This corpus is provider-free, so it is the stable portability gate. The existing
``tests.eval.report`` command exercises scripted provider trajectories.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlanner
from app.modules.agents.semantic.runtime import (
    SemanticModelCandidate,
    SemanticModelRouter,
)
from app.modules.assistant.intelligence import TurnIntent, TurnRouter


@dataclass(frozen=True)
class RouteCase:
    question: str
    intent: TurnIntent
    tools: tuple[str, ...]


ROUTE_CASES = (
    RouteCase("Revenue this month", TurnIntent.SEMANTIC_ANALYTICS, ("semantic_query",)),
    RouteCase("Omzet Jakarta bulan lalu", TurnIntent.SEMANTIC_ANALYTICS, ("semantic_query",)),
    RouteCase("Active customers by city", TurnIntent.SEMANTIC_ANALYTICS, ("semantic_query",)),
    RouteCase("Gross margin by quarter", TurnIntent.SEMANTIC_ANALYTICS, ("semantic_query",)),
    RouteCase("SELECT * FROM sales.orders", TurnIntent.RAW_SQL_QUERY, ("query_execute",)),
    RouteCase("WITH x AS (SELECT 1) SELECT * FROM x", TurnIntent.RAW_SQL_QUERY, ("query_execute",)),
    RouteCase("SHOW TABLES", TurnIntent.SCHEMA_INSPECTION, ("query_execute",)),
    RouteCase("EXPLAIN SELECT * FROM orders", TurnIntent.RAW_SQL_QUERY, ("query_execute",)),
    RouteCase("Describe table orders", TurnIntent.SCHEMA_INSPECTION, ("query_execute",)),
    RouteCase("List columns in customers", TurnIntent.SCHEMA_INSPECTION, ("query_execute",)),
    RouteCase("Struktur tabel orders", TurnIntent.SCHEMA_INSPECTION, ("query_execute",)),
    RouteCase(
        "Forecast revenue next 30 days",
        TurnIntent.MACHINE_LEARNING,
        ("semantic_query", "ml_execute"),
    ),
    RouteCase("Ramalkan penjualan", TurnIntent.MACHINE_LEARNING, ("semantic_query", "ml_execute")),
    RouteCase("Cluster customers", TurnIntent.MACHINE_LEARNING, ("semantic_query", "ml_execute")),
    RouteCase("Segment customers", TurnIntent.MACHINE_LEARNING, ("semantic_query", "ml_execute")),
    RouteCase("Find unusual orders", TurnIntent.MACHINE_LEARNING, ("semantic_query", "ml_execute")),
    RouteCase("Detect transaction anomalies", TurnIntent.MACHINE_LEARNING, ("ml_execute",)),
    RouteCase(
        "Predict customer churn", TurnIntent.MACHINE_LEARNING, ("semantic_query", "ml_execute")
    ),
    RouteCase("Classify support tickets", TurnIntent.MACHINE_LEARNING, ("ml_execute",)),
    RouteCase(
        "Forecast revenue and chart it",
        TurnIntent.COMPOUND_ANALYTICS,
        ("semantic_query", "ml_execute", "data_to_chart"),
    ),
    RouteCase("Plot sales by month", TurnIntent.CHART, ("semantic_query", "data_to_chart")),
    RouteCase("Visualize this query", TurnIntent.CHART, ("query_execute", "data_to_chart")),
    RouteCase("Search product Aqua", TurnIntent.SEMANTIC_SEARCH, ("semantic_search",)),
    RouteCase("Cari customer named Sari", TurnIntent.SEMANTIC_SEARCH, ("semantic_search",)),
    RouteCase("Write SQL for a new role", TurnIntent.SQL_AUTHORING, ()),
    RouteCase("What tools are available?", TurnIntent.CAPABILITY_HELP, ()),
    RouteCase("Hello", TurnIntent.DIRECT_ANSWER, ()),
    RouteCase("Thanks", TurnIntent.DIRECT_ANSWER, ()),
)


def _model(name: str, description: str, metric: str, synonym: str) -> SemanticModelIR:
    return SemanticModelIR.from_ossie(
        {
            "name": name,
            "description": description,
            "version": "1",
            "datasets": [
                {
                    "name": name,
                    "source": f"analytics.{name}",
                    "grain": {"keys": ["id"]},
                    "fields": [
                        {"name": "id", "expression": "id", "kind": "dimension"},
                        {"name": "region", "expression": "region", "kind": "dimension"},
                    ],
                }
            ],
            "metrics": [
                {
                    "name": metric,
                    "base_dataset": name,
                    "expression": "COUNT(*)",
                    "description": description,
                    "synonyms": [synonym],
                }
            ],
        }
    )


SEMANTIC_MODELS = [
    SemanticModelCandidate(
        "sales", _model("sales", "Revenue and orders", "total_revenue", "revenue")
    ),
    SemanticModelCandidate(
        "finance", _model("finance", "Gross margin and balance", "gross_margin", "margin")
    ),
    SemanticModelCandidate(
        "marketing", _model("marketing", "Campaign acquisition", "campaign_cac", "cac")
    ),
]

SEMANTIC_CASES = (
    ("Revenue by region", "sales", "total_revenue", "region"),
    ("Sales revenue", "sales", "total_revenue", None),
    ("Gross margin by region", "finance", "gross_margin", "region"),
    ("Finance margin", "finance", "gross_margin", None),
    ("Campaign CAC by region", "marketing", "campaign_cac", "region"),
    ("Marketing acquisition CAC", "marketing", "campaign_cac", None),
)


def evaluate() -> dict[str, object]:
    router = TurnRouter()
    route_correct = 0
    tool_correct = 0
    route_details = []
    for case in ROUTE_CASES:
        result = router.route(case.question)
        route_ok = result.intent == case.intent
        tool_ok = result.required_capabilities == case.tools
        route_correct += int(route_ok)
        tool_correct += int(tool_ok)
        route_details.append(
            {
                "question": case.question,
                "route_ok": route_ok,
                "tool_ok": tool_ok,
                "actual_intent": result.intent.value,
                "actual_tools": result.required_capabilities,
            }
        )

    semantic_router = SemanticModelRouter()
    planner = SemanticPlanner()
    model_correct = metric_correct = dimension_correct = 0
    semantic_details = []
    by_id = {candidate.model_id: candidate.model for candidate in SEMANTIC_MODELS}
    for question, expected_model, expected_metric, expected_dimension in SEMANTIC_CASES:
        selection = semantic_router.route(question, SEMANTIC_MODELS)
        model_ok = selection.model_id == expected_model
        plan = planner.plan(by_id[expected_model], question).plan
        metric_ok = bool(plan and plan.metrics == (expected_metric,))
        dimension_ok = bool(
            plan
            and (
                (expected_dimension is None and not plan.dimensions)
                or plan.dimensions == (expected_dimension,)
            )
        )
        model_correct += int(model_ok)
        metric_correct += int(metric_ok)
        dimension_correct += int(dimension_ok)
        semantic_details.append(
            {
                "question": question,
                "model_ok": model_ok,
                "metric_ok": metric_ok,
                "dimension_ok": dimension_ok,
            }
        )

    return {
        "agent": {
            "cases": len(ROUTE_CASES),
            "route_accuracy": route_correct / len(ROUTE_CASES),
            "tool_accuracy": tool_correct / len(ROUTE_CASES),
            "details": route_details,
        },
        "semantic": {
            "cases": len(SEMANTIC_CASES),
            "model_routing_accuracy": model_correct / len(SEMANTIC_CASES),
            "metric_accuracy": metric_correct / len(SEMANTIC_CASES),
            "dimension_accuracy": dimension_correct / len(SEMANTIC_CASES),
            "details": semantic_details,
        },
    }


def main() -> int:
    report = evaluate()
    print(json.dumps(report, indent=2, default=str))
    scores = [
        report["agent"]["route_accuracy"],
        report["agent"]["tool_accuracy"],
        report["semantic"]["model_routing_accuracy"],
        report["semantic"]["metric_accuracy"],
        report["semantic"]["dimension_accuracy"],
    ]
    return 0 if all(score >= 0.95 for score in scores) else 1


if __name__ == "__main__":
    sys.exit(main())
