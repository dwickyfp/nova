"""Deterministic semantic intelligence scorecard.

Run with::

    cd backend && uv run python -m tests.eval.intelligence_report

This corpus is provider-free, so it is the stable portability gate. The existing
``tests.eval.report`` command exercises scripted provider trajectories.
"""

from __future__ import annotations

import json
import sys

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlanner
from app.modules.agents.semantic.runtime import (
    SemanticModelCandidate,
    SemanticModelRouter,
    scope_semantic_model,
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
        "marketing", _model("marketing", "Campaign acquisition", "campaign_cac", "acquisition CAC")
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
    semantic_router = SemanticModelRouter()
    planner = SemanticPlanner()
    model_correct = metric_correct = dimension_correct = 0
    semantic_details = []
    by_id = {candidate.model_id: candidate.model for candidate in SEMANTIC_MODELS}
    for question, expected_model, expected_metric, expected_dimension in SEMANTIC_CASES:
        selection = semantic_router.route(question, SEMANTIC_MODELS)
        model_ok = selection.model_id == expected_model
        planned = planner.plan(by_id[expected_model], question)
        plan = planned.plan
        metric_ok = bool(
            plan and not planned.confidence.unresolved_count and plan.metrics == (expected_metric,)
        )
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

    unresolved = planner.plan(by_id["sales"], "Revenue Enterprise")
    empty_scope = scope_semantic_model(by_id["sales"], set())
    adversarial = {
        "unknown_constraint_is_unresolved": unresolved.confidence.unresolved_count > 0,
        "unknown_constraint_not_high_confidence": unresolved.confidence.level != "high",
        "explicit_empty_authorization_denies_all": not empty_scope.datasets
        and not empty_scope.metrics,
    }
    return {
        "adversarial": adversarial,
        "scope": "Deterministic offline cases; not a production-quality estimate.",
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
        report["semantic"]["model_routing_accuracy"],
        report["semantic"]["metric_accuracy"],
        report["semantic"]["dimension_accuracy"],
    ]
    return (
        0 if all(score >= 0.95 for score in scores) and all(report["adversarial"].values()) else 1
    )


if __name__ == "__main__":
    sys.exit(main())
