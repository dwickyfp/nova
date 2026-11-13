"""Per-reader reactions to News stories and the ordering learned from them.

A like or a dislike belongs to one reader. It never changes what another reader
sees and never widens access: ranking only reorders stories the reader has
already proven they can read. The learning is a transparent count of what the
reader liked and disliked, kept with the story's features so it can later feed
a model without replaying the stories themselves.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Literal

from app.modules.intelligence.metadata_repository import intelligence_metadata, user_actor

logger = logging.getLogger(__name__)

Reaction = Literal["like", "dislike"]

#: How much each shared trait of a story counts toward a reader's interest.
_WEIGHTS = {"slice": 1.0, "metric": 0.4, "dimension": 0.4, "direction": 0.2}
_HISTORY = 2000
LARGEST = "Largest move in this edition"


def features(story: dict) -> dict[str, str]:
    """The traits of a public story that preferences are learned over."""
    sliced = story.get("slice") or {}
    traits = {
        "view": str(story["view_id"]),
        "metric": str(story["metric"]),
        "direction": "increase" if story["change"] > 0 else "decrease",
    }
    if sliced:
        traits["dimension"] = str(sliced["dimension"])
        traits["slice"] = f"{sliced['dimension']}={sliced['value']}"
    return traits


class FeedbackStore:
    """The reader's own reactions, kept in the control metadata as that reader."""

    async def set(self, user: dict, story: dict, reaction: Reaction | None) -> None:
        async with intelligence_metadata(user_actor(user)) as repo:
            await repo.set_feedback(
                story["id"], reaction, features(story) if reaction is not None else None
            )

    async def rows(self, user: dict) -> list[dict]:
        """The reader's own reactions, newest first; an outage means no preference."""
        try:
            async with intelligence_metadata(user_actor(user), read_only=True) as repo:
                rows = await repo.feedback(_HISTORY)
        except Exception as exc:
            logger.warning("News preferences are unavailable: %s", type(exc).__name__)
            return []
        return [
            {"story_id": row["story_id"], "reaction": row["reaction"],
             "features": row["features"] or {}}
            for row in rows
        ]


def _interest(rows: list[dict]) -> dict[tuple[str, str], float]:
    """Smoothed like-minus-dislike share per trait; a single vote moves it little."""
    votes: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        for kind in _WEIGHTS:
            value = (row.get("features") or {}).get(kind)
            if value:
                votes[(kind, value)][0 if row["reaction"] == "like" else 1] += 1
    return {
        key: (likes - dislikes) / (likes + dislikes + 2) for key, (likes, dislikes) in votes.items()
    }


def _label(kind: str, value: str) -> str:
    """A trait as a reader would name it: "City · Bandung", "Revenue"."""
    if kind == "slice":
        dimension, _, name = value.partition("=")
        return f"{dimension.replace('_', ' ').strip().capitalize()} · {name}"
    return value.replace("_", " ").strip().capitalize()


def rank(stories: list[dict], rows: list[dict]) -> list[dict]:
    """Order one reader's visible stories; the first is their head story.

    ``base`` is how startling the story is for anyone. ``interest`` leans it
    toward what this reader liked and away from what they disliked. A story
    the reader disliked sinks to the end but is never removed.
    """
    interest = _interest(rows)
    reacted = {row["story_id"]: row["reaction"] for row in rows}
    scored = []
    for story in stories:
        traits = features(story)
        pull = {
            (kind, traits[kind]): _WEIGHTS[kind] * interest.get((kind, traits[kind]), 0.0)
            for kind in _WEIGHTS
            if kind in traits
        }
        lean = max(-1.0, min(1.0, sum(pull.values())))
        # A critical move outranks a material one, and a move of the whole view
        # outranks a slice of similar size.
        significance = story.get("significance") or {}
        base = (
            (2.0 if story["severity"] == "critical" else 1.0)
            + min(abs(story.get("relative_change") or 0.0), 1.0)
            + (0.0 if story.get("slice") else 0.5)
            + float(significance.get("boost") or 0.0)
        )
        reaction = reacted.get(story["id"])
        score = base * (1 + lean) - (100.0 if reaction == "dislike" else 0.0)
        strongest = max(pull.items(), key=lambda item: item[1], default=(None, 0.0))
        scored.append(
            story
            | {
                "reaction": reaction,
                "score": round(score, 4),
                "head": False,
                "reason": (
                    f"Matches stories you liked: {_label(*strongest[0])}"
                    if strongest[0] is not None and strongest[1] > 0.1
                    else (significance.get("reasons") or [LARGEST])[0]
                ),
            }
        )
    scored.sort(key=lambda story: (-story["score"], story["rank"], story["id"]))
    if scored:
        scored[0] = scored[0] | {"head": True}
    return scored


feedback_store = FeedbackStore()
