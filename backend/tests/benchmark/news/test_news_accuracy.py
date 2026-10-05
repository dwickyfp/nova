"""The offline News benchmark as a gate: accuracy, access and cost thresholds."""

import json

import pytest

from tests.benchmark.news import dataset
from tests.benchmark.news.evaluate import desk_failures, evaluate, evaluate_desks, failures
from tests.benchmark.news.run import GOLD_PATH, gold_file

pytestmark = pytest.mark.benchmark


async def test_the_newsroom_meets_every_threshold_on_the_labelled_dataset():
    report = await evaluate()

    assert failures(report) == [], report
    assert report["expected_stories"] == len(dataset.gold())
    assert report["access_checks"] > 100


async def test_the_result_does_not_depend_on_one_lucky_seed():
    for seed in (1, 7, 2026):
        report = await evaluate(seed)
        assert failures(report) == [], (seed, report)


def test_the_committed_gold_file_matches_the_dataset():
    assert json.loads(GOLD_PATH.read_text()) == gold_file()


def test_every_label_names_who_may_read_it():
    stories = gold_file()["expected_stories"]

    bandung = next(item for item in stories if item["anomaly"] == "bandung-drop")
    assert bandung["visible_to"] == [
        "news_bandung", "news_manager", "news_west_java", "nova_task_service_news",
    ]
    total = next(item for item in stories if item["type"] == "total")
    assert total["visible_to"] == ["news_manager", "nova_task_service_news"]
    assert all("news_off" not in item["visible_to"] for item in stories)


@pytest.mark.parametrize("seed", [dataset.SEED, 7])
async def test_the_extra_desks_report_their_designed_situations_and_leak_nothing(seed):
    report = await evaluate_desks(seed)

    assert desk_failures(report) == [], report
    assert set(report["desks"]) == {"workforce", "expenses", "reliability"}
    assert all(score["required"] >= 2 for score in report["desks"].values())
    assert report["access_checks"] > 150
