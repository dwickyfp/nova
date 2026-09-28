"""What the user asked, in a form code can check, whatever language they wrote in.

The turn planner (a model) reads the question and fills an :class:`IntentFrame`.
Code never parses the user's sentence; it validates the frame and then uses it:
the user's period, ranking, and threshold win over a later rewrite of the
question, Nova's own sentences use the user's language, and so on. A field the
model got wrong in shape is dropped, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

GRAINS = ("day", "week", "month", "quarter", "year")
COMPARISONS = (
    "previous_period", "year_over_year", "quarter_over_quarter", "month_over_month",
    "week_over_week",
)
OPERATORS = (">", ">=", "<", "<=")
#: BCP-47 shape only ("id", "pt-BR", "zh-Hant"); the model names the language.
_LANGUAGE_TAG = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}")


@dataclass(frozen=True)
class Threshold:
    operator: str
    value: float
    metric: str | None = None


@dataclass(frozen=True)
class IntentFrame:
    #: The language the user wrote in, as a BCP-47 tag.
    language: str = "en"
    #: The period the user named, in the plan range grammar ("previous_quarter").
    range: str | None = None
    compare: str | None = None
    #: A grouping period the user asked for ("per month", "每月").
    grain: str | None = None
    #: The user wants a series over time (a trend or a per-period breakdown).
    asks_series: bool = False
    top_n: int | None = None
    #: "desc" for the most / highest, "asc" for the least / lowest.
    order: str | None = None
    #: The catalog dimension a ranking restarts in ("top 2 categories in each city").
    per_group_dimension: str | None = None
    threshold: Threshold | None = None
    #: The request points at something on screen ("this query", "この結果").
    refers_to_screen: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> IntentFrame:
        """Keep every well-formed field; drop the rest."""
        if not isinstance(value, dict):
            return cls()
        language = str(value.get("language") or "en").strip()
        range_value = _range(value.get("range"))
        compare = value.get("compare") if value.get("compare") in COMPARISONS else None
        grain = value.get("grain") if value.get("grain") in GRAINS else None
        top_n = value.get("top_n")
        order = value.get("order") if value.get("order") in {"asc", "desc"} else None
        dimension = value.get("per_group_dimension")
        return cls(
            language=language if _LANGUAGE_TAG.fullmatch(language) else "en",
            range=range_value,
            compare=compare if range_value else None,
            grain=grain,
            asks_series=bool(value.get("asks_series")) or grain is not None,
            top_n=top_n if isinstance(top_n, int) and 1 <= top_n <= 1000 else None,
            order=order,
            per_group_dimension=dimension if isinstance(dimension, str) and dimension else None,
            threshold=_threshold(value.get("threshold")),
            refers_to_screen=bool(value.get("refers_to_screen")),
        )


def _range(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    from app.modules.agents.semantic.planning import SemanticPlanError
    from app.modules.agents.semantic.time_ranges import resolve_time_range

    try:
        resolve_time_range(value)
    except SemanticPlanError:
        return None
    return value.strip()


def _threshold(value: Any) -> Threshold | None:
    if not isinstance(value, dict) or value.get("operator") not in OPERATORS:
        return None
    number = value.get("value")
    if isinstance(number, bool) or not isinstance(number, int | float):
        return None
    metric = value.get("metric")
    return Threshold(
        operator=value["operator"], value=number,
        metric=metric if isinstance(metric, str) and metric else None,
    )


def intent_frame_schema() -> dict[str, Any]:
    """Strict JSON schema; every key present, unused ones null."""
    nullable_string = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "language": {"type": "string"},
            "range": nullable_string,
            "compare": {"type": ["string", "null"], "enum": [*COMPARISONS, None]},
            "grain": {"type": ["string", "null"], "enum": [*GRAINS, None]},
            "asks_series": {"type": "boolean"},
            "top_n": {"type": ["integer", "null"]},
            "order": {"type": ["string", "null"], "enum": ["asc", "desc", None]},
            "per_group_dimension": nullable_string,
            "threshold": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "operator": {"type": "string", "enum": list(OPERATORS)},
                            "value": {"type": "number"},
                            "metric": nullable_string,
                        },
                        "required": ["operator", "value", "metric"],
                        "additionalProperties": False,
                    },
                    {"type": "null"},
                ],
            },
            "refers_to_screen": {"type": "boolean"},
        },
        "required": [
            "language", "range", "compare", "grain", "asks_series", "top_n", "order",
            "per_group_dimension", "threshold", "refers_to_screen",
        ],
        "additionalProperties": False,
    }


INTENT_FRAME_RULES = (
    "intent_frame records what the user asked, independent of their language. "
    "language: the BCP-47 tag of the user's language (en, id, ja, es, ar, ...). "
    "range and compare: the period the user named, in the time.range grammar, and a "
    "comparison only when they compare periods; null when they named none. grain: a "
    "grouping period the user asked for (per month, weekly, 每月), else null. "
    "asks_series: true when they want a trend or a per-period series. top_n and order: "
    "a requested count of top or bottom items, and desc for the most or highest, asc "
    "for the least or lowest; a superlative without a number ('which city sold the "
    "most') sets order and leaves top_n null. per_group_dimension: the exact catalog "
    "dimension a ranking restarts in ('top 2 categories in each city' -> city). "
    "threshold: a condition on a metric value ('above 1 billion', '10億を超えた'), with "
    "the value as a plain number and the exact metric name. refers_to_screen: the "
    "request points at something on the user's screen. Use null or false for "
    "anything the user did not say."
)
