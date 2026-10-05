"""Reading the shared edition: a story exists only for a caller who proves it."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.modules.intelligence.newsroom_contracts import NewsSettings
from tests.benchmark.news import dataset, warehouse
from tests.unit._newsroom import Newsroom, subject

BANDUNG_DAY = dataset.LAST_DAY
NATIONAL_DAY = dataset.LAST_DAY - timedelta(days=1)
ONLINE_DAY = dataset.LAST_DAY - timedelta(days=2)
BANDUNG = ("city", "Bandung")
TOTAL = (None, None)


@pytest.fixture
async def room(monkeypatch):
    newsroom = Newsroom(monkeypatch)
    await newsroom.enable()
    for day in (ONLINE_DAY, NATIONAL_DAY, BANDUNG_DAY):
        await newsroom.press(day)
    return newsroom


async def subjects(room, name, day):
    return [subject(story) for story in await room.read(name, day)]


@pytest.mark.parametrize(
    ("reader", "sees"),
    [
        ("news_manager", [BANDUNG]),
        ("news_bandung", [BANDUNG]),
        ("news_west_java", [BANDUNG]),
        ("news_jakarta", []),
        ("news_online", []),
        ("news_masked", []),
        ("news_outsider", []),
    ],
)
async def test_the_bandung_story_follows_row_access(room, reader, sees):
    assert await subjects(room, reader, BANDUNG_DAY) == sees


async def test_same_role_users_with_different_cities_see_different_stories(room):
    bandung, jakarta = room.user("news_bandung"), room.user("news_jakarta")
    assert bandung["active_role"] == jakarta["active_role"]

    assert await subjects(room, "news_bandung", NATIONAL_DAY) == [BANDUNG]
    assert await subjects(room, "news_jakarta", NATIONAL_DAY) == [("city", "Jakarta")]


async def test_an_aggregate_story_is_hidden_from_partial_access(room):
    manager = await subjects(room, "news_manager", NATIONAL_DAY)
    assert manager[0] == TOTAL

    for reader in ("news_bandung", "news_jakarta", "news_west_java", "news_online"):
        assert TOTAL not in await subjects(room, reader, NATIONAL_DAY)


async def test_a_scope_on_another_dimension_hides_a_city_story(room):
    assert await subjects(room, "news_online", ONLINE_DAY) == [("channel", "Online")]
    assert BANDUNG not in await subjects(room, "news_online", NATIONAL_DAY)


async def test_every_reader_matches_the_access_oracle_on_every_edition(room):
    leaks = []
    for day in (ONLINE_DAY, NATIONAL_DAY, BANDUNG_DAY):
        pressed = {subject(row) for row in room.stories(day)}
        for name, principal in room.warehouse.principals.items():
            shown = set(await subjects(room, name, day))
            allowed = {item for item in pressed if warehouse.may_see(principal, *item)}
            if principal.news_enabled and shown != allowed:
                leaks.append((day.isoformat(), name, sorted(shown ^ allowed, key=str)))

    assert leaks == []


async def test_drivers_of_other_cities_reach_only_readers_of_the_whole_view(room):
    stories = await room.read("news_manager", NATIONAL_DAY)
    [lead] = [story for story in stories if not story["slice"]]
    assert {driver["value"] for driver in lead["drivers"]} == set(dataset.CITIES)

    for story in await room.read("news_bandung", NATIONAL_DAY):
        assert story["drivers"] == []
        assert "Jakarta" not in str(story)


async def test_a_reader_receives_no_proof_or_producer_identity(room):
    [story] = await room.read("news_bandung", BANDUNG_DAY)

    assert not {"proofs", "scope", "dedup_key", "edition_id", "narrative_model"} & set(story)
    assert warehouse.SERVICE.username not in str(story)
    assert story["view_id"] == warehouse.VIEW_ID
    assert story["narrative"]["headline"] == "Revenue fell 34.6% in Bandung"


async def test_a_hidden_story_leaves_no_trace_in_the_section(room):
    jakarta = await room.service.newspaper(room.user("news_jakarta"), day=BANDUNG_DAY)
    await room.press(dataset.LAST_DAY - timedelta(days=4))
    quiet = await room.service.newspaper(
        room.user("news_jakarta"), day=dataset.LAST_DAY - timedelta(days=4)
    )

    [hidden], [empty] = jakarta["sections"], quiet["sections"]
    assert hidden["stories"] == empty["stories"] == []
    assert set(hidden) == set(empty) == {
        "view_id", "name", "edition_date", "pressed_at", "stories",
    }


async def test_a_caller_without_access_to_the_view_gets_no_section(room):
    paper = await room.service.newspaper(room.user("news_outsider"), day=BANDUNG_DAY)

    assert paper == {"edition_date": None, "sections": []}


async def test_the_reader_runs_the_same_queries_whatever_stories_exist(room):
    counts = {}
    for day in (BANDUNG_DAY, NATIONAL_DAY):
        for name in ("news_jakarta", "news_manager"):
            before = room.warehouse.queries[name]
            await room.read(name, day)
            counts[(day, name)] = room.warehouse.queries[name] - before

    assert set(counts.values()) == {4}


async def test_the_latest_edition_is_read_when_no_date_is_given(room):
    paper = await room.service.newspaper(room.user("news_manager"))

    assert paper["edition_date"] == BANDUNG_DAY.isoformat()
    assert [subject(story) for story in paper["sections"][0]["stories"]] == [BANDUNG]


async def test_switching_the_view_off_removes_the_edition_at_once(room):
    await room.service.configure(
        warehouse.VIEW_ID, NewsSettings(enabled=False), room.user("news_manager")
    )

    assert await room.service.newspaper(room.user("news_manager"), day=BANDUNG_DAY) == {
        "edition_date": None,
        "sections": [],
    }


async def test_a_new_row_filter_hides_the_story_on_the_next_read(room):
    assert await subjects(room, "news_manager", BANDUNG_DAY) == [BANDUNG]

    room.warehouse.principals["news_manager"].scopes = {"city": {"Jakarta"}}

    assert await subjects(room, "news_manager", BANDUNG_DAY) == []


async def test_a_new_mask_hides_the_story_on_the_next_read(room):
    room.warehouse.principals["news_bandung"].masked = True

    assert await subjects(room, "news_bandung", BANDUNG_DAY) == []


async def test_a_revoked_role_hides_the_whole_section(room):
    room.warehouse.principals["news_bandung"].can_read = False

    paper = await room.service.newspaper(room.user("news_bandung"), day=BANDUNG_DAY)

    assert paper["sections"] == []


async def test_switching_to_a_role_without_access_hides_the_section(room):
    switched = room.user("news_bandung") | {
        "active_role": "finance",
        "roles": ["finance"],
        "assigned_roles": ["finance"],
        "security_context_version": 2,
    }

    assert (await room.service.newspaper(switched, day=BANDUNG_DAY))["sections"] == []


async def test_data_that_changed_after_the_press_hides_the_story_until_repressed(room):
    room.warehouse.rows.append(
        (BANDUNG_DAY, "Bandung", "Store", "Grocery", Decimal(5_000_000), 40)
    )

    assert await subjects(room, "news_manager", BANDUNG_DAY) == []

    await room.press(BANDUNG_DAY)

    assert await subjects(room, "news_manager", BANDUNG_DAY) == [BANDUNG]
    assert await subjects(room, "news_bandung", BANDUNG_DAY) == [BANDUNG]


async def test_an_edition_from_another_account_is_never_read(room):
    room.warehouse.view["news_config"]["execution_user"] = "nova_task_service_other"

    assert (await room.service.newspaper(room.user("news_manager"), day=BANDUNG_DAY))[
        "sections"
    ] == []


async def test_a_story_opens_for_a_reader_who_proves_it(room):
    [listed] = await room.read("news_bandung", BANDUNG_DAY)

    story = await room.service.story(listed["id"], room.user("news_bandung"))

    assert story["id"] == listed["id"]
    assert story["view_name"] == "news_retail_sales"
    assert story["narrative"] == listed["narrative"]


async def test_opening_a_story_runs_only_its_own_proof_query(room):
    [listed] = await room.read("news_bandung", BANDUNG_DAY)
    before = room.warehouse.queries["news_bandung"]

    await room.service.story(listed["id"], room.user("news_bandung"))

    assert room.warehouse.queries["news_bandung"] - before == 1


@pytest.mark.parametrize(
    "reader", ["news_jakarta", "news_online", "news_masked", "news_outsider"]
)
async def test_a_known_id_reveals_nothing_to_a_reader_without_access(room, reader):
    [listed] = await room.read("news_manager", BANDUNG_DAY)

    with pytest.raises(HTTPException) as hidden:
        await room.service.story(listed["id"], room.user(reader))
    with pytest.raises(HTTPException) as missing:
        await room.service.story("0" * 64, room.user(reader))

    assert (hidden.value.status_code, hidden.value.detail) == (404, "Record unavailable")
    assert (missing.value.status_code, missing.value.detail) == (404, "Record unavailable")


async def test_a_story_of_a_disabled_view_cannot_be_opened(room):
    [listed] = await room.read("news_manager", BANDUNG_DAY)
    await room.service.configure(
        warehouse.VIEW_ID, NewsSettings(enabled=False), room.user("news_manager")
    )

    with pytest.raises(HTTPException) as refused:
        await room.service.story(listed["id"], room.user("news_manager"))

    assert refused.value.status_code == 404


async def test_a_story_retired_from_its_edition_cannot_be_opened(room):
    [listed] = await room.read("news_manager", BANDUNG_DAY)
    [edition] = [row for row in room.editions() if row.edition_date == BANDUNG_DAY]
    room.journal.rows[("editions", edition.id)] = edition.model_copy(update={"story_ids": []})

    with pytest.raises(HTTPException) as refused:
        await room.service.story(listed["id"], room.user("news_manager"))

    assert refused.value.status_code == 404


async def test_reading_the_newspaper_writes_one_audit_row_with_the_visible_count(room):
    room.audit.reset_mock()

    await room.read("news_bandung", NATIONAL_DAY)

    [call] = room.audit.await_args_list
    assert call.kwargs["action"] == "READ_NEWSPAPER"
    assert (call.kwargs["status"], call.kwargs["decision"]) == ("SUCCESS", "ALLOW")
    assert call.kwargs["rows_affected"] == 1
    assert call.kwargs["user_name"] == "news_bandung"
    assert call.kwargs["active_role"] == "news_reader"
    assert call.kwargs["security_context_version"] == 1


async def test_a_refused_story_is_audited_without_its_content(room):
    [listed] = await room.read("news_manager", BANDUNG_DAY)
    room.audit.reset_mock()

    with pytest.raises(HTTPException):
        await room.service.story(listed["id"], room.user("news_jakarta"))

    [call] = room.audit.await_args_list
    assert (call.kwargs["action"], call.kwargs["status"], call.kwargs["decision"]) == (
        "READ_STORY", "DENIED", "DENY",
    )
    assert call.kwargs["object_name"] == listed["id"]
    assert "Bandung" not in str(call.kwargs)
