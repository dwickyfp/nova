"""Versioned deterministic assertions over recorded execution evidence."""

from __future__ import annotations

import math
import time
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.modules.intelligence.contracts import Contract

SCORERS = frozenset(
    {
        "semantic_selection",
        "tool_selection",
        "tool_arguments",
        "numeric_consistency",
        "evidence_coverage",
        "clarification_quality",
        "task_completeness",
        "policy_compliance",
        "action_verification",
        "latency",
        "efficiency",
    }
)
PERFORMANCE_SCORERS = frozenset({"latency", "efficiency"})
MEASURED_COUNTS = frozenset(
    {
        "tool_calls",
        "provider_calls",
        "total_tokens",
        "context_tokens",
        "participants",
        "metadata_reads",
    }
)
SCORER_VERSION = "1"
FAILURES = {
    "semantic_selection": "WRONG_SEMANTIC_METRIC",
    "tool_selection": "WRONG_TOOL",
    "tool_arguments": "BAD_TOOL_ARGUMENTS",
    "numeric_consistency": "UNSUPPORTED_CONCLUSION",
    "evidence_coverage": "MISSING_CONTEXT",
    "clarification_quality": "MISSING_CONTEXT",
    "task_completeness": "INCOMPLETE_ANSWER",
    "policy_compliance": "POLICY_FAILURE",
    "action_verification": "ACTION_VERIFICATION_FAILED",
    "latency": "TOO_SLOW",
    "efficiency": "INEFFICIENT_EXECUTION",
}


