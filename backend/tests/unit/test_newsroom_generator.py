"""Edition generation: sliced detection, ranking, decoys, refresh and bounds."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.modules.intelligence.newsroom import (
    canonical,
    edition_day,
    format_number,
    row_digest,
)
from app.modules.intelligence.newsroom_contracts import NewsConfig, NewsSettings
from tests.benchmark.news import dataset, warehouse
from tests.unit._newsroom import Newsroom, config, press_time, subject

BANDUNG_DAY = dataset.LAST_DAY
NATIONAL_DAY = dataset.LAST_DAY - timedelta(days=1)
ONLINE_DAY = dataset.LAST_DAY - timedelta(days=2)
SURABAYA_DAY = dataset.LAST_DAY - timedelta(days=3)
QUIET_DAY = dataset.LAST_DAY - timedelta(days=4)
BELOW_THRESHOLD_DAY = dataset.LAST_DAY - timedelta(days=5)


@pytest.fixture
def room(monkeypatch):
    return Newsroom(monkeypatch)


async def test_a_slice_anomaly_becomes_one_story_with_its_own_rows_only(room):
    await room.enable()

    result = await room.press(BANDUNG_DAY)

    assert result["status"] == "pressed"
    [story] = room.stories(BANDUNG_DAY)
    assert subject(story) == ("city", "Bandung")
    assert story.severity == "critical"
    assert story.relative_change == pytest.approx(-0.35, abs=0.03)
    assert story.after < story.before
    assert [proof.scope for proof in story.proofs] == ["slice"]
    assert story.proofs[0].value == "Bandung"
    assert story.drivers == []
    assert len(story.series) == config().days
    assert story.series[-1].date == BANDUNG_DAY
    assert story.series[-1].value == story.after
    assert story.baseline_dates == [BANDUNG_DAY - timedelta(weeks=week) for week in (1, 2, 3, 4)]


async def test_a_low_volume_swing_is_not_reported(room):
    await room.enable()

    await room.press(BANDUNG_DAY)

    assert ("category", "Collectibles") not in [subject(row) for row in room.stories()]


async def test_a_quiet_day_presses_an_empty_edition(room):
    await room.enable()

    result = await room.press(QUIET_DAY)

    assert result["stories"] == 0
    [edition] = room.editions()
    assert edition.story_ids == []
    assert len(edition.groups) == 4


async def test_a_change_below_the_materiality_threshold_is_not_reported(room):
    await room.enable()

    assert (await room.press(BELOW_THRESHOLD_DAY))["stories"] == 0


async def test_one_outlier_in_a_baseline_week_does_not_create_a_story(room):
    await room.enable()

    await room.press(SURABAYA_DAY)

    assert [subject(row) for row in room.stories()] == [("city", "Surabaya")]


async def test_a_channel_anomaly_is_reported_on_its_dimension_only(room):
    await room.enable()

    await room.press(ONLINE_DAY)

    [story] = room.stories()
    assert subject(story) == ("channel", "Online")
    assert story.severity == "warning"


async def test_a_national_move_leads_with_the_total_and_carries_drivers(room):
    await room.enable()

    await room.press(NATIONAL_DAY)

    stories = room.stories()
    lead = stories[0]
    assert subject(lead) == (None, None)
    assert [proof.scope for proof in lead.proofs] == ["total", "group"]
    assert {driver.dimension for driver in lead.drivers} == {"city"}
    assert len(lead.drivers) == 8
    assert lead.drivers[0].value == "Jakarta"
    assert [row.rank for row in stories] == list(range(1, len(stories) + 1))
    assert {subject(row) for row in stories[1:]} >= {("city", "Bandung"), ("channel", "Online")}


async def test_a_total_explained_by_one_slice_is_not_repeated(monkeypatch):
    rows = [row for row in dataset.generate() if row[1] in {"Bandung", "Denpasar"}]
    room = Newsroom(monkeypatch, rows=rows)
    await room.enable()

    await room.press(BANDUNG_DAY)

    subjects = [subject(row) for row in room.stories()]
    assert ("city", "Bandung") in subjects
    assert (None, None) not in subjects


async def test_the_story_limit_keeps_the_highest_ranked(room):
    await room.enable(max_stories=3)

    await room.press(NATIONAL_DAY)

    stories = room.stories()
    assert len(stories) == 3
    assert subject(stories[0]) == (None, None)


async def test_a_dimension_over_the_slice_limit_is_skipped_with_a_coverage_note(room):
    await room.enable(max_slice_values=5)

    result = await room.press(BANDUNG_DAY)

    assert result["stories"] == 0
    assert result["warnings"] == [
        "revenue by city: more values than the configured slice limit",
        "revenue by category: more values than the configured slice limit",
    ]
    [edition] = room.editions()
    assert [group.dimension for group in edition.groups] == [None, "channel"]


async def test_a_cycle_uses_one_query_per_metric_and_dimension(room):
    await room.enable()

    result = await room.press(NATIONAL_DAY)

    assert result["queries"] == 4
    assert room.warehouse.queries[warehouse.SERVICE.username] == 4


async def test_pressing_again_with_unchanged_data_writes_nothing(room):
    await room.enable()
    await room.press(BANDUNG_DAY)
    [first] = room.stories()
    saves = room.journal.saves

    await room.press(BANDUNG_DAY)

    [second] = room.stories()
    assert (second.id, second.revision) == (first.id, first.revision)
    assert room.journal.saves == saves


async def test_late_data_revises_the_story_and_resets_a_model_narrative(room):
    await room.enable()
    await room.press(BANDUNG_DAY)
    [first] = room.stories()
    room.journal.rows[("stories", first.id)] = first.model_copy(
        update={"narrative_source": "model", "narrative_model": "writer"}
    )
    late = (BANDUNG_DAY, "Bandung", "Store", "Grocery", Decimal(5_000_000), 40)
    room.warehouse.rows.append(late)

    await room.press(BANDUNG_DAY)

    [second] = room.stories()
    assert second.id == first.id
    assert second.revision == first.revision + 1
    assert second.proofs[0].digest != first.proofs[0].digest
    assert second.after == first.after + 5_000_000
    assert second.narrative_source == "template"


async def test_a_story_that_no_longer_holds_leaves_the_edition(room):
    await room.enable()
    await room.press(BANDUNG_DAY)
    index = {name: position for position, name in enumerate(dataset.COLUMNS)}
    room.warehouse.rows = [
        (
            (*row[:4], Decimal(round(row[4] / Decimal("0.65"))), round(row[5] / 0.65))
            if row[index["sale_date"]] == BANDUNG_DAY and row[index["city"]] == "Bandung"
            else row
        )
        for row in room.warehouse.rows
    ]

    await room.press(BANDUNG_DAY)

    [edition] = room.editions()
    assert edition.story_ids == []
    assert await room.read("news_manager", BANDUNG_DAY) == []


async def test_story_ids_do_not_derive_from_the_subject(room):
    await room.enable()
    await room.press(BANDUNG_DAY)
    [story] = room.stories()
    [edition] = room.editions()

    assert story.id != story.dedup_key
    assert len(edition.nonce) == 32
    assert edition.nonce not in story.id


async def test_the_cycle_refuses_to_run_as_any_other_identity(room):
    await room.enable()

    result = await room.service.run_cycle(
        warehouse.VIEW_ID, room.user("news_manager"), now=press_time(BANDUNG_DAY)
    )

    assert result == {"status": "disabled"}
    assert room.editions() == []


async def test_a_disabled_view_presses_nothing(room):
    await room.enable()
    await room.service.configure(
        warehouse.VIEW_ID, NewsSettings(enabled=False), room.user("news_manager")
    )

    assert await room.press(BANDUNG_DAY) == {"status": "disabled"}


async def test_an_account_that_reads_no_rows_presses_an_empty_edition_with_a_note(room):
    room.warehouse.principals[warehouse.SERVICE.username].scopes = {"city": set()}
    await room.enable()

    result = await room.press(BANDUNG_DAY)

    assert result["stories"] == 0
    assert "revenue by day: the execution account reads no rows" in result["warnings"]


async def test_generation_is_audited_without_story_content(room):
    await room.enable()
    room.audit.reset_mock()

    await room.press(BANDUNG_DAY)

    [call] = room.audit.await_args_list
    assert call.kwargs["action"] == "PRESS_NEWS"
    assert call.kwargs["user_name"] == warehouse.SERVICE.username
    assert call.kwargs["decision"] == "stories=1 written=0 rejected=0"
    # The audit column holds 32 characters, even for the largest edition.
    assert len("stories=24 written=3 rejected=3") <= 32
    assert "Bandung" not in str(call.kwargs)


def test_the_edition_is_the_last_complete_local_day():
    cfg = config()

    assert edition_day(cfg, press_time(BANDUNG_DAY)) == BANDUNG_DAY
    # 23:30 UTC on the 4th is already the 5th in Jakarta.
    assert edition_day(cfg, datetime(2026, 10, 4, 23, 30, tzinfo=UTC)) == BANDUNG_DAY
    assert edition_day(cfg, datetime(2026, 10, 4, 16, 59, tzinfo=UTC)) == BANDUNG_DAY - timedelta(
        days=1
    )


def test_equal_numbers_hash_equally_whatever_their_type():
    assert canonical(100) == canonical(Decimal("100.00")) == canonical(100.0)
    assert canonical(0.1 + 0.2) == canonical(Decimal("0.3"))
    assert canonical(0.0) == canonical(Decimal("-0")) == ["n", "0"]
    assert row_digest(["a", "b"], [[1, "x"], [2, "y"]]) == row_digest(
        ["a", "b"], [[Decimal("2.0"), "y"], [1.0, "x"]]
    )
    assert row_digest(["a"], [[1]]) != row_digest(["a"], [[1], [1]])
    assert row_digest(["a"], [[1]]) != row_digest(["b"], [[1]])


def test_amounts_read_naturally():
    assert format_number(254505601.4, "IDR") == "IDR 254,505,601"
    assert format_number(12.5, "orders") == "12.5 orders"
    assert format_number(0.25) == "0.25"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"metrics": ["revenue", "revenue"]}, "listed once"),
        ({"slice_dimensions": ["city", "city"]}, "listed once"),
        ({"timezone": "Mars/Olympus"}, "IANA timezone"),
        ({"max_slice_values": 100}, "at most 1000 rows"),
        ({"cadence_minutes": 5}, "greater than or equal to 15"),
        ({"metrics": []}, "at least 1 item"),
    ],
)
def test_the_configuration_is_bounded(overrides, message):
    with pytest.raises(ValueError, match=message):
        config(**overrides)


def test_one_query_never_asks_for_more_than_a_thousand_rows():
    assert NewsConfig.model_fields["max_slice_values"].default * config().days <= 1000


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"metrics": ["margin"]}, "Metric 'margin' is not in this view"),
        ({"count_metric": "visits"}, "Choose a record count metric of this view"),
        ({"time_dimension": "city"}, "Choose a time dimension of this view"),
        ({"slice_dimensions": ["region"]}, "Dimension 'region' is not available for News"),
        ({"slice_dimensions": ["sale_date"]}, "Dimension 'sale_date' is not available for News"),
    ],
)
async def test_the_configuration_must_match_the_published_view(room, overrides, detail):
    with pytest.raises(HTTPException) as refused:
        await room.enable(**overrides)

    assert refused.value.status_code == 422
    assert refused.value.detail == detail
    assert room.warehouse.view["news_enabled"] is False
    assert room.schedules == []


async def test_a_press_refused_by_the_view_is_audited_and_writes_nothing(room):
    await room.enable()
    room.warehouse.principals[warehouse.SERVICE.username].can_read = False
    room.audit.reset_mock()

    with pytest.raises(HTTPException) as refused:
        await room.press(BANDUNG_DAY)

    assert refused.value.status_code == 404
    [call] = room.audit.await_args_list
    assert (call.kwargs["action"], call.kwargs["status"]) == ("PRESS_NEWS", "FAILED")
    assert room.editions() == []
