"""Routing, selective retrieval, literal resolution, VQR, and semantic lint."""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from app.modules.agents.semantic.ir import (
    Additivity,
    SemanticFieldIR,
    SemanticMetricIR,
    SemanticModelIR,
)
from app.modules.agents.semantic.planning import SemanticGraph, SemanticPlan


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
    }


@dataclass(frozen=True)
class SemanticSlice:
    metrics: tuple[dict[str, Any], ...]
    dimensions: tuple[dict[str, Any], ...]
    datasets: tuple[dict[str, Any], ...]
    relationships: tuple[dict[str, Any], ...]
    named_filters: tuple[dict[str, Any], ...]
    examples: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SemanticCatalogRetriever:
    """Return the matching semantic objects and only their connecting graph."""

    def retrieve(
        self,
        model: SemanticModelIR,
        question: str,
        *,
        limit: int = 8,
        authorized_datasets: set[str] | None = None,
    ) -> SemanticSlice:
        model = scope_semantic_model(model, authorized_datasets)
        allowed = {dataset.name for dataset in model.datasets}
        terms = _words(question)
        metric_scores = sorted(
            (
                (_score(terms, metric.name, metric.description, metric.synonyms), metric)
                for metric in model.metrics
                if metric.base_dataset in allowed
            ),
            key=lambda item: (-item[0], item[1].name),
        )
        fields = [
            field
            for dataset in model.datasets
            if dataset.name in allowed
            for field in dataset.fields
        ]
        field_scores = sorted(
            (
                (_score(terms, field.name, field.description, field.synonyms), field)
                for field in fields
            ),
            key=lambda item: (-item[0], item[1].name),
        )
        selected_metrics = [item for score, item in metric_scores if score > 0][:limit]
        selected_fields = [item for score, item in field_scores if score > 0][:limit]
        permitted_metrics = [metric for metric in model.metrics if metric.base_dataset in allowed]
        if not selected_metrics and len(permitted_metrics) == 1:
            selected_metrics = [permitted_metrics[0]]
        for metric in selected_metrics:
            time_field = (
                model.field(metric.default_time_dimension)
                if metric.default_time_dimension
                else None
            )
            if time_field is not None and time_field not in selected_fields:
                selected_fields.append(time_field)
        dataset_names = {metric.base_dataset for metric in selected_metrics if metric.base_dataset}
        dataset_names |= {field.dataset for field in selected_fields}
        graph = SemanticGraph(model)
        relationships = []
        names = sorted(dataset_names)
        if names:
            base = names[0]
            for target in names[1:]:
                try:
                    path = graph.path(base, target)
                except ValueError:
                    continue
                for relationship in path.relationships:
                    if (
                        relationship.from_dataset not in allowed
                        or relationship.to_dataset not in allowed
                    ):
                        continue
                    if relationship not in relationships:
                        relationships.append(relationship)
                    dataset_names.update((relationship.from_dataset, relationship.to_dataset))
        selected_datasets = [dataset for dataset in model.datasets if dataset.name in dataset_names]
        selected_filters = [
            named_filter
            for named_filter in model.named_filters
            if _score(terms, named_filter.name, named_filter.description, named_filter.synonyms) > 0
        ][:3]
        selected_examples = sorted(
            ((len(terms & _words(example.question)), example) for example in model.examples),
            key=lambda item: (-item[0], item[1].question),
        )
        return SemanticSlice(
            metrics=tuple(asdict(metric) for metric in selected_metrics),
            dimensions=tuple(asdict(field) for field in selected_fields),
            datasets=tuple(
                {
                    "name": dataset.name,
                    "source": dataset.source,
                    "grain": asdict(dataset.grain),
                }
                for dataset in selected_datasets
            ),
            relationships=tuple(asdict(relationship) for relationship in relationships),
            named_filters=tuple(asdict(item) for item in selected_filters),
            examples=tuple(asdict(item) for score, item in selected_examples[:3] if score > 0),
        )


@dataclass(frozen=True)
class LiteralCandidate:
    value: str
    score: float
    source: str


