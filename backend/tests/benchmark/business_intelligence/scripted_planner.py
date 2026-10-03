"""Deliberately limited offline planner over the provider's public catalog input.

It knows no evaluator cases, expected plans, SQL, or answer values. Unsupported
language and ambiguous concepts produce clarification, not guessed metrics.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from app.modules.agents.semantic.planning import SemanticPlan


def words(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.lower().replace("_", " ")))


def plan_from_catalog(question: str, catalog: dict) -> dict | None:
    normalized = f" {words(question)} "
    matches = []
    for metric in catalog.get("metrics", []):
        aliases = [metric["name"], *metric.get("synonyms", [])]
        score = max(
            (len(words(alias)) for alias in aliases if f" {words(alias)} " in normalized),
            default=0,
        )
        if score:
            matches.append((score, metric))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None
    metric = matches[0][1]
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", question)
    if len(dates) != 2:
        return None
    try:
        start, end = (date.fromisoformat(value) for value in dates)
    except ValueError:
        return None
    exclusive = "sebelum" in normalized or "before" in normalized
    if exclusive:
        end -= timedelta(days=1)
    if end < start:
        return None
    dimensions = catalog.get("dimensions", [])
    dataset = metric.get("dataset")
    time = metric.get("default_time_dimension")
    if not time:
        times = [row for row in dimensions if row["dataset"] == dataset and row.get("is_time")]
        if len(times) != 1:
            return None
        time = f"{dataset}.{times[0]['name']}"
    daily = " berdasarkan hari " in normalized or " daily " in normalized
    filters = []
    # A literal city qualifier uses a catalog dimension; it never maps aliases
    # to hidden values or reads sample/gold tables to infer the answer.
    city = re.search(r"\b(?:di|in)\s+([A-Z][A-Za-z -]*?)\s*,", question)
    if city:
        geography_dataset = time.rsplit(".", 1)[0] if "." in time else dataset
        candidates = [
            row
            for row in dimensions
            if row["dataset"] == geography_dataset and row["name"] == "city"
        ]
        if len(candidates) != 1:
            return None
        filters.append({"field": f"{geography_dataset}.city", "operator": "=", "value": city[1]})
    plan = {
        "metrics": [metric["name"]],
        "filters": filters,
        "time": {"dimension": time, "range": f"{start}..{end}", "grain": "day" if daily else None},
        "order_by": [{"field": time, "direction": "asc"}] if daily else [],
        "limit": 100,
    }
    return json.loads(json.dumps(SemanticPlan.from_dict(plan).as_dict()))
