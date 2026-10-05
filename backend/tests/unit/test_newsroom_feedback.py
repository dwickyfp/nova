"""Likes and dislikes: private to a reader, proof-gated, and the order they teach."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.intelligence import newsroom_feedback
from app.modules.intelligence.newsroom import impact
from app.modules.intelligence.newsroom_feedback import LARGEST, features, rank
from app.modules.intelligence.newsroom_writer import judge_polarity
from tests.benchmark.news import dataset, warehouse
from tests.unit._newsroom import Newsroom, subject

BANDUNG_DAY = dataset.LAST_DAY
NATIONAL_DAY = dataset.LAST_DAY - timedelta(days=1)
IR = SemanticModelIR.from_ossie(warehouse.DEFINITION)


@pytest.fixture
async def room(monkeypatch):
    newsroom = Newsroom(monkeypatch)
    await newsroom.enable()
    await newsroom.press(NATIONAL_DAY)
    await newsroom.press(BANDUNG_DAY)
    return newsroom


def find(stories, dimension, value):
    return next(row for row in stories if subject(row) == (dimension, value))


async def test_without_reactions_the_most_startling_story_leads(room):
    stories = await room.read("news_manager", NATIONAL_DAY)

    assert subject(stories[0]) == (None, None)
    assert stories[0]["head"] is True
    assert stories[0]["reason"] == LARGEST
    assert [row["head"] for row in stories[1:]] == [False] * (len(stories) - 1)
    assert all(row["reaction"] is None for row in stories)
    assert [row["score"] for row in stories] == sorted(
        (row["score"] for row in stories), reverse=True
    )


async def test_liking_a_subject_makes_it_the_head_story_for_that_reader_only(room):
    manager = room.user("news_manager")
    stories = await room.read("news_manager", NATIONAL_DAY)
    for day in (NATIONAL_DAY, BANDUNG_DAY):
        bandung = find(await room.read("news_manager", day), "city", "Bandung")
        await room.service.react(bandung["id"], "like", manager)

    again = await room.read("news_manager", NATIONAL_DAY)

    assert subject(again[0]) == ("city", "Bandung")
    assert again[0]["reason"] == "Matches stories you liked: City · Bandung"
    assert again[0]["reaction"] == "like"
    assert {row["id"] for row in again} == {row["id"] for row in stories}
    # Another reader of the same edition keeps the unpersonalised order.
    other = dict(room.warehouse.principals)
    other["news_manager_2"] = warehouse.Principal("news_manager_2", "news_editor")
    room.warehouse.principals = other
    assert subject((await room.read("news_manager_2", NATIONAL_DAY))[0]) == (None, None)


async def test_a_disliked_story_sinks_but_stays_and_similar_ones_fall(room):
    manager = room.user("news_manager")
    before = await room.read("news_manager", NATIONAL_DAY)
    total = before[0]
    online = find(before, "channel", "Online")
    position = [row["id"] for row in before].index(online["id"])
    await room.service.react(total["id"], "dislike", manager)
    await room.service.react(online["id"], "dislike", manager)

    after = await room.read("news_manager", NATIONAL_DAY)

    assert len(after) == len(before)
    assert {after[-1]["id"], after[-2]["id"]} == {total["id"], online["id"]}
    assert after[0]["id"] != total["id"]
    # Other channel stories lose ground because the reader disliked a channel story.
    store = find(after, "channel", "Store")
    assert store["score"] < find(before, "channel", "Store")["score"]
    assert position < len(before) - 2


async def test_clearing_a_reaction_restores_the_order(room):
    manager = room.user("news_manager")
    before = [row["id"] for row in await room.read("news_manager", NATIONAL_DAY)]
    total = before[0]
    await room.service.react(total, "dislike", manager)

    result = await room.service.react(total, None, manager)

    assert result == {"id": total, "reaction": None}
    assert [row["id"] for row in await room.read("news_manager", NATIONAL_DAY)] == before


@pytest.mark.parametrize("reader", ["news_jakarta", "news_online", "news_outsider"])
async def test_a_reader_cannot_react_to_a_story_they_cannot_read(room, reader):
    [bandung] = await room.read("news_manager", BANDUNG_DAY)

    with pytest.raises(HTTPException) as refused:
        await room.service.react(bandung["id"], "like", room.user(reader))

    assert refused.value.status_code == 404
    assert room.reactions.saved == {}


async def test_the_stored_traits_come_from_the_story_not_the_caller(room):
    [bandung] = await room.read("news_bandung", BANDUNG_DAY)

    await room.service.react(bandung["id"], "like", room.user("news_bandung"))

    [saved] = room.reactions.saved.values()
    assert saved["features"] == {
        "view": warehouse.VIEW_ID,
        "metric": "revenue",
        "direction": "decrease",
        "dimension": "city",
        "slice": "city=Bandung",
    }
    assert list(room.reactions.saved) == [("news_bandung", bandung["id"])]


async def test_a_reaction_is_audited_without_story_content(room):
    [bandung] = await room.read("news_manager", BANDUNG_DAY)
    room.audit.reset_mock()

    await room.service.react(bandung["id"], "like", room.user("news_manager"))

    calls = [call.kwargs for call in room.audit.await_args_list]
    assert [(call["action"], call["decision"]) for call in calls] == [
        ("READ_STORY", "ALLOW"),
        ("REACT_STORY", "LIKE"),
    ]
    assert "Bandung" not in str(calls)


def test_one_vote_moves_interest_a_little_and_many_move_it_more():
    def story(identifier, value):
        return {
            "id": identifier, "view_id": "v", "metric": "revenue", "change": -1.0,
            "relative_change": -0.2, "severity": "warning", "rank": 1,
            "slice": {"dimension": "city", "value": value},
        }

    stories = [story("a", "Bandung"), story("b", "Jakarta")]
    one = [{"story_id": "x", "reaction": "like", "features": features(story("x", "Jakarta"))}]

    assert [row["id"] for row in rank(stories, [])] == ["a", "b"]
    once, many = rank(stories, one), rank(stories, one * 6)
    assert [row["id"] for row in once] == ["b", "a"]
    assert many[0]["score"] > once[0]["score"]


async def test_a_reaction_store_outage_means_no_personalisation(monkeypatch):
    async def down(*_args, **_kwargs):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(newsroom_feedback.db, "execute_system", down)

    assert await newsroom_feedback.FeedbackStore().rows("news_manager") == []


@pytest.mark.parametrize(
    ("polarity", "change", "expected"),
    [
        ("better", 5.0, "favorable"),
        ("better", -5.0, "unfavorable"),
        ("worse", 5.0, "unfavorable"),
        ("worse", -5.0, "favorable"),
        ("neutral", 5.0, "neutral"),
        (None, 5.0, None),
    ],
)
def test_impact_follows_the_metric_polarity(polarity, change, expected):
    assert impact(polarity, change) == expected


class Judge:
    def __init__(self, verdict="better"):
        self.verdict = verdict
        self.calls = []

    async def __call__(self, ir, metric):
        self.calls.append(metric)
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


async def test_the_judged_polarity_colours_stories_and_is_reused(monkeypatch):
    judge = Judge("better")
    room = Newsroom(monkeypatch, judge=judge)
    await room.enable(narrative="model")

    await room.press(NATIONAL_DAY)
    await room.press(BANDUNG_DAY)

    assert judge.calls == ["revenue"]
    [bandung] = await room.read("news_manager", BANDUNG_DAY)
    assert bandung["impact"] == "unfavorable"
    assert (await room.read("news_manager", NATIONAL_DAY))[0]["impact"] == "favorable"


@pytest.mark.parametrize("verdict", [None, RuntimeError("provider down")])
async def test_an_unjudged_metric_leaves_the_impact_open(monkeypatch, verdict):
    room = Newsroom(monkeypatch, judge=Judge(verdict))
    await room.enable(narrative="model")

    result = await room.press(BANDUNG_DAY)

    assert result["status"] == "pressed"
    assert (await room.read("news_manager", BANDUNG_DAY))[0]["impact"] is None


async def test_a_template_view_never_asks_the_model_about_polarity(monkeypatch):
    judge = Judge("better")
    room = Newsroom(monkeypatch, judge=judge)
    await room.enable(narrative="template")

    await room.press(BANDUNG_DAY)

    assert judge.calls == []


class Provider:
    def __init__(self, content):
        self.content = content
        self.messages = None

    async def resolve(self, **_kwargs):
        return SimpleNamespace(model="scripted")

    async def complete(self, *, messages, provider=None, **_kwargs):
        self.messages = messages
        return {"content": self.content}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"higher_is": "better"}', "better"),
        ('```json\n{"higher_is": "worse"}\n```', "worse"),
        ('{"higher_is": "neutral"}', "neutral"),
        ('{"higher_is": "good"}', None),
        ('{"higher_is": "better", "why": "x"}', None),
        ("Higher revenue is better.", None),
    ],
)
async def test_polarity_is_accepted_only_as_an_exact_value(content, expected):
    provider = Provider(content)

    assert await judge_polarity(IR, "revenue", provider=provider) == expected
    sent = provider.messages[1]["content"]
    assert "revenue" in sent and "Bandung" not in sent


async def test_highlights_are_only_proven_values(room):
    [bandung] = await room.read("news_manager", BANDUNG_DAY)

    assert bandung["highlights"] == [
        {"text": "IDR 134,621,612", "kind": "change"},
        {"text": "34.6%", "kind": "change"},
        {"text": "IDR 254,505,601", "kind": "figure"},
        {"text": "IDR 389,127,213", "kind": "figure"},
        {"text": "Revenue", "kind": "subject"},
        {"text": "Bandung", "kind": "subject"},
    ]
