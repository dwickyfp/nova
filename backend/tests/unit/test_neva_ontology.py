"""Neva and the business glossary: tools, consent, wording and hidden domains."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.agents.tools import ontology as tools
from app.modules.assistant import router as neva
from app.modules.assistant.plain_language import make_plain
from app.modules.assistant.planning import validate_turn_plan
from app.modules.assistant.registry import build_neva_registry
from app.modules.assistant.state import ConsentPolicy
from app.modules.assistant.tools import ToolInvocation, requires_consent
from tests.unit._ontology import ADMIN, ANALYST, OUTSIDER, SALES_STEWARD
from tests.unit.test_business_ontology import World

GLOSSARY = ("ontology_resolve", "ontology_explain", "ontology_status",
            "knowledge_propose", "knowledge_withdraw")


@pytest.fixture
async def world(monkeypatch):
    world = World(monkeypatch)
    await world.domains()
    return world


def test_neva_has_the_glossary_and_grounds_terms_in_order():
    registry = build_neva_registry()
    assert all(registry.get(name) is not None for name in GLOSSARY)
    prompt = neva.NEVA_GROUNDING
    order = [prompt.index(word) for word in
             ("business glossary", "Semantic View", "verified query", "new SQL")]
    assert order == sorted(order)
    assert "ask" in prompt and "proposed, never changed" in prompt
    # Ambiguity: the data's own period is checked before asking which period
    # is meant.
    assert "check what period the data covers" in prompt
    assert "Never ask permission for a read-only lookup" in prompt


def test_writing_sql_may_check_what_a_business_word_means():
    plan = validate_turn_plan(
        {"intent": "sql_authoring", "tools": ["ontology_resolve", "catalog_search"],
         "required_tools": ["ontology_resolve"], "skills": [], "ml_task": None},
        {"ontology_resolve", "catalog_search", "validate_sql"},
    )
    assert {"ontology_resolve", "validate_sql"} <= set(plan.selected_tools)


@pytest.mark.parametrize("name", ["knowledge_propose", "knowledge_withdraw"])
def test_a_glossary_change_asks_every_time_even_with_read_access_allowed(name):
    tool = build_neva_registry().get(name)
    assert requires_consent(tool)
    assert not ConsentPolicy(always_allow_read_only=True).covers(tool.classification)


def test_reads_are_read_only_and_names_stay_out_of_the_answer():
    registry = build_neva_registry()
    assert all(registry.get(name).classification == "read_only"
               for name in ("ontology_resolve", "ontology_explain", "ontology_status"))
    assert "ontology_status" not in make_plain("I used ontology_status to check.")
    assert "knowledge_withdraw" not in make_plain("Then knowledge_withdraw ran.")


async def test_a_hidden_domain_is_refused_exactly_like_a_missing_concept(world, monkeypatch):
    proposal = await world.approved(
        [world.concept(world.finance, "NetRevenue")],
    )
    await world.release()
    concept_id = world.concept_id(proposal)
    monkeypatch.setattr("app.modules.ontology.service.ontology_service", world.reads)
    errors = []
    for reference in (concept_id, "Finance.DoesNotExist"):
        outcome = await tools.ontology_explain_tool.run(
            ToolInvocation("e1", "ontology_explain", {"concept": reference}),
            SimpleNamespace(user=OUTSIDER, agent_id=None, ontology_release_id=None),
        )
        assert not outcome.ok
        errors.append(outcome.error)
    assert errors[0] == errors[1]
    with pytest.raises(HTTPException):
        await world.reads.find(concept_id, OUTSIDER)
    assert (await world.reads.find(concept_id, ANALYST))["id"] == concept_id


async def test_status_lists_only_the_caller_s_proposals_and_withdraw_keeps_meaning(
    world, monkeypatch
):
    await world.approved([world.concept(world.sales, "Pipeline")], reviewers=(SALES_STEWARD,))
    await world.release()
    mine = await world.propose([world.concept(world.sales, "Quota")], submitter=ANALYST)
    other = await world.propose([world.concept(world.sales, "Forecast")], submitter=ADMIN)
    monkeypatch.setattr("app.modules.ontology.stewardship.stewardship_service", world.stewardship)
    monkeypatch.setattr("app.modules.ontology.releases.release_service", world.releases)
    context = SimpleNamespace(user=ANALYST, agent_id=None)
    status = await tools.ontology_status_tool.run(
        ToolInvocation("s1", "ontology_status", {}), context
    )
    ids = {item["id"] for item in status.data["my_proposals"]}
    assert status.ok and mine.id in ids and other.id not in ids
    assert status.data["release"]["sequence"] == 1
    withdrawn = await tools.knowledge_withdraw_tool.run(
        ToolInvocation("w1", "knowledge_withdraw", {"proposal_id": mine.id}), context
    )
    assert withdrawn.ok and withdrawn.data["status"] == "WITHDRAWN"
    assert "nothing was changed" in withdrawn.summary
    names = [item["slug"] for item in (await world.reads.list_concepts(ANALYST))["concepts"]]
    assert names == ["Pipeline"]
