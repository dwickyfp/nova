"""Semantic plans, lexical planning, relationship paths, and confidence."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

from app.modules.agents.semantic.ir import (
    SemanticFieldIR,
    SemanticModelIR,
    SemanticRelationshipIR,
)


class SemanticPlanError(ValueError):
    pass


@dataclass(frozen=True)
class SemanticFilter:
    field: str
    operator: str
    value: Any


@dataclass(frozen=True)
class SemanticTime:
    dimension: str
    grain: str | None = None
    range: str | None = None
    compare: str | None = None


@dataclass(frozen=True)
class SemanticOrder:
    field: str
    direction: str = "desc"


@dataclass(frozen=True)
class SemanticPlan:
    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    filters: tuple[SemanticFilter, ...] = ()
    named_filters: tuple[str, ...] = ()
    time: SemanticTime | None = None
    order_by: tuple[SemanticOrder, ...] = ()
    limit: int | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SemanticPlan:
        if not isinstance(value, dict):
            raise SemanticPlanError("Semantic plan must be an object.")
        filters = tuple(
            SemanticFilter(
                field=str(item.get("field") or ""),
                operator=str(item.get("operator") or "=").upper(),
                value=item.get("value"),
            )
            for item in value.get("filters") or []
            if isinstance(item, dict)
        )
        raw_time = value.get("time")
        time = None
        if isinstance(raw_time, dict) and raw_time.get("dimension"):
            time = SemanticTime(
                dimension=str(raw_time["dimension"]),
                grain=_optional(raw_time.get("grain")),
                range=_optional(raw_time.get("range")),
                compare=_optional(raw_time.get("compare")),
            )
        orders = tuple(
            SemanticOrder(
                field=str(item.get("field") or ""),
                direction="asc" if str(item.get("direction")).lower() == "asc" else "desc",
            )
            for item in value.get("order_by") or []
            if isinstance(item, dict)
        )
        raw_limit = value.get("limit")
        limit = min(max(int(raw_limit), 1), 1000) if isinstance(raw_limit, int) else None
        plan = cls(
            metrics=tuple(str(item) for item in value.get("metrics") or []),
            dimensions=tuple(str(item) for item in value.get("dimensions") or []),
            filters=filters,
            named_filters=tuple(str(item) for item in value.get("named_filters") or []),
            time=time,
            order_by=orders,
            limit=limit,
        )
        if not plan.metrics and not plan.dimensions:
            raise SemanticPlanError("Semantic plan needs at least one metric or dimension.")
        return plan

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticConfidence:
    score: float
    level: str
    signals: dict[str, float]
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannedSemantics:
    plan: SemanticPlan | None
    confidence: SemanticConfidence
    clarification: str | None = None


@dataclass(frozen=True)
class RelationshipPath:
    relationships: tuple[SemanticRelationshipIR, ...]
    ambiguous: bool = False


class SemanticGraph:
    def __init__(self, model: SemanticModelIR) -> None:
        self.model = model
        self._edges: dict[str, list[tuple[str, SemanticRelationshipIR]]] = {}
        for relationship in model.relationships:
            self._edges.setdefault(relationship.from_dataset, []).append(
                (relationship.to_dataset, relationship)
            )
            self._edges.setdefault(relationship.to_dataset, []).append(
                (relationship.from_dataset, relationship)
            )
        for values in self._edges.values():
            values.sort(key=lambda item: (not item[1].preferred, item[1].name, item[0]))

    def path(
        self,
        source: str,
        target: str,
        *,
        preferred_names: tuple[str, ...] = (),
    ) -> RelationshipPath:
        if source == target:
            return RelationshipPath(())
        queue: deque[tuple[str, tuple[SemanticRelationshipIR, ...]]] = deque([(source, ())])
        best_depth: int | None = None
        paths: list[tuple[SemanticRelationshipIR, ...]] = []
        visited_depth: dict[str, int] = {source: 0}
        while queue:
            node, path = queue.popleft()
            if best_depth is not None and len(path) >= best_depth:
                continue
            for neighbour, relationship in self._edges.get(node, []):
                new_path = (*path, relationship)
                if neighbour == target:
                    best_depth = len(new_path)
                    paths.append(new_path)
                    continue
                prior = visited_depth.get(neighbour)
                if prior is None or len(new_path) <= prior:
                    visited_depth[neighbour] = len(new_path)
                    queue.append((neighbour, new_path))
        if not paths:
            raise SemanticPlanError(f"No relationship path connects {source!r} to {target!r}.")
        unique = {tuple(rel.name for rel in path): path for path in paths}
        candidates = list(unique.values())
        if preferred_names:
            preferred = [
                path for path in candidates if tuple(rel.name for rel in path) == preferred_names
            ]
            if preferred:
                return RelationshipPath(preferred[0], ambiguous=False)
        candidates.sort(
            key=lambda path: (
                -sum(1 for rel in path if rel.preferred),
                tuple(rel.name for rel in path),
            )
        )
        best = candidates[0]
        best_preference = sum(1 for rel in best if rel.preferred)
        equally_ranked = [
            path
            for path in candidates
            if sum(1 for rel in path if rel.preferred) == best_preference
        ]
        return RelationshipPath(best, ambiguous=len(equally_ranked) > 1)


class SemanticPlanner:
    """Exact/synonym planner. Fuzzy ambiguity is returned, not guessed."""

    def plan(self, model: SemanticModelIR, question: str) -> PlannedSemantics:
        normalized = _normalize(question)
        metric_scores = _object_scores(
            normalized,
            [(metric.name, metric.synonyms, metric.description) for metric in model.metrics],
        )
        dimension_fields = [
            field
            for dataset in model.datasets
            for field in dataset.fields
            if field.kind.value == "dimension"
        ]
        dimension_scores = _object_scores(
            normalized,
            [(field.name, field.synonyms, field.description) for field in dimension_fields],
        )
        selected_metrics, metric_ambiguous = _select(metric_scores)
        selected_dimensions, dimension_ambiguous = _select(dimension_scores, threshold=0.42)

        filters: list[SemanticFilter] = []
        literal_scores: list[float] = []
        for field in dimension_fields:
            for sample in field.sample_values:
                if _normalize(sample) in normalized:
                    filters.append(SemanticFilter(field.name, "=", sample))
                    literal_scores.append(1.0)

        named_filters = tuple(
            item.name
            for item in model.named_filters
            if any(_normalize(term) in normalized for term in (item.name, *item.synonyms) if term)
        )
        time = _time_plan(question, model, selected_metrics, dimension_fields)
        limit = _limit(question)
        order: tuple[SemanticOrder, ...] = ()
        if limit and selected_metrics:
            order = (SemanticOrder(selected_metrics[0], "desc"),)

        unresolved: list[str] = []
        if metric_ambiguous:
            unresolved.append("metric")
        if dimension_ambiguous:
            unresolved.append("dimension")
        if not selected_metrics and model.metrics:
            unresolved.append("metric")
        metric_score = metric_scores[0][0] if metric_scores else 0.0
        dimension_score = dimension_scores[0][0] if dimension_scores else 1.0
        signals = {
            "metric_match": metric_score,
            "dimension_match": dimension_score,
            "literal_resolution": min(literal_scores) if literal_scores else 1.0,
            "ambiguity": 0.0 if unresolved else 1.0,
        }
        score = sum(signals.values()) / len(signals)
        level = "high" if score >= 0.8 else "medium" if score >= 0.55 else "low"
        confidence = SemanticConfidence(score, level, signals, tuple(unresolved))
        if unresolved and score < 0.62:
            return PlannedSemantics(
                None,
                confidence,
                "The semantic metric is ambiguous. Specify the governed metric to use.",
            )
        plan = SemanticPlan(
            metrics=tuple(selected_metrics),
            dimensions=tuple(selected_dimensions),
            filters=tuple(filters),
            named_filters=named_filters,
            time=time,
            order_by=order,
            limit=limit,
        )
        return PlannedSemantics(plan, confidence)


def validate_plan(model: SemanticModelIR, plan: SemanticPlan) -> list[str]:
    errors: list[str] = []
    for name in plan.metrics:
        if model.metric(name) is None:
            errors.append(f"Unknown metric {name!r}.")
    for name in plan.dimensions:
        field = model.field(name)
        if field is None or field.kind.value != "dimension":
            errors.append(f"Unknown dimension {name!r}.")
    for item in plan.filters:
        if model.field(item.field) is None:
            errors.append(f"Unknown filter field {item.field!r}.")
        if item.operator not in {"=", "!=", ">", ">=", "<", "<=", "IN", "LIKE"}:
            errors.append(f"Unsupported filter operator {item.operator!r}.")
    for name in plan.named_filters:
        if model.named_filter(name) is None:
            errors.append(f"Unknown named filter {name!r}.")
    if plan.time and (model.field(plan.time.dimension) is None):
        errors.append(f"Unknown time dimension {plan.time.dimension!r}.")
    return errors


def _object_scores(
    question: str, values: list[tuple[str, tuple[str, ...], str]]
) -> list[tuple[float, str]]:
    question_words = set(question.split())
    scored: list[tuple[float, str]] = []
    for name, synonyms, description in values:
        terms = [_normalize(name), *(_normalize(item) for item in synonyms)]
        best = 0.0
        for term in terms:
            if not term:
                continue
            if term in question:
                best = max(best, 1.0)
            else:
                words = set(term.split())
                best = max(best, len(words & question_words) / max(len(words), 1))
        description_words = set(_normalize(description).split())
        if description_words:
            best = max(best, min(0.7, len(description_words & question_words) / 3))
        if best:
            scored.append((best, name))
    return sorted(scored, key=lambda item: (-item[0], item[1]))


def _select(scores: list[tuple[float, str]], *, threshold: float = 0.5) -> tuple[list[str], bool]:
    if not scores or scores[0][0] < threshold:
        return [], False
    top = scores[0][0]
    winners = [name for score, name in scores if score == top]
    return ([winners[0]] if len(winners) == 1 else winners, len(winners) > 1)


def _time_plan(
    question: str,
    model: SemanticModelIR,
    selected_metrics: list[str],
    fields: list[SemanticFieldIR],
) -> SemanticTime | None:
    lowered = question.lower()
    range_value = None
    compare = None
    if re.search(r"\b(last month|bulan lalu|previous month)\b", lowered):
        range_value = "previous_month"
    elif re.search(r"\b(this month|bulan ini|current month)\b", lowered):
        range_value = "current_month"
    elif re.search(r"\b(last 30 days|30 hari terakhir)\b", lowered):
        range_value = "last_30_days"
    if re.search(r"\b(compare|compared|versus|vs|dibanding|yoy|year over year)\b", lowered):
        compare = "previous_period"
    grain = next(
        (value for value in ("day", "week", "month", "quarter", "year") if value in lowered),
        None,
    )
    dimension = None
    if selected_metrics:
        metric = model.metric(selected_metrics[0])
        dimension = metric.default_time_dimension if metric else None
    if not dimension:
        match = next((field.name for field in fields if field.is_time), None)
        dimension = match
    if dimension and (range_value or compare or grain):
        return SemanticTime(dimension, grain=grain, range=range_value, compare=compare)
    return None


def _limit(question: str) -> int | None:
    match = re.search(r"\b(?:top|first|limit)\s+(\d{1,4})\b", question, re.I)
    return min(int(match.group(1)), 1000) if match else None


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower().replace("_", " ")))


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
