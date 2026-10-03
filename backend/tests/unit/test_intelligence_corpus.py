"""Freeze discipline and compiler admission without executing official holdouts."""

import json
from pathlib import Path

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.autopilot import SemanticChange, apply_changes
from tests.benchmark.business_intelligence.freeze import freeze, read_frozen_corpora, verify
from tests.benchmark.business_intelligence.gold.cases import CATEGORIES, cases
from tests.benchmark.business_intelligence.learning_corpus import interactions
from tests.benchmark.business_intelligence.model import starter_definition, teaching_changes


def test_corpus_counts_categories_languages_and_separation():
    learning, a, b = interactions(), cases("A"), cases("B")
    assert [len(learning), len(a), len(b)] == [80, 60, 40]
    assert len({row.id for row in [*learning, *a, *b]}) == 180
    assert {row.category for row in a} == set(CATEGORIES) == {row.category for row in b}
    assert not ({row.content for row in learning} & {row.question for row in [*a, *b]})
    assert not ({row.question for row in a} & {row.question for row in b})
    assert not any("holdout" in row.content.lower() for row in learning)


def test_expected_metric_plans_compile_without_using_gold_sql():
    model = SemanticModelIR.from_ossie(
        apply_changes(
            starter_definition(), [SemanticChange.model_validate(row) for row in teaching_changes()]
        )
    )
    for case in [*cases("A"), *cases("B")]:
        if case.expected_plan:
            compiled = SemanticCompiler().compile(model, SemanticPlan.from_dict(case.expected_plan))
            assert "SELECT" in compiled.sql


def test_freeze_rejects_mutated_observations_provider_or_code(tmp_path):
    root = Path(__file__).resolve().parents[3]
    provider = {"mode": "scripted", "model": "scripted-pipeline-v1"}
    data = {"dataset_hash": "frozen"}
    destination = tmp_path / "frozen"
    manifest = freeze(root, data, provider, destination)
    verify(root, manifest, data, provider)
    assert json.loads((destination / "holdout_a.json").read_text())[0]["expected_plan"]
    with pytest.raises(ValueError, match="overwritten"):
        freeze(root, data, provider, destination)
    with pytest.raises(ValueError, match="configuration"):
        verify(root, manifest, data, {**provider, "model": "different"})
    with pytest.raises(ValueError, match="data"):
        verify(root, manifest, {"dataset_hash": "changed"}, provider)
    with pytest.raises(ValueError, match="public"):
        freeze(root, data, {**provider, "api_key": "forbidden"}, tmp_path / "invalid")


def test_frozen_holdout_tampering_is_rejected(tmp_path):
    root = Path(__file__).resolve().parents[3]
    destination = tmp_path / "frozen"
    manifest = freeze(
        root, {"dataset_hash": "frozen"}, {"mode": "scripted", "model": "scripted"}, destination
    )
    corpus = read_frozen_corpora(destination, manifest)
    corpus["holdout_b"][0]["question"] = "substituted evaluation question"
    (destination / "holdout_b.json").write_text(json.dumps(corpus["holdout_b"]))
    with pytest.raises(ValueError, match="Frozen corpus changed: holdout_b"):
        read_frozen_corpora(destination, manifest)


def test_qualified_dimension_alias_survives_grouping_and_ordering():
    model = SemanticModelIR.from_ossie(starter_definition())
    plan = SemanticPlan.from_dict(
        {
            "metrics": ["gross_order_value"],
            "dimensions": ["orders.city"],
            "order_by": [{"field": "orders.city", "direction": "asc"}],
            "limit": 10,
        }
    )
    compiled = SemanticCompiler().compile(model, plan)
    assert "AS `orders.city`" in compiled.sql
    assert "ORDER BY `orders.city` ASC" in compiled.sql


def test_scripted_planner_uses_only_supplied_catalog_and_rejects_ambiguity():
    from tests.benchmark.business_intelligence.scripted_planner import plan_from_catalog

    question = "Calculate widget receipts in Depot, 2026-01-02 before 2026-01-05 daily."
    catalog = {
        "metrics": [
            {"name": "received_widgets", "synonyms": ["widget receipts"], "dataset": "widgets"}
        ],
        "dimensions": [
            {"name": "received_at", "dataset": "widgets", "is_time": True},
            {"name": "city", "dataset": "widgets"},
        ],
    }
    assert plan_from_catalog(question, {}) is None
    plan = plan_from_catalog(question, catalog)
    from app.modules.agents.semantic.plan_contract import validate_generated_plan

    validate_generated_plan(plan)
    assert plan["metrics"] == ["received_widgets"]
    assert plan["time"] == {
        "dimension": "widgets.received_at",
        "range": "2026-01-02..2026-01-04",
        "grain": "day",
        "compare": None,
    }
    assert plan["filters"] == [{"field": "widgets.city", "operator": "=", "value": "Depot"}]
    catalog["metrics"].append(
        {"name": "other", "synonyms": ["widget receipts"], "dataset": "widgets"}
    )
    assert plan_from_catalog(question, catalog) is None


