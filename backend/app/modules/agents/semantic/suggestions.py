"""Follow-up questions a Studio answer can offer, built from its governed plan.

Deterministic and catalog-bound: every suggestion names only metrics and
dimensions of the bound Semantic View, so clicking one is answerable. No model
call and no data is involved.
"""

from __future__ import annotations

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticGraph, SemanticPlan, SemanticPlanError


def _words(name: str) -> str:
    return name.replace("_", " ")


def suggest_follow_ups(
    plan: SemanticPlan, model: SemanticModelIR, *, language: str = "en", limit: int = 3
) -> list[str]:
    """Up to ``limit`` follow-up questions, written in the user's language."""
    from app.modules.assistant.messages import MESSAGES, say

    if not plan.metrics:
        return []
    metric = model.metric(plan.metrics[0])
    if metric is None:
        return []
    graph = SemanticGraph(model)

    def reachable(dataset: str) -> bool:
        if dataset == metric.base_dataset:
            return True
        try:
            path = graph.path(metric.base_dataset, dataset)
        except SemanticPlanError:
            return False
        return not path.ambiguous and all(
            relationship.cardinality in {"many_to_one", "one_to_one"}
            for relationship in path.relationships
        )

    used = set(plan.dimensions) | {item.field for item in plan.filters}
    candidates = [
        field.name
        for dataset in model.datasets
        for field in dataset.fields
        if field.kind.value == "dimension" and not field.is_time
        and field.name not in used and reachable(dataset.name)
    ]
    period = plan.time.range if plan.time else None
    period_key = f"period.{period}"
    suffix = f" {say(period_key, language)}" if period_key in MESSAGES else ""
    name = _words(metric.name)
    output: list[str] = [
        say("suggest.by", language, metric=name, dimension=_words(dimension), period=suffix)
        for dimension in candidates[:2]
    ]
    if period_key in MESSAGES and not (plan.time and plan.time.compare):
        output.append(say("suggest.previous", language, metric=name, period=suffix))
    if not (plan.time and plan.time.grain) and metric.default_time_dimension:
        output.append(say("suggest.trend", language, metric=name))
    if plan.dimensions and not plan.limit:
        output.append(say(
            "suggest.top", language, dimension=_words(plan.dimensions[0]), metric=name,
            period=suffix,
        ))
    return list(dict.fromkeys(output))[:limit]
