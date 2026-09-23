"""Reproducible numeric-answer acceptance corpus and overhead report."""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass

from app.modules.assistant.answer_contract import check_numeric_answer


@dataclass(frozen=True)
class Case:
    name: str
    question: str
    answer: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    should_accept: bool


CASES = (
    Case("exact amount", "What is revenue?", "Revenue is 100.", ("revenue",), ((100,),), True),
    Case("invented amount", "What is revenue?", "Revenue is 101.", ("revenue",), ((100,),), False),
    Case(
        "question answer bait", "Is revenue 101?", "Revenue is 101.", ("revenue",), ((100,),), False
    ),
    Case("zero", "How many returns?", "There were 0 returns.", ("returns",), ((0,),), True),
    Case("false zero", "How many returns?", "There were 0 returns.", ("returns",), ((2,),), False),
    Case("negative", "What is the change?", "The change is -5.", ("change",), ((-5,),), True),
    Case(
        "negative sign lost",
        "What is the change?",
        "The change is 5.",
        ("change",),
        ((-5,),),
        False,
    ),
    Case(
        "drop magnitude", "Why did revenue fall?", "Revenue fell by 5.", ("change",), ((-5,),), True
    ),
    Case(
        "unsigned change",
        "Why did revenue fall?",
        "Revenue changed by 5.",
        ("change",),
        ((-5,),),
        False,
    ),
    Case("IDR grouping", "Berapa omzet?", "Omzet Rp1.234.", ("omzet",), ((1234,),), True),
    Case("IDR wrong", "Berapa omzet?", "Omzet Rp1.235.", ("omzet",), ((1234,),), False),
    Case("US grouping", "What is revenue?", "Revenue $1,234.", ("revenue",), ((1234,),), True),
    Case(
        "two correct values",
        "Revenue and cost?",
        "Revenue 100; cost 40.",
        ("revenue", "cost"),
        ((100, 40),),
        True,
    ),
    Case(
        "one wrong value",
        "Revenue and cost?",
        "Revenue 100; cost 41.",
        ("revenue", "cost"),
        ((100, 40),),
        False,
    ),
    Case("percent column", "What is margin?", "Margin is 20%.", ("margin_pct",), ((20,),), True),
    Case("percent ratio", "What is margin?", "Margin is 20%.", ("margin_rate",), ((0.2,),), True),
    Case("percent invented", "What is margin?", "Margin is 21%.", ("margin_pct",), ((20,),), False),
    Case("untyped percent", "What is revenue?", "Revenue rose 20%.", ("revenue",), ((20,),), False),
    Case(
        "question percent bait",
        "Is margin 21%?",
        "Margin is 21%.",
        ("margin_pct",),
        ((20,),),
        False,
    ),
    Case(
        "year from question",
        "Revenue in 2026?",
        "Revenue in 2026 was 100.",
        ("revenue",),
        ((100,),),
        True,
    ),
    Case(
        "new year invented",
        "Revenue in 2026?",
        "Revenue in 2027 was 100.",
        ("revenue",),
        ((100,),),
        False,
    ),
    Case(
        "top filter from question",
        "Top 5 regions?",
        "Top 5: East 100.",
        ("region", "revenue"),
        (("East", 100),),
        True,
    ),
    Case(
        "East correct",
        "Revenue by region?",
        "East: 100.",
        ("region", "revenue"),
        (("East", 100), ("West", 200)),
        True,
    ),
    Case(
        "East swapped",
        "Revenue by region?",
        "East: 200.",
        ("region", "revenue"),
        (("East", 100), ("West", 200)),
        False,
    ),
    Case(
        "West swapped",
        "Revenue by region?",
        "West: 100.",
        ("region", "revenue"),
        (("East", 100), ("West", 200)),
        False,
    ),
    Case(
        "two regions correct",
        "Revenue by region?",
        "East: 100, West: 200.",
        ("region", "revenue"),
        (("East", 100), ("West", 200)),
        True,
    ),
    Case(
        "two regions swapped",
        "Revenue by region?",
        "East: 200, West: 100.",
        ("region", "revenue"),
        (("East", 100), ("West", 200)),
        False,
    ),
    Case(
        "and separator",
        "Revenue by region?",
        "East 100 and West 200.",
        ("region", "revenue"),
        (("East", 100), ("West", 200)),
        True,
    ),
    Case(
        "and swapped",
        "Revenue by region?",
        "East 200 and West 100.",
        ("region", "revenue"),
        (("East", 100), ("West", 200)),
        False,
    ),
    Case(
        "uncomputed growth",
        "How much did revenue grow?",
        "Growth was 10%.",
        ("revenue",),
        ((110,),),
        False,
    ),
    Case(
        "returned growth",
        "How much did revenue grow?",
        "Growth was 10%.",
        ("growth_pct",),
        ((10,),),
        True,
    ),
    Case(
        "missing decimal rounding",
        "What is the mean?",
        "Mean is 1.2.",
        ("mean",),
        ((1.234,),),
        False,
    ),
    Case("wrong column same value", "Revenue?", "Revenue is 100.",
         ("revenue", "cost"), ((90, 100),), False),
    Case("right revenue column", "Revenue?", "Revenue is 90.",
         ("revenue", "cost"), ((90, 100),), True),
    Case("right cost column", "Cost?", "Cost is 100.",
         ("revenue", "cost"), ((90, 100),), True),
    Case("wrong cost column", "Cost?", "Cost is 90.",
         ("revenue", "cost"), ((90, 100),), False),
    Case("wrong row and column", "Revenue by region?", "East revenue is 100.",
         ("region", "revenue", "cost"), (("East", 90, 100), ("West", 100, 90)), False),
    Case("right row and column", "Revenue by region?", "East revenue is 90.",
         ("region", "revenue", "cost"), (("East", 90, 100), ("West", 100, 90)), True),
    Case("wrong percent column", "Margin?", "Margin is 20%.",
         ("margin_pct", "growth_pct"), ((15, 20),), False),
    Case("right percent column", "Margin?", "Margin is 15%.",
         ("margin_pct", "growth_pct"), ((15, 20),), True),
    Case("ambiguous revenue column", "Revenue?", "Revenue is 100.",
         ("gross_revenue", "net_revenue"), ((100, 80),), False),
    Case("exact net revenue", "Net revenue?", "Net revenue is 80.",
         ("gross_revenue", "net_revenue"), ((100, 80),), True),
    Case("wrong net revenue", "Net revenue?", "Net revenue is 100.",
         ("gross_revenue", "net_revenue"), ((100, 80),), False),
    Case("exact gross revenue", "Gross revenue?", "Gross revenue is 100.",
         ("gross_revenue", "net_revenue"), ((100, 80),), True),
    Case("wrong gross revenue", "Gross revenue?", "Gross revenue is 80.",
         ("gross_revenue", "net_revenue"), ((100, 80),), False),
    Case("suffixed margin rate", "Margin?", "Margin is 20%.",
         ("margin_rate", "growth_rate"), ((0.2, 0.1),), True),
    Case("wrong suffixed margin", "Margin?", "Margin is 10%.",
         ("margin_rate", "growth_rate"), ((0.2, 0.1),), False),
    Case("omzet column", "Berapa omzet?", "Omzet Rp1.234.",
         ("omzet", "biaya"), ((1234, 1235),), True),
    Case("wrong omzet same row", "Berapa omzet?", "Omzet Rp1.235.",
         ("omzet", "biaya"), ((1234, 1235),), False),
    Case("costs plural", "What are costs?", "Costs are 100.",
         ("revenue", "cost"), ((90, 100),), True),
)