async def test_scripted_guard_restores_provider_and_blocks_embedding_calls():
    from app.modules.ai_ml.embeddings import EmbeddingError, EmbeddingService
    from app.modules.assistant.provider import AssistantProviderClient
    from tests.benchmark.business_intelligence.scripted_provider import ScriptedBoundary

    original = AssistantProviderClient.complete
    boundary = ScriptedBoundary()
    with boundary.guard():
        config = await AssistantProviderClient().resolve()
        assert config.provider_id == "scripted-bi"
        with pytest.raises(EmbeddingError, match="lexical"):
            await EmbeddingService.embed_batch(None, [])
    assert AssistantProviderClient.complete is original


def test_scripted_cli_needs_no_paid_permission_but_live_still_does(tmp_path):
    from tests.benchmark.business_intelligence.run import arguments

    common = [
        "--dataset-manifest",
        str(tmp_path / "dataset.json"),
        "--frozen",
        str(tmp_path),
        "--output",
        str(tmp_path / "output"),
    ]
    assert arguments(["scripted", *common, "--repetitions", "1"]).provider_mode == "scripted"
    with pytest.raises(SystemExit):
        arguments(["live", *common])


def story_export_fixture():
    report = json.loads(
        (
            Path(__file__).resolve().parents[3] / "docs/benchmarks/business-intelligence/"
            "development-stories-20261002/stories.json"
        ).read_text()
    )
    for story in report["stories"]:
        driver = (
            "Inventory availability"
            if story["title"] == "Jakarta stockout recovery"
            else "Payment-service latency"
        )
        observations = {
            driver: {
                "before": 14000 if driver == "Inventory availability" else 90,
                "after": 0 if driver == "Inventory availability" else 1400,
            },
            "Marketing spend": {"before": 1750000, "after": 1750000},
        }
        story["independent_cross_domain"] = observations
        story["investigation"]["timeline"].extend(
            {
                "kind": "related_observation",
                "label": label,
                "causal_status": "association",
                **values,
            }
            for label, values in observations.items()
        )
        story["decision"]["options"].append(
            {
                **story["decision"]["options"][0],
                "id": "unit-fixture-alternative",
            }
        )
        story["studio_followup"] = {
            "inferred_context_verified": True,
            "knowledge_revision": story["knowledge"]["revision"],
            "evidence_type": "unit_fixture",
        }
    return report


@pytest.mark.parametrize("changed", ["missing_comparison", "wrong_value", "causal_overclaim"])
def test_story_export_rejects_missing_or_contradictory_cross_domain_proof(tmp_path, changed):
    from tests.benchmark.business_intelligence.stories import write_report

    report = story_export_fixture()
    story = report["stories"][0]
    if changed == "missing_comparison":
        story["independent_cross_domain"].pop("Marketing spend")
    elif changed == "wrong_value":
        story["independent_cross_domain"]["Inventory availability"]["after"] = 1
    else:
        story["investigation"]["timeline"][-2]["causal_status"] = "supported_effect"
    output = tmp_path / "report"
    with pytest.raises(ValueError, match="cross-domain evidence"):
        write_report(output, report["stories"], observation_manifest=report["observation_manifest"])
    assert not output.exists()


def test_story_export_keeps_each_business_driver_in_its_own_narrative(tmp_path):
    from tests.benchmark.business_intelligence.stories import write_report

    report = story_export_fixture()
    write_report(
        tmp_path / "report", report["stories"], observation_manifest=report["observation_manifest"]
    )
    narrative = (tmp_path / "report/stories.md").read_text()
    jakarta, payment = narrative.split("## Payment-service degradation", 1)
    assert "Inventory availability: 14000 before and 0 after" in jakarta
    assert "Payment-service latency: 90 before and 1400 after" in payment
    assert "Payment-service latency" not in jakarta
