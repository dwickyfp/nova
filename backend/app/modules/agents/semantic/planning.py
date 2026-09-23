"""Semantic plans, lexical planning, relationship paths, and confidence."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Any

from app.modules.agents.semantic.ir import SemanticFieldIR, SemanticModelIR, SemanticRelationshipIR


class SemanticPlanError(ValueError):
    pass


class TimeComparison(StrEnum):
    NONE = "none"
    PREVIOUS_PERIOD = "previous_period"
    YEAR_OVER_YEAR = "year_over_year"
    QUARTER_OVER_QUARTER = "quarter_over_quarter"
    MONTH_OVER_MONTH = "month_over_month"
    WEEK_OVER_WEEK = "week_over_week"
    CUSTOM = "custom"


@dataclass(frozen=True)
class UnresolvedConcept:
    text: str
    type_hint: str = "unknown"
    material: bool = True


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
    unresolved_concepts: tuple[UnresolvedConcept, ...] = ()

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
                compare=_normalise_comparison(raw_time.get("compare")),
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
        unresolved = tuple(
            UnresolvedConcept(
                text=str(item.get("text") or ""),
                type_hint=str(item.get("type_hint") or "unknown"),
                material=bool(item.get("material", True)),
            )
            for item in value.get("unresolved_concepts") or []
            if isinstance(item, dict) and item.get("text")
        )
        plan = cls(
            metrics=tuple(str(item) for item in value.get("metrics") or []),
            dimensions=tuple(str(item) for item in value.get("dimensions") or []),
            filters=filters,
            named_filters=tuple(str(item) for item in value.get("named_filters") or []),
            time=time,
            order_by=orders,
            limit=limit,
            unresolved_concepts=unresolved,
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
    components: dict[str, str] | None = None
    unresolved_count: int = 0
    ambiguity_count: int = 0


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
        candidates = list({tuple(rel.name for rel in path): path for path in paths}.values())
        if preferred_names:
            preferred = [
                path for path in candidates if tuple(rel.name for rel in path) == preferred_names
            ]
            if preferred:
                return RelationshipPath(preferred[0])
        candidates.sort(
            key=lambda path: (
                -sum(1 for rel in path if rel.preferred),
                tuple(rel.name for rel in path),
            )
        )
        best = candidates[0]
        rank = sum(1 for rel in best if rel.preferred)
        return RelationshipPath(
            best,
            ambiguous=sum(1 for path in candidates if sum(rel.preferred for rel in path) == rank)
            > 1,
        )


class SemanticPlanner:
    """Exact/synonym planner with explicit unresolved-concept accounting."""

    def latest_month_with_data(
        self, model: SemanticModelIR, question: str
    ) -> SemanticPlan | None:
        from app.modules.agents.semantic.guidance import (
            enforce_routing_guidance,
            required_named_filters,
        )

        normalized = _normalize(question)
        match = re.fullmatch(
            r"(?:(?:what|which) is|show me)?\s*(?:the )?"
            r"(?:latest|most recent|newest) month (?:with|containing) data "
            r"(?:for|of) (.+)",
            normalized,
        ) or re.fullmatch(
            r"(?:apa|tampilkan)?\s*bulan (?:terbaru|terakhir) "
            r"(?:yang )?(?:punya|memiliki|dengan|ada) data "
            r"(?:untuk|dari) (.+)",
            normalized,
        )
        if match is None:
            return None
        requested_metric = match[1]
        matching_metrics = [
            metric
            for metric in model.metrics
            if requested_metric
            in {_normalize(name) for name in (metric.name, *metric.synonyms)}
        ]
        if len(matching_metrics) != 1:
            return None
        metric = matching_metrics[0]
        time_field = model.field(metric.default_time_dimension or "")
        if time_field is None or not time_field.is_time:
            return None
        enforce_routing_guidance(model, question, allow_natural_language=True)
        named_filters = required_named_filters(model, allow_natural_language=True)
        return SemanticPlan(
            metrics=(metric.name,),
            named_filters=named_filters,
            time=SemanticTime(time_field.name, grain="month"),
            order_by=(SemanticOrder(time_field.name),),
            limit=1,
        )

    def follow_up(
        self,
        model: SemanticModelIR,
        question: str,
        prior: SemanticPlan,
        *,
        literal_candidates: dict[str, list[str] | tuple[str, ...]] | None = None,
    ) -> PlannedSemantics:
        if validate_plan(model, prior):
            raise SemanticPlanError("Previous semantic state is no longer available.")
        request = " ".join((*prior.metrics, question))
        planned = self.plan(model, request, literal_candidates=literal_candidates)
        if planned.plan is None or planned.confidence.unresolved_count:
            return planned
        current = planned.plan
        filters = {item.field: item for item in prior.filters}
        filters.update({item.field: item for item in current.filters})
        time = current.time or prior.time
        if current.time and prior.time:
            time = replace(
                current.time,
                range=current.time.range or prior.time.range,
                grain=current.time.grain or prior.time.grain,
            )
        return replace(
            planned,
            plan=replace(
                current,
                dimensions=current.dimensions or prior.dimensions,
                filters=tuple(filters.values()),
                named_filters=tuple(dict.fromkeys((*prior.named_filters, *current.named_filters))),
                time=time,
            ),
        )

    def plan(
        self,
        model: SemanticModelIR,
        question: str,
        *,
        literal_candidates: dict[str, list[str] | tuple[str, ...]] | None = None,
    ) -> PlannedSemantics:
        from app.modules.agents.semantic.guidance import (
            enforce_routing_guidance,
            required_named_filters,
        )

        enforce_routing_guidance(model, question)
        required_filters = required_named_filters(model)
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
        if metric_ambiguous:
            metric_ambiguous = _overlapping_matches(
                normalized,
                [
                    (item.name, item.synonyms)
                    for item in model.metrics
                    if item.name in selected_metrics
                ],
            )
        if dimension_ambiguous:
            dimension_ambiguous = _overlapping_matches(
                normalized,
                [
                    (item.name, item.synonyms)
                    for item in dimension_fields
                    if item.name in selected_dimensions
                ],
            )

        filters: list[SemanticFilter] = []
        literal_scores: list[float] = []
        resolved_literal_terms: set[str] = set()
        from app.modules.agents.semantic.runtime import LiteralResolver

        resolver = LiteralResolver()
        for field in dimension_fields:
            search_values = tuple((literal_candidates or {}).get(field.name, ()))
            for candidate in (*field.sample_values, *search_values):
                candidate_normalized = _normalize(candidate)
                if not candidate_normalized or not _contains(normalized, candidate_normalized):
                    continue
                resolved = resolver.resolve(
                    candidate, field, search_candidates=search_values, limit=1
                )
                if resolved and resolved[0].score >= 0.72:
                    filters.append(SemanticFilter(field.name, "=", resolved[0].value))
                    literal_scores.append(resolved[0].score)
                    resolved_literal_terms.update(candidate_normalized.split())

        values_by_field: dict[str, set[str]] = {}
        fields_by_value: dict[str, set[str]] = {}
        for item in filters:
            value = _normalize(str(item.value))
            values_by_field.setdefault(item.field, set()).add(value)
            fields_by_value.setdefault(value, set()).add(item.field)
        conflicts = {
            value
            for value, fields in fields_by_value.items()
            if len(fields) > 1 or any(len(values_by_field[field]) > 1 for field in fields)
        }
        if conflicts:
            filters = [item for item in filters if _normalize(str(item.value)) not in conflicts]
            resolved_literal_terms = {
                word for item in filters for word in _normalize(str(item.value)).split()
            }

        named_filters = tuple(
            item.name
            for item in model.named_filters
            if any(
                _contains(normalized, _normalize(term))
                for term in (item.name, *item.synonyms)
                if term
            )
        )
        named_filters = tuple(dict.fromkeys((*required_filters, *named_filters)))
        time = _time_plan(question, model, selected_metrics, dimension_fields)
        limit = _limit(question)
        order = (SemanticOrder(selected_metrics[0]),) if limit and selected_metrics else ()

        unresolved: list[str] = []
        if metric_ambiguous:
            unresolved.append("metric")
        if dimension_ambiguous:
            unresolved.append("dimension")
        if not selected_metrics and model.metrics:
            unresolved.append("metric")
        residual = _material_residual(
            question,
            model=model,
            selected_metrics=selected_metrics,
            selected_dimensions=selected_dimensions,
            named_filters=named_filters,
            resolved_literal_terms=resolved_literal_terms,
        )
        if residual:
            literal = " ".join(residual)
            candidates = []
            for field in dimension_fields:
                matches = resolver.resolve(
                    literal,
                    field,
                    search_candidates=tuple((literal_candidates or {}).get(field.name, ())),
                    limit=2,
                )
                for match in matches:
                    if match.score >= 0.72:
                        candidates.append((match.score, field.name, match.value))
            candidates.sort(reverse=True)
            if candidates and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.15):
                score, name, value = candidates[0]
                filters.append(SemanticFilter(name, "=", value))
                literal_scores.append(score)
                residual = []
        unresolved_concepts = tuple(
            UnresolvedConcept(text=item, type_hint="literal_or_dimension_filter")
            for item in residual
        )
        unresolved.extend(item.text for item in unresolved_concepts)

        metric_score = metric_scores[0][0] if metric_scores else 0.0
        dimension_required = bool(selected_dimensions or _dimension_language(question))
        literal_required = bool(filters or unresolved_concepts)
        dimension_score = dimension_scores[0][0] if dimension_required and dimension_scores else 0.0
        literal_score = min(literal_scores) if filters else 0.0
        components = {
            "metric": "ambiguous"
            if metric_ambiguous
            else "resolved"
            if selected_metrics
            else "unresolved",
            "dimension": (
                "ambiguous"
                if dimension_ambiguous
                else "resolved"
                if selected_dimensions
                else "unresolved"
                if dimension_required
                else "not_required"
            ),
            "literal": (
                "resolved" if filters else "unresolved" if literal_required else "not_required"
            ),
            "relationship": "not_required",
        }
        signals = {
            "metric_match": metric_score,
            "dimension_match": dimension_score,
            "literal_resolution": literal_score,
            "ambiguity": 0.0 if unresolved else 1.0,
        }
        applicable = [metric_score]
        if dimension_required:
            applicable.append(dimension_score)
        if literal_required:
            applicable.append(literal_score)
        score = sum(applicable) / max(len(applicable), 1)
        ambiguity_count = int(metric_ambiguous) + int(dimension_ambiguous)
        if unresolved or ambiguity_count:
            score = min(score, 0.49 if unresolved_concepts else 0.74)
            level = "low" if score < 0.75 or unresolved_concepts else "medium"
        else:
            level = "high" if score >= 0.8 else "medium" if score >= 0.55 else "low"
        confidence = SemanticConfidence(
            score,
            level,
            signals,
            tuple(unresolved),
            components,
            len(unresolved_concepts),
            ambiguity_count,
        )
        if (metric_ambiguous or dimension_ambiguous or not selected_metrics) and score < 0.75:
            return PlannedSemantics(
                None,
                confidence,
                "The semantic request is ambiguous or contains unresolved constraints. "
                "Specify the governed metric or filter value to use.",
            )
        return PlannedSemantics(
            SemanticPlan(
                metrics=tuple(selected_metrics),
                dimensions=tuple(selected_dimensions),
                filters=tuple(filters),
                named_filters=named_filters,
                time=time,
                order_by=order,
                limit=limit,
                unresolved_concepts=unresolved_concepts,
            ),
            confidence,
        )


def validate_plan(model: SemanticModelIR, plan: SemanticPlan) -> list[str]:
    errors: list[str] = []
    material_unresolved = [item.text for item in plan.unresolved_concepts if item.material]
    if material_unresolved:
        errors.append("Unresolved material concepts: " + ", ".join(material_unresolved))
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
    if plan.time and model.field(plan.time.dimension) is None:
        errors.append(f"Unknown time dimension {plan.time.dimension!r}.")
    if plan.time and plan.time.compare:
        try:
            TimeComparison(plan.time.compare)
        except ValueError:
            errors.append(f"Unsupported time comparison {plan.time.compare!r}.")
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
            if _contains(question, term):
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
    year = re.search(r"\b(?:for|in|untuk|tahun|year)\s+([12]\d{3})(?![\d/-])\b", lowered)
    range_value = year[1] if year else None
    compare = None
    if re.search(r"\b(this month|bulan ini|current month)\b", lowered):
        range_value = "current_month"
    elif re.search(r"\b(last month|bulan lalu|previous month)\b", lowered):
        range_value = "previous_month"
    elif re.search(r"\b(this quarter|current quarter|kuartal ini)\b", lowered):
        range_value = "current_quarter"
    elif re.search(r"\b(this week|current week|minggu ini)\b", lowered):
        range_value = "current_week"
    elif re.search(r"\b(last 30 days|30 hari terakhir)\b", lowered):
        range_value = "last_30_days"
    if re.search(r"\b(yoy|year over year|tahun lalu)\b", lowered):
        compare = TimeComparison.YEAR_OVER_YEAR.value
    elif re.search(r"\b(qoq|quarter over quarter|kuartal lalu)\b", lowered):
        compare = TimeComparison.QUARTER_OVER_QUARTER.value
    elif re.search(r"\b(mom|month over month|dibanding bulan lalu|vs last month)\b", lowered):
        compare = TimeComparison.MONTH_OVER_MONTH.value
    elif re.search(r"\b(wow|week over week|dibanding minggu lalu)\b", lowered):
        compare = TimeComparison.WEEK_OVER_WEEK.value
    elif (
        range_value
        and not year
        and re.search(r"\b(compare|compared|versus|vs|dibanding)\b", lowered)
    ):
        compare = TimeComparison.PREVIOUS_PERIOD.value
    grain = next(
        (
            value
            for value in ("day", "week", "month", "quarter", "year")
            if (
                re.search(rf"\b(?:by|per)\s+{value}\b|\b{value}ly\b", lowered)
                if year
                else value in lowered
            )
        ),
        None,
    )
    dimension = None
    if selected_metrics:
        metric = model.metric(selected_metrics[0])
        dimension = metric.default_time_dimension if metric else None
    if not dimension:
        dimension = next((field.name for field in fields if field.is_time), None)
    if dimension and (range_value or compare or grain):
        return SemanticTime(dimension, grain=grain, range=range_value, compare=compare)
    return None


def _limit(question: str) -> int | None:
    match = re.search(r"\b(?:top|first|limit)\s+(\d{1,4})\b", question, re.I)
    return min(int(match.group(1)), 1000) if match else None


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower().replace("_", " ")))


_RESIDUAL_STOPWORDS = {
    "a",
    "and",
    "apa",
    "berapa",
    "by",
    "bandingkan",
    "compare",
    "compared",
    "current",
    "dengan",
    "dari",
    "di",
    "did",
    "for",
    "hari",
    "how",
    "hanya",
    "in",
    "ini",
    "last",
    "month",
    "much",
    "of",
    "over",
    "previous",
    "quarter",
    "show",
    "tahun",
    "the",
    "this",
    "top",
    "total",
    "vs",
    "week",
    "what",
    "year",
    "lalu",
    "bulan",
    "minggu",
    "yoy",
    "mom",
    "qoq",
    "wow",
    "please",
    "tolong",
    "cek",
    "dibanding",
    "versus",
    "kuartal",
    "was",
    "were",
    "is",
    "are",
    "we",
    "our",
    "now",
    "sekarang",
    "saja",
    "only",
    "customers",
    "customer",
    "grouped",
    "breakdown",
    "per",
    "dan",
    "untuk",
}


def _material_residual(
    question: str,
    *,
    model: SemanticModelIR,
    selected_metrics: list[str],
    selected_dimensions: list[str],
    named_filters: tuple[str, ...],
    resolved_literal_terms: set[str],
) -> list[str]:
    consumed = set(_RESIDUAL_STOPWORDS) | resolved_literal_terms
    consumed.update(_normalize(model.name).split())
    for metric_name in selected_metrics:
        metric = model.metric(metric_name)
        if metric:
            consumed.update(_normalize(" ".join((metric.name, *metric.synonyms))).split())
    for dimension_name in selected_dimensions:
        field = model.field(dimension_name)
        if field:
            consumed.update(_normalize(" ".join((field.name, *field.synonyms))).split())
    for filter_name in named_filters:
        named = model.named_filter(filter_name)
        if named:
            consumed.update(_normalize(" ".join((named.name, *named.synonyms))).split())
    output: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", question):
        normalized = _normalize(token)
        if not normalized or set(normalized.split()) <= consumed or normalized.isdigit():
            continue
        output.append(token)
    return list(dict.fromkeys(output))


def _dimension_language(question: str) -> bool:
    return bool(re.search(r"\b(by|per|grouped by|breakdown by)\b", question, re.I))


def _contains(question: str, term: str) -> bool:
    return bool(term) and f" {term} " in f" {question} "


def _overlapping_matches(question: str, objects: list[tuple[str, tuple[str, ...]]]) -> bool:
    used: set[str] = set()
    for name, synonyms in objects:
        matches = {
            term
            for item in (name, *synonyms)
            if (term := _normalize(item)) and _contains(question, term)
        }
        if not matches or matches & used:
            return True
        used.update(matches)
    return False


def _normalise_comparison(value: Any) -> str | None:
    text = _optional(value)
    if not text or text.lower() == "none":
        return None
    aliases = {
        "yoy": TimeComparison.YEAR_OVER_YEAR.value,
        "qoq": TimeComparison.QUARTER_OVER_QUARTER.value,
        "mom": TimeComparison.MONTH_OVER_MONTH.value,
        "wow": TimeComparison.WEEK_OVER_WEEK.value,
    }
    return aliases.get(text.lower(), text.lower())


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
