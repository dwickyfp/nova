"""The News writer against a scripted provider: grounded drafts pass, the rest fall back.

Each case scripts what a model might return for the Bandung story and checks the
contract the newsroom relies on: a figure, another segment or a causal claim can
never reach a reader, and a failed draft leaves the deterministic narrative.
"""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.intelligence import newsroom_writer
from app.modules.intelligence.newsroom_writer import NarrativeRejected, validate_draft, write_story
from tests.benchmark.news import dataset, warehouse
from tests.unit._newsroom import Newsroom, config, subject

BANDUNG_DAY = dataset.LAST_DAY
NATIONAL_DAY = dataset.LAST_DAY - timedelta(days=1)
IR = SemanticModelIR.from_ossie(warehouse.DEFINITION)
KNOWN = {value for name in ("city", "channel", "category") for value in dataset.values(name)}
GROUNDED = {
    "headline": "{metric} {direction} sharply in {slice}",
    "deck": "The day closed at {after}, well under a normal {weekday} of {before}.",
    "what_happened": (
        "{metric} in {slice} came to {after} on {date}. A typical {weekday} over the last "
        "{baseline_weeks} weeks brought in {before}, leaving a gap of {change} ({change_pct})."
    ),
    "why_it_matters": (
        "A daily move of this size has to be explained in the weekly business review, and the "
        "regional manager is accountable for this {dimension}."
    ),
    "what_to_check": (
        "The reason is not yet known. Confirm the day's data is complete, then review store "
        "trading and stock availability in {slice}."
    ),
}


CLEAN = {"states_cause": False, "writes_quantity_in_words": False}


class ScriptedProvider:
    """Answers the draft request, then the review request, from a script."""

    def __init__(self, content, verdict=CLEAN, *, delay=0.0):
        self.content = content
        self.verdict = verdict
        self.delay = delay
        self.requests = []

    async def resolve(self, **_kwargs):
        return SimpleNamespace(model="scripted-writer", provider_id="scripted")

    async def complete(self, *, messages, provider=None, **_kwargs):
        self.requests.append(messages)
        await asyncio.sleep(self.delay)
        reviewing = messages[0]["content"] == newsroom_writer.REVIEW_INSTRUCTIONS
        content = self.verdict if reviewing else self.content
        return {
            "content": content if isinstance(content, str) else json.dumps(content),
            "usage": {"total_tokens": 321},
        }


@pytest.fixture
async def stories(monkeypatch):
    room = Newsroom(monkeypatch)
    await room.enable()
    await room.press(BANDUNG_DAY)
    await room.press(NATIONAL_DAY)
    [bandung] = room.stories(BANDUNG_DAY)
    return bandung, room.stories(NATIONAL_DAY)[0]


async def test_a_grounded_draft_is_published_with_server_rendered_figures(stories):
    bandung, _ = stories
    provider = ScriptedProvider(GROUNDED)

    narrative, model = await write_story(bandung, config(), IR, KNOWN, provider=provider)

    assert model == "scripted-writer"
    assert narrative.headline == "Revenue fell sharply in Bandung"
    assert narrative.deck == (
        "The day closed at IDR 254,505,601, well under a normal Sunday of IDR 389,127,213."
    )
    assert "IDR 134,621,612 (34.6%)" in narrative.what_happened
    assert not any("{" in text for text in narrative.model_dump().values())


async def test_the_provider_receives_only_this_story_and_published_business_rules(stories):
    bandung, _ = stories
    provider = ScriptedProvider(GROUNDED)

    await write_story(bandung, config(), IR, KNOWN, provider=provider)

    [[system, user], review] = provider.requests
    assert json.loads(review[1]["content"]) == GROUNDED
    payload = json.loads(user["content"])
    assert system["role"] == "system"
    assert payload["may_name"] == ["Bandung"]
    assert payload["slots"]["after"] == "IDR 254,505,601"
    assert [rule["about"] for rule in payload["business_rules"]] == [
        "Revenue", "City", "news_retail_sales", "Materiality",
    ]
    sent = user["content"]
    assert "Jakarta" not in sent and "Surabaya" not in sent
    assert warehouse.SERVICE.username not in sent
    assert "digest" not in sent and "proof" not in sent


