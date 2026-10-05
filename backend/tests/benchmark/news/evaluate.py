"""Score the newsroom against the labelled dataset: detection, access, grounding, cost."""

from __future__ import annotations

import re
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, time, timedelta
from statistics import median
from time import perf_counter
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.modules.intelligence import engine, newsroom
from app.modules.intelligence.engine import IntelligenceService
from app.modules.intelligence.newsroom import NewsroomService, story_slots
from app.modules.intelligence.newsroom_contracts import NewsConfig, NewsSettings
from tests.benchmark.news import dataset, warehouse

_FIGURE = re.compile(r"\d[\d,.]*\d|\d")

THRESHOLDS = {
    "precision": 0.90,
    "recall": 0.90,
    "recall_small_magnitude": 0.75,
    "severity_agreement": 0.90,
    "decoy_false_positives": 0,
    "ungrounded_figures": 0,
    "leaks": 0,
    "wrongly_hidden": 0,
    "reader_queries_per_view": 12,
    "generator_queries_per_cycle": 12,
}


def config() -> NewsConfig:
    return NewsConfig(
        execution_role="news_editor",
        metrics=["revenue"],
        count_metric="order_count",
        slice_dimensions=["city", "channel", "category"],
        time_dimension="sale_date",
        max_stories=24,
        narrative="template",
    )


def _key(metric, dimension, value, direction) -> tuple:
    return (metric, dimension, value, direction)


def _story_key(story) -> tuple:
    return _key(
        story.metric,
        story.slice.dimension if story.slice else None,
        story.slice.value if story.slice else None,
        "increase" if story.change > 0 else "decrease",
    )


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


def ungrounded(story, cfg: NewsConfig) -> list[str]:
    """Figures in the narrative that no proven value accounts for."""
    grounded = " ".join(story_slots(story, cfg).values())
    allowed = set(_FIGURE.findall(grounded))
    text = " ".join(story.narrative.model_dump().values())
    return [figure for figure in _FIGURE.findall(text) if figure not in allowed]


async def evaluate(seed: int = dataset.SEED) -> dict:
    @asynccontextmanager
    async def lock(_key, **_kwargs):
        yield

    async def schedule(*_args, **_kwargs):
        return {}

    with ExitStack() as stack:
        stack.enter_context(patch.object(newsroom, "metadata_lock", lock))
        stack.enter_context(patch.object(newsroom, "write_audit_log", AsyncMock()))
        stack.enter_context(patch.object(engine, "write_audit_log", AsyncMock()))
        stack.enter_context(patch.object(newsroom, "configure_schedule", schedule))
        house, journal = warehouse.Warehouse(dataset.generate(seed)), warehouse.Journal()
        service = NewsroomService(
            IntelligenceService(journal, house),
            house,
            journal,
            warehouse.Bindings(),
            feedback=warehouse.Reactions(),
        )
        cfg = config()
        await service.configure(
            warehouse.VIEW_ID,
            NewsSettings(enabled=True, config=cfg),
            house.principals["news_manager"].user(),
        )
        gold = dataset.gold()
        decoys = {
            (item.day, item.dimension, item.value)
            for item in dataset.INJECTIONS
            if item.kind != "anomaly"
        }
        found = missed = spurious = decoy_hits = severity_hits = 0
        small_found = small_total = 0
        ungrounded_figures: list[str] = []
        leaks: list[tuple] = []
        hidden: list[tuple] = []
        cycle_queries: list[int] = []
        reader_queries: list[int] = []
        latencies: list[float] = []
        by_type: dict[str, list[int]] = {}
        for day in dataset.EDITION_DAYS:
            now = datetime.combine(day + timedelta(days=1), time(9), ZoneInfo(cfg.timezone))
            result = await service.run_cycle(
                warehouse.VIEW_ID, warehouse.SERVICE.user(), now=now
            )
            cycle_queries.append(result["queries"])
            stories = [
                row for (kind, _), row in journal.rows.items()
                if kind == "stories" and row.edition_date == day
            ]
            pressed = {_story_key(story): story for story in stories}
            expected = {
                _key(item["metric"], item["dimension"], item["value"], item["direction"]): item
                for item in gold
                if item["edition_date"] == day.isoformat()
            }
            for key, item in expected.items():
                hit = key in pressed
                found += hit
                missed += not hit
                score = by_type.setdefault(item["type"], [0, 0])
                score[0] += hit
                score[1] += 1
                if item["magnitude"] < 0.20:
                    small_total += 1
                    small_found += hit
                if hit:
                    severity_hits += pressed[key].severity == item["expected_severity"]
            for key, story in pressed.items():
                if key not in expected:
                    spurious += 1
                    decoy_hits += (day, key[1], key[2]) in decoys
                ungrounded_figures.extend(ungrounded(story, cfg))
            for name, principal in house.principals.items():
                if not principal.news_enabled:
                    continue
                before = house.queries[name]
                started = perf_counter()
                paper = await service.newspaper(principal.user(), day=day)
                latencies.append((perf_counter() - started) * 1000)
                if principal.can_read:
                    reader_queries.append(house.queries[name] - before)
                shown = {
                    (
                        story["metric"],
                        story["slice"]["dimension"] if story["slice"] else None,
                        story["slice"]["value"] if story["slice"] else None,
                    )
                    for section in paper["sections"]
                    for story in section["stories"]
                }
                for key in pressed:
                    subject = key[:3]
                    allowed = warehouse.may_see(principal, key[1], key[2])
                    if subject in shown and not allowed:
                        leaks.append((day.isoformat(), name, key[1], key[2]))
                    if subject not in shown and allowed:
                        hidden.append((day.isoformat(), name, key[1], key[2]))
        precision = found / (found + spurious) if found + spurious else 1.0
        recall = found / (found + missed) if found + missed else 1.0
        return {
            "editions": len(dataset.EDITION_DAYS),
            "rows": len(house.rows),
            "expected_stories": found + missed,
            "pressed_stories": found + spurious,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(2 * precision * recall / (precision + recall), 4)
            if precision + recall
            else 0.0,
            "recall_by_type": {
                name: round(hit / total, 4) for name, (hit, total) in sorted(by_type.items())
            },
            "recall_small_magnitude": round(small_found / small_total, 4) if small_total else 1.0,
            "severity_agreement": round(severity_hits / found, 4) if found else 1.0,
            "false_positives": spurious,
            "decoy_false_positives": decoy_hits,
            "ungrounded_figures": len(ungrounded_figures),
            "access_checks": sum(
                1 for item in house.principals.values() if item.news_enabled
            ) * (found + spurious),
            "leaks": len(leaks),
            "wrongly_hidden": len(hidden),
            "leak_examples": leaks[:5],
            "hidden_examples": hidden[:5],
            "generator_queries_per_cycle": max(cycle_queries),
            "reader_queries_per_view": max(reader_queries),
            "reader_overhead_ms_p50": round(median(latencies), 2),
            "reader_overhead_ms_p95": round(_percentile(latencies, 0.95), 2),
        }


def failures(report: dict) -> list[str]:
    """Threshold breaches, empty when the report passes."""
    floor = ("precision", "recall", "recall_small_magnitude", "severity_agreement")
    return [
        f"{name}={report[name]} (limit {limit})"
        for name, limit in THRESHOLDS.items()
        if (report[name] < limit if name in floor else report[name] > limit)
    ]
