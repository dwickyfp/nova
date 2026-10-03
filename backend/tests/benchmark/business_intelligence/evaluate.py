"""Score persisted evidence; unreviewed narrative dimensions stay unavailable."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.modules.agents.semantic.planning import SemanticPlan
from tests.benchmark.business_intelligence.client import Turn
from tests.benchmark.business_intelligence.gold.cases import Case
from tests.benchmark.business_intelligence.gold.queries import metric_case


def equal_rows(actual, expected) -> bool:
    if len(actual) != len(expected):
        return False
    for left, right in zip(actual, expected, strict=True):
        if len(left) != len(right):
            return False
        for value, reference in zip(left, right, strict=True):
            if value is None or reference is None:
                if value != reference:
                    return False
                continue
            if isinstance(reference, date | datetime):
                if str(value)[:10] != reference.isoformat()[:10]:
                    return False
                continue
            try:
                a, b = Decimal(str(value)), Decimal(str(reference))
            except InvalidOperation:
                if str(value) != str(reference):
                    return False
            else:
                if (
                    not a.is_finite()
                    or not b.is_finite()
                    or not math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-6)
                ):
                    return False
    return True


async def evaluate(case: Case, turn: Turn, execute_gold, *, repetition: int) -> dict:
    steps = turn.message.get("steps") or []
    row = {
        "case_id": case.id,
        "category": case.category,
        "repetition": repetition,
        "learning_sensitive": case.learning_sensitive,
        "thread_id": turn.thread_id,
        "message_id": turn.message_id,
        "latency_ms": turn.latency_ms,
        "tokens": turn.message.get("total_tokens"),
        "finish_reason": turn.finish_reason,
        "provider_calls": None,
        "traced_provider_steps": sum(step.get("kind") == "provider" for step in steps),
        "tool_calls": sum(step.get("kind") == "tool" for step in steps),
        "scores": {dimension: None for dimension in case.dimensions},
        "severe_unsupported_causal_claims": None,
        "authorization_bypasses": None,
        "silent_wrong": None,
        "review_rubric": list(case.rubric),
    }
    if case.expected_plan:
        traces = [
            step
            for step in steps
            if step.get("name") == "semantic_query" and step.get("status") == "done"
        ]
        expected = SemanticPlan.from_dict(case.expected_plan).as_dict()

        def normalized(value):
            return json.loads(json.dumps(value, sort_keys=True))

        matching_calls = {
            step["tool_call_id"]
            for step in traces
            if step.get("tool_call_id")
            and normalized((step.get("trace_detail") or {}).get("semantic_plan"))
            == normalized(expected)
        }
        plan_correct = bool(matching_calls)
        sql, params = metric_case(case.parameters)
        gold = await execute_gold(sql, params)
        tables = [step for step in steps if step.get("kind") == "table"]
        values_correct = any(
            table.get("tool_call_id") in matching_calls
            and equal_rows(table.get("rows", []), gold["rows"])
            for table in tables
        )
        correct = values_correct and plan_correct and turn.finish_reason == "stop"
        row["scores"].update(
            answer_accuracy=correct, rule_accuracy=plan_correct, plan_accuracy=plan_correct
        )
        row["silent_wrong"] = bool(tables and not values_correct)
    return row