@pytest.mark.parametrize(
    ("field", "text", "reason"),
    [
        ("headline", "{metric} fell 35% in {slice}", "ungrounded figure"),
        ("deck", "Sales fell ٣٥ percent.", "ungrounded figure"),
        ("what_to_check", "Compare {slice} with Jakarta, which held steady.", "another segment"),
        ("why_it_matters", "Online orders moved to other cities.", "another segment"),
        ("headline", "{metric} reached {total}", "unknown placeholder"),
        ("deck", "See <b>{after}</b> for details.", "markup"),
        ("what_happened", "{metric} {direction} in {slice}.", "figures missing"),
        ("headline", "", "empty"),
    ],
)
async def test_an_ungrounded_draft_is_rejected(stories, field, text, reason):
    bandung, _ = stories

    with pytest.raises(NarrativeRejected, match=reason):
        validate_draft(GROUNDED | {field: text}, bandung, config(), KNOWN)


@pytest.mark.parametrize(
    ("verdict", "reason"),
    [
        (CLEAN | {"states_cause": True}, "review: states_cause"),
        (CLEAN | {"writes_quantity_in_words": True}, "review: writes_quantity_in_words"),
        (CLEAN | {"states_cause": "no"}, "review: states_cause"),
        ({"states_cause": False}, "review: malformed"),
        (CLEAN | {"approved": True}, "review: malformed"),
        ("The draft looks fine.", "not JSON"),
    ],
)
async def test_a_draft_is_published_only_on_a_clean_review(stories, verdict, reason):
    bandung, _ = stories
    provider = ScriptedProvider(GROUNDED, verdict)

    with pytest.raises(NarrativeRejected, match=reason):
        await write_story(bandung, config(), IR, KNOWN, provider=provider)


async def test_a_structurally_rejected_draft_is_never_sent_for_review(stories):
    bandung, _ = stories
    provider = ScriptedProvider(GROUNDED | {"headline": "{metric} fell 35% in {slice}"})

    with pytest.raises(NarrativeRejected, match="ungrounded figure"):
        await write_story(bandung, config(), IR, KNOWN, provider=provider)

    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "draft",
    [
        "The revenue in Bandung fell 35%.",
        ["headline"],
        {"headline": "{metric} {direction}"},
        GROUNDED | {"summary": "extra"},
    ],
)
async def test_a_malformed_response_is_rejected(stories, draft):
    bandung, _ = stories

    with pytest.raises(NarrativeRejected):
        await write_story(bandung, config(), IR, KNOWN, provider=ScriptedProvider(draft))


async def test_a_figure_copied_verbatim_from_a_slot_is_accepted_and_any_other_is_not(stories):
    bandung, _ = stories
    literal = GROUNDED | {
        "what_happened": (
            "Revenue in Bandung was IDR 254,505,601 on Sunday, 4 October 2026, against a "
            "typical {weekday} of {before}."
        )
    }

    narrative = validate_draft(literal, bandung, config(), KNOWN)

    assert "IDR 254,505,601 on Sunday, 4 October 2026" in narrative.what_happened
    with pytest.raises(NarrativeRejected, match="ungrounded figure"):
        validate_draft(
            literal | {"deck": "Down from IDR 254,505,602 a week earlier."},
            bandung, config(), KNOWN,
        )


async def test_a_fenced_json_response_is_accepted(stories):
    bandung, _ = stories
    provider = ScriptedProvider("```json\n" + json.dumps(GROUNDED) + "\n```")

    narrative, _ = await write_story(bandung, config(), IR, KNOWN, provider=provider)

    assert narrative.headline == "Revenue fell sharply in Bandung"


async def test_a_total_story_may_name_its_drivers_but_has_no_slice_placeholder(stories):
    _, total = stories
    draft = GROUNDED | {
        "headline": "{metric} {direction} across the business",
        "deck": "The day closed at {after}, above a normal {weekday} of {before}.",
        "what_happened": (
            "{metric} came to {after} on {date} against {before}. Jakarta and Surabaya "
            "showed the largest movements."
        ),
        "why_it_matters": "A move of this size has to be explained in the weekly review.",
        "what_to_check": "The reason is not yet known. Confirm the data is complete.",
    }

    narrative = validate_draft(draft, total, config(), KNOWN)

    assert "Jakarta and Surabaya" in narrative.what_happened
    with pytest.raises(NarrativeRejected, match="unknown placeholder"):
        validate_draft(draft | {"headline": "{metric} in {slice}"}, total, config(), KNOWN)
    with pytest.raises(NarrativeRejected, match="another segment"):
        validate_draft(draft | {"deck": "{after} with Online ahead."}, total, config(), KNOWN)


