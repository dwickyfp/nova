"""Semantic plans, their validation, and relationship paths between datasets."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.modules.agents.semantic.ir import SemanticModelIR, SemanticRelationshipIR

if TYPE_CHECKING:
    pass


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
