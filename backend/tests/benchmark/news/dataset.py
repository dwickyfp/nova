"""Deterministic retail sales with labelled anomalies for News simulation.

The table has one row per day, city, channel and category. Seven consecutive
edition days each carry a known situation: a real change that News must report,
or a decoy that it must not. The labels are written from how the data is built,
never from what the detector returns.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

SEED = 20261005
DAYS = 70
#: The last complete day of the dataset and the newest edition.
LAST_DAY = date(2026, 10, 4)

CITIES = {
    "Jakarta": 3.0,
    "Surabaya": 1.8,
    "Bandung": 1.5,
    "Medan": 1.2,
    "Semarang": 1.0,
    "Makassar": 0.9,
    "Palembang": 0.8,
    "Denpasar": 0.7,
}
CHANNELS = {"Store": 0.4, "Online": 0.3, "Marketplace": 0.2, "Wholesale": 0.1}
#: Collectibles sells a handful of orders a day: too few to support a story.
CATEGORIES = {
    "Grocery": (0.30, 85_000),
    "Electronics": (0.25, 1_450_000),
    "Fashion": (0.20, 240_000),
    "Home": (0.15, 380_000),
    "Beauty": (0.0995, 130_000),
    "Collectibles": (0.0005, 900_000),
}
WEEKDAY = (0.95, 0.97, 1.0, 1.02, 1.10, 1.30, 1.25)
BASE_ORDERS = 400


@dataclass(frozen=True)
class Injection:
    id: str
    day: date
    factor: float
    dimension: str | None = None
    value: str | None = None
    #: ``anomaly`` must be reported; every other kind must stay silent.
    kind: str = "anomaly"
    note: str = ""


def _day(offset: int) -> date:
    return LAST_DAY - timedelta(days=offset)


INJECTIONS = (
    Injection("bandung-drop", _day(0), 0.65, "city", "Bandung",
              note="Bandung revenue falls 35% on the newest edition day."),
    Injection("collectibles-low-volume", _day(0), 0.40, "category", "Collectibles",
              kind="decoy_low_volume",
              note="A 60% swing on a category with a few orders a day."),
    Injection("national-rise", _day(1), 1.15,
              note="Every city, channel and category rises 15%."),
    Injection("online-drop", _day(2), 0.80, "channel", "Online",
              note="The Online channel falls 20% in every city."),
    Injection("surabaya-spike", _day(3), 1.40, "city", "Surabaya",
              note="Surabaya revenue rises 40%."),
    Injection("semarang-below-threshold", _day(5), 0.94, "city", "Semarang",
              kind="decoy_below_threshold",
              note="A 6% dip, under the 10% materiality threshold."),
    Injection("makassar-dip", _day(6), 0.86, "city", "Makassar",
              note="Makassar revenue falls 14%: a small but material change."),
    Injection("medan-baseline-outlier", _day(10), 1.80, "city", "Medan",
              kind="decoy_baseline_outlier",
              note="One outlier in a baseline week of the Surabaya edition."),
)

#: Edition days evaluated by the benchmark, oldest first. Day 4 is a quiet day.
EDITION_DAYS = tuple(_day(offset) for offset in range(6, -1, -1))

COLUMNS = ("sale_date", "city", "channel", "category", "revenue", "orders")


def generate(seed: int = SEED, *, last_day: date | None = None) -> list[tuple]:
    """Rows of ``(sale_date, city, channel, category, revenue, orders)``.

    ``last_day`` moves the whole calendar so a live demonstration ends yesterday;
    every row and every injection shifts by the same number of days.
    """
    rng = random.Random(seed)
    shift = (last_day - LAST_DAY) if last_day else timedelta(0)
    rows = []
    for index in range(DAYS):
        day = LAST_DAY - timedelta(days=DAYS - 1 - index)
        trend = 1 + 0.0005 * index
        for city, city_scale in CITIES.items():
            for channel, channel_share in CHANNELS.items():
                for category, (category_share, ticket) in CATEGORIES.items():
                    factor = 1.0
                    for injection in INJECTIONS:
                        if injection.day == day and (
                            injection.dimension is None
                            or {"city": city, "channel": channel, "category": category}[
                                injection.dimension
                            ]
                            == injection.value
                        ):
                            factor *= injection.factor
                    expected = (
                        BASE_ORDERS * city_scale * channel_share * category_share
                        * WEEKDAY[day.weekday()] * trend * factor
                    )
                    orders = max(0, round(expected * rng.uniform(0.96, 1.04)))
                    revenue = Decimal(round(orders * ticket * rng.uniform(0.97, 1.03)))
                    rows.append((day + shift, city, channel, category, revenue, orders))
    return rows


def values(dimension: str) -> list[str]:
    return list({"city": CITIES, "channel": CHANNELS, "category": CATEGORIES}[dimension])


#: Categories with enough daily orders to carry a story.
SUPPORTED_CATEGORIES = [name for name in CATEGORIES if name != "Collectibles"]


def gold() -> list[dict]:
    """Stories each edition must contain, derived from the injections alone."""
    expected = []
    for injection in INJECTIONS:
        if injection.kind != "anomaly":
            continue
        direction = "increase" if injection.factor > 1 else "decrease"
        base = {
            "anomaly": injection.id,
            "edition_date": injection.day.isoformat(),
            "metric": "revenue",
            "direction": direction,
            "magnitude": round(abs(injection.factor - 1), 4),
            "expected_severity": "critical" if abs(injection.factor - 1) >= 0.25 else "warning",
        }
        if injection.dimension:
            expected.append(
                base | {"dimension": injection.dimension, "value": injection.value,
                        "type": "slice"}
            )
            continue
        # A national move is true for the total and for every supported slice.
        expected.append(base | {"dimension": None, "value": None, "type": "total"})
        for dimension, names in (
            ("city", values("city")),
            ("channel", values("channel")),
            ("category", SUPPORTED_CATEGORIES),
        ):
            expected.extend(
                base | {"dimension": dimension, "value": name, "type": "national_component"}
                for name in names
            )
    return expected
