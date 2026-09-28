"""One time-range grammar for semantic plans, intent frames, and the compiler.

A ``SemanticTime.range`` is a string so plans stay portable (stored verified
queries, provider JSON). This module is the only place that gives that string a
meaning. The compiler asks it for SQL bounds, and the plan contract and the
intent frame ask it whether a range is valid. The model reads the user's words
in any language and writes a token of this grammar; nothing here parses a
question.

Supported ranges (case-insensitive, spaces or hyphens accepted in word forms):

* relative calendar periods: ``current_{day,week,month,quarter,year}`` and
  ``previous_{day,week,month,quarter,year}`` (aliases: today, yesterday,
  this_month, last_month, ...)
* the last N complete periods: ``last_N_{days,weeks,months,quarters,years}``
* period to date: ``ytd``, ``qtd``, ``mtd``
* absolute: ``YYYY``, ``YYYY-Qn``, ``YYYY-MM``, ``YYYY-MM-DD..YYYY-MM-DD``
  (inclusive end date), ``since_YYYY`` and ``YYYY-MM-DD+`` (open to today)

Every window is half-open: ``start <= value < end``.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.modules.agents.semantic.planning import SemanticPlanError

UNITS = ("day", "week", "month", "quarter", "year")

#: Upper bounds for ``last_N_<unit>`` so a plan cannot request an unbounded scan.
_MAX_COUNT = {"day": 3660, "week": 520, "month": 120, "quarter": 40, "year": 10}

RANGE_GRAMMAR_HELP = (
    "time.range grammar: current_day|current_week|current_month|current_quarter|"
    "current_year, previous_day|previous_week|previous_month|previous_quarter|"
    "previous_year, last_N_days|last_N_weeks|last_N_months|last_N_quarters|"
    "last_N_years (N complete periods before the current one), ytd, qtd, mtd, "
    "YYYY, YYYY-Qn, YYYY-MM, YYYY-MM-DD..YYYY-MM-DD (inclusive end), since_YYYY, "
    "YYYY-MM-DD+ (through today). 'Last quarter' is previous_quarter, not a grain."
)


@dataclass(frozen=True)
class Interval:
    amount: int
    unit: str  # SQL unit: DAY, WEEK, MONTH, YEAR

    def sql(self) -> str:
        return f"{self.amount} {self.unit}"


@dataclass(frozen=True)
class TimeWindow:
    """Half-open SQL bounds plus what a previous-period comparison needs."""

    start: str
    end: str
    #: The window's own length, used for ``previous_period``. ``None`` when the
    #: window is open-ended and has no natural previous period.
    period: Interval | None
    #: The calendar unit the window covers, used to validate MoM/QoQ/WoW.
    granularity: str | None
    #: Literal bounds for absolute windows, so shifted bounds stay literals.
    start_date: date | None = None
    end_date: date | None = None
    #: A period still in progress (``current_month``): a comparison stops at
    #: today in both periods, so a partial month is not compared with a full one.
    in_progress: bool = False

    def shifted(self, interval: Interval) -> tuple[str, str]:
        """The window moved back by ``interval`` as ``(start, end)`` SQL."""
        if self.start_date is not None and self.end_date is not None:
            return (
                _literal(_shift_date(self.start_date, interval)),
                _literal(_shift_date(self.end_date, interval)),
            )
        return (
            f"DATE_SUB({self.start}, INTERVAL {interval.sql()})",
            f"DATE_SUB({self.end}, INTERVAL {interval.sql()})",
        )


_UNIT_INTERVAL = {
    "day": Interval(1, "DAY"),
    "week": Interval(1, "WEEK"),
    "month": Interval(1, "MONTH"),
    "quarter": Interval(3, "MONTH"),
    "year": Interval(1, "YEAR"),
}
_ALIASES = {
    "today": "current_day",
    "yesterday": "previous_day",
    "year_to_date": "ytd",
    "quarter_to_date": "qtd",
    "month_to_date": "mtd",
}
_TODATE = {"ytd": "year", "qtd": "quarter", "mtd": "month"}


#: Spellings of the grammar's own tokens a model may use ("this_month").
_PREFIX_ALIASES = {
    "this": "current", "current": "current", "last": "previous", "previous": "previous",
    "prior": "previous",
}


def _word_form(value: str) -> str:
    text = "_".join(value.strip().lower().replace("-", " ").split())
    text = _ALIASES.get(text, text)
    prefix, _, unit = text.partition("_")
    if prefix in _PREFIX_ALIASES and unit in UNITS:
        return f"{_PREFIX_ALIASES[prefix]}_{unit}"
    return text


def _literal(value: date) -> str:
    return f"'{value.isoformat()}'"


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    day = min(value.day, calendar.monthrange(year, month + 1)[1])
    return date(year, month + 1, day)


def _shift_date(value: date, interval: Interval) -> date:
    if interval.unit == "DAY":
        return value - timedelta(days=interval.amount)
    if interval.unit == "WEEK":
        return value - timedelta(weeks=interval.amount)
    if interval.unit == "MONTH":
        return _add_months(value, -interval.amount)
    return _add_months(value, -12 * interval.amount)


def _trunc(unit: str) -> str:
    return "CURRENT_DATE()" if unit == "day" else f"DATE_TRUNC('{unit}', CURRENT_DATE())"


def _plus(expression: str, interval: Interval) -> str:
    return f"DATE_ADD({expression}, INTERVAL {interval.sql()})"


def _minus(expression: str, interval: Interval) -> str:
    return f"DATE_SUB({expression}, INTERVAL {interval.sql()})"


def _absolute(start: date, end: date, period: Interval, granularity: str | None) -> TimeWindow:
    return TimeWindow(_literal(start), _literal(end), period, granularity, start, end)


def resolve_time_range(value: str) -> TimeWindow:
    """Resolve a plan range string to SQL bounds, or raise ``SemanticPlanError``."""
    raw = str(value or "").strip()
    if not raw:
        raise SemanticPlanError("The time range is empty.")
    lowered = raw.lower()

    explicit = r"(\d{4}-\d{2}-\d{2})\s*(?:\.\.|_to_| to )\s*(\d{4}-\d{2}-\d{2})"
    if match := re.fullmatch(explicit, lowered):
        start, last = _parse_date(match[1]), _parse_date(match[2])
        if last < start:
            raise SemanticPlanError("The time range ends before it starts.")
        end = last + timedelta(days=1)
        return _absolute(start, end, Interval((end - start).days, "DAY"), None)
    if match := re.fullmatch(r"(\d{4}-\d{2}-\d{2})\+", lowered):
        start = _parse_date(match[1])
        return TimeWindow(_literal(start), _plus("CURRENT_DATE()", Interval(1, "DAY")), None, None)
    if match := re.fullmatch(r"(?:(\d{4})[-_ ]?q([1-4])|q([1-4])[-_ ]?(\d{4}))", lowered):
        year = int(match[1] or match[4])
        quarter = int(match[2] or match[3])
        start = date(year, 3 * quarter - 2, 1)
        return _absolute(start, _add_months(start, 3), _UNIT_INTERVAL["quarter"], "quarter")
    if match := re.fullmatch(r"(\d{4})-(\d{2})", lowered):
        month = int(match[2])
        if not 1 <= month <= 12:
            raise SemanticPlanError("The time range has an invalid month.")
        start = date(int(match[1]), month, 1)
        return _absolute(start, _add_months(start, 1), _UNIT_INTERVAL["month"], "month")
    if re.fullmatch(r"[12]\d{3}", lowered):
        # Kept as the historical literal pair so stored verified SQL still matches.
        year = int(lowered)
        start, end = date(year, 1, 1), date(year + 1, 1, 1)
        return _absolute(start, end, _UNIT_INTERVAL["year"], "year")

    text = _word_form(raw)
    if match := re.fullmatch(r"since_([12]\d{3})", text):
        start = date(int(match[1]), 1, 1)
        return TimeWindow(_literal(start), _plus("CURRENT_DATE()", Interval(1, "DAY")), None, None)
    if text in _TODATE:
        unit = _TODATE[text]
        return TimeWindow(
            _trunc(unit), _plus("CURRENT_DATE()", Interval(1, "DAY")), _UNIT_INTERVAL[unit], None
        )
    position, _, unit = text.partition("_")
    if position in {"current", "previous"} and unit in UNITS:
        step = _UNIT_INTERVAL[unit]
        if position == "current":
            return TimeWindow(
                _trunc(unit), _plus(_trunc(unit), step), step, unit, in_progress=unit != "day"
            )
        return TimeWindow(_minus(_trunc(unit), step), _trunc(unit), step, unit)
    recent = r"(?:last|past|previous)_(\d{1,4})_(" + "|".join(UNITS) + ")s?"
    if match := re.fullmatch(recent, text):
        count, unit = int(match[1]), match[2]
        if not 1 <= count <= _MAX_COUNT[unit]:
            raise SemanticPlanError(f"last_N_{unit}s supports N from 1 to {_MAX_COUNT[unit]}.")
        step = _UNIT_INTERVAL[unit]
        span = Interval(step.amount * count, step.unit)
        return TimeWindow(
            _minus(_trunc(unit), span), _trunc(unit), span, unit if count == 1 else None
        )
    raise SemanticPlanError(f"Unsupported time range {raw!r}. {RANGE_GRAMMAR_HELP}")


def is_supported_time_range(value: str) -> bool:
    try:
        resolve_time_range(value)
    except SemanticPlanError:
        return False
    return True


def range_predicates(expression: str, value: str) -> list[str]:
    window = resolve_time_range(value)
    return [f"{expression} >= {window.start}", f"{expression} < {window.end}"]


#: Comparison kind -> (fixed shift, calendar unit the range must cover).
_COMPARISONS: dict[str, tuple[Interval | None, str | None]] = {
    "previous_period": (None, None),
    "year_over_year": (Interval(1, "YEAR"), None),
    "quarter_over_quarter": (Interval(3, "MONTH"), "quarter"),
    "month_over_month": (Interval(1, "MONTH"), "month"),
    "week_over_week": (Interval(1, "WEEK"), "week"),
}


def comparison_bounds(value: str | None, comparison: str | None) -> tuple[str, str, str, str]:
    """``(current_start, current_end, prior_start, prior_end)`` SQL for a comparison."""
    if not value:
        raise SemanticPlanError("Comparison needs an explicit time range.")
    kind = (comparison or "previous_period").lower()
    if kind == "custom":
        raise SemanticPlanError("Custom comparison needs explicit bounded dates.")
    if kind not in _COMPARISONS:
        raise SemanticPlanError(f"Unsupported comparison {comparison!r}.")
    window = resolve_time_range(value)
    shift, required_unit = _COMPARISONS[kind]
    if required_unit is not None and window.granularity != required_unit:
        raise SemanticPlanError(f"{kind} is incompatible with semantic range {value!r}.")
    shift = shift or window.period
    if shift is None:
        raise SemanticPlanError("An open-ended time range has no previous period to compare.")
    if window.in_progress:
        # Like for like: this month so far against the same days of the prior period.
        today_end = _plus("CURRENT_DATE()", Interval(1, "DAY"))
        return window.start, today_end, _minus(window.start, shift), _minus(today_end, shift)
    prior_start, prior_end = window.shifted(shift)
    return window.start, window.end, prior_start, prior_end


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SemanticPlanError("The time range has an invalid date.") from exc
