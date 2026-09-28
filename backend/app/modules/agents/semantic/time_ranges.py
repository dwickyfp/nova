"""One time-range grammar for semantic plans, the compiler, and question parsing.

A ``SemanticTime.range`` is a string so plans stay portable (stored verified
queries, provider JSON). This module is the only place that gives that string a
meaning. The compiler asks it for SQL bounds, the plan contract asks it whether
a range is valid, and the lexical planner asks it which range a question names.
Keeping the three together is what stops a phrase such as "last quarter" from
being parsed as a grouping grain while the compiler silently ignores it.

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


def _word_form(value: str) -> str:
    text = re.sub(r"[\s-]+", "_", value.strip().lower())
    text = _ALIASES.get(text, text)
    match = re.fullmatch(r"(this|current|last|previous|prior)_(day|week|month|quarter|year)", text)
    if match:
        prefix = "current" if match[1] in {"this", "current"} else "previous"
        return f"{prefix}_{match[2]}"
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
    if match := re.fullmatch(r"(current|previous)_(day|week|month|quarter|year)", text):
        unit = match[2]
        step = _UNIT_INTERVAL[unit]
        if match[1] == "current":
            return TimeWindow(
                _trunc(unit), _plus(_trunc(unit), step), step, unit, in_progress=unit != "day"
            )
        return TimeWindow(_minus(_trunc(unit), step), _trunc(unit), step, unit)
    recent = r"(?:last|past|previous)_(\d{1,4})_(day|week|month|quarter|year)s?"
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


# ── question parsing ────────────────────────────────────────────────────────

_MONTHS = {
    **{name: index for index, name in enumerate(
        ["january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"], start=1)},
    **{name: index for index, name in enumerate(
        ["januari", "februari", "maret", "april", "mei", "juni", "juli", "agustus",
         "september", "oktober", "november", "desember"], start=1)},
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "agu": 8,
    "agt": 8, "sep": 9, "sept": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "des": 12,
}
_MONTH = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + ")"
_UNIT_WORDS = {
    "day": "day", "days": "day", "hari": "day",
    "week": "week", "weeks": "week", "minggu": "week", "pekan": "week",
    "month": "month", "months": "month", "bulan": "month",
    "quarter": "quarter", "quarters": "quarter", "kuartal": "quarter", "triwulan": "quarter",
    "year": "year", "years": "year", "tahun": "year",
}
_UNIT = "(" + "|".join(sorted(_UNIT_WORDS, key=len, reverse=True)) + ")"
_RANGE_JOIN = r"(?:to|until|through|thru|sampai|hingga|s/d|sd|-|–|and|dan)"

_VERSUS = r"(?:dibanding(?:kan)?|vs\.?|versus|compared (?:to|with))\s+(?:dengan\s+)?"


def _compare_pattern(short: str, unit: str, previous: str) -> str:
    return rf"\b(?:{short}|{unit}[- ]over[- ]{unit}|{_VERSUS}(?:{previous}))\b"


_COMPARE_PATTERNS = (
    ("year_over_year", _compare_pattern(
        "yoy", "year", "last year|the previous year|tahun lalu|tahun sebelumnya")),
    ("quarter_over_quarter", _compare_pattern(
        "qoq", "quarter",
        "last quarter|the previous quarter|kuartal lalu|kuartal sebelumnya|triwulan lalu")),
    ("month_over_month", _compare_pattern(
        "mom", "month", "last month|the previous month|bulan lalu|bulan sebelumnya")),
    ("week_over_week", _compare_pattern(
        "wow", "week", "last week|the previous week|minggu lalu|pekan lalu|minggu sebelumnya")),
)
#: A two-period answer needs a period word: "online vs offline" compares
#: channels, not periods.
_GENERIC_COMPARE = (
    r"\b(?:growth|pertumbuhan|tumbuh|perubahan|change)\b"
    r"|\b(?:compare[ds]?|versus|vs\.?|dibanding(?:kan)?|bandingkan)\s+(?:dengan\s+|to\s+|with\s+)?"
    r"(?:the\s+)?(?:previous|prior|sebelumnya|periode sebelumnya)\b"
)

_PREVIOUS_ID = r"(?:lalu|kemarin|sebelumnya)"


def _relative(english: str, indonesian: str) -> tuple[str, str]:
    current = rf"\b(?:this|current) {english}\b|\b{indonesian} ini\b"
    previous = rf"\b(?:last|previous|prior) {english}\b|\b{indonesian} {_PREVIOUS_ID}\b"
    return current, previous


#: Longer units first: "bulan kemarin" is last month, not yesterday.
_RELATIVE = tuple(
    item
    for english, indonesian in (
        ("year", "tahun"), ("quarter", "(?:kuartal|triwulan)"), ("month", "bulan"),
        ("week", "(?:minggu|pekan)"),
    )
    for item in zip(
        _relative(english, indonesian),
        (f"current_{english}", f"previous_{english}"),
        strict=True,
    )
) + (
    (r"\b(?:today|hari ini)\b", "current_day"),
    (r"\b(?:yesterday|kemarin)\b", "previous_day"),
)
_TO_DATE = (
    (r"\b(?:ytd|year[- ]to[- ]date|sejak awal tahun|tahun berjalan)\b", "ytd"),
    (r"\b(?:qtd|quarter[- ]to[- ]date|sejak awal (?:kuartal|triwulan))\b", "qtd"),
    (r"\b(?:mtd|month[- ]to[- ]date|sejak awal bulan|bulan berjalan)\b", "mtd"),
)
_GRAIN = (
    rf"\b(?:by|per|each|every|tiap|setiap)\s+{_UNIT}\b",
    r"\b(daily|weekly|monthly|quarterly|yearly|annually|harian|mingguan|bulanan|kuartalan|triwulanan|tahunan)\b",
)
_GRAIN_ADJECTIVES = {
    "daily": "day", "harian": "day", "weekly": "week", "mingguan": "week",
    "monthly": "month", "bulanan": "month", "quarterly": "quarter", "kuartalan": "quarter",
    "triwulanan": "quarter", "yearly": "year", "annually": "year", "tahunan": "year",
}


@dataclass(frozen=True)
class TimePhrase:
    range: str | None = None
    compare: str | None = None
    grain: str | None = None
    #: Normalized words this parse explained, so they are not reported as
    #: unresolved business concepts.
    consumed: frozenset[str] = frozenset()


def parse_time_phrase(question: str) -> TimePhrase:
    """Find the range, comparison, and explicit grain a question asks for."""
    text = " " + question.lower() + " "
    consumed: set[str] = set()

    def take(match: re.Match[str]) -> None:
        consumed.update(re.findall(r"[a-z0-9]+", match.group(0)))

    def blank(match: re.Match[str]) -> str:
        take(match)
        return " " * len(match.group(0))

    compare = None
    for kind, pattern in _COMPARE_PATTERNS:
        updated = re.sub(pattern, blank, text)
        if updated != text:
            compare, text = kind, updated
            break

    range_value = None
    date_range = re.search(
        rf"(?:between|from|dari|antara)?\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*(?:\.\.|{_RANGE_JOIN})\s*(\d{{4}}-\d{{2}}-\d{{2}})",
        text,
    )
    month_range = re.search(
        rf"(?:between|from|dari|antara)?\s*\b{_MONTH}\s*{_RANGE_JOIN}\s*{_MONTH}\s+(\d{{4}})\b",
        text,
    )
    quarter = re.search(
        r"\b(?:q|kuartal\s*|triwulan\s*|quarter\s*)([1-4])(?:\s*(?:of|tahun|[-/]))?\s*(\d{4})\b"
        r"|\b(\d{4})\s*[-/ ]?q([1-4])\b",
        text,
    )
    month_year = re.search(rf"\b{_MONTH}\s+(\d{{4}})\b", text)
    last_n = re.search(
        rf"\b(?:the\s+)?(?:last|past|previous)\s+(\d{{1,4}})\s+{_UNIT}\b"
        rf"|\b(\d{{1,4}})\s+{_UNIT}\s+(?:terakhir|belakangan|ke belakang)\b",
        text,
    )
    since = re.search(r"\b(?:since|sejak)\s+(?:(\d{4}-\d{2}-\d{2})|(\d{4}))\b", text)

    if date_range:
        range_value = f"{date_range[1]}..{date_range[2]}"
        take(date_range)
    elif month_range:
        range_year = int(month_range[3])
        start = date(range_year, _MONTHS[month_range[1]], 1)
        last_month = date(range_year, _MONTHS[month_range[2]], 1)
        if last_month >= start:
            end = _add_months(last_month, 1) - timedelta(days=1)
            range_value = f"{start.isoformat()}..{end.isoformat()}"
            take(month_range)
    elif quarter:
        quarter_year = quarter[2] or quarter[3]
        number = quarter[1] or quarter[4]
        range_value = f"{quarter_year}-Q{number}"
        take(quarter)
    elif month_year:
        range_value = f"{month_year[2]}-{_MONTHS[month_year[1]]:02d}"
        take(month_year)
    elif last_n:
        count = last_n[1] or last_n[3]
        unit = _UNIT_WORDS[last_n[2] or last_n[4]]
        range_value = f"last_{int(count)}_{unit}s"
        take(last_n)
    elif since:
        range_value = f"{since[1]}+" if since[1] else f"since_{since[2]}"
        take(since)
    if range_value is None:
        for pattern, value in (*_TO_DATE, *_RELATIVE):
            match = re.search(pattern, text)
            if match:
                range_value = value
                take(match)
                break
    if range_value is None:
        bare_year = re.search(
            r"\b(?:for|in|untuk|tahun|year|during|selama)?\s*\b((?:19|20)\d{2})\b(?![\d/-])", text
        )
        if bare_year:
            range_value = bare_year[1]
            take(bare_year)

    grain = None
    for pattern in _GRAIN:
        match = re.search(pattern, text)
        if match:
            word = match[1]
            grain = _UNIT_WORDS.get(word) or _GRAIN_ADJECTIVES.get(word)
            take(match)
            break

    generic = re.search(_GENERIC_COMPARE, text)
    if compare is None and range_value and generic:
        compare = "previous_period"
        take(generic)
    if compare and not range_value:
        # A comparison without the period it compares is left unresolved rather
        # than guessed; the caller reports it or asks.
        return TimePhrase(None, compare, grain, frozenset(consumed))
    if range_value and not is_supported_time_range(range_value):
        range_value = None
    return TimePhrase(range_value, compare, grain, frozenset(consumed))