def _measurement(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


class Assertion(Contract):
    scorer: str
    required: bool = True
    expected: dict[str, Any] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def registered(self):
        if self.scorer not in SCORERS:
            raise ValueError("Unknown deterministic scorer")
        expected = self.expected
        if self.scorer == "latency":
            if set(expected) != {"max_ms"} or not _measurement(expected["max_ms"]):
                raise ValueError("Latency requires a finite nonnegative max_ms")
        elif self.scorer == "efficiency":
            if set(expected) - MEASURED_COUNTS or any(
                type(value) is not int or value < 0 for value in expected.values()
            ):
                raise ValueError("Efficiency requires nonnegative measured-count limits")
        elif self.scorer == "tool_selection":
            if set(expected) - {"required", "forbidden"} or any(
                not isinstance(value, list)
                or any(not isinstance(name, str) or not name for name in value)
                for value in expected.values()
            ):
                raise ValueError("Tool selection requires required/forbidden tool name lists")
            if set(expected.get("required", [])) & set(expected.get("forbidden", [])):
                raise ValueError("A tool cannot be both required and forbidden")
        elif self.scorer == "evidence_coverage" and (
            set(expected) - {"minimum", "minimum_health", "complete"}
            or (
                "minimum" in expected
                and (type(expected["minimum"]) is not int or expected["minimum"] < 0)
            )
            or (
                "minimum_health" in expected
                and (
                    not isinstance(expected["minimum_health"], str)
                    or expected["minimum_health"]
                    not in {
                        "insufficient",
                        "limited",
                        "moderate",
                        "strong",
                    }
                )
            )
            or ("complete" in expected and type(expected["complete"]) is not bool)
        ):
            raise ValueError("Invalid evidence coverage assertion")
        return self


class PromotionGates(Contract):
    other_cases: Literal["report_only", "mandatory", "all"] = "report_only"
    required_scorers: list[str] = Field(default_factory=list, max_length=9)
    performance: Literal["report_only", "required"] = "report_only"
    max_latency_ms: float | None = Field(default=None, ge=0)
    count_budgets: dict[str, Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=dict, max_length=6
    )

    @model_validator(mode="after")
    def budgets(self):
        if set(self.required_scorers) - (SCORERS - PERFORMANCE_SCORERS) or len(
            set(self.required_scorers)
        ) != len(self.required_scorers):
            raise ValueError("Additional gates require distinct behavioral scorers")
        if set(self.count_budgets) - MEASURED_COUNTS or any(
            type(value) is not int or value < 0 for value in self.count_budgets.values()
        ):
            raise ValueError("Performance budgets require nonnegative measured-count limits")
        return self

    def performance_assertions(self) -> list[Assertion]:
        assertions = []
        if self.max_latency_ms is not None:
            assertions.append(Assertion(scorer="latency", expected={"max_ms": self.max_latency_ms}))
        if self.count_budgets:
            assertions.append(Assertion(scorer="efficiency", expected=self.count_budgets))
        return assertions


class Score(Contract):
    scorer: str
    scorer_version: str = SCORER_VERSION
    status: Literal["pass", "fail", "unavailable"]
    required: bool
    detail: str
    failure_taxonomy: str | None = None
    scoring_duration_ms: float | None = Field(default=None, ge=0)


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _subset(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(_subset(value, item) for item in actual) for value in expected
        )
    return type(expected) is type(actual) and expected == actual


def _recorded(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _recorded(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list)
    return actual is not None


def score_assertion(assertion: Assertion, trace: dict) -> Score:
    from app.observability.metrics import studio_operation

    started = time.perf_counter()
    with studio_operation("quality", "score"):
        result = _score_assertion(assertion, trace)
    result.scoring_duration_ms = (time.perf_counter() - started) * 1000
    return result


def _score_assertion(assertion: Assertion, trace: dict) -> Score:
    name, expected = assertion.scorer, assertion.expected
    facts = trace.get("facts")
    actual = facts.get(name) if isinstance(facts, dict) else None
    passed: bool | None = None
    detail = "Required execution evidence is unavailable"
    if name in {
        "semantic_selection",
        "tool_arguments",
        "clarification_quality",
        "task_completeness",
        "policy_compliance",
        "action_verification",
    }:
        if _recorded(expected, actual):
            passed = _subset(expected, actual)
            detail = (
                "Recorded facts match the assertion"
                if passed
                else ("Recorded facts do not match the assertion")
            )
    elif name == "tool_selection":
        tools = trace.get("tool_names")
        if isinstance(tools, list) and all(isinstance(tool, str) for tool in tools):
            passed = set(expected.get("required", [])).issubset(tools) and not (
                set(expected.get("forbidden", [])) & set(tools)
            )
            detail = "Recorded required and forbidden tool selection checked"
    elif name == "numeric_consistency":
        if _recorded(expected, actual):
            claims = actual.get("claims") if isinstance(actual, dict) else None
            if "claims" in expected:
                if isinstance(claims, list) and all(
                    isinstance(item, dict)
                    and type(item.get("value")) in {int, float}
                    and math.isfinite(item["value"])
                    and "supported" in item
                    and "evidence_id" in item
                    for item in claims
                ):
                    passed = _subset(expected, actual) and all(
                        item["supported"] is True and bool(item["evidence_id"]) for item in claims
                    )
                    detail = "Numeric claims checked against recorded supporting evidence"
            else:
                passed = _subset(expected, actual)
                detail = "Recorded numeric verification checked"
    elif name == "evidence_coverage":
        evidence = trace.get("evidence")
        if isinstance(evidence, list) and all(
            isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
            for item in evidence
        ):
            passed = len({item["id"] for item in evidence}) >= expected.get("minimum", 1)
            health_rank = {"insufficient": 0, "limited": 1, "moderate": 2, "strong": 3}
            if passed and "minimum_health" in expected:
                labels = [
                    item.get("health", {}).get("label")
                    if isinstance(item.get("health"), dict)
                    else None
                    for item in evidence
                ]
                if any(not isinstance(label, str) or label not in health_rank for label in labels):
                    passed = None
                else:
                    passed = all(
                        health_rank[label] >= health_rank[expected["minimum_health"]]
                        for label in labels
                    )
            if passed and "complete" in expected:
                if any(type(item.get("complete")) is not bool for item in evidence):
                    passed = None
                else:
                    passed = all(item["complete"] is expected["complete"] for item in evidence)
            if passed is not None:
                detail = "Recorded distinct evidence coverage and health checked"
    elif name == "latency":
        elapsed = trace.get("duration_ms")
        if _measurement(elapsed):
            passed = elapsed <= expected["max_ms"]
            detail = "Measured execution latency checked against the configured limit"
    elif name == "efficiency":
        counts = trace.get("counts")
        if isinstance(counts, dict) and all(
            type(counts.get(key)) is int and counts[key] >= 0 for key in expected
        ):
            passed = all(counts[key] <= limit for key, limit in expected.items())
            detail = "Measured counts checked against configured limits"
    status = "unavailable" if passed is None else "pass" if passed else "fail"
    return Score(
        scorer=name,
        required=assertion.required,
        status=status,
        detail=detail,
        failure_taxonomy=None if status == "pass" else FAILURES[name],
    )


def aggregate_status(statuses: list[str]) -> str:
    if "fail" in statuses or "failed" in statuses:
        return "failed"
    if not statuses or any(status not in {"pass", "passed"} for status in statuses):
        return "unavailable"
    return "passed"


def score_case(assertions: list[Assertion], trace: dict) -> tuple[str, list[Score]]:
    scores = [score_assertion(assertion, trace) for assertion in assertions]
    return aggregate_status(
        [
            score.status
            for score in scores
            if score.required and score.scorer not in PERFORMANCE_SCORERS
        ]
    ), scores


def gate_results(cases: list[dict], results: list[dict], gates: PromotionGates) -> dict:
    by_id = {result["case_id"]: result for result in results}
    unique = len(by_id) == len(results) and len({case["id"] for case in cases}) == len(cases)

    def case_status(case):
        result = by_id.get(case["id"], {})
        if not unique or result.get("case_revision") != case["revision"]:
            return "unavailable"
        if "scores" in result:
            recorded = aggregate_status(
                [
                    score["status"]
                    for score in result["scores"]
                    if score.get("required") and score["scorer"] not in PERFORMANCE_SCORERS
                ]
            )
            if recorded != "passed":
                return recorded
        return result.get("status", "unavailable")

    critical = [case for case in cases if case.get("mandatory") and case.get("critical")]
    other = [
        case
        for case in cases
        if case not in critical
        and (
            gates.other_cases == "all" or gates.other_cases == "mandatory" and case.get("mandatory")
        )
    ]
    additional = [case_status(case) for case in other]
    for case in cases:
        result = by_id.get(case["id"], {})
        for scorer in gates.required_scorers:
            scores = [score for score in result.get("scores", []) if score["scorer"] == scorer]
            additional.append(
                aggregate_status([score["status"] for score in scores])
                if (unique and result.get("case_revision") == case["revision"])
                else "unavailable"
            )
    performance = []
    for case in cases:
        result = by_id.get(case["id"], {})
        performance.extend(
            score["status"]
            if unique and result.get("case_revision") == case["revision"]
            else "unavailable"
            for score in [*result.get("scores", []), *result.get("budget_scores", [])]
            if score["scorer"] in PERFORMANCE_SCORERS and score.get("required", True)
        )
    return {
        "mandatory_critical": {
            "status": aggregate_status([case_status(case) for case in critical]),
            "case_ids": [case["id"] for case in critical],
            "required": True,
        },
        "other_quality": {
            "status": aggregate_status(additional) if additional else "not_configured",
            "case_ids": [case["id"] for case in other],
            "required_scorers": gates.required_scorers,
            "mode": gates.other_cases,
            "required": bool(additional),
        },
        "performance": {
            "status": aggregate_status(performance),
            "mode": gates.performance,
            "required": gates.performance == "required",
            "max_latency_ms": gates.max_latency_ms,
            "count_budgets": gates.count_budgets,
        },
    }


def promotion_eligible(
    cases: list[dict], results: list[dict], gates: PromotionGates | None = None
) -> bool:
    assessment = gate_results(cases, results, gates or PromotionGates())
    return all(value["status"] == "passed" for value in assessment.values() if value["required"])