class LiteralResolver:
    def resolve(
        self,
        literal: str,
        field: SemanticFieldIR,
        *,
        search_candidates: list[str] | tuple[str, ...] = (),
        limit: int = 5,
    ) -> tuple[LiteralCandidate, ...]:
        values = [(value, "sample") for value in field.sample_values]
        values.extend((value, "search") for value in search_candidates)
        target = _phrase(literal)
        target_words = _literal_words(literal)
        candidates = [
            LiteralCandidate(
                value=value,
                score=_literal_score(target, target_words, value),
                source=source,
            )
            for value, source in dict.fromkeys(values)
        ]
        candidates.sort(key=lambda item: (-item.score, item.value))
        return tuple(candidates[:limit])


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


class VerifiedQueryRetriever:
    def retrieve(
        self,
        question: str,
        entries: list[VerifiedQuery],
        *,
        model_fingerprint: str,
        limit: int = 3,
        model: SemanticModelIR | None = None,
    ) -> tuple[VerifiedQuery, ...]:
        normalized = _vqr_concepts(question, model)
        words = _words(normalized)
        scored = [
            (
                max(
                    2.0 if normalized == _vqr_concepts(entry.question, model) else 0.0,
                    len(words & _words(_vqr_concepts(entry.question, model)))
                    / max(len(words | _words(_vqr_concepts(entry.question, model))), 1),
                    difflib.SequenceMatcher(
                        None, normalized, _vqr_concepts(entry.question, model)
                    ).ratio()
                    * 0.85,
                    _plan_term_similarity(words, entry),
                ),
                entry,
            )
            for entry in entries
            if entry.model_fingerprint == model_fingerprint
        ]
        scored.sort(key=lambda item: (-item[0], item[1].verified_query_id))
        return tuple(entry for score, entry in scored[:limit] if score > 0)


def _vqr_concepts(text: str, model: SemanticModelIR | None) -> str:
    normalized = _phrase(text)
    if model is None:
        return normalized
    objects: list[SemanticMetricIR | SemanticFieldIR] = [
        *model.metrics,
        *(field for dataset in model.datasets for field in dataset.fields),
    ]
    aliases: dict[str, set[str]] = {}
    for item in objects:
        for term in (item.name, *item.synonyms):
            aliases.setdefault(_phrase(term), set()).add(_phrase(item.name))
    # Ambiguous synonyms stay lexical; they must not collapse distinct concepts.
    mapping = {
        term: next(iter(names)) for term, names in aliases.items() if term and len(names) == 1
    }
    if not mapping:
        return normalized
    pattern = (
        r"(?<!\w)(?:"
        + "|".join(re.escape(term) for term in sorted(mapping, key=len, reverse=True))
        + r")(?!\w)"
    )
    return re.sub(pattern, lambda match: mapping[match.group()], normalized)


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


def _score(words: set[str], name: str, description: str, synonyms: tuple[str, ...]) -> float:
    candidates = [name, *synonyms]
    if any(_phrase(candidate) in " ".join(sorted(words)) for candidate in candidates if candidate):
        return 1.0
    terms = _words(" ".join([name, description, *synonyms]))
    return len(words & terms) / max(len(words), 1)


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower().replace("_", " ")))


def _phrase(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower().replace("_", " ")))


def _literal_words(value: str) -> set[str]:
    expanded = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", value.lower())
    return set(re.findall(r"[a-z0-9]+", expanded))


def _literal_score(target: str, target_words: set[str], candidate: str) -> float:
    candidate_words = _literal_words(candidate)
    overlap = len(target_words & candidate_words) / max(len(target_words), 1)
    sequence = difflib.SequenceMatcher(None, target, _phrase(candidate)).ratio()
    return round((overlap * 0.75) + (sequence * 0.25), 6)


def _plan_term_similarity(words: set[str], entry: VerifiedQuery) -> float:
    plan_terms = _words(
        " ".join(
            [
                *entry.semantic_plan.metrics,
                *entry.semantic_plan.dimensions,
                *entry.semantic_plan.named_filters,
            ]
        )
    )
    return (len(words & plan_terms) / max(len(words), 1)) * 0.9
