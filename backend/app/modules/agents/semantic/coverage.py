"""Tell a period without data apart from a period whose value is empty.

A governed query for December when the data ends in November returns no rows,
and a comparison with a year the data does not reach returns an empty
baseline. Both look like an answer ("no sales") but are not one: the view has
no observations there. When a dated result comes back empty, the first and
last day the caller can see for the metric are read through two governed
plans, so row filters and masks apply exactly as they did to the result, and
a requested period that lies wholly outside that window is reported instead
of the empty result.

The check runs only on an empty result. An answer over all the data reads the
same window, so it can name the dates it covers.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import date, datetime, timedelta
from typing import Any

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan, SemanticPlanError
from app.modules.agents.semantic.time_ranges import is_relative_range, resolve_time_range

#: The planner's mark for a request to see which dates the data covers.
DATA_PERIOD = "data_period"
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


@dataclass(frozen=True)
class DataAnchor:
    """The same question asked of the dates the data has, and how to say so."""

    plan: SemanticPlan
    #: For a range counted from the clock: the day after the last day with data.
    clock_day: date | None
    note: str


#: How many years back a month named without a year may be moved.
_MAX_YEAR_SHIFT = 50


def anchor_to_data(
    plan: SemanticPlan, gap: CoverageGap, anchor: str | None
) -> DataAnchor | None:
    """Ask a period that fell outside the data again, inside it, when the user meant that.

    ``anchor`` is the turn planner's reading of the user: ``"data"`` when they count
    from the data or name a month or day without a year, ``"today"`` when they name a
    year or count from today. A range counted from the clock ("the last 7 days") is
    counted from the last day with data instead, unless the user tied it to today. An
    explicit period moves by whole years only when the user named no year and exactly
    one such year lies in the data. Anything else stays a gap the agent reports.
    """
    if anchor == "today" or gap.period != "requested" or plan.time is None:
        return None
    value = plan.time.range
    if not value:
        return None
    last = gap.window.last
    if is_relative_range(value):
        return DataAnchor(plan=plan, clock_day=last + timedelta(days=1), note=(
            f" The data ends on {last.isoformat()}, so this period is counted back from "
            "that day, not from today. Say so first, naming the dates the figures cover."
        ))
    if anchor != "data":
        return None
    try:
        window = resolve_time_range(value)
    except SemanticPlanError:
        return None
    if window.start_date is None or window.end_date is None:
        return None
    fits = []
    for years in range(1, _MAX_YEAR_SHIFT + 1):
        shifted = _shift_years(value, years)
        try:
            moved = resolve_time_range(shifted)
        except SemanticPlanError:
            continue
        if moved.start_date is None or moved.end_date is None:
            continue
        if moved.start_date <= gap.window.last and moved.end_date > gap.window.first:
            fits.append(shifted)
    if len(fits) != 1:
        return None
    time = dataclass_replace(plan.time, range=fits[0])
    return DataAnchor(plan=dataclass_replace(plan, time=time), clock_day=None, note=(
        f" The user named no year; the only matching period in the data is {fits[0]}, "
        "so that year is used. Name the year in the answer."
    ))


def _shift_years(value: str, years: int) -> str:
    return re.sub(r"\d{4}", lambda match: str(int(match[0]) - years), value)


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


def window_plans(
    plan: SemanticPlan, dimension: str | None = None
) -> list[dict[str, Any]] | None:
    """The plans that read the first and the last dated observation of the plan's metric.

    ``dimension`` is the time field to read; by default the plan's own.
    """
    dimension = dimension or (plan.time.dimension if plan.time else None)
    if not plan.metrics or not dimension:
        return None
    base = {
        "metrics": [plan.metrics[0]],
        "dimensions": [],
        "filters": [],
        "named_filters": list(plan.named_filters),
        "time": {"dimension": dimension, "grain": "day", "range": WINDOW_RANGE},
        "limit": 1,
    }
    return [
        {**base, "order_by": [{"field": dimension, "direction": direction}]}
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


async def data_window(
    plan: SemanticPlan, run_plan: RunPlan, dimension: str | None = None
) -> DataWindow | None:
    """The first and last day with data for the plan's metric, as the caller sees them."""
    plans = window_plans(plan, dimension)
    if plans is None:
        return None
    field = plans[0]["time"]["dimension"]
    days: list[date] = []
    # The first and the last day are independent one-row reads: run them together.
    for columns, rows, error in await asyncio.gather(*(run_plan(item) for item in plans)):
        if error or not rows:
            return None
        index = next((i for i, name in enumerate(columns) if str(name) == field), 0)
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


async def covered_period(
    plan: SemanticPlan, model: SemanticModelIR, run_plan: RunPlan
) -> DataWindow | None:
    """The days the data covers, for an answer over all the data.

    A plan without a requested period answers for everything there is, so the
    answer says which dates that is ("1 May to 4 November 2026") instead of
    leaving the reader to guess or saying it could not be confirmed. A dated
    question already names its period and reads nothing more. The metric's own
    time field is read as the caller, through two one-row governed plans.
    """
    if not plan.metrics or (plan.time is not None and plan.time.range):
        return None
    metric = model.metric(plan.metrics[0])
    dimension = (plan.time.dimension if plan.time else None) or (
        metric.default_time_dimension if metric is not None else None
    )
    if not dimension:
        return None
    return await data_window(plan, run_plan, dimension)
