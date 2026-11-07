"""Tell a period without data apart from a period whose value is empty.

A governed query for December when the data ends in November returns no rows,
and a comparison with a year the data does not reach returns an empty
baseline. Both look like an answer ("no sales") but are not one: the view has
no observations there. When a dated result comes back empty, the first and
last day the caller can see for the metric are read through two governed
plans, so row filters and masks apply exactly as they did to the result, and
a requested period that lies wholly outside that window is reported instead
of the empty result.

The check runs only on an empty result, so a normal answer costs nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.modules.agents.semantic.planning import SemanticPlan, SemanticPlanError
from app.modules.agents.semantic.time_ranges import resolve_time_range

#: Wide enough for any business history; the planner's own range is still bounded.
WINDOW_RANGE = "2000-01-01..2099-12-31"

RunPlan = Callable[[dict[str, Any]], Awaitable[tuple[list, list, str | None]]]


@dataclass(frozen=True)
class DataWindow:
    first: date
    last: date


@dataclass(frozen=True)
class CoverageGap:
    """A requested period with no observations in the view."""

    window: DataWindow
    period: str  # "requested" or "comparison"
    start: date
    end: date  # inclusive

    def detail(self, metric: str) -> str:
        which = "The requested period" if self.period == "requested" else "The comparison period"
        return (
            f"{which} {self.start.isoformat()} to {self.end.isoformat()} has no data: "
            f"{metric} in this Semantic View covers {self.window.first.isoformat()} to "
            f"{self.window.last.isoformat()}. Tell the user the period is outside the data "
            "and give the covered range. Do not present an empty or zero result as the answer."
        )


def future_period(plan: SemanticPlan, today: date | None) -> str | None:
    """Why a period that starts after today has no data yet, or ``None``."""
    if today is None or plan.time is None or not plan.time.range:
        return None
    try:
        start = resolve_time_range(plan.time.range).start_date
    except SemanticPlanError:
        return None
    if start is None or start <= today:
        return None
    return (
        f"The requested period starts on {start.isoformat()}, after today "
        f"({today.isoformat()}), so no data exists for it yet. Tell the user; do not "
        "answer with another period."
    )


def looks_empty(columns: Sequence[Any], rows: Sequence[Sequence[Any]]) -> bool:
    """No rows, or a column with no value in any row (an empty comparison baseline)."""
    if not rows:
        return True
    width = max((len(row) for row in rows), default=0)
    return any(all(index >= len(row) or row[index] is None for row in rows)
               for index in range(width))


def window_plans(plan: SemanticPlan) -> list[dict[str, Any]] | None:
    """The plans that read the first and the last dated observation of the plan's metric."""
    if not plan.metrics or plan.time is None or not plan.time.dimension:
        return None
    base = {
        "metrics": [plan.metrics[0]],
        "dimensions": [],
        "filters": [],
        "named_filters": list(plan.named_filters),
        "time": {"dimension": plan.time.dimension, "grain": "day", "range": WINDOW_RANGE},
        "limit": 1,
    }
    return [
        {**base, "order_by": [{"field": plan.time.dimension, "direction": direction}]}
        for direction in ("asc", "desc")
    ]


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


async def data_window(plan: SemanticPlan, run_plan: RunPlan) -> DataWindow | None:
    """The first and last day with data for the plan's metric, as the caller sees them."""
    plans = window_plans(plan)
    if plans is None:
        return None
    days: list[date] = []
    for window_plan in plans:
        columns, rows, error = await run_plan(window_plan)
        if error or not rows:
            return None
        index = next(
            (i for i, name in enumerate(columns) if str(name) == plan.time.dimension), 0
        ) if plan.time else 0
        day = _day(rows[0][index]) if rows[0] else None
        if day is None:
            return None
        days.append(day)
    return DataWindow(first=days[0], last=days[1])


def _outside(window: DataWindow, start: date, end_exclusive: date) -> bool:
    return end_exclusive <= window.first or start > window.last


def coverage_gap(
    window: DataWindow,
    current: tuple[datetime, datetime],
    baseline: tuple[datetime, datetime] | None = None,
) -> CoverageGap | None:
    """The requested or comparison period that lies wholly outside the data window."""
    for period, bounds in (("requested", current), ("comparison", baseline)):
        if bounds is None:
            continue
        start, end = bounds[0].date(), bounds[1].date()
        # A half-open end at midnight excludes that day; a mid-day end ("today") includes it.
        end_exclusive = end if bounds[1].time() == datetime.min.time() else date.fromordinal(
            end.toordinal() + 1)
        if end_exclusive <= start:
            continue
        if _outside(window, start, end_exclusive):
            return CoverageGap(window=window, period=period, start=start,
                               end=date.fromordinal(end_exclusive.toordinal() - 1))
    return None


async def period_without_data(
    plan: SemanticPlan,
    columns: Sequence[Any],
    rows: Sequence[Sequence[Any]],
    time_context: Any,
    run_plan: RunPlan,
) -> CoverageGap | None:
    """When a dated result is empty, the requested period it fell outside of, if any."""
    if time_context is None or not looks_empty(columns, rows):
        return None
    window = await data_window(plan, run_plan)
    if window is None:
        return None
    current = (time_context.current.start, time_context.current.end)
    baseline = (
        (time_context.baseline.start, time_context.baseline.end)
        if getattr(time_context, "baseline", None) is not None else None
    )
    return coverage_gap(window, current, baseline)
