"""The Studio accuracy corpus: generated time cases plus ``cases.yaml``.

A case names what a correct answer is built from (``expect``), not the SQL a
particular planner writes. Fields:

* ``outcome``: ``answer`` (a governed query answers it), ``clarify`` (the
  Semantic View cannot express it; the agent must ask or explain), or
  ``refuse`` (a policy boundary).
* ``lexical``: the deterministic fast path is expected to resolve it on its own.
  Cases without it may need the model planner; the offline gate then only
  requires that the fast path does not produce a *confident wrong* plan.
* ``turns``: earlier user turns for a follow-up case (live runs only).
* ``phase``: the plan milestone that makes the case answerable (1, 2, or 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

CASES_FILE = Path(__file__).with_name("cases.yaml")
MULTILINGUAL_FILE = Path(__file__).with_name("cases_multilingual.yaml")


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    lang: str
    category: str
    expect: dict[str, Any]
    outcome: str = "answer"
    lexical: bool = False
    turns: tuple[str, ...] = ()
    phase: int = 1
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    #: For derived cases: the number a correct answer states, computed from gold.
    answer: dict[str, Any] = field(default_factory=dict)


def _time_phrases(today: date) -> list[tuple[str, str, str]]:
    year = today.year - 1
    return [
        ("current_month", "this month", "bulan ini"),
        ("previous_month", "last month", "bulan lalu"),
        ("current_quarter", "this quarter", "kuartal ini"),
        ("previous_quarter", "last quarter", "kuartal lalu"),
        ("current_year", "this year", "tahun ini"),
        ("previous_year", "last year", "tahun lalu"),
        ("current_week", "this week", "minggu ini"),
        ("previous_week", "last week", "minggu lalu"),
        ("current_day", "today", "hari ini"),
        ("previous_day", "yesterday", "kemarin"),
        ("last_7_days", "in the last 7 days", "7 hari terakhir"),
        ("last_30_days", "in the last 30 days", "30 hari terakhir"),
        ("last_3_months", "over the last 3 months", "3 bulan terakhir"),
        ("ytd", "year to date", "sejak awal tahun"),
        ("mtd", "month to date", "bulan berjalan"),
        (f"{year}-Q2", f"in Q2 {year}", f"kuartal 2 {year}"),
        (f"{year}-03", f"in March {year}", f"Maret {year}"),
        (f"{year}-01-01..{year}-03-31", f"from January to March {year}",
         f"Januari sampai Maret {year}"),
        (str(year), f"in {year}", f"tahun {year}"),
        (f"since_{year}", f"since {year}", f"sejak {year}"),
    ]


def generated_cases(today: date) -> list[Case]:
    cases: list[Case] = []
    templates = (
        ("total_revenue", "What was total revenue {p}?", "Berapa penjualan {p}?"),
        ("order_count", "How many orders {p}?", "Berapa jumlah pesanan {p}?"),
    )
    for range_value, english, indonesian in _time_phrases(today):
        for metric, en_template, id_template in templates:
            for lang, template, phrase in (("en", en_template, english),
                                           ("id", id_template, indonesian)):
                cases.append(Case(
                    id=f"time.{range_value}.{metric}.{lang}",
                    question=template.format(p=phrase),
                    lang=lang,
                    category="time_range",
                    expect={"metrics": [metric], "range": range_value},
                    lexical=True,
                ))
    year = today.year - 1
    comparisons = (
        ("current_month", "year_over_year", "Revenue this month vs last year",
         "Penjualan bulan ini dibanding tahun lalu"),
        ("current_month", "month_over_month", "Revenue this month vs last month",
         "Penjualan bulan ini dibanding bulan lalu"),
        ("current_quarter", "quarter_over_quarter", "Revenue this quarter vs last quarter",
         "Penjualan kuartal ini dibanding kuartal lalu"),
        (f"{year}-Q2", "previous_period", f"Revenue growth in Q2 {year}",
         f"Pertumbuhan penjualan kuartal 2 {year}"),
        ("ytd", "year_over_year", "Revenue year to date vs last year",
         "Penjualan sejak awal tahun dibanding tahun lalu"),
        ("last_30_days", "previous_period", "Revenue change in the last 30 days",
         "Perubahan penjualan 30 hari terakhir"),
    )
    for range_value, compare, english, indonesian in comparisons:
        for lang, question in (("en", english), ("id", indonesian)):
            cases.append(Case(
                id=f"compare.{range_value}.{compare}.{lang}",
                question=question,
                lang=lang,
                category="comparison",
                expect={"metrics": ["total_revenue"], "range": range_value, "compare": compare},
                lexical=True,
            ))
    grains = (
        ("month", str(year), f"Monthly revenue in {year}", f"Penjualan per bulan tahun {year}"),
        ("week", "last_3_months", "Weekly orders over the last 3 months",
         "Jumlah pesanan per minggu 3 bulan terakhir"),
        ("quarter", "since_" + str(year), f"Revenue by quarter since {year}",
         f"Penjualan per kuartal sejak {year}"),
    )
    for grain, range_value, english, indonesian in grains:
        metric = "order_count" if "orders" in english else "total_revenue"
        for lang, question in (("en", english), ("id", indonesian)):
            cases.append(Case(
                id=f"grain.{grain}.{range_value}.{lang}",
                question=question,
                lang=lang,
                category="time_grain",
                expect={"metrics": [metric], "range": range_value, "grain": grain},
                lexical=True,
            ))
    return cases


def file_cases(path: Path = CASES_FILE) -> list[Case]:
    raw = yaml.safe_load(path.read_text()) or []
    return [
        Case(
            id=item["id"],
            question=item["question"],
            lang=item.get("lang", "en"),
            category=item["category"],
            expect=item.get("expect") or {},
            outcome=item.get("outcome", "answer"),
            lexical=bool(item.get("lexical", False)),
            turns=tuple(item.get("turns") or ()),
            phase=int(item.get("phase", 1)),
            notes=item.get("notes", ""),
            tags=tuple(item.get("tags") or ()),
            answer=item.get("answer") or {},
        )
        for item in raw
    ]


def multilingual_cases(path: Path = MULTILINGUAL_FILE) -> list[Case]:
    """One case per base question and language, with the base's expectation."""
    raw = yaml.safe_load(path.read_text()) or {}
    languages = list(raw.get("languages") or [])
    cases = []
    for item in raw.get("cases") or []:
        missing = [lang for lang in languages if lang not in (item.get("questions") or {})]
        if missing:
            raise ValueError(f"{item['base']} has no question in {missing}")
        for lang in languages:
            cases.append(Case(
                id=f"ml.{item['base']}.{lang}",
                question=item["questions"][lang],
                lang=lang,
                category=item["category"],
                expect=item.get("expect") or {},
                outcome=item.get("outcome", "answer"),
                phase=int(item.get("phase", 1)),
                tags=tuple(item.get("tags") or ()),
                answer=item.get("answer") or {},
            ))
    return cases


def all_cases(today: date) -> list[Case]:
    cases = [*generated_cases(today), *file_cases(), *multilingual_cases()]
    ids = [case.id for case in cases]
    duplicates = {case_id for case_id in ids if ids.count(case_id) > 1}
    if duplicates:
        raise ValueError(f"Duplicate case ids: {sorted(duplicates)}")
    return cases
