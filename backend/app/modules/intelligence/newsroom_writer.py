"""Model-written narrative for one News story, accepted only when it is grounded.

Detection is deterministic and already complete when the writer runs. The model
receives one story's proven figures and the business rules quoted from the
published Semantic View, and returns prose in which every figure is a
placeholder. The server fills the placeholders from the proven rows, so the
model cannot state a number, and a deterministic check rejects any digit or any
other slice's name. A second model call then reviews the draft for what only
reading can tell: a stated cause, or a quantity spelled in words. A rejected
draft leaves the template narrative in place.
"""

from __future__ import annotations

import asyncio
import json
import re

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.assistant.provider import assistant_provider
from app.modules.intelligence.newsroom import story_slots
from app.modules.intelligence.newsroom_contracts import Narrative, NewsConfig, Story

TIMEOUT_SECONDS = 25
FIELDS = ("headline", "deck", "what_happened", "why_it_matters", "what_to_check")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
INSTRUCTIONS = (
    "You are the business editor of an internal company newspaper read by managers. "
    "Rewrite one detected change in a business metric as a short, plain news story. "
    "Return only a JSON object with the string keys headline, deck, what_happened, "
    "why_it_matters and what_to_check.\n"
    "Rules:\n"
    "1. Never write a digit or a spelled-out number. Every figure and every date must be one "
    "of the placeholders listed under slots, written exactly like {after} or {date}. The "
    "values shown are for your understanding only. Do not restate quantities that appear in "
    "the business rules.\n"
    "2. what_happened must use {after} and {before}. {before} is the typical level for the "
    "same weekday over the last {baseline_weeks} weeks, not the previous week.\n"
    "3. Name no city, channel, category or other segment except the ones listed under "
    "may_name.\n"
    "4. Do not state or imply a cause. The cause is not established; say what a manager should "
    "check next instead.\n"
    "5. why_it_matters explains the business importance using only the business_rules given, "
    "in your own words and without figures.\n"
    "6. Plain sentences, no markup, no bullet points. headline at most 12 words; each other "
    "field at most 3 sentences."
)


REVIEW_INSTRUCTIONS = (
    "You check one draft story for an internal company newspaper before it is published. "
    "The draft may be in any language. Text in curly braces such as {after} is a placeholder "
    "for a verified figure and is always acceptable. Return only a JSON object with two boolean "
    "keys. states_cause: true if any sentence states or implies why the change happened. "
    "writes_quantity_in_words: true if any sentence expresses an amount, share, multiple or "
    "count in words instead of a placeholder."
)
VERDICT = {"states_cause", "writes_quantity_in_words"}


class NarrativeRejected(ValueError):
    """The draft broke a grounding rule; the template narrative stays."""


def _allowed_slots(story: Story, slots: dict[str, str]) -> set[str]:
    return {name for name, value in slots.items() if value}


def validate_draft(
    draft: object, story: Story, config: NewsConfig, known_values: set[str]
) -> Narrative:
    """Render a model draft into a narrative, or raise :class:`NarrativeRejected`."""
    if not isinstance(draft, dict) or set(draft) != set(FIELDS):
        raise NarrativeRejected("fields")
    slots = story_slots(story, config)
    allowed = _allowed_slots(story, slots)
    may_name = {driver.value for driver in story.drivers}
    if story.slice:
        may_name.add(story.slice.value)
    foreign = [value for value in known_values if value not in may_name]
    rendered = {}
    for name in FIELDS:
        text = draft[name]
        if not isinstance(text, str) or not text.strip():
            raise NarrativeRejected(f"{name}: empty")
        # A figure copied verbatim from a slot is that slot; any other digit is not.
        for slot, value in slots.items():
            if value and any(character.isdigit() for character in value):
                text = text.replace(value, "{" + slot + "}")
        used = set(_PLACEHOLDER.findall(text))
        if used - allowed:
            raise NarrativeRejected(f"{name}: unknown placeholder")
        prose = _PLACEHOLDER.sub(" ", text)
        if "{" in prose or "}" in prose or "<" in prose or ">" in prose or "http" in prose.lower():
            raise NarrativeRejected(f"{name}: markup")
        if any(character.isdigit() for character in prose):
            raise NarrativeRejected(f"{name}: ungrounded figure")
        # Matched as written in the data: "Store" the channel, not "store" the noun.
        for value in foreign:
            if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", prose):
                raise NarrativeRejected(f"{name}: names another segment")
        rendered[name] = " ".join(
            _PLACEHOLDER.sub(lambda match: slots[match.group(1)], text).split()
        )
    if not all(slots[name] in rendered["what_happened"] for name in ("after", "before")):
        raise NarrativeRejected("what_happened: figures missing")
    try:
        return Narrative(**rendered)
    except ValueError as exc:
        raise NarrativeRejected("length") from exc


def _parse(content: str) -> object:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise NarrativeRejected("not JSON") from exc


def request(story: Story, config: NewsConfig, ir: SemanticModelIR) -> list[dict]:
    """The only content sent to the provider: one story's proven facts."""
    slots = story_slots(story, config)
    return [
        {"role": "system", "content": INSTRUCTIONS},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "view": ir.name,
                    "severity": story.severity,
                    "scope": "one segment" if story.slice else "the whole view",
                    "slots": {name: value for name, value in slots.items() if value},
                    "may_name": ([story.slice.value] if story.slice else [])
                    + [driver.value for driver in story.drivers],
                    "business_rules": [
                        {"about": rule.name, "rule": rule.text} for rule in story.business_rules
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]


def check_verdict(verdict: object) -> None:
    """Accept a draft only on an explicit, well-formed clean verdict."""
    if not isinstance(verdict, dict) or set(verdict) != VERDICT:
        raise NarrativeRejected("review: malformed")
    for name in sorted(VERDICT):
        if verdict[name] is not False:
            raise NarrativeRejected(f"review: {name}")


async def write_story(
    story: Story,
    config: NewsConfig,
    ir: SemanticModelIR,
    known_values: set[str],
    *,
    provider=assistant_provider,
) -> tuple[Narrative, str]:
    """Draft, check structurally, then have the model review what code cannot read.

    Code verifies what is structural in any language: the fields, the
    placeholders, the absence of digits and of other segments' names. Whether a
    sentence asserts a cause or spells a quantity is a question of meaning, so a
    second model call answers it with two booleans and code checks that answer.
    """
    async with asyncio.timeout(TIMEOUT_SECONDS):
        resolved = await provider.resolve()
        answer = await provider.complete(messages=request(story, config, ir), provider=resolved)
        draft = _parse(str(answer.get("content") or ""))
        narrative = validate_draft(draft, story, config, known_values)
        review = await provider.complete(
            messages=[
                {"role": "system", "content": REVIEW_INSTRUCTIONS},
                {"role": "user", "content": json.dumps(draft, ensure_ascii=False)},
            ],
            provider=resolved,
        )
    check_verdict(_parse(str(review.get("content") or "")))
    return narrative, str(resolved.model)[:256]
