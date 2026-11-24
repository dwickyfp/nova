"""Semantic plans, their validation, and relationship paths between datasets."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import asdict, dataclass, replace
from dataclasses import field as dataclass_field
from datetime import date, timedelta
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
class SemanticReading:
    """How the plan read a measure word that more than one metric fits.

    The answer states it, so the person sees the reading and the other metrics
    they could ask for instead of being asked first.
    """

    text: str
    metric: str
    alternatives: tuple[str, ...] = ()


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
    offset: int = 0
    calculations: tuple[dict[str, Any], ...] = ()
    time_output: str | None = None
    fiscal_start_month: int = 1
    week_start: int = 0
    distinct: bool = False
    #: The reading of an ambiguous measure word; it explains the plan, it does not
    #: change what is computed, so two plans that compute the same thing are equal.
    readings: tuple[SemanticReading, ...] = dataclass_field(default=(), compare=False)

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
            time = harmonize_time(SemanticTime(
                dimension=str(raw_time["dimension"]),
                grain=_optional(raw_time.get("grain")),
                range=_optional(raw_time.get("range")),
                compare=_normalise_comparison(raw_time.get("compare")),
            ))
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
        readings = tuple(
            SemanticReading(
                text=str(item.get("text") or ""),
                metric=str(item.get("metric") or ""),
                alternatives=tuple(
                    dict.fromkeys(str(name) for name in item.get("alternatives") or [] if name)
                ),
            )
            for item in value.get("readings") or []
            if isinstance(item, dict) and item.get("text") and item.get("metric")
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
            offset=value.get("offset", 0),
            calculations=tuple(value.get("calculations") or []),
            time_output=value.get("time_output"),
            fiscal_start_month=value.get("fiscal_start_month", 1),
            week_start=value.get("week_start", 0),
            distinct=value.get("distinct", False),
            readings=readings,
        )
        if not plan.metrics and not plan.dimensions and not any(
            item.material for item in plan.unresolved_concepts
        ):
            # A plan of only material unresolved concepts is a question back, never executed.
            raise SemanticPlanError("Semantic plan needs at least one metric or dimension.")
        return plan

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        # Preserve the serialized shape of plans created before report extensions.
        for key, default in (
            ("offset", 0), ("calculations", ()), ("time_output", None),
            ("fiscal_start_month", 1), ("week_start", 0),
            ("distinct", False), ("readings", ()),
        ):
            if result[key] == default:
                result.pop(key)
        return result


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
    if type(plan.distinct) is not bool:
        errors.append("distinct must be a boolean.")
    elif plan.distinct and (
        not plan.dimensions or plan.metrics or plan.calculations or plan.transforms
        or plan.having or plan.top_n_per_group
    ):
        errors.append("distinct requires a dimension-only projection.")
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
    for reading in plan.readings:
        if reading.metric not in plan.metrics:
            errors.append(
                f"The reading of {reading.text!r} names {reading.metric!r}, which is not a "
                "selected metric."
            )
        for name in reading.alternatives:
            if model.metric(name) is None:
                errors.append(f"Unknown alternative metric {name!r}.")
    for item in plan.filters:
        if model.field(item.field) is None:
            errors.append(f"Unknown filter field {item.field!r}.")
        if item.operator not in {
            "=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "LIKE", "IS NULL", "IS NOT NULL"
        }:
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


_DATE_SPAN = re.compile(r"(\d{4})-(\d{2})-(\d{2})\.\.(\d{4})-(\d{2})-(\d{2})")


#: Comparison -> the calendar unit it steps by.
_COMPARISON_UNITS = {
    "week_over_week": "week", "month_over_month": "month",
    "quarter_over_quarter": "quarter", "year_over_year": "year",
}


#: The longest a single calendar unit can be, in days.
_UNIT_DAYS = {"week": 7, "month": 31, "quarter": 92}


def _whole_units(start: date, end: date, unit: str) -> int | None:
    """How many whole calendar ``unit``s ``start``..``end`` (inclusive) covers, if exact."""
    if unit == "week":
        days = (end - start).days + 1
        return days // 7 if days % 7 == 0 and start.weekday() == 0 else None
    months = {"month": 1, "quarter": 3, "year": 12}[unit]
    if start.day != 1 or (start.month - 1) % months:
        return None
    following = end + timedelta(days=1)
    if following.day != 1:
        return None
    span = (following.year - start.year) * 12 + following.month - start.month
    if span <= 0 or span % months or (following.month - 1) % months:
        return None
    return span // months


def _range_token(start: date, unit: str) -> str:
    if unit == "month":
        return f"{start.year:04d}-{start.month:02d}"
    if unit == "quarter":
        return f"{start.year:04d}-Q{(start.month - 1) // 3 + 1}"
    return f"{start.year:04d}"


def harmonize_time(time: SemanticTime | None) -> SemanticTime | None:
    """A period comparison in the shape the compiler can answer.

    Plans and intent frames name comparisons the compiler cannot run as written;
    each was a "could not be compiled safely" failure in the benchmark:

    * two named periods ("June and July", one range with previous_period or
      month_over_month) are those two periods as groups, not the range against
      the one before it;
    * a range that is one whole month, quarter or year written as dates
      (2025-07-01..2025-07-31) with that unit's comparison is that period;
    * a unit comparison over several periods ("week over week" for the last 8
      weeks) is a series by that unit; the change per period is computed from it.
    """
    if time is None or not time.range or not time.compare:
        return time
    from app.modules.agents.semantic.time_ranges import resolve_time_range

    compare = time.compare
    unit = _COMPARISON_UNITS.get(compare)
    span = _DATE_SPAN.fullmatch(time.range.strip())
    if span is not None:
        try:
            start = date(*(int(part) for part in span.groups()[:3]))
            end = date(*(int(part) for part in span.groups()[3:]))
        except ValueError:
            return time
        units = [unit] if unit else ["month", "quarter", "year"]
        for candidate in units:
            count = _whole_units(start, end, candidate) if end >= start else None
            if count == 2 and (unit or not time.grain):
                # Two named periods: each is its own group.
                return SemanticTime(time.dimension, grain=candidate, range=time.range)
            if count == 1 and unit and candidate != "week":
                return SemanticTime(time.dimension, grain=time.grain,
                                    range=_range_token(start, candidate), compare=compare)
            if count and count > 2 and unit:
                return SemanticTime(time.dimension, grain=unit, range=time.range)
        if not unit:
            return time
    if unit is None or unit == "year":
        return time
    try:
        window = resolve_time_range(time.range)
    except SemanticPlanError:
        return time
    if window.granularity == unit:
        return time
    recent = re.fullmatch(r"(?:last|past|previous)[_ -](\d{1,4})[_ -]([a-z]+?)s?",
                          time.range.strip().lower())
    longer = (
        (recent is not None and recent[2] == unit and int(recent[1]) > 1)
        or (span is not None and (end - start).days + 1 > _UNIT_DAYS[unit])
    )
    if longer:
        # last_8_weeks, or June to early July, week over week: the weekly series
        # answers it, and the change per week is computed from that series.
        return SemanticTime(time.dimension, grain=unit, range=time.range)
    if window.period is not None:
        # A range of another length: compare it with the period just before it.
        return SemanticTime(time.dimension, grain=time.grain, range=time.range,
                            compare="previous_period")
    return time


def harmonize_plan(plan: SemanticPlan) -> SemanticPlan:
    """The plan with its comparison harmonized and no ordering on an unselected period.

    A two-period comparison groups by period, so the time dimension is no output
    column: an ``order_by`` on it ("Order field 'order_date' is not selected")
    failed every retry once the user's period replaced the plan's grain.
    """
    time = harmonize_time(plan.time)
    order_by = plan.order_by
    if time is not None and not time.grain and time.dimension not in plan.dimensions:
        hidden = {time.dimension, plan.time_output}
        order_by = tuple(item for item in order_by if item.field not in hidden)
    if time == plan.time and order_by == plan.order_by:
        return plan
    return replace(plan, time=time, order_by=order_by)


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