async def test_a_slow_provider_times_out(stories, monkeypatch):
    bandung, _ = stories
    monkeypatch.setattr(newsroom_writer, "TIMEOUT_SECONDS", 0.05)

    with pytest.raises(TimeoutError):
        await write_story(
            bandung, config(), IR, KNOWN, provider=ScriptedProvider(GROUNDED, delay=1)
        )


def _writer(provider):
    async def write(story, cfg, ir, known):
        return await write_story(story, cfg, ir, known, provider=provider)

    return write


async def test_the_cycle_publishes_an_accepted_draft(monkeypatch):
    room = Newsroom(monkeypatch, writer=_writer(ScriptedProvider(GROUNDED)))
    await room.enable(narrative="model")

    result = await room.press(BANDUNG_DAY)

    [story] = room.stories(BANDUNG_DAY)
    assert (result["written"], result["rejected"]) == (1, 0)
    assert (story.narrative_source, story.narrative_model) == ("model", "scripted-writer")
    assert story.narrative.headline == "Revenue fell sharply in Bandung"
    [shown] = await room.read("news_bandung", BANDUNG_DAY)
    assert shown["narrative"]["headline"] == "Revenue fell sharply in Bandung"
    assert shown["narrative_source"] == "model"
    assert await room.read("news_jakarta", BANDUNG_DAY) == []


async def test_the_cycle_keeps_the_template_when_the_draft_is_rejected(monkeypatch):
    hallucinated = GROUNDED | {"headline": "Revenue fell 35% in Bandung as Jakarta surged"}
    room = Newsroom(monkeypatch, writer=_writer(ScriptedProvider(hallucinated)))
    await room.enable(narrative="model")

    result = await room.press(BANDUNG_DAY)

    [story] = room.stories(BANDUNG_DAY)
    assert (result["written"], result["rejected"]) == (0, 1)
    assert story.narrative_source == "template"
    assert story.narrative.headline == "Revenue fell 34.6% in Bandung"


async def test_the_cycle_keeps_the_template_when_the_review_finds_a_cause(monkeypatch):
    provider = ScriptedProvider(GROUNDED, CLEAN | {"states_cause": True})
    room = Newsroom(monkeypatch, writer=_writer(provider))
    await room.enable(narrative="model")

    result = await room.press(BANDUNG_DAY)

    assert (result["written"], result["rejected"]) == (0, 1)
    assert room.stories(BANDUNG_DAY)[0].narrative_source == "template"


async def test_the_cycle_keeps_the_template_when_the_provider_fails(monkeypatch):
    class Unavailable(ScriptedProvider):
        async def resolve(self, **_kwargs):
            raise RuntimeError("no default model")

    room = Newsroom(monkeypatch, writer=_writer(Unavailable(GROUNDED)))
    await room.enable(narrative="model")

    result = await room.press(BANDUNG_DAY)

    assert (result["status"], result["written"], result["rejected"]) == ("pressed", 0, 1)
    assert room.stories(BANDUNG_DAY)[0].narrative_source == "template"


async def test_the_cycle_never_calls_the_model_for_a_template_view(monkeypatch):
    provider = ScriptedProvider(GROUNDED)
    room = Newsroom(monkeypatch, writer=_writer(provider))
    await room.enable(narrative="template")

    result = await room.press(BANDUNG_DAY)

    assert result["written"] == 0
    assert provider.requests == []


async def test_the_cycle_drafts_at_most_three_stories_and_never_rewrites_one(monkeypatch):
    total_draft = GROUNDED | {
        "headline": "{metric} {direction} on {date}",
        "deck": "The day closed at {after}, against {before}.",
        "what_happened": "{metric} came to {after} against a usual {before}.",
        "why_it_matters": "A move of this size has to be explained in the weekly review.",
        "what_to_check": "The reason is not yet known. Confirm the data is complete.",
    }
    provider = ScriptedProvider(total_draft)
    room = Newsroom(monkeypatch, writer=_writer(provider))
    await room.enable(narrative="model")

    first = await room.press(NATIONAL_DAY)
    second = await room.press(NATIONAL_DAY)

    assert (first["written"], second["written"]) == (3, 3)
    assert len(provider.requests) == 12
    written = [row for row in room.stories(NATIONAL_DAY) if row.narrative_source == "model"]
    assert [row.rank for row in written] == [1, 2, 3, 4, 5, 6]
    assert subject(written[0]) == (None, None)