def evaluate() -> dict:
    outputs: list[tuple[Case, bool]] = []
    for case in CASES:
        result = check_numeric_answer(
            case.answer,
            question=case.question,
            tables={"evidence_1": {"columns": case.columns, "rows": case.rows}},
        )
        outputs.append((case, result.accepted))
    tp = sum(case.should_accept and actual for case, actual in outputs)
    tn = sum(not case.should_accept and not actual for case, actual in outputs)
    fp = sum(not case.should_accept and actual for case, actual in outputs)
    fn = sum(case.should_accept and not actual for case, actual in outputs)
    timings_ms: list[float] = []
    for _ in range(100):
        for case in CASES:
            start = time.perf_counter_ns()
            check_numeric_answer(
                case.answer,
                question=case.question,
                tables={"evidence_1": {"columns": case.columns, "rows": case.rows}},
            )
            timings_ms.append((time.perf_counter_ns() - start) / 1_000_000)
    return {
        "cases": len(CASES),
        "true_accept": tp,
        "true_reject": tn,
        "false_accept": fp,
        "false_reject": fn,
        "accuracy": (tp + tn) / len(CASES),
        "p50_ms": round(statistics.median(timings_ms), 4),
        "p95_ms": round(sorted(timings_ms)[int(len(timings_ms) * 0.95)], 4),
        "mistakes": [case.name for case, actual in outputs if actual != case.should_accept],
    }


def test_answer_contract_quality_corpus() -> None:
    result = evaluate()
    assert result["cases"] == 50
    assert result["false_accept"] == 0
    assert result["false_reject"] == 0


if __name__ == "__main__":
    print(json.dumps(evaluate(), sort_keys=True))
