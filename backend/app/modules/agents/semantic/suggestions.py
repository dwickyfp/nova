"""Follow-up questions a Studio answer can offer, built from its governed plan.

Deterministic and catalog-bound: every suggestion names only metrics and
dimensions of the bound Semantic View, so clicking one is answerable. No model
call and no data is involved.
"""

from __future__ import annotations

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticGraph, SemanticPlan, SemanticPlanError

_PERIOD_NAMES = {
    "current_month": ("this month", "bulan ini"),
    "previous_month": ("last month", "bulan lalu"),
    "current_quarter": ("this quarter", "kuartal ini"),
    "previous_quarter": ("last quarter", "kuartal lalu"),
    "current_year": ("this year", "tahun ini"),
    "previous_year": ("last year", "tahun lalu"),
    "current_week": ("this week", "minggu ini"),
    "previous_week": ("last week", "minggu lalu"),
    "ytd": ("year to date", "sejak awal tahun"),
    "last_30_days": ("in the last 30 days", "30 hari terakhir"),
}


def _words(name: str) -> str:
    return name.replace("_", " ")


def suggest_follow_ups(
    plan: SemanticPlan, model: SemanticModelIR, *, indonesian: bool, limit: int = 3
) -> list[str]:
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
    period_text = _PERIOD_NAMES.get(period or "", (None, None))[1 if indonesian else 0]
    suffix = f" {period_text}" if period_text else ""
    name = _words(metric.name)
    output: list[str] = []
    for dimension in candidates[:2]:
        output.append(
            f"{name} per {_words(dimension)}{suffix}" if indonesian
            else f"{name} by {_words(dimension)}{suffix}"
        )
    if period in _PERIOD_NAMES and not (plan.time and plan.time.compare):
        output.append(
            f"{name}{suffix} dibanding periode sebelumnya" if indonesian
            else f"{name}{suffix} compared with the previous period"
        )
    if not (plan.time and plan.time.grain) and metric.default_time_dimension:
        output.append(
            f"tren {name} per bulan tahun ini" if indonesian
            else f"monthly {name} trend this year"
        )
    if plan.dimensions and not plan.limit:
        output.append(
            f"top 5 {_words(plan.dimensions[0])} berdasarkan {name}{suffix}" if indonesian
            else f"top 5 {_words(plan.dimensions[0])} by {name}{suffix}"
        )
    return list(dict.fromkeys(output))[:limit]
