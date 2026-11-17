"""The grain at which a semantic dataset's metrics are computed for one chart.

A semantic dataset stores its rows at the grain of its selected dimensions, with
each metric already computed per row. A chart that groups by fewer dimensions
must not simply add those rows up: a margin, an average or a distinct count is
not the sum of its parts. When a chart is coarser than the dataset, the
semantic query is compiled again at the chart's own grain, so the semantic
layer computes every metric once per chart group. Where that is not possible
(a coarser date grain, or a filter on a field the chart does not group by), a
metric is re-totalled only when its definition is a plain sum or count;
otherwise the query is refused rather than shown with a wrong number.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from app.modules.agents.semantic.ir import Additivity, SemanticModelIR

from .contracts.datasets import DatasetField
from .contracts.filters import And, Not, Or, Predicate
from .contracts.queries import DatasetQueryRequest


class MetricGrainError(ValueError):
    """The chart asks for a metric at a grain it cannot be computed at."""


@dataclass(frozen=True)
class SemanticGrain:
    """Compile the semantic query again with these fields, or keep the dataset grain."""

    dimensions: tuple[str, ...]
    metrics: tuple[str, ...]
    fields: tuple[DatasetField, ...]


_SUMMABLE = re.compile(r"^\s*(SUM|COUNT)\s*\(", re.IGNORECASE)
_DISTINCT = re.compile(r"^\s*COUNT\s*\(\s*DISTINCT\b", re.IGNORECASE)
_CONSTANT_FACTOR = re.compile(r"^\s*(\*\s*[0-9]+(\.[0-9]+)?\s*)?$")


def summable(expression: str) -> bool:
    """Whether the metric is one SUM or COUNT (optionally times a constant).

    Only such a metric can be added up across rows of the dataset grain and give
    the same value as computing it over the coarser group.
    """
    if not _SUMMABLE.match(expression) or _DISTINCT.match(expression):
        return False
    depth = 0
    for index, char in enumerate(expression):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return bool(_CONSTANT_FACTOR.match(expression[index + 1:]))
    return False


def _predicates(node: object) -> Iterator[Predicate]:
    if isinstance(node, Predicate):
        yield node
    elif isinstance(node, And | Or):
        for child in node.children:
            yield from _predicates(child)
    elif isinstance(node, Not):
        yield from _predicates(node.child)


def plan_semantic_grain(
    model: SemanticModelIR,
    fields: tuple[DatasetField, ...],
    request: DatasetQueryRequest,
) -> SemanticGrain | None:
    """Return the grain to compile at, or None when the dataset grain is exact."""
    by_id = {field.field_id: field for field in fields}
    metric_ids = {
        field.field_id for field in fields
        if field.semantic_role == "measure" and model.metric(field.source_column) is not None
    }
    requested_metrics = [
        measure for measure in request.measures
        if measure.field_id is not None and measure.field_id in metric_ids
    ]
    if not requested_metrics:
        return None
    dataset_dimensions = [field.field_id for field in fields if field.field_id not in metric_ids]
    grouped = [item.field_id for item in request.dimensions]
    coarser_date = any(item.date_grain is not None for item in request.dimensions)
    if set(grouped) >= set(dataset_dimensions) and not coarser_date:
        return None  # One dataset row per chart group: every metric is exact.

    filtered = {
        predicate.field.field_id
        for predicate in (_predicates(request.filters.expression) if request.filters else ())
    }
    other_measures = [
        measure for measure in request.measures
        if measure.field_id is None or measure.field_id not in metric_ids
    ]
    if not coarser_date and filtered <= set(grouped) and not other_measures:
        dimensions = tuple(dict.fromkeys(by_id[item].source_column for item in grouped))
        metrics = tuple(dict.fromkeys(by_id[str(item.field_id)].source_column
                                      for item in requested_metrics))
        kept = {*grouped, *(str(item.field_id) for item in requested_metrics)}
        return SemanticGrain(dimensions=dimensions, metrics=metrics,
                             fields=tuple(field for field in fields if field.field_id in kept))

    for measure in requested_metrics:
        field = by_id[str(measure.field_id)]
        metric = model.metric(field.source_column)
        assert metric is not None
        declared = metric.additivity is Additivity.ADDITIVE
        if not (declared and summable(metric.expression) and measure.aggregation == "sum"):
            raise MetricGrainError(
                f"{field.name} is calculated per "
                + ", ".join(by_id[item].name for item in dataset_dimensions)
                + " and cannot be added up across them. Group the chart by those fields, "
                "or remove the date grouping or filters on fields the chart does not show."
            )
    return None
