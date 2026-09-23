"""Nova's internal semantic representation compiled from Ossie metadata."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FieldKind(StrEnum):
    FACT = "fact"
    DIMENSION = "dimension"


class Additivity(StrEnum):
    ADDITIVE = "additive"
    SEMI_ADDITIVE = "semi_additive"
    NON_ADDITIVE = "non_additive"


@dataclass(frozen=True)
class SemanticGrain:
    keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticFieldIR:
    name: str
    dataset: str
    expression: str
    kind: FieldKind
    datatype: str | None = None
    description: str = ""
    synonyms: tuple[str, ...] = ()
    is_time: bool = False
    sample_values: tuple[str, ...] = ()
    search_strategy: str | None = None


@dataclass(frozen=True)
class SemanticDatasetIR:
    name: str
    source: str
    description: str = ""
    grain: SemanticGrain = field(default_factory=SemanticGrain)
    fields: tuple[SemanticFieldIR, ...] = ()
    synonyms: tuple[str, ...] = ()

    def field(self, name: str) -> SemanticFieldIR | None:
        return next((item for item in self.fields if item.name == name), None)


@dataclass(frozen=True)
class SemanticMetricIR:
    name: str
    expression: str
    base_dataset: str
    datatype: str | None = None
    description: str = ""
    grain: SemanticGrain = field(default_factory=SemanticGrain)
    additivity: Additivity = Additivity.ADDITIVE
    default_time_dimension: str | None = None
    allowed_dimensions: tuple[str, ...] = ()
    non_additive_dimensions: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    format: str | None = None
    unit: str | None = None
    dependencies: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    visibility: str = "public"
    preferred_relationship_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticRelationshipIR:
    name: str
    from_dataset: str
    to_dataset: str
    from_columns: tuple[str, ...]
    to_columns: tuple[str, ...]
    cardinality: str = "unknown"
    preferred: bool = False


@dataclass(frozen=True)
class SemanticNamedFilterIR:
    name: str
    expression: str
    dataset: str | None = None
    description: str = ""
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticExampleIR:
    question: str
    semantic_plan: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticHierarchyIR:
    name: str
    dimensions: tuple[str, ...]


@dataclass(frozen=True)
class SemanticModelIR:
    name: str
    description: str
    version: str
    fingerprint: str
    datasets: tuple[SemanticDatasetIR, ...]
    metrics: tuple[SemanticMetricIR, ...]
    relationships: tuple[SemanticRelationshipIR, ...]
    hierarchies: tuple[SemanticHierarchyIR, ...] = ()
    entity_ids: tuple[str, ...] = ()
    named_filters: tuple[SemanticNamedFilterIR, ...] = ()
    examples: tuple[SemanticExampleIR, ...] = ()
    question_routing_instructions: str = ""
    query_generation_instructions: str = ""

    @classmethod
    def from_ossie(cls, definition: dict[str, Any]) -> SemanticModelIR:
        datasets: list[SemanticDatasetIR] = []
        for raw_dataset in definition.get("datasets") or []:
            if not isinstance(raw_dataset, dict):
                continue
            dataset_name = str(raw_dataset.get("name") or "")
            fields = tuple(
                _field_from(dataset_name, raw)
                for raw in raw_dataset.get("fields") or []
                if isinstance(raw, dict) and raw.get("name")
            )
            grain_value = raw_dataset.get("grain") or {}
            grain_keys = (
                (grain_value.get("keys") if isinstance(grain_value, dict) else grain_value)
                or raw_dataset.get("primary_key")
                or []
            )
            datasets.append(
                SemanticDatasetIR(
                    name=dataset_name,
                    source=str(raw_dataset.get("source") or ""),
                    description=str(raw_dataset.get("description") or ""),
                    grain=SemanticGrain(tuple(str(key) for key in grain_keys)),
                    fields=fields,
                    synonyms=_synonyms(raw_dataset),
                )
            )

        dataset_names = {dataset.name for dataset in datasets}
        metrics: list[SemanticMetricIR] = []
        for raw in definition.get("metrics") or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            expression = str(raw.get("expression") or "")
            base = str(raw.get("base_dataset") or _infer_dataset(expression, dataset_names) or "")
            additivity_value = raw.get("additivity") or "additive"
            if isinstance(additivity_value, dict):
                additivity_value = additivity_value.get("type") or "additive"
            raw_additivity = str(additivity_value).lower().replace("-", "_")
            try:
                additivity = Additivity(raw_additivity)
            except ValueError as exc:
                raise ValueError(
                    f"Metric {raw.get('name')!r} has unsupported additivity {raw_additivity!r}"
                ) from exc
            metric_grain = raw.get("grain") or {}
            metric_keys = metric_grain.get("keys") if isinstance(metric_grain, dict) else []
            metrics.append(
                SemanticMetricIR(
                    name=str(raw["name"]),
                    expression=expression,
                    base_dataset=base,
                    datatype=_optional_str(raw.get("datatype")),
                    description=str(raw.get("description") or ""),
                    grain=SemanticGrain(tuple(str(key) for key in (metric_keys or []))),
                    additivity=additivity,
                    default_time_dimension=_optional_str(raw.get("default_time_dimension")),
                    allowed_dimensions=tuple(str(x) for x in raw.get("allowed_dimensions") or []),
                    non_additive_dimensions=tuple(
                        str(x)
                        for x in raw.get("non_additive_dimensions")
                        or (
                            raw.get("additivity", {}).get("non_additive_dimensions", [])
                            if isinstance(raw.get("additivity"), dict)
                            else []
                        )
                    ),
                    synonyms=_synonyms(raw),
                    format=_optional_str(raw.get("format")),
                    unit=_optional_str(raw.get("currency") or raw.get("unit")),
                    dependencies=tuple(str(x) for x in raw.get("dependencies") or []),
                    filters=tuple(str(x) for x in raw.get("filters") or []),
                    visibility=str(raw.get("visibility") or "public"),
                    preferred_relationship_path=tuple(
                        str(x) for x in raw.get("preferred_relationship_path") or []
                    ),
                )
            )

        relationships = tuple(
            SemanticRelationshipIR(
                name=str(raw.get("name") or ""),
                from_dataset=str(raw.get("from") or ""),
                to_dataset=str(raw.get("to") or ""),
                from_columns=tuple(str(x) for x in raw.get("from_columns") or []),
                to_columns=tuple(str(x) for x in raw.get("to_columns") or []),
                cardinality=str(raw.get("cardinality") or "unknown").lower(),
                preferred=bool(raw.get("preferred")),
            )
            for raw in definition.get("relationships") or []
            if isinstance(raw, dict)
        )
        named_filters = tuple(
            SemanticNamedFilterIR(
                name=str(raw.get("name") or ""),
                expression=str(raw.get("expression") or ""),
                dataset=_optional_str(raw.get("dataset")),
                description=str(raw.get("description") or ""),
                synonyms=_synonyms(raw),
            )
            for raw in definition.get("named_filters") or []
            if isinstance(raw, dict) and raw.get("name") and raw.get("expression")
        )
        examples = tuple(_examples(definition.get("ai_context")))
        raw_hierarchies = definition.get("hierarchies") or {}
        hierarchies = (
            tuple(
                SemanticHierarchyIR(str(name), tuple(str(field) for field in dimensions))
                for name, dimensions in raw_hierarchies.items()
                if isinstance(dimensions, list)
            )
            if isinstance(raw_hierarchies, dict)
            else ()
        )
        raw_entities = definition.get("entities") or {}
        entity_ids = (
            tuple(str(value) for value in raw_entities.values())
            if isinstance(raw_entities, dict)
            else tuple(str(value) for value in raw_entities)
        )
        canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), default=str)
        return cls(
            name=str(definition.get("name") or ""),
            description=str(definition.get("description") or ""),
            version=str(definition.get("version") or "0.1.1"),
            fingerprint=hashlib.sha256(canonical.encode()).hexdigest(),
            datasets=tuple(datasets),
            metrics=tuple(metrics),
            relationships=relationships,
            hierarchies=hierarchies,
            entity_ids=entity_ids,
            named_filters=named_filters,
            examples=examples,
            question_routing_instructions=str(
                definition.get("question_routing_instructions") or ""
            ),
            query_generation_instructions=str(
                definition.get("query_generation_instructions") or ""
            ),
        )

    def dataset(self, name: str) -> SemanticDatasetIR | None:
        return next((item for item in self.datasets if item.name == name), None)

    def metric(self, name: str) -> SemanticMetricIR | None:
        return next((item for item in self.metrics if item.name == name), None)

    def field(self, name: str) -> SemanticFieldIR | None:
        qualified_dataset, _, bare = name.partition(".")
        if bare:
            dataset = self.dataset(qualified_dataset)
            return dataset.field(bare) if dataset else None
        matches = [
            field for dataset in self.datasets for field in dataset.fields if field.name == name
        ]
        return matches[0] if len(matches) == 1 else None

    def named_filter(self, name: str) -> SemanticNamedFilterIR | None:
        return next((item for item in self.named_filters if item.name == name), None)


def _field_from(dataset: str, raw: dict[str, Any]) -> SemanticFieldIR:
    dimension = raw.get("dimension")
    datatype = _optional_str(raw.get("datatype"))
    is_time = bool(isinstance(dimension, dict) and dimension.get("is_time")) or datatype in {
        "Date",
        "Time",
        "DateTime",
        "DateTimeTz",
    }
    raw_kind = str(raw.get("kind") or "").lower()
    kind = (
        FieldKind.DIMENSION
        if raw_kind == "dimension" or (raw_kind != "fact" and (dimension is not None or is_time))
        else FieldKind.FACT
    )
    sample_values = raw.get("sample_values")
    if not sample_values and isinstance(dimension, dict):
        sample_values = dimension.get("sample_values")
    return SemanticFieldIR(
        name=str(raw.get("name") or ""),
        dataset=dataset,
        expression=str(raw.get("expression") or raw.get("name") or ""),
        kind=kind,
        datatype=datatype,
        description=str(raw.get("description") or ""),
        synonyms=_synonyms(raw),
        is_time=is_time,
        sample_values=tuple(str(value) for value in sample_values or []),
        search_strategy=_optional_str(raw.get("search_strategy")),
    )


def _synonyms(raw: dict[str, Any]) -> tuple[str, ...]:
    direct = raw.get("synonyms")
    ai_context = raw.get("ai_context")
    nested = ai_context.get("synonyms") if isinstance(ai_context, dict) else None
    return tuple(dict.fromkeys(str(value) for value in (direct or nested or []) if str(value)))


def _infer_dataset(expression: str, names: set[str]) -> str | None:
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\.", expression):
        if match.group(1) in names:
            return match.group(1)
    return next(iter(names)) if len(names) == 1 else None


def _examples(ai_context: Any) -> list[SemanticExampleIR]:
    if not isinstance(ai_context, dict):
        return []
    output: list[SemanticExampleIR] = []
    for item in ai_context.get("examples") or []:
        if isinstance(item, str):
            output.append(SemanticExampleIR(question=item))
        elif isinstance(item, dict) and item.get("question"):
            plan = item.get("semantic_plan") or item.get("plan") or {}
            output.append(SemanticExampleIR(question=str(item["question"]), semantic_plan=plan))
    return output


def _optional_str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
