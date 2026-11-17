"""Release QA: native relative windows, calendar boundaries and typed text filters.

NDV-METRIC-013, NDV-METRIC-014, NDV-METRIC-015, NDV-FILTER-005, NDV-FILTER-006.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.viz import aggregate as module
from app.modules.viz.aggregate import _shift_months, plan_aggregate, relative_bounds
from app.modules.viz.contracts.datasets import DatasetField
from app.modules.viz.contracts.filters import FilterTree, RelativeWindow
from app.modules.viz.contracts.queries import DatasetQueryRequest
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.read_queries import validate_read_query

DATASET = UUID("aaaaaaaa-0000-4000-8000-000000000002")
ZONE = "Asia/Jakarta"


def _field(field_id, column, logical, physical, role="dimension", timezone=None):
    return DatasetField(field_id=field_id, name=column.title(), source_column=column,
                        logical_type=logical, physical_type=physical, semantic_role=role,
                        nullable=True, timezone=timezone)


FIELDS = (
    _field("f_city", "city", "string", "VARCHAR(64)"),
    _field("f_day", "sale_day", "date", "DATE"),
    _field("f_at", "sold_at", "timestamp", "DATETIME", timezone=ZONE),
    _field("f_amount", "amount", "decimal", "DECIMAL(18,2)", "measure"),
)


def _predicate(field_id, op, *values, **extra):
    return {"type": "predicate", "op": op, "values": list(values), **extra,
            "field": {"dataset_id": str(DATASET), "dataset_revision": 1, "field_id": field_id}}


def _plan(expression=None, now=None, **request):
    filters = FilterTree(expression=expression) if expression is not None else None
    body = {"measures": [{"aggregation": "count"}], **request}
    return plan_aggregate("SELECT * FROM sales", FIELDS,
                          DatasetQueryRequest(revision=1, filters=filters, **body),
                          dataset_id=str(DATASET), reserved_parameters=set(), now=now)


def _window(**values):
    return RelativeWindow(timezone=ZONE, **values)


# ── NDV-METRIC-013: month over month across December to January ──────────


def test_previous_month_in_january_is_the_whole_of_december():
    january = datetime(2024, 1, 15, 5, tzinfo=UTC)
    assert relative_bounds(_window(unit="month", mode="previous"), january) == (
        date(2023, 12, 1), date(2024, 1, 1))
    # The current month to date and the month before it are adjacent and disjoint.
    current = relative_bounds(_window(unit="month", mode="to_date"), january)
    assert current == (date(2024, 1, 1), date(2024, 1, 16))
    # Two previous months reach back across the year boundary into November.
    assert relative_bounds(_window(unit="month", mode="previous", count=2), january) == (
        date(2023, 11, 1), date(2024, 1, 1))
    # Rolling two months: December plus January to date.
    assert relative_bounds(_window(unit="month", count=2), january) == (
        date(2023, 12, 1), date(2024, 1, 16))


def test_new_year_in_the_dashboard_time_zone_decides_the_previous_month():
    # 31 Dec 20:00 UTC is already 1 January in Jakarta (UTC+7).
    eve = datetime(2023, 12, 31, 20, tzinfo=UTC)
    assert relative_bounds(_window(unit="month", mode="previous"), eve) == (
        date(2023, 12, 1), date(2024, 1, 1))
    utc = RelativeWindow(unit="month", mode="previous", timezone="UTC")
    assert relative_bounds(utc, eve) == (date(2023, 11, 1), date(2023, 12, 1))


def test_previous_month_on_a_timestamp_field_binds_local_midnights():
    result = _plan(_predicate("f_at", "relative_date",
                              relative={"unit": "month", "mode": "previous", "timezone": ZONE}),
                   now=datetime(2024, 1, 15, tzinfo=UTC))
    assert "(`sold_at` >= :nova_q0 AND `sold_at` < :nova_q1)" in result.sql
    values = dict(result.values)
    assert values["nova_q0"].isoformat() == "2023-12-01T00:00:00+07:00"
    assert values["nova_q1"].isoformat() == "2024-01-01T00:00:00+07:00"
    validate_read_query(parse_statement(result.sql))


# ── NDV-METRIC-014: leap day boundaries ──────────────────────────────────


def test_month_shifts_from_a_leap_day_clamp_to_the_last_day_of_the_month():
    # The policy: a shifted date past the end of a shorter month lands on its last day.
    assert _shift_months(date(2024, 2, 29), -12) == date(2023, 2, 28)
    assert _shift_months(date(2024, 2, 29), 12) == date(2025, 2, 28)
    assert _shift_months(date(2024, 2, 29), 48) == date(2028, 2, 29)
    assert _shift_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert _shift_months(date(2023, 1, 31), 1) == date(2023, 2, 28)


def test_windows_on_and_around_a_leap_day_stay_on_calendar_units():
    leap_day = datetime(2024, 2, 29, 5, tzinfo=UTC)
    assert relative_bounds(_window(unit="year", mode="to_date"), leap_day) == (
        date(2024, 1, 1), date(2024, 3, 1))
    assert relative_bounds(_window(unit="month", mode="to_date"), leap_day) == (
        date(2024, 2, 1), date(2024, 3, 1))
    assert relative_bounds(_window(unit="year", mode="previous"), leap_day) == (
        date(2023, 1, 1), date(2024, 1, 1))
    assert relative_bounds(_window(unit="month", count=12), leap_day) == (
        date(2023, 3, 1), date(2024, 3, 1))
    # A year later the previous year is 2024 in full, leap day included.
    following = datetime(2025, 2, 28, 5, tzinfo=UTC)
    assert relative_bounds(_window(unit="year", mode="previous"), following) == (
        date(2024, 1, 1), date(2025, 1, 1))
    assert relative_bounds(_window(unit="day", count=1), leap_day) == (
        date(2024, 2, 29), date(2024, 3, 1))
    day_after = datetime(2024, 3, 1, 5, tzinfo=UTC)
    assert relative_bounds(_window(unit="day", mode="previous"), day_after) == (
        date(2024, 2, 29), date(2024, 3, 1))


# ── NDV-METRIC-015: ISO weeks at the calendar year boundary ──────────────


@pytest.mark.parametrize(("today", "start"), [
    (date(2020, 12, 31), date(2020, 12, 28)),  # Thursday of ISO week 2020-W53
    (date(2021, 1, 1), date(2020, 12, 28)),
    (date(2021, 1, 3), date(2020, 12, 28)),  # Sunday still closes 2020-W53
    (date(2021, 1, 4), date(2021, 1, 4)),  # Monday opens 2021-W01
    (date(2024, 12, 31), date(2024, 12, 30)),  # Tuesday of 2025-W01
    (date(2025, 1, 1), date(2024, 12, 30)),
])
def test_iso_weeks_cross_the_year_with_their_iso_week_year(today, start):
    now = datetime(today.year, today.month, today.day, 5, tzinfo=UTC)
    bounds = relative_bounds(_window(unit="week", mode="to_date"), now)
    assert bounds[0] == start
    assert start.isocalendar().weekday == 1
    assert start.isocalendar()[:2] == today.isocalendar()[:2]
    assert relative_bounds(_window(unit="week", mode="previous"), now) == (
        date.fromordinal(start.toordinal() - 7), start)


def test_sunday_weeks_at_the_year_boundary():
    now = datetime(2021, 1, 2, 5, tzinfo=UTC)  # Saturday
    assert relative_bounds(_window(unit="week", mode="to_date", week_start="sunday"), now) == (
        date(2020, 12, 27), date(2021, 1, 3))


def test_week_grain_groups_by_the_engine_week_start():
    result = _plan(dimensions=[{"field_id": "f_day", "date_grain": "week"}])
    assert "CAST(date_trunc('week', `sale_day`) AS DATE) AS `d0`" in result.sql
    assert "GROUP BY CAST(date_trunc('week', `sale_day`) AS DATE)" in result.sql
    validate_read_query(parse_statement(result.sql))


# ── NDV-FILTER-005: the same relative filter at two different times ──────


def test_the_same_relative_filter_moves_with_the_evaluation_time():
    seven_days = _predicate("f_day", "relative_date",
                            relative={"unit": "day", "count": 7, "timezone": ZONE})
    first = _plan(seven_days, now=datetime(2024, 3, 10, 5, tzinfo=UTC))
    second = _plan(seven_days, now=datetime(2024, 3, 17, 5, tzinfo=UTC))
    assert first.sql == second.sql  # same SQL text, only the bound values move
    assert dict(first.values) == {"nova_q0": date(2024, 3, 4), "nova_q1": date(2024, 3, 11)}
    assert dict(second.values) == {"nova_q0": date(2024, 3, 11), "nova_q1": date(2024, 3, 18)}
    assert dict(first.values) != dict(second.values)


def test_the_server_clock_is_used_when_no_time_is_given(monkeypatch):
    class Frozen(datetime):
        current = datetime(2024, 5, 20, 5, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(module, "datetime", Frozen)
    seven_days = _predicate("f_day", "relative_date",
                            relative={"unit": "day", "count": 7, "timezone": ZONE})
    first = dict(_plan(seven_days).values)
    Frozen.current = datetime(2024, 5, 27, 5, tzinfo=UTC)
    second = dict(_plan(seven_days).values)
    assert first == {"nova_q0": date(2024, 5, 14), "nova_q1": date(2024, 5, 21)}
    assert second == {"nova_q0": date(2024, 5, 21), "nova_q1": date(2024, 5, 28)}


@pytest.mark.parametrize("name", ["now", "as_of", "evaluated_at"])
def test_a_client_cannot_supply_the_evaluation_time(name):
    with pytest.raises(ValidationError):
        DatasetQueryRequest(revision=1, measures=[{"aggregation": "count"}],
                            **{name: "2020-01-01T00:00:00Z"})


# ── NDV-FILTER-006: text with escaping and Unicode ───────────────────────


UNICODE = ["Café", "Café", "東京", "Zürich 🚀", "O'Brien \"quoted\"", "  padded  ",
           "tab\tand\\backslash"]


def test_unicode_members_are_bound_exactly_and_never_enter_sql_text():
    tree = {"type": "or", "children": [
        _predicate("f_city", "eq", {"type": "string", "value": UNICODE[0]}),
        _predicate("f_city", "in", *({"type": "string", "value": value} for value in UNICODE)),
    ]}
    result = _plan(tree)
    values = dict(result.values)
    assert values["nova_q0"] == "Café"
    assert values["nova_q1"] == tuple(UNICODE)
    # Composed and decomposed spellings stay distinct: matching is exact, not normalized.
    assert values["nova_q1"][0] != values["nova_q1"][1]
    assert values["nova_q1"][0].encode() == "Café".encode()
    assert values["nova_q1"][1].encode() == "Café".encode()
    assert "(`city` = :nova_q0)" in result.sql and "(`city` IN (:nova_q1))" in result.sql
    for value in UNICODE:
        assert value not in result.sql
    assert result.sql.isascii()
    assert [spec.type for spec in result.specs] == ["string", "string"]
    assert result.specs[1].multiple
    validate_read_query(parse_statement(result.sql))


def test_unicode_text_entry_escapes_only_like_wildcards():
    result = _plan(_predicate("f_city", "like_literal",
                              {"type": "string", "value": "東京_50%\\Café"}))
    assert "(`city` LIKE :nova_q0)" in result.sql
    assert dict(result.values)["nova_q0"] == "%東京\\_50\\%\\\\Café%"
    assert "東京" not in result.sql
