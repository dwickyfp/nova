"""Routing, selective retrieval, literal resolution, VQR, and semantic lint."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from app.modules.agents.semantic.ir import (
    Additivity,
    SemanticModelIR,
)
from app.modules.agents.semantic.planning import SemanticPlan


@dataclass(frozen=True)
class SemanticModelCandidate:
    model_id: str
    model: SemanticModelIR


@dataclass(frozen=True)
class SemanticModelSelection:
    model_id: str | None
    score: float
    ambiguity: bool
    alternatives: tuple[str, ...] = ()


class SemanticModelRouter:
    def route(
        self, question: str, candidates: list[SemanticModelCandidate]
    ) -> SemanticModelSelection:
        words = _words(question)
        scored: list[tuple[float, str]] = []
        for candidate in candidates:
            terms = _words(candidate.model.name + " " + candidate.model.description)
            for metric in candidate.model.metrics:
                terms |= _words(" ".join((metric.name, metric.description, *metric.synonyms)))
            for dataset in candidate.model.datasets:
                terms |= _words(" ".join((dataset.name, dataset.description, *dataset.synonyms)))
                for field in dataset.fields:
                    terms |= _words(" ".join((field.name, field.description, *field.synonyms)))
            for named_filter in candidate.model.named_filters:
                terms |= _words(
                    " ".join((named_filter.name, named_filter.description, *named_filter.synonyms))
                )
            for example in candidate.model.examples:
                terms |= _words(example.question)
            overlap = len(words & terms)
            exact = sum(
                3
                for metric in candidate.model.metrics
                if _phrase(metric.name) in _phrase(question)
                or any(_phrase(synonym) in _phrase(question) for synonym in metric.synonyms)
            )
            score = (overlap + exact) / max(len(words), 1)
            scored.append((score, candidate.model_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        if not scored or scored[0][0] == 0:
            return SemanticModelSelection(None, 0.0, bool(candidates))
        top = scored[0][0]
        tied = tuple(model_id for score, model_id in scored if score == top)
        return SemanticModelSelection(
            tied[0] if len(tied) == 1 else None,
            min(top, 1.0),
            len(tied) > 1,
            tied,
        )


def scope_semantic_model(
    model: SemanticModelIR, authorized_datasets: set[str] | None
) -> SemanticModelIR:
    """Return the provider-visible semantic model after authorization filtering."""
    if authorized_datasets is None:
        return model
    allowed = set(authorized_datasets)
    from app.modules.agents.semantic.expressions import referenced_datasets
    from app.modules.agents.semantic.planning import SemanticPlanError

    def visible(expression: str, dataset: str) -> bool:
        try:
            return referenced_datasets(expression, dataset) <= allowed
        except SemanticPlanError:
            return False

    datasets = tuple(
        replace(
            item,
            fields=tuple(field for field in item.fields if visible(field.expression, item.name)),
        )
        for item in model.datasets
        if item.name in allowed
    )
    metrics = tuple(
        item
        for item in model.metrics
        if item.base_dataset in allowed
        and visible(item.expression, item.base_dataset)
        and all(visible(predicate, item.base_dataset) for predicate in item.filters)
    )
    # Remove dependency chains whose upstream metric was filtered out.
    while True:
        names = {item.name for item in metrics}
        retained = tuple(item for item in metrics if set(item.dependencies) <= names)
        if retained == metrics:
            break
        metrics = retained
    relationships = tuple(
        item
        for item in model.relationships
        if item.from_dataset in allowed and item.to_dataset in allowed
    )
    fully_authorized = len(datasets) == len(model.datasets)
    return replace(
        model,
        description=model.description if fully_authorized else "",
        datasets=datasets,
        metrics=metrics,
        relationships=relationships,
        named_filters=tuple(
            item
            for item in model.named_filters
            if (item.dataset in allowed or (item.dataset is None and fully_authorized))
            and visible(item.expression, item.dataset or next(iter(allowed), ""))
        ),
        examples=model.examples if fully_authorized else (),
        question_routing_instructions=model.question_routing_instructions
        if fully_authorized
        else "",
        query_generation_instructions=model.query_generation_instructions
        if fully_authorized
        else "",
    )


def semantic_ir_to_definition(model: SemanticModelIR) -> dict[str, Any]:
    """Serialize IR back to the normalized definition shape consumed by Nova."""
    return {
        "name": model.name,
        "description": model.description,
        "version": model.version,
        "question_routing_instructions": model.question_routing_instructions,
        "query_generation_instructions": model.query_generation_instructions,
        "datasets": [
            {
                "name": dataset.name,
                "source": dataset.source,
                "description": dataset.description,
                "grain": {"keys": list(dataset.grain.keys)},
                "synonyms": list(dataset.synonyms),
                "fields": [
                    {
                        "name": field.name,
                        "expression": field.expression,
                        "kind": field.kind.value,
                        "datatype": field.datatype,
                        "description": field.description,
                        "synonyms": list(field.synonyms),
                        "dimension": {
                            "is_time": field.is_time,
                            "sample_values": list(field.sample_values),
                        },
                        "search_strategy": field.search_strategy,
                    }
                    for field in dataset.fields
                ],
            }
            for dataset in model.datasets
        ],
        "metrics": [
            {
                "name": metric.name,
                "expression": metric.expression,
                "base_dataset": metric.base_dataset,
                "datatype": metric.datatype,
                "description": metric.description,
                "grain": {"keys": list(metric.grain.keys)},
                "additivity": metric.additivity.value,
                "default_time_dimension": metric.default_time_dimension,
                "allowed_dimensions": list(metric.allowed_dimensions),
                "non_additive_dimensions": list(metric.non_additive_dimensions),
                "synonyms": list(metric.synonyms),
                "format": metric.format,
                "unit": metric.unit,
                "dependencies": list(metric.dependencies),
                "filters": list(metric.filters),
                "visibility": metric.visibility,
                "preferred_relationship_path": list(metric.preferred_relationship_path),
            }
            for metric in model.metrics
        ],
        "relationships": [
            {
                "name": relationship.name,
                "from": relationship.from_dataset,
                "to": relationship.to_dataset,
                "from_columns": list(relationship.from_columns),
                "to_columns": list(relationship.to_columns),
                "cardinality": relationship.cardinality,
                "preferred": relationship.preferred,
            }
            for relationship in model.relationships
        ],
        "named_filters": [
            {
                "name": item.name,
                "expression": item.expression,
                "dataset": item.dataset,
                "description": item.description,
                "synonyms": list(item.synonyms),
            }
            for item in model.named_filters
        ],
        "ai_context": {
            "examples": [
                {"question": item.question, "semantic_plan": item.semantic_plan}
                for item in model.examples
            ]
        },
        "conformed_dimensions": [
            {"fields": list(group)} for group in model.conformed_dimensions
        ],
    }










@dataclass(frozen=True)
class VerifiedQuery:
    verified_query_id: str
    semantic_model_id: str
    model_fingerprint: str
    question: str
    semantic_plan: SemanticPlan
    verified_sql: str
    tags: tuple[str, ...] = ()
    usage_count: int = 0
    success_count: int = 0






@dataclass(frozen=True)
class SemanticLint:
    code: str
    severity: str
    message: str
    object_name: str | None = None


@dataclass(frozen=True)
class SemanticValidation:
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors


def validate_semantic_model_ir(model: SemanticModelIR) -> SemanticValidation:
    """Validate cross-object references that Ossie shape validation cannot."""
    from app.modules.agents.semantic.derived import compile_metric_expression
    from app.modules.agents.semantic.ir import FieldKind

    errors: list[str] = []
    dataset_names = {dataset.name for dataset in model.datasets}
    for dataset in model.datasets:
        field_names = {field.name for field in dataset.fields}
        missing_grain = [key for key in dataset.grain.keys if key not in field_names]
        if missing_grain:
            errors.append(
                f"Dataset {dataset.name!r} grain references unknown fields: "
                + ", ".join(missing_grain)
            )
    for relationship in model.relationships:
        left = model.dataset(relationship.from_dataset)
        right = model.dataset(relationship.to_dataset)
        if left is None or right is None:
            errors.append(f"Relationship {relationship.name!r} references an unknown dataset.")
            continue
        left_fields = {field.name for field in left.fields}
        right_fields = {field.name for field in right.fields}
        if any(column not in left_fields for column in relationship.from_columns):
            errors.append(f"Relationship {relationship.name!r} has an unknown source field.")
        if any(column not in right_fields for column in relationship.to_columns):
            errors.append(f"Relationship {relationship.name!r} has an unknown target field.")
    for metric in model.metrics:
        if metric.base_dataset and metric.base_dataset not in dataset_names:
            errors.append(
                f"Metric {metric.name!r} references unknown base dataset {metric.base_dataset!r}."
            )
        if metric.default_time_dimension:
            field = model.field(metric.default_time_dimension)
            if field is None or not field.is_time:
                errors.append(f"Metric {metric.name!r} has an invalid default time dimension.")
        try:
            compile_metric_expression(model, metric)
        except ValueError as exc:
            errors.append(f"Metric {metric.name!r}: {exc}")
    for hierarchy in model.hierarchies:
        if len(hierarchy.dimensions) < 2 or len(set(hierarchy.dimensions)) != len(
            hierarchy.dimensions
        ):
            errors.append(f"Hierarchy {hierarchy.name!r} needs distinct ordered dimensions.")
        for dimension in hierarchy.dimensions:
            field = model.field(dimension)
            if field is None or field.kind != FieldKind.DIMENSION:
                errors.append(
                    f"Hierarchy {hierarchy.name!r} references unknown dimension {dimension!r}."
                )
    for named_filter in model.named_filters:
        if named_filter.dataset and named_filter.dataset not in dataset_names:
            errors.append(
                f"Named filter {named_filter.name!r} references unknown dataset "
                f"{named_filter.dataset!r}."
            )
    warnings = tuple(item.message for item in lint_semantic_model(model))
    return SemanticValidation(tuple(errors), warnings)


def lint_semantic_model(model: SemanticModelIR) -> list[SemanticLint]:
    findings: list[SemanticLint] = []
    synonym_owner: dict[str, str] = {}
    referenced_datasets = {metric.base_dataset for metric in model.metrics}
    referenced_datasets.update(
        dataset
        for relationship in model.relationships
        for dataset in (relationship.from_dataset, relationship.to_dataset)
    )
    for dataset in model.datasets:
        if not dataset.grain.keys:
            findings.append(
                SemanticLint(
                    "dataset_missing_grain", "warning", "Dataset has no grain keys.", dataset.name
                )
            )
        for field in dataset.fields:
            if (
                field.kind.value == "dimension"
                and not field.sample_values
                and not field.search_strategy
            ):
                findings.append(
                    SemanticLint(
                        "dimension_missing_literal_strategy",
                        "warning",
                        "Dimension has neither sample values nor a search strategy.",
                        f"{dataset.name}.{field.name}",
                    )
                )
            for synonym in field.synonyms:
                key = _phrase(synonym)
                if key in synonym_owner and synonym_owner[key] != field.name:
                    findings.append(
                        SemanticLint(
                            "duplicate_synonym",
                            "warning",
                            f"Synonym {synonym!r} is also used by {synonym_owner[key]!r}.",
                            field.name,
                        )
                    )
                synonym_owner[key] = field.name
        if len(model.datasets) > 1 and dataset.name not in referenced_datasets:
            findings.append(
                SemanticLint(
                    "unused_dataset",
                    "warning",
                    "Dataset is not referenced by a metric or relationship.",
                    dataset.name,
                )
            )
    for relationship in model.relationships:
        if relationship.cardinality == "unknown":
            findings.append(
                SemanticLint(
                    "relationship_missing_cardinality",
                    "warning",
                    "Relationship has no cardinality; fanout safety cannot be proven.",
                    relationship.name,
                )
            )
    for metric in model.metrics:
        if not metric.description:
            findings.append(
                SemanticLint(
                    "metric_missing_description",
                    "warning",
                    "Metric has no description.",
                    metric.name,
                )
            )
        if not metric.synonyms:
            findings.append(
                SemanticLint(
                    "metric_missing_synonyms", "warning", "Metric has no synonyms.", metric.name
                )
            )
        if metric.additivity != Additivity.ADDITIVE and not metric.allowed_dimensions:
            findings.append(
                SemanticLint(
                    "metric_additivity_scope_missing",
                    "warning",
                    "Non-additive metric does not declare allowed dimensions.",
                    metric.name,
                )
            )
        base = model.dataset(metric.base_dataset)
        if (
            base is not None
            and any(field.is_time for field in base.fields)
            and not metric.default_time_dimension
        ):
            findings.append(
                SemanticLint(
                    "metric_missing_default_time_dimension",
                    "warning",
                    "Metric dataset has time fields but no default time dimension.",
                    metric.name,
                )
            )
        unsafe_relationships = [
            relationship.name
            for relationship in model.relationships
            if relationship.from_dataset == metric.base_dataset
            and relationship.cardinality in {"unknown", "one_to_many", "many_to_many"}
        ]
        if unsafe_relationships:
            findings.append(
                SemanticLint(
                    "metric_fanout_risk",
                    "warning",
                    "Metric can cross relationships without proven fanout safety: "
                    + ", ".join(unsafe_relationships),
                    metric.name,
                )
            )
    return findings


def semantic_quality(model: SemanticModelIR, *, verified_query_count: int = 0) -> dict[str, Any]:
    dataset_count = len(model.datasets)
    relationship_count = len(model.relationships)
    metric_count = len(model.metrics)

    def ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 3) if denominator else 1.0

    return {
        "datasets_with_grain": ratio(
            sum(bool(item.grain.keys) for item in model.datasets), dataset_count
        ),
        "relationships_with_cardinality": ratio(
            sum(item.cardinality != "unknown" for item in model.relationships), relationship_count
        ),
        "metrics_with_descriptions": ratio(
            sum(bool(item.description) for item in model.metrics), metric_count
        ),
        "metrics_with_synonyms": ratio(
            sum(bool(item.synonyms) for item in model.metrics), metric_count
        ),
        "time_metrics_with_default_dimension": ratio(
            sum(bool(item.default_time_dimension) for item in model.metrics), metric_count
        ),
        "verified_query_count": verified_query_count,
        "fingerprint": model.fingerprint,
    }


@dataclass(frozen=True)
class SemanticFeedback:
    kind: str
    question: str
    detail: str
    semantic_model_id: str


def feedback_suggestions(events: list[SemanticFeedback]) -> list[dict[str, Any]]:
    """Produce reviewable suggestions. Never mutates a governed model."""
    grouped: dict[tuple[str, str], list[SemanticFeedback]] = {}
    for event in events:
        grouped.setdefault((event.semantic_model_id, event.kind), []).append(event)
    return [
        {
            "semantic_model_id": model_id,
            "kind": kind,
            "count": len(items),
            "examples": [item.question for item in items[:5]],
            "requires_adoption": True,
        }
        for (model_id, kind), items in sorted(grouped.items())
    ]


def materialized_view_suggestions(
    usage: list[dict[str, Any]], *, minimum_count: int = 10
) -> list[dict[str, Any]]:
    """Aggregate workload shapes into review-only StarRocks MV candidates."""
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for event in usage:
        if not event.get("succeeded", True):
            continue
        metrics = tuple(sorted(str(item) for item in event.get("metrics") or []))
        dimensions = tuple(sorted(str(item) for item in event.get("dimensions") or []))
        key = (
            event.get("semantic_model_id"),
            event.get("model_fingerprint"),
            metrics,
            dimensions,
            event.get("time_grain"),
        )
        group = groups.setdefault(
            key,
            {
                "semantic_model_id": key[0],
                "model_fingerprint": key[1],
                "metrics": list(metrics),
                "dimensions": list(dimensions),
                "time_grain": key[4],
                "query_count": 0,
                "requires_adoption": True,
            },
        )
        group["query_count"] += 1
    return sorted(
        (item for item in groups.values() if item["query_count"] >= minimum_count),
        key=lambda item: (-item["query_count"], str(item["semantic_model_id"])),
    )




def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower().replace("_", " ")))


def _phrase(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower().replace("_", " ")))






