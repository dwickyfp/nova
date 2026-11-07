"""Gold cases built from published Semantic Views, for any domain.

Answerable cases are governed plans, so their answers are correct by
construction: the compiler that serves Studio writes the SQL, and the result is
re-executed as the evaluating user on every run. Nothing domain-specific is
coded here; every intent comes from the view's own metrics, breakdowns, time
dimensions and values.

Templates per metric, rotated with a fixed seed:

- ``total``: the metric over one absolute period;
- ``by_dimension``: broken down by one categorical field it can reach;
- ``top_n``: the top five of a breakdown;
- ``trend``: by month over the observed window;
- ``filtered``: for one real value of a breakdown (read from the data).

Periods are absolute (``2026-10``, ``2026-Q3``) so an answer never changes
because time passed. A model phrases each intent in business words without the
metric's identifier; a judge then checks the question asks exactly that.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

from app.modules.agents.semantic.ir import FieldKind, SemanticModelIR
from app.modules.ontology.derived import NUMERIC, humanize

TEMPLATES = ("total", "by_dimension", "top_n", "trend", "filtered")
_COORDINATE = re.compile(r"(^|_)(lat|latitude|lon|lng|longitude)$", re.IGNORECASE)
_DISTINCT = re.compile(r"COUNT\s*\(\s*DISTINCT\s+([`\w.]+)", re.IGNORECASE)


@dataclass(frozen=True)
class Period:
    range: str
    words_id: str
    words_en: str


def periods(start: str, end: str) -> tuple[list[Period], Period]:
    """Recent whole months and a quarter inside the data window, and the full trend."""
    months_id = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
                 "September", "Oktober", "November", "Desember"]
    months_en = ["January", "February", "March", "April", "May", "June", "July", "August",
                 "September", "October", "November", "December"]
    first_year, first_month = int(start[:4]), int(start[5:7])
    last_year, last_month = int(end[:4]), int(end[5:7])
    # The last month counts only when the window covers it to its end.
    if int(end[8:10]) < 28:
        last_year, last_month = (last_year, last_month - 1) if last_month > 1 else (
            last_year - 1, 12)
    whole: list[Period] = []
    year, month = last_year, last_month
    while (year, month) >= (first_year, first_month) and len(whole) < 3:
        whole.append(Period(f"{year}-{month:02d}", f"{months_id[month - 1]} {year}",
                            f"{months_en[month - 1]} {year}"))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    quarter = (last_month - 1) // 3 + 1
    quarter_end = quarter * 3
    if quarter_end > last_month:  # the current quarter is not complete
        quarter, quarter_end = (quarter - 1, quarter_end - 3) if quarter > 1 else (4, 12)
    if (last_year, quarter_end - 2) >= (first_year, first_month):
        whole.append(Period(f"{last_year}-Q{quarter}", f"kuartal {quarter} {last_year}",
                            f"Q{quarter} {last_year}"))
    trend = Period(
        f"{first_year}-{first_month:02d}-01..{last_year}-{last_month:02d}-"
        f"{_month_end(last_year, last_month):02d}",
        f"{months_id[first_month - 1]} sampai {months_id[last_month - 1]} {last_year}",
        f"{months_en[first_month - 1]} to {months_en[last_month - 1]} {last_year}",
    )
    return whole, trend


def _month_end(year: int, month: int) -> int:
    if month == 12:
        return 31
    from datetime import date, timedelta

    return (date(year, month + 1, 1) - timedelta(days=1)).day


def breakdowns(model: SemanticModelIR, metric_name: str) -> list[tuple[str, str]]:
    """Categorical fields a metric can be broken down by, as (qualified name, label)."""
    from app.modules.agents.semantic.suggestions import identifier_fields

    metric = model.metric(metric_name)
    if metric is None:
        return []
    identifiers = identifier_fields(model)
    reachable = {metric.base_dataset}
    for relationship in model.relationships:
        if relationship.from_dataset == metric.base_dataset and relationship.cardinality in {
            "many_to_one", "one_to_one", "unknown",
        }:
            reachable.add(relationship.to_dataset)
        if relationship.to_dataset == metric.base_dataset and relationship.cardinality == (
            "one_to_many"
        ):
            reachable.add(relationship.from_dataset)
    allowed = set(metric.allowed_dimensions)
    # A count of distinct X is never broken down or filtered by X itself ("active
    # outlets for outlet Y" is always 1): leave out the counted entity's fields.
    counted: set[str] = set()
    distinct = _DISTINCT.search(metric.expression or "")
    if distinct:
        column = distinct.group(1).split(".")[-1].strip("`")
        counted.add(f"{metric.base_dataset}.{column}")
        for relationship in model.relationships:
            if relationship.from_dataset == metric.base_dataset and column in [
                item.strip("`") for item in relationship.from_columns
            ]:
                counted.add(relationship.to_dataset)
    found = []
    for dataset in model.datasets:
        if dataset.name not in reachable or dataset.name in counted:
            continue
        for field in dataset.fields:
            name = f"{dataset.name}.{field.name}"
            if (
                field.kind != FieldKind.DIMENSION or field.is_time
                or (dataset.name, field.name) in identifiers
                or (field.datatype or "").casefold() in NUMERIC
                or _COORDINATE.search(field.name)
                or (allowed and name not in allowed and field.name not in allowed)
            ):
                continue
            # The qualified name keeps two fields with the same words apart for the
            # judge; the phrasing step never copies it into a question.
            found.append((name, f"{humanize(field.name)} [{name}]"))
    return found


@dataclass
class Intent:
    key: str
    view_id: str
    view_name: str
    template: str
    metric: str
    plan: dict[str, Any]
    meaning: str
    needs_value: str | None = None  # a breakdown whose top value must be read first


def _metric_words(model: SemanticModelIR, name: str) -> str:
    metric = model.metric(name)
    description = (metric.description if metric else "") or ""
    return f"{humanize(name)}" + (f" ({description.strip()})" if description.strip() else "")


def intents(
    model: SemanticModelIR, view_id: str, view_name: str, metrics: list[str],
    window: tuple[str, str], *, per_metric: int, seed: int,
) -> list[Intent]:
    rng = random.Random(f"{seed}:{view_id}")
    whole, trend = periods(*window)
    found: list[Intent] = []
    for position, name in enumerate(sorted(metrics)):
        metric = model.metric(name)
        if metric is None:
            continue
        time = metric.default_time_dimension
        dims = breakdowns(model, name)
        offset = rng.randrange(len(TEMPLATES))
        chosen = 0
        for step in range(len(TEMPLATES)):
            if chosen >= per_metric:
                break
            template = TEMPLATES[(offset + step) % len(TEMPLATES)]
            if template in {"by_dimension", "top_n", "filtered"} and not dims:
                continue
            if template == "trend" and not time:
                continue
            period = whole[(position + step) % len(whole)] if time and whole else None
            words = _metric_words(model, name)
            plan: dict[str, Any] = {"metrics": [name]}
            meaning = words
            dim, dim_label = rng.choice(dims) if dims else ("", "")
            needs_value = None
            if template == "trend" and time:
                plan["time"] = {"dimension": time, "grain": "month", "range": trend.range}
                meaning += f", per month from {trend.words_en}"
            else:
                if time and period:
                    plan["time"] = {"dimension": time, "range": period.range}
                if template == "by_dimension":
                    plan["dimensions"] = [dim]
                    meaning += f", for each {dim_label}"
                elif template == "top_n":
                    plan["dimensions"] = [dim]
                    plan["order_by"] = [{"field": name, "direction": "desc"}]
                    plan["limit"] = 5
                    meaning += f", the 5 {dim_label} values with the highest result"
                elif template == "filtered":
                    needs_value = dim
                    meaning += f", only where {dim_label} is {{value}}"
                if time and period:
                    meaning += f", in {period.words_en}"
            found.append(Intent(
                key=f"{view_id}:{name}:{template}", view_id=view_id, view_name=view_name,
                template=template, metric=name, plan=plan, meaning=meaning,
                needs_value=needs_value,
            ))
            chosen += 1
    return found


def value_probe(intent: Intent) -> dict[str, Any]:
    """The plan that reads the most common value of the breakdown a filter needs."""
    plan: dict[str, Any] = {
        "metrics": [intent.metric],
        "dimensions": [intent.needs_value],
        "order_by": [{"field": intent.metric, "direction": "desc"}],
        "limit": 3,
    }
    if "time" in intent.plan:
        plan["time"] = dict(intent.plan["time"])
    return plan


def with_value(intent: Intent, value: Any) -> Intent:
    plan = dict(intent.plan)
    plan["filters"] = [{"field": intent.needs_value, "operator": "=", "value": value}]
    return Intent(
        key=intent.key, view_id=intent.view_id, view_name=intent.view_name,
        template=intent.template, metric=intent.metric, plan=plan,
        meaning=intent.meaning.replace("{value}", str(value)), needs_value=None,
    )


def ordered(plan: dict[str, Any]) -> bool:
    return bool(plan.get("order_by"))
