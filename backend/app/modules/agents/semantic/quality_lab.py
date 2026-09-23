"""Replay reviewed questions against the current semantic model definition."""

from __future__ import annotations

from typing import Any

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.verification import verify_sql_compatibility


def evaluate_verified_queries(
    definition: dict[str, Any], queries: list[dict[str, Any]], *, limit: int = 100
) -> dict[str, Any]:
    model = SemanticModelIR.from_ossie(definition)
    cases: list[dict[str, str]] = []
    for query in queries[:limit]:
        status = "matched"
        try:
            plan = SemanticPlan.from_dict(query["semantic_plan"])
            compiled = SemanticCompiler().compile(model, plan)
            verify_sql_compatibility(compiled.sql, query["verified_sql"])
        except (KeyError, TypeError, ValueError):
            status = "changed"
        cases.append({
            "verified_query_id": str(query["verified_query_id"]),
            "question": str(query["question"]),
            "status": status,
        })
    matched = sum(case["status"] == "matched" for case in cases)
    return {
        "model_fingerprint": model.fingerprint,
        "total": len(cases),
        "matched": matched,
        "changed": len(cases) - matched,
        "cases": cases,
    }
