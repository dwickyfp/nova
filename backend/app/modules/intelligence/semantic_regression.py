"""Bounded, caller-governed comparisons for Semantic View versions."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.verification import verify_sql_compatibility

MAX_VERIFIED_QUERIES = 20
MAX_RESULT_ROWS = 100
QueryRunner = Callable[[str], Awaitable[tuple[list[str], list[list[Any]]]]]


def _rows(columns: list[str], rows: list[list[Any]], *, ordered: bool) -> list[str]:
    """Ignore result order unless the plan itself orders rows."""
    encoded = [
        json.dumps([columns, row], default=str, separators=(",", ":"))
        for row in rows
    ]
    return encoded if ordered else sorted(encoded)


async def compare_semantic_versions(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
    verified_queries: list[dict[str, Any]],
    run_query: QueryRunner,
) -> dict[str, Any]:
    """Replay active verified plans against both versions without storing row data."""
    if len(verified_queries) > MAX_VERIFIED_QUERIES:
        raise ValueError(f"At most {MAX_VERIFIED_QUERIES} verified queries are supported")
    old_model = SemanticModelIR.from_ossie(baseline)
    new_model = SemanticModelIR.from_ossie(candidate)
    compiler = SemanticCompiler()
    cases: list[dict[str, Any]] = []
    for item in verified_queries:
        case: dict[str, Any] = {
            "verified_query_id": str(item.get("verified_query_id") or ""),
            "question": str(item.get("question") or ""),
        }
        try:
            plan = SemanticPlan.from_dict(item["semantic_plan"])
            old_sql = compiler.compile(old_model, plan).sql
            new_sql = compiler.compile(new_model, plan).sql
            try:
                verify_sql_compatibility(new_sql, item["verified_sql"])
                sql_changed = False
            except (KeyError, TypeError, ValueError):
                sql_changed = True
            old_columns, old_rows = await run_query(old_sql)
            new_columns, new_rows = await run_query(new_sql)
            truncated = (
                (len(old_rows) >= MAX_RESULT_ROWS or len(new_rows) >= MAX_RESULT_ROWS)
                and (plan.limit is None or plan.limit > MAX_RESULT_ROWS)
            )
            case.update(
                status=(
                    "result_changed" if _rows(old_columns, old_rows, ordered=bool(plan.order_by))
                    != _rows(new_columns, new_rows, ordered=bool(plan.order_by))
                    else "truncated" if truncated
                    else "sql_changed" if sql_changed else "matched"
                ),
                baseline_row_count=len(old_rows),
                candidate_row_count=len(new_rows),
            )
        except (KeyError, TypeError, ValueError):
            case["status"] = "compile_failed"
        except Exception:
            case["status"] = "execution_failed"
        cases.append(case)
    matched = sum(case["status"] == "matched" for case in cases)
    return {
        "model_fingerprint": new_model.fingerprint,
        "total": len(cases),
        "matched": matched,
        "changed": len(cases) - matched,
        "cases": cases,
    }
