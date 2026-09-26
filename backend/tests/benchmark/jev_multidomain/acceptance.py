"""Acceptance is derived from measured outcomes, including incomplete coverage."""

from __future__ import annotations


def acceptance(summary: dict, comparisons: dict) -> list[dict]:
    categories = summary.get("categories", {})
    groups = [categories.get(name, {}) for name in ["semantic_finance", "semantic_marketing"]]
    semantic_n = sum(g.get("n", 0) for g in groups)
    semantic_accuracy = (
        sum(g.get("n", 0) * g.get("routing_accuracy", 0) for g in groups) / semantic_n
        if semantic_n
        else None
    )
    specifications = [
        ("Explicit Finance", categories.get("explicit_finance", {}).get("routing_accuracy"), 0.98),
        (
            "Explicit Marketing",
            categories.get("explicit_marketing", {}).get("routing_accuracy"),
            0.98,
        ),
        ("Overall exact routing", summary.get("routing_accuracy"), 0.95),
        ("Implicit / semantic routing", semantic_accuracy, 0.95),
        ("Cross-domain", categories.get("cross_domain", {}).get("routing_accuracy"), 0.92),
        ("Ambiguous category", categories.get("ambiguous", {}).get("routing_accuracy"), 0.90),
        ("Adversarial", categories.get("adversarial", {}).get("routing_accuracy"), 0.90),
        ("Paraphrase modal consistency", summary.get("paraphrase_modal_agreement"), 0.95),
        (
            "Repeated clear-intent Smart decisions",
            comparisons.get("three_run_smart_clear", {}).get("modal_agreement"),
            0.95,
        ),
        ("Deterministic answer accuracy", summary.get("deterministic_answer_accuracy"), 0.95),
    ]
    complete = summary.get("attempted") == summary.get("planned") and not summary.get(
        "judge_missing"
    )
    result = [
        {
            "metric": name,
            "observed": observed,
            "target": target,
            "operator": ">=",
            "status": "UNMEASURED"
            if observed is None
            else "INCOMPLETE"
            if not complete
            else "PASS"
            if observed >= target
            else "FAIL",
        }
        for name, observed, target in specifications
    ]
    order = comparisons.get("variant-reverse", {})
    observed = order.get("change_rate")
    result.append(
        {
            "metric": "Smart registration order sensitivity",
            "observed": observed,
            "target": 0.02,
            "operator": "<",
            "n": order.get("paired_cases", 0),
            "status": "UNMEASURED"
            if observed is None
            else "INCONCLUSIVE; FAILED DECISIONS"
            if order.get("valid_paired_cases") != order.get("paired_cases")
            else "OBSERVED PASS; SMALL SAMPLE"
            if observed < 0.02
            else "FAIL",
        }
    )
    return result
