"""Unit tests for Nova's pure cron/interval next-fire computation (NOVA-35).

Acceptance criteria 2 (cron parse + invalid), 3 (IANA timezone shifts next-fire),
and 4 (interval) are proven here without a database. Everything in
``task_orchestration.schedule`` is pure, which is why these can be exact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.task_orchestration.schedule import (
    ScheduleError,
    engine_timezone_matches,
    is_due,
    latest_occurrence,
    next_fire,
    parse_cron,
    parse_interval,
    resolve_timezone,
)


class TestCronParsing:
    def test_accepts_a_five_field_expression(self):
        assert parse_cron("0 2 * * *") is not None

    def test_accepts_step_and_range_fields(self):
        assert parse_cron("*/15 9-17 * * MON-FRI") is not None

    @pytest.mark.parametrize(
        "expression",
        [
            "",
            "   ",
            "not a cron",
            "0 2 * *",  # 4 fields
            "0 2 * * * *",  # 6 fields
            "61 2 * * *",  # minute out of range
            "0 25 * * *",  # hour out of range
        ],
    )
    def test_rejects_invalid_expressions(self, expression):
        with pytest.raises(ScheduleError):
            parse_cron(expression)

    def test_invalid_expression_reaches_next_fire_as_schedule_error(self):
        with pytest.raises(ScheduleError):
            next_fire("cron", "bad", "UTC", datetime(2026, 1, 1, tzinfo=UTC))


class TestCronNextFire:
    def test_next_fire_is_strictly_after_the_reference(self):
        reference = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        result = next_fire("cron", "0 2 * * *", "UTC", reference)
        assert result == datetime(2026, 1, 2, 2, 0, tzinfo=UTC)

    def test_daily_cron_in_jakarta_is_not_utc(self):
        """A 02:00 Asia/Jakarta task is 19:00 UTC the previous day."""
        reference = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        result = next_fire("cron", "0 2 * * *", "Asia/Jakarta", reference)
        assert result == datetime(2026, 1, 1, 19, 0, tzinfo=UTC)

    def test_changing_timezone_shifts_the_result(self):
        reference = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        utc_fire = next_fire("cron", "0 2 * * *", "UTC", reference)
        jakarta_fire = next_fire("cron", "0 2 * * *", "Asia/Jakarta", reference)
        assert utc_fire != jakarta_fire
        assert utc_fire == datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        # Jakarta is UTC+7: 02:00 local is 19:00 UTC the day before.
        assert jakarta_fire == datetime(2026, 1, 1, 19, 0, tzinfo=UTC)

    def test_naive_reference_is_rejected(self):
        with pytest.raises(ScheduleError):
            next_fire("cron", "0 2 * * *", "UTC", datetime(2026, 1, 1))


class TestTimezoneResolution:
    def test_known_iana_name_resolves(self):
        assert str(resolve_timezone("Asia/Jakarta")) == "Asia/Jakarta"

    @pytest.mark.parametrize("name", ["", "   ", "Not/AZone", "GMT+7"])
    def test_unknown_or_missing_timezone_is_rejected(self, name):
        with pytest.raises(ScheduleError):
            resolve_timezone(name)

    def test_next_fire_requires_an_explicit_timezone(self):
        with pytest.raises(ScheduleError):
            next_fire("cron", "0 2 * * *", "", datetime(2026, 1, 1, tzinfo=UTC))


class TestEngineTimezoneAlignment:
    """NOVA-39: the startup guard compares an override against ``@@time_zone``."""

    def test_matching_iana_names_agree(self):
        assert engine_timezone_matches("Asia/Jakarta", "Asia/Jakarta") is True

    def test_utc_override_against_jakarta_engine_is_rejected(self):
        assert engine_timezone_matches("UTC", "Asia/Jakarta") is False

    def test_engine_reporting_fixed_offset_still_matches(self):
        assert engine_timezone_matches("Asia/Jakarta", "+07:00") is True

    def test_configured_fixed_offset_against_named_engine_matches(self):
        assert engine_timezone_matches("+07:00", "Asia/Jakarta") is True

    def test_different_fixed_offset_is_rejected(self):
        assert engine_timezone_matches("+07:00", "+00:00") is False

    @pytest.mark.parametrize(
        ("configured", "engine"),
        [("Not/AZone", "UTC"), ("UTC", "Not/AZone"), ("UTC", "SYSTEM")],
    )
    def test_unusable_values_are_not_a_match(self, configured, engine):
        assert engine_timezone_matches(configured, engine) is False


class TestInterval:
    @pytest.mark.parametrize(
        "expression",
        ["EVERY(INTERVAL 5 MINUTE)", "every(interval 5 minute)", "5 minute", "EVERY 5 MINUTE"],
    )
    def test_accepts_supported_interval_forms(self, expression):
        interval = parse_interval(expression)
        assert interval.amount == 5
        assert interval.unit.upper().startswith("MINUTE")

    @pytest.mark.parametrize(
        "expression",
        ["", "EVERY(INTERVAL 0 MINUTE)", "EVERY(INTERVAL -1 HOUR)", "5 fortnights", "soon"],
    )
    def test_rejects_invalid_intervals(self, expression):
        with pytest.raises(ScheduleError):
            parse_interval(expression)

    def test_next_fire_adds_the_interval(self):
        reference = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
        result = next_fire("interval", "EVERY(INTERVAL 5 MINUTE)", "UTC", reference)
        assert result == datetime(2026, 1, 1, 0, 6, tzinfo=UTC)

    def test_day_interval(self):
        reference = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        result = next_fire("interval", "EVERY(INTERVAL 1 DAY)", "UTC", reference)
        assert result == datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


class TestManualSchedule:
    def test_manual_has_no_next_fire(self):
        assert next_fire("manual", "", "UTC", datetime(2026, 1, 1, tzinfo=UTC)) is None

    def test_manual_is_never_due(self):
        now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        assert is_due("manual", "", "UTC", datetime(2025, 1, 1, tzinfo=UTC), now) is False

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ScheduleError):
            next_fire("hourly", "", "UTC", datetime(2026, 1, 1, tzinfo=UTC))


class TestIsDue:
    def test_no_recorded_fire_is_not_due(self):
        """A brand-new task must not fire just because Nova has no history."""
        now = datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
        assert is_due("cron", "0 2 * * *", "UTC", None, now) is False

    def test_due_once_the_occurrence_has_passed(self):
        last = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        assert is_due("cron", "0 2 * * *", "UTC", last, datetime(2026, 1, 2, 2, 0, tzinfo=UTC))

    def test_not_due_before_the_occurrence(self):
        last = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        assert not is_due("cron", "0 2 * * *", "UTC", last, datetime(2026, 1, 1, 12, 0, tzinfo=UTC))


class TestLatestOccurrence:
    def test_interval_latest_occurrence_is_aligned_to_the_anchor(self):
        anchor = datetime(2026, 1, 1, 0, 3, tzinfo=UTC)
        now = datetime(2026, 1, 1, 0, 17, tzinfo=UTC)
        # anchor 00:03, step 5m -> 00:08, 00:13; 00:17 is not an occurrence.
        result = latest_occurrence("interval", "5 minute", "UTC", anchor, now)
        assert result == datetime(2026, 1, 1, 0, 13, tzinfo=UTC)

    def test_interval_first_fire_is_one_step_after_creation(self):
        """An interval task does not fire at the creation instant itself."""
        anchor = datetime(2026, 1, 1, 0, 3, tzinfo=UTC)
        assert latest_occurrence("interval", "5 minute", "UTC", anchor, anchor) is None
        assert latest_occurrence(
            "interval", "5 minute", "UTC", anchor, anchor + timedelta(minutes=1)
        ) is None
        assert latest_occurrence(
            "interval", "5 minute", "UTC", anchor, anchor + timedelta(minutes=5)
        ) == datetime(2026, 1, 1, 0, 8, tzinfo=UTC)

    def test_cron_occurrence_before_anchor_is_not_returned(self):
        """A task created after today's fire does not fire until tomorrow's."""
        anchor = datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
        now = datetime(2026, 1, 1, 4, 0, tzinfo=UTC)
        assert latest_occurrence("cron", "0 2 * * *", "UTC", anchor, now) is None

    def test_cron_occurrence_at_or_after_anchor_is_returned(self):
        anchor = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        now = datetime(2026, 1, 2, 5, 0, tzinfo=UTC)
        assert latest_occurrence("cron", "0 2 * * *", "UTC", anchor, now) == datetime(
            2026, 1, 2, 2, 0, tzinfo=UTC
        )

    def test_latest_occurrence_tracks_timezone(self):
        anchor = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        now = datetime(2026, 1, 2, 5, 0, tzinfo=UTC)
        result = latest_occurrence("cron", "0 2 * * *", "Asia/Jakarta", anchor, now)
        assert result == datetime(2026, 1, 1, 19, 0, tzinfo=UTC)

    def test_now_before_anchor_returns_none(self):
        anchor = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
        now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        assert latest_occurrence("interval", "5 minute", "UTC", anchor, now) is None

    def test_naive_inputs_are_rejected(self):
        with pytest.raises(ScheduleError):
            latest_occurrence(
                "interval", "5 minute", "UTC", datetime(2026, 1, 1), datetime(2026, 1, 2)
            )


class TestCronExactFireInstant:
    """NOVA-40: a cron task is due exactly on its fire instant, like interval.

    ``croniter.get_prev`` is strictly-before, so without the explicit instant
    check a cron task whose tick lands exactly on the fire time would skip that
    occurrence and not be due until the following tick.
    """

    def test_due_exactly_on_the_fire_instant(self):
        anchor = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        assert latest_occurrence("cron", "0 2 * * *", "UTC", anchor, now) == now

    def test_due_within_the_fire_minute(self):
        anchor = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        now = datetime(2026, 1, 1, 2, 0, 45, tzinfo=UTC)
        assert latest_occurrence("cron", "0 2 * * *", "UTC", anchor, now) == datetime(
            2026, 1, 1, 2, 0, tzinfo=UTC
        )

    def test_previous_occurrence_respected_between_fires(self):
        anchor = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        now = datetime(2026, 1, 2, 5, 0, tzinfo=UTC)
        assert latest_occurrence("cron", "0 2 * * *", "UTC", anchor, now) == datetime(
            2026, 1, 2, 2, 0, tzinfo=UTC
        )

    def test_not_due_one_second_before_the_fire(self):
        anchor = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        now = datetime(2026, 1, 1, 1, 59, 59, tzinfo=UTC)
        assert latest_occurrence("cron", "0 2 * * *", "UTC", anchor, now) is None

    def test_exact_instant_is_timezone_local(self):
        """The fire instant is the task's wall clock, not UTC."""
        anchor = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        # 02:00 Asia/Jakarta on 2026-01-02 == 19:00Z on 2026-01-01.
        now = datetime(2026, 1, 1, 19, 0, tzinfo=UTC)
        assert latest_occurrence("cron", "0 2 * * *", "Asia/Jakarta", anchor, now) == now

    def test_is_due_true_on_the_fire_instant(self):
        last = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        now = datetime(2026, 1, 2, 2, 0, tzinfo=UTC)
        assert is_due("cron", "0 2 * * *", "UTC", last, now) is True
