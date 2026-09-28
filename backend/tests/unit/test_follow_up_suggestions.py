"""Follow-up suggestions name only catalog objects the metric can reach."""

from __future__ import annotations

from app.modules.agents.semantic.planning import SemanticPlan, SemanticTime
from app.modules.agents.semantic.suggestions import suggest_follow_ups
from app.modules.assistant import events
from tests.benchmark.studio_accuracy.model import bench_model

MODEL = bench_model()
DIMENSIONS = {field.name.replace("_", " ") for dataset in MODEL.datasets
              for field in dataset.fields if field.kind.value == "dimension"}


def test_suggestions_are_answerable_and_in_the_users_language():
    plan = SemanticPlan(metrics=("total_revenue",), dimensions=("city",),
                        time=SemanticTime("order_date", range="previous_month"))
    suggestions = suggest_follow_ups(plan, MODEL, language="id")
    assert len(suggestions) == 3
    assert all("bulan lalu" in item for item in suggestions)
    assert not any(" per city" in item for item in suggestions)
    # Marketing dimensions are not reachable from order revenue.
    assert not any("marketing channel" in item for item in suggestions)


def test_a_comparison_is_not_suggested_twice():
    plan = SemanticPlan(metrics=("order_count",), time=SemanticTime(
        "order_date", range="current_month", compare="previous_period"))
    assert not any("previous period" in item
                   for item in suggest_follow_ups(plan, MODEL, language="en"))


def test_unknown_metric_yields_nothing_and_event_is_bounded():
    assert suggest_follow_ups(SemanticPlan(metrics=("payroll",)), MODEL, language="en") == []
    frame = events.suggestions([str(index) for index in range(9)])
    assert frame.startswith("event: suggestions") and '"8"' not in frame
