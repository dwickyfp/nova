"""CI gate for the Studio accuracy benchmark's offline level (L0).

Planning is the model's job and is measured live (L3). Offline, every case's
expected plan must be valid for the catalog and compile, in every language of
the corpus, so the corpus, the plan contract, and the compiler stay in step.
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.benchmark.studio_accuracy.cases import all_cases
from tests.benchmark.studio_accuracy.gold import prior_window, window
from tests.benchmark.studio_accuracy.model import bench_model
from tests.benchmark.studio_accuracy.run import evaluate_offline, plan_from_expect


@pytest.fixture(scope="module")
def results():
    return evaluate_offline(all_cases(date.today()), bench_model())


def test_every_expected_plan_is_valid_and_compiles(results):
    failed = [f"{item.id}: {item.detail}" for item in results
              if not item.passed and not item.skipped]
    assert failed == []


def test_the_corpus_covers_thirteen_languages():
    assert len({case.lang for case in all_cases(date.today())}) >= 13


def test_corpus_covers_every_category_the_plan_names():
    categories = {case.category for case in all_cases(date.today())}
    assert {
        "time_range", "comparison", "time_grain", "dimension", "filter", "synonym",
        "ranking", "multi_metric", "paraphrase", "follow_up", "derived", "share",
        "top_per_group", "having", "multi_fact", "out_of_scope", "policy",
    } <= categories


def test_every_answer_case_compiles_from_its_expectation():
    from app.modules.agents.semantic.compiler import SemanticCompiler

    model = bench_model()
    for case in all_cases(date.today()):
        if case.outcome == "answer" and case.phase == 1:
            SemanticCompiler().compile(model, plan_from_expect(model, case.expect))


@pytest.mark.parametrize(
    ("value", "start", "end"),
    [
        ("previous_quarter", date(2026, 4, 1), date(2026, 7, 1)),
        ("current_week", date(2026, 9, 28), date(2026, 10, 5)),
        ("last_7_days", date(2026, 9, 23), date(2026, 9, 30)),
        ("ytd", date(2026, 1, 1), date(2026, 10, 1)),
        ("2025-Q2", date(2025, 4, 1), date(2025, 7, 1)),
    ],
)
def test_oracle_windows(value, start, end):
    assert window(value, date(2026, 9, 30))[:2] == (start, end)


def test_oracle_prior_windows():
    today = date(2026, 9, 30)
    assert prior_window("current_month", "year_over_year", today) == (
        date(2025, 9, 1), date(2025, 10, 1)
    )
    assert prior_window("last_30_days", "previous_period", today) == (
        date(2026, 8, 1), date(2026, 8, 31)
    )
