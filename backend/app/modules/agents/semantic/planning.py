"""Semantic plans, lexical planning, relationship paths, and confidence."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.modules.agents.semantic.ir import SemanticFieldIR, SemanticModelIR, SemanticRelationshipIR

if TYPE_CHECKING:
    from app.modules.agents.semantic.time_ranges import TimePhrase


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


TRANSFORM_KINDS = ("share_of_total", "running_total", "rank")
HAVING_OPERATORS = ("=", "!=", ">", ">=", "<", "<=")


@dataclass(frozen=True)
class SemanticHaving:
    """A condition on an aggregated metric ("cities with revenue above 500 juta")."""

    metric: str
    operator: str
    value: Any


@dataclass(frozen=True)
class SemanticTransform:
    """A window calculation over the aggregated result."""

    metric: str
    kind: str


@dataclass(frozen=True)
class SemanticTopN:
    """Keep the top ``n`` rows by ``metric`` within each ``partition_by`` group."""

    n: int
    partition_by: tuple[str, ...]
    metric: str


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
    having: tuple[SemanticHaving, ...] = ()
    transforms: tuple[SemanticTransform, ...] = ()
    top_n_per_group: SemanticTopN | None = None

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
        having = tuple(
            SemanticHaving(
                metric=str(item.get("metric") or ""),
                operator=str(item.get("operator") or ">"),
                value=item.get("value"),
            )
            for item in value.get("having") or []
            if isinstance(item, dict)
        )
        transforms = tuple(
            SemanticTransform(
                metric=str(item.get("metric") or ""), kind=str(item.get("kind") or "")
            )
            for item in value.get("transforms") or []
            if isinstance(item, dict)
        )
        raw_top = value.get("top_n_per_group")
        top_n = None
        if isinstance(raw_top, dict) and raw_top.get("n"):
            top_n = SemanticTopN(
                n=min(max(int(raw_top["n"]), 1), 100),
                partition_by=tuple(str(item) for item in raw_top.get("partition_by") or []),
                metric=str(raw_top.get("metric") or ""),
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
            having=having,
            transforms=transforms,
            top_n_per_group=top_n,
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
        from app.modules.agents.semantic.time_ranges import parse_time_phrase

        enforce_routing_guidance(model, question)
        required_filters = required_named_filters(model)
        normalized = _normalize(question)
        phrase = parse_time_phrase(question)
        # Time words ("year to date", "last 7 days") never name a metric or a
        # grouping dimension; scoring them matched date fields as dimensions.
        scoring = (
            " ".join(word for word in normalized.split() if word not in phrase.consumed)
            or normalized
        )
        dimension_fields = [
            field
            for dataset in model.datasets
            for field in dataset.fields
            if field.kind.value == "dimension"
        ]
        metric_scores, dimension_scores = _phrase_scores(
            scoring,
            [(metric.name, metric.synonyms, metric.description) for metric in model.metrics],
            [(field.name, field.synonyms, field.description) for field in dimension_fields],
        )
        selected_metrics, metric_ambiguous = _select(metric_scores)
        selected_dimensions, dimension_ambiguous = _select(dimension_scores, threshold=0.42)
        if metric_ambiguous:
            metric_ambiguous = _overlapping_matches(
                scoring,
                [
                    (item.name, item.synonyms)
                    for item in model.metrics
                    if item.name in selected_metrics
                ],
            )
        if dimension_ambiguous:
            dimension_ambiguous = _overlapping_matches(
                scoring,
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
        if conflicts and selected_metrics:
            # A value shared by two fields ("Mobile App" is a sales and a
            # marketing channel) belongs to the field the metric can reach.
            base = next(
                (metric.base_dataset for metric in model.metrics
                 if metric.name == selected_metrics[0]),
                "",
            )
            reachable = {
                field.name for field in dimension_fields if _reachable(model, base, field.dataset)
            }
            kept = [item for item in filters if item.field in reachable]
            if kept and len(kept) < len(filters):
                filters = kept
                values_by_field = {}
                fields_by_value = {}
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
        named_terms = {
            word
            for item in model.named_filters if item.name in named_filters
            for term in (item.name, *item.synonyms)
            for word in _normalize(term).split()
        }
        if named_terms:
            # "Completed orders" names the governed filter; the same word as a
            # raw value filter would apply the condition twice.
            filters = [
                item for item in filters
                if not set(_normalize(str(item.value)).split()) <= named_terms
            ]
        if not _dimension_language(scoring):
            # "Omzet segmen Enterprise" qualifies a value; it is not a grouping.
            filtered_fields = {item.field for item in filters if item.operator == "="}
            selected_dimensions = [
                name for name in selected_dimensions if name not in filtered_fields
            ]
        time, time_terms, missing_period = _time_plan(
            phrase, model, selected_metrics, dimension_fields
        )
        ranked = requested_rank(question)
        limit = ranked[0] if ranked else None
        order = (
            (SemanticOrder(selected_metrics[0], ranked[1]),)
            if ranked and selected_metrics else ()
        )

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
            selected_dimensions=[
                *selected_dimensions, *(item.field for item in filters)
            ],
            named_filters=named_filters,
            resolved_literal_terms=resolved_literal_terms | time_terms,
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
        if missing_period:
            unresolved_concepts = (
                *unresolved_concepts,
                UnresolvedConcept(text="comparison period", type_hint="time_range"),
            )
        unresolved.extend(item.text for item in unresolved_concepts)

        metric_score = metric_scores[0][0] if metric_scores else 0.0
        dimension_required = bool(selected_dimensions or _dimension_language(scoring))
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
    selected = set(plan.metrics)
    for condition in plan.having:
        if condition.metric not in selected:
            errors.append(f"having uses {condition.metric!r}, which is not a selected metric.")
        if condition.operator not in HAVING_OPERATORS:
            errors.append(f"Unsupported having operator {condition.operator!r}.")
        if isinstance(condition.value, bool) or not isinstance(condition.value, int | float):
            errors.append("having compares a metric with a number.")
    for transform in plan.transforms:
        if transform.metric not in selected:
            errors.append(f"Transform uses {transform.metric!r}, which is not a selected metric.")
        if transform.kind not in TRANSFORM_KINDS:
            errors.append(f"Unsupported transform {transform.kind!r}.")
        if transform.kind == "running_total" and not (plan.time and plan.time.grain):
            errors.append("running_total needs a time grain to order by.")
    if plan.top_n_per_group is not None:
        top = plan.top_n_per_group
        if top.metric not in selected:
            errors.append("top_n_per_group ranks by a metric that is not selected.")
        if not top.partition_by or not set(top.partition_by) < set(plan.dimensions):
            errors.append(
                "top_n_per_group partitions by some, but not all, selected dimensions."
            )
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


def _word_matches(word: str, term_word: str) -> bool:
    """A question word matches a catalog word, allowing a plain English plural."""
    return word == term_word or _singular(word) == term_word


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _phrase_scores(
    question: str,
    metrics: list[tuple[str, tuple[str, ...], str]],
    dimensions: list[tuple[str, tuple[str, ...], str]],
) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
    """Score metrics and dimensions so that each question word is used once.

    Exact phrase matches are allocated longest first: "sales channel" claims
    both words before the metric synonym "sales" can, and "product revenue"
    before "revenue". Partial and description overlap is then scored only on
    words no exact match claimed, so it cannot re-use a word either.
    """
    words = question.split()
    matches: list[tuple[int, int, str, str]] = []
    for kind, values in (("metric", metrics), ("dimension", dimensions)):
        for name, synonyms, _description in values:
            for term in (_normalize(name), *(_normalize(item) for item in synonyms)):
                term_words = term.split()
                if not term_words:
                    continue
                for start in range(len(words) - len(term_words) + 1):
                    if all(
                        _word_matches(words[start + offset], term_word)
                        for offset, term_word in enumerate(term_words)
                    ):
                        matches.append((start, start + len(term_words), kind, name))
    matches.sort(key=lambda item: (-(item[1] - item[0]), item[0], item[2], item[3]))
    claimed: list[tuple[int, int]] = []
    exact: dict[str, set[str]] = {"metric": set(), "dimension": set()}
    for start, end, kind, name in matches:
        overlap = [span for span in claimed if span[0] < end and start < span[1]]
        # The same span matched by two objects of equal length stays ambiguous.
        if overlap and overlap != [(start, end)]:
            continue
        claimed.append((start, end))
        exact[kind].add(name)
    used = {index for start, end in claimed for index in range(start, end)}
    remaining = {_singular(word) for index, word in enumerate(words) if index not in used}

    def fuzzy(kind: str, values: list[tuple[str, tuple[str, ...], str]]) -> list[tuple[float, str]]:
        scored: list[tuple[float, str]] = []
        for name, synonyms, description in values:
            if name in exact[kind]:
                scored.append((1.0, name))
                continue
            best = 0.0
            for term in (_normalize(name), *(_normalize(item) for item in synonyms)):
                term_words = {_singular(word) for word in term.split()}
                if term_words:
                    best = max(best, min(0.99, len(term_words & remaining) / len(term_words)))
            description_words = {_singular(word) for word in _normalize(description).split()}
            if description_words:
                best = max(best, min(0.7, len(description_words & remaining) / 3))
            if best:
                scored.append((best, name))
        return sorted(scored, key=lambda item: (-item[0], item[1]))

    return fuzzy("metric", metrics), fuzzy("dimension", dimensions)


def _reachable(model: SemanticModelIR, source: str, target: str) -> bool:
    if not source or source == target:
        return bool(source)
    try:
        SemanticGraph(model).path(source, target)
    except SemanticPlanError:
        return False
    return True


def _select(scores: list[tuple[float, str]], *, threshold: float = 0.5) -> tuple[list[str], bool]:
    if not scores or scores[0][0] < threshold:
        return [], False
    top = scores[0][0]
    winners = [name for score, name in scores if score == top]
    return ([winners[0]] if len(winners) == 1 else winners, len(winners) > 1)


def _time_plan(
    phrase: TimePhrase,
    model: SemanticModelIR,
    selected_metrics: list[str],
    fields: list[SemanticFieldIR],
) -> tuple[SemanticTime | None, frozenset[str], bool]:
    """The question's time constraint, the words it explained, and whether a
    requested comparison is missing the period it compares."""
    dimension = None
    if selected_metrics:
        metric = model.metric(selected_metrics[0])
        dimension = metric.default_time_dimension if metric else None
    if not dimension:
        dimension = next((field.name for field in fields if field.is_time), None)
    missing_period = bool(phrase.compare and not phrase.range)
    if dimension and (phrase.range or phrase.grain) and not missing_period:
        time = SemanticTime(
            dimension, grain=phrase.grain, range=phrase.range, compare=phrase.compare
        )
        return time, phrase.consumed, False
    return None, phrase.consumed, missing_period


_RANK_BEFORE = re.compile(
    r"\b(?P<word>top|first|limit|bottom|lowest|highest|largest|smallest)\s+(?P<n>\d{1,4})\b",
    re.I,
)
_RANK_AFTER = re.compile(
    r"\b(?P<n>\d{1,3})\s+(?:(?!ribu|juta|miliar|triliun)[a-z]+\s+)?(?P<word>teratas|terbesar|tertinggi|terbanyak|terlaris|"
    r"besar|terbawah|terendah|terkecil|tersedikit)\b",
    re.I,
)
_ASCENDING_RANK = frozenset(
    {"bottom", "lowest", "smallest", "terbawah", "terendah", "terkecil", "tersedikit"}
)


_THRESHOLD = re.compile(
    r"(?P<op>>=|<=|>|<|\bdi\s+atas\b|\blebih\s+dari\b|\bmelebihi\b|\bover\b|\babove\b|"
    r"\bmore\s+than\b|\bgreater\s+than\b|\bexceeding\b|\bminimal\b|\bsetidaknya\b|"
    r"\bat\s+least\b|\bdi\s+bawah\b|\bkurang\s+dari\b|\bbelow\b|\bunder\b|"
    r"\bless\s+than\b|\bmaksimal\b|\bat\s+most\b)"
    r"\s*(?:rp\.?\s*|idr\s*|\$\s*)?(?P<n>\d+(?:[.,]\d+)*)\s*"
    r"(?P<scale>ribu|rb|juta|jt|miliar|milyar|triliun|thousand|million|billion|trillion|"
    r"[kmb](?![a-z]))?(?P<after>\s*[a-z]*)",
    re.I,
)
_THRESHOLD_OPERATORS = {
    ">": ">", ">=": ">=", "<": "<", "<=": "<=", "di atas": ">", "lebih dari": ">",
    "melebihi": ">", "over": ">", "above": ">", "more than": ">", "greater than": ">",
    "exceeding": ">", "minimal": ">=", "setidaknya": ">=", "at least": ">=",
    "di bawah": "<", "kurang dari": "<", "below": "<", "under": "<", "less than": "<",
    "maksimal": "<=", "at most": "<=",
}
_THRESHOLD_SCALES = {
    "ribu": 10**3, "rb": 10**3, "thousand": 10**3, "k": 10**3,
    "juta": 10**6, "jt": 10**6, "million": 10**6, "m": 10**6,
    "miliar": 10**9, "milyar": 10**9, "billion": 10**9, "b": 10**9,
    "triliun": 10**12, "trillion": 10**12,
}
_PERIOD_UNIT = re.compile(
    r"\s*(?:days?|weeks?|months?|quarters?|years?|hari|minggu|pekan|bulan|kuartal|"
    r"triwulan|tahun|persen|percent)\b|\s*%",
    re.I,
)


def requested_threshold(question: str) -> tuple[str, int | float] | None:
    """``(operator, value)`` for "di atas 1 miliar", "more than 500k", "< 10 juta".

    A count of periods ("over 3 months") or a percentage is not a threshold.
    """
    for match in _THRESHOLD.finditer(question):
        tail = question[match.end("n"):]
        if not match.group("scale") and _PERIOD_UNIT.match(tail):
            continue
        digits = match.group("n")
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", digits):
            number = float(re.sub(r"[.,]", "", digits))
        else:
            number = float(digits.replace(",", "."))
        scale = (match.group("scale") or "").lower()
        number *= _THRESHOLD_SCALES.get(scale, 1)
        operator = _THRESHOLD_OPERATORS[" ".join(match.group("op").lower().split())]
        return operator, int(number) if number.is_integer() else number
    return None


def requested_rank(question: str) -> tuple[int, str] | None:
    """``(n, direction)`` for "top 5", "bottom 3", "5 kota teratas", "3 terendah"."""
    match = _RANK_BEFORE.search(question) or _RANK_AFTER.search(question)
    if not match:
        return None
    direction = "asc" if match.group("word").lower() in _ASCENDING_RANK else "desc"
    return min(max(int(match.group("n")), 1), 1000), direction


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
    "many",
    "give",
    "tell",
    "me",
    "list",
    "get",
    "which",
    "do",
    "does",
    "based",
    "on",
    "berdasarkan",
    "berapakah",
    "coba",
    "lihat",
    "tampilkan",
    "ada",
    "yang",
    "ya",
    "dong",
    "aja",
    "jumlah",
    "nilai",
    "sum",
    "amount",
    "at",
    "to",
    "from",
    "during",
    "selama",
    "pada",
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
        words = set(normalized.split())
        if (
            not normalized or normalized.isdigit()
            or words <= consumed or {_singular(word) for word in words} <= consumed
        ):
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
