"""Three more business desks for News: workforce, operating expenses, reliability.

Each desk is a deterministic daily table of the same fictional retailer as the
sales dataset, with labelled situations on its newest days. What a desk should
report is derived here from the design alone (weights and injected factors),
never from what the detector returns.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from tests.benchmark.news.dataset import CITIES, DAYS, LAST_DAY, SEED

#: The detector's defaults; the expectations below are stated against them.
THRESHOLD = 0.10
MINIMUM_SAMPLES = 30


@dataclass(frozen=True)
class Measure:
    name: str
    column: str
    base: float
    #: SQL type of the column; counts are whole numbers.
    integer: bool = False


@dataclass(frozen=True)
class Injection:
    id: str
    offset: int
    factor: float
    measure: str
    dimension: str | None = None
    value: str | None = None
    note: str = ""


@dataclass(frozen=True)
class Domain:
    key: str
    desk: str
    view_name: str
    database: str
    table: str
    model_file: str
    date_column: str
    dimensions: dict[str, dict[str, float]]
    measures: tuple[Measure, ...]
    count_metric: str
    watched: tuple[str, ...]
    slices: tuple[str, ...]
    weekday: tuple[float, ...]
    injections: tuple[Injection, ...]
    #: Whether a combination of dimension values exists at all.
    exists: Callable[[dict[str, str]], bool] = field(default=lambda _row: True)
    #: Dimension a reader's city scope applies to; ``None`` when the desk has none.
    scoped_by: str | None = "city"

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.date_column, *self.dimensions, *(item.column for item in self.measures))

    def measure(self, name: str) -> Measure:
        return next(item for item in self.measures if item.name == name)

    def combos(self) -> list[tuple[dict[str, str], float]]:
        """Every existing combination of dimension values and its share."""
        rows: list[tuple[dict[str, str], float]] = [({}, 1.0)]
        for dimension, weights in self.dimensions.items():
            rows = [
                (row | {dimension: value}, share * weight)
                for row, share in rows
                for value, weight in weights.items()
            ]
        return [(row, share) for row, share in rows if self.exists(row)]


def _factor(domain: Domain, measure: str, day_offset: int, row: dict[str, str]) -> float:
    factor = 1.0
    for item in domain.injections:
        if (
            item.offset == day_offset
            and item.measure == measure
            and (item.dimension is None or row[item.dimension] == item.value)
        ):
            factor *= item.factor
    return factor


def generate(domain: Domain, seed: int = SEED, *, last_day: date | None = None) -> list[tuple]:
    """Rows in ``domain.columns`` order, ending on ``last_day``."""
    rng = random.Random(f"{seed}:{domain.key}")
    end = last_day or LAST_DAY
    combos = domain.combos()
    rows = []
    for index in range(DAYS):
        offset = DAYS - 1 - index
        day = end - timedelta(days=offset)
        season = domain.weekday[day.weekday()] * (1 + 0.0005 * index)
        for row, share in combos:
            values = []
            for measure in domain.measures:
                amount = (
                    measure.base * share * season
                    * _factor(domain, measure.name, offset, row)
                    * rng.uniform(0.96, 1.04)
                )
                values.append(
                    max(0, round(amount)) if measure.integer else Decimal(str(round(amount, 2)))
                )
            rows.append((day, *row.values(), *values))
    return rows


def expectations(domain: Domain, offset: int) -> dict[tuple, str]:
    """What an edition ``offset`` days back must, may, or must not report.

    Keys are ``(metric, dimension, value, direction)``; the total uses ``None``
    for dimension and value. A subject is ``required`` when the designed change
    clears the threshold with margin and enough records support it, ``optional``
    when it sits near the threshold or a single slice explains the total (the
    edition then keeps only the slice), and absent when it must stay silent.
    """
    combos = domain.combos()
    count = domain.measure(domain.count_metric)
    verdicts: dict[tuple, str] = {}
    for metric in domain.watched:
        subjects: list[tuple[str | None, str | None]] = [(None, None)] + [
            (dimension, value)
            for dimension in domain.slices
            for value in domain.dimensions[dimension]
        ]
        changes = {}
        for dimension, value in subjects:
            members = [
                (row, share)
                for row, share in combos
                if dimension is None or row[dimension] == value
            ]
            before = sum(share for _row, share in members)
            if not before:
                continue
            after = sum(share * _factor(domain, metric, offset, row) for row, share in members)
            changes[(dimension, value)] = (
                after / before - 1,
                count.base * before * min(domain.weekday),
                after - before,
            )
        total = changes[(None, None)]
        for (dimension, value), (relative, samples, _absolute) in changes.items():
            if samples < MINIMUM_SAMPLES or abs(relative) < 0.7 * THRESHOLD:
                continue
            explained = dimension is None and any(
                other[0] is not None and abs(change[2]) >= 0.8 * abs(total[2])
                for other, change in changes.items()
            )
            verdict = (
                "required" if abs(relative) >= 1.3 * THRESHOLD and not explained else "optional"
            )
            direction = "increase" if relative > 0 else "decrease"
            verdicts[(metric, dimension, value, direction)] = verdict
    return verdicts


_OWNER = {
    "payments-api": "Payments",
    "checkout-web": "Commerce",
    "order-service": "Commerce",
    "mobile-gateway": "Platform",
    "inventory-sync": "Platform",
    "legacy-reports": "Platform",
    "catalog-search": "Data",
}

WORKFORCE = Domain(
    key="workforce",
    desk="Workforce",
    view_name="news_workforce",
    database="news_demo",
    table="workforce_daily",
    model_file="workforce.ossie.yaml",
    date_column="work_date",
    dimensions={
        "city": dict(CITIES),
        "department": {
            "Store Operations": 0.35,
            "Warehouse": 0.25,
            "Delivery": 0.20,
            "Customer Care": 0.119,
            "Head Office": 0.08,
            # A handful of people: too few to support a story.
            "Executive Office": 0.001,
        },
        "employment_type": {"Permanent": 0.70, "Contract": 0.27, "Intern": 0.03},
    },
    measures=(
        Measure("headcount_days", "headcount_days", 1200, integer=True),
        Measure("overtime_hours", "overtime_hours", 300),
        Measure("absence_hours", "absence_hours", 180),
    ),
    count_metric="headcount_days",
    watched=("overtime_hours", "absence_hours"),
    slices=("city", "department"),
    weekday=(1.0, 1.0, 1.0, 1.05, 1.20, 1.30, 0.70),
    injections=(
        Injection("bandung-overtime", 0, 1.45, "overtime_hours", "city", "Bandung",
                  "Overtime in Bandung rises 45%."),
        Injection("executive-overtime", 0, 1.60, "overtime_hours", "department",
                  "Executive Office", "A 60% swing on a department of a few people."),
        Injection("medan-absence", 0, 0.94, "absence_hours", "city", "Medan",
                  "A 6% dip, under the materiality threshold."),
        Injection("warehouse-absence", 1, 1.25, "absence_hours", "department", "Warehouse",
                  "Absence in the Warehouse department rises 25%."),
    ),
)

EXPENSES = Domain(
    key="expenses",
    desk="Finance",
    view_name="news_operating_expenses",
    database="news_demo",
    table="operating_expenses",
    model_file="operating_expenses.ossie.yaml",
    date_column="posting_date",
    dimensions={
        "city": dict(CITIES),
        "cost_center": {
            "Logistics": 0.30,
            "Marketing": 0.25,
            "Store Operations": 0.25,
            "IT": 0.12,
            "Facilities": 0.078,
            # A few postings a day: too few to support a story.
            "Legal": 0.002,
        },
        "expense_type": {"Recurring": 0.70, "One-off": 0.30},
    },
    measures=(
        Measure("transaction_count", "transactions", 500, integer=True),
        Measure("operating_expense", "amount", 60_000_000),
    ),
    count_metric="transaction_count",
    watched=("operating_expense",),
    slices=("city", "cost_center"),
    weekday=(1.10, 1.0, 1.0, 1.0, 1.05, 0.60, 0.50),
    injections=(
        Injection("marketing-spend", 0, 1.26, "operating_expense", "cost_center", "Marketing",
                  "Marketing spend rises 26% in every city."),
        Injection("legal-swing", 0, 1.60, "operating_expense", "cost_center", "Legal",
                  "A 60% swing on a cost center with a few postings a day."),
        Injection("surabaya-savings", 1, 0.78, "operating_expense", "city", "Surabaya",
                  "Operating expense in Surabaya falls 22%."),
        Injection("jakarta-spend", 2, 1.18, "operating_expense", "city", "Jakarta",
                  "Operating expense in Jakarta rises 18%."),
        Injection("medan-baseline-outlier", 8, 1.80, "operating_expense", "city", "Medan",
                  "One outlier in a baseline week of the Surabaya edition."),
    ),
)

RELIABILITY = Domain(
    key="reliability",
    desk="Engineering",
    view_name="news_service_reliability",
    database="news_engineering",
    table="service_reliability",
    model_file="service_reliability.ossie.yaml",
    date_column="event_date",
    dimensions={
        "service": {
            "payments-api": 0.22,
            "checkout-web": 0.20,
            "catalog-search": 0.18,
            "mobile-gateway": 0.16,
            "order-service": 0.14,
            "inventory-sync": 0.099,
            # Barely monitored: too few checks to support a story.
            "legacy-reports": 0.001,
        },
        "team": {"Platform": 1.0, "Payments": 1.0, "Commerce": 1.0, "Data": 1.0},
        "environment": {"production": 0.80, "staging": 0.20},
    },
    measures=(
        Measure("monitored_checks", "checks", 20_000, integer=True),
        Measure("incident_count", "incidents", 220, integer=True),
        Measure("downtime_minutes", "downtime_minutes", 600),
    ),
    count_metric="monitored_checks",
    watched=("incident_count", "downtime_minutes"),
    slices=("service", "team"),
    weekday=(1.10, 1.10, 1.05, 1.05, 1.0, 0.80, 0.75),
    injections=(
        Injection("payments-incidents", 0, 3.0, "incident_count", "service", "payments-api",
                  "Incidents on payments-api triple."),
        Injection("legacy-incidents", 0, 1.60, "incident_count", "service", "legacy-reports",
                  "A 60% swing on a service with a handful of checks."),
        Injection("platform-downtime", 1, 1.40, "downtime_minutes", "team", "Platform",
                  "Downtime of the Platform team's services rises 40%."),
        Injection("checkout-downtime", 2, 1.07, "downtime_minutes", "service", "checkout-web",
                  "A 7% rise, under the materiality threshold."),
    ),
    exists=lambda row: _OWNER[row["service"]] == row["team"],
    scoped_by=None,
)

DOMAINS = (WORKFORCE, EXPENSES, RELIABILITY)
#: Edition days each desk is scored on, newest last.
OFFSETS = (3, 2, 1, 0)
