"""Who reads unreleased ontology work, its history, and what reaches a model."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.modules.ontology.contracts import ConceptBody, ConceptChange
from app.modules.ontology.resolver import ConceptResolver
from tests.unit._ontology import ADMIN, ANALYST, SALES_STEWARD
from tests.unit.test_business_ontology import World


@pytest.fixture
async def world(monkeypatch):
    world = World(monkeypatch)
    await world.domains()
    return world


async def _sales_concept(world, slug="Pipeline", **extra):
    proposal = await world.approved(
        [world.concept(world.sales, slug, **extra)], reviewers=(SALES_STEWARD,)
    )
    return world.concept_id(proposal)


async def test_unreleased_work_is_for_its_stewards_only(world):
    concept_id = await _sales_concept(world)
    names = lambda listing: [item["slug"] for item in listing["concepts"]]  # noqa: E731
    assert names(await world.reads.list_concepts(SALES_STEWARD, scope="working")) == ["Pipeline"]
    assert names(await world.reads.list_concepts(ADMIN, scope="working")) == ["Pipeline"]
    # A reader who can see the domain still sees no unreleased work.
    assert names(await world.reads.list_concepts(ANALYST, scope="working")) == []
    for read in (world.reads.get_concept, world.reads.related, world.reads.representations):
        with pytest.raises(HTTPException) as hidden:
            await read(concept_id, ANALYST, scope="working")
        assert hidden.value.status_code == 404
    await world.release()
    assert names(await world.reads.list_concepts(ANALYST)) == ["Pipeline"]


async def test_history_shows_others_only_released_revisions_without_authors(world):
    concept_id = await _sales_concept(world)
    await world.release()
    current = (await world.reads.get_concept(concept_id, ADMIN))["concept"]
    await world.approved(
        [ConceptChange(
            op="update", concept_id=concept_id, base_revision=current["revision"],
            body=ConceptBody(
                domain_id=world.sales, kind="METRIC", slug="Pipeline", label="Pipeline",
                definition="A revised meaning, not released yet.",
            ),
        )],
        reviewers=(SALES_STEWARD,),
    )
    reader = (await world.reads.get_concept(concept_id, ANALYST))["history"]
    assert [item["revision"] for item in reader] == [current["revision"]]
    assert all("changed_by" not in item and "proposal_id" not in item for item in reader)
    steward = (await world.reads.get_concept(concept_id, SALES_STEWARD))["history"]
    assert len(steward) == 2 and all("changed_by" in item for item in steward)


@pytest.mark.parametrize(
    ("classification", "reader", "for_model"),
    [
        ("CONFIDENTIAL", ANALYST, True),
        ("RESTRICTED", ANALYST, False),
        ("RESTRICTED", SALES_STEWARD, True),
        ("HIGHLY_SENSITIVE", SALES_STEWARD, False),
        ("HIGHLY_SENSITIVE", ADMIN, False),
    ],
)
async def test_classification_limits_what_reaches_a_model(
    world, classification, reader, for_model
):
    concept_id = await _sales_concept(world, classification=classification)
    await world.release()
    resolver = ConceptResolver(world.reads)
    # A person reading the ontology page sees every concept the role can see.
    page = await resolver.resolve("pipeline", reader, for_model=False)
    assert [item["id"] for item in page.concepts] == [concept_id]
    prompt = await resolver.resolve("pipeline", reader)
    assert bool(prompt.concepts) is for_model
    if for_model:
        assert (await world.reads.find(concept_id, reader))["id"] == concept_id
    else:
        with pytest.raises(HTTPException) as hidden:
            await world.reads.find(concept_id, reader)
        assert hidden.value.status_code == 404


async def test_proposals_cannot_name_unreleased_work_of_another_domain(world, monkeypatch):
    from app.modules.agents.tools.ontology import ProposeRequest, _proposed_changes

    concept_id = await _sales_concept(world)  # applied, not released
    monkeypatch.setattr("app.modules.ontology.service.ontology_service", world.reads)
    request = ProposeRequest(change="add_aliases", concept=concept_id, aliases=["pipe"],
                             rationale="r")
    with pytest.raises(ValueError, match="not found"):
        await _proposed_changes(request, ANALYST)
    await world.release()
    changes, _title = await _proposed_changes(request, ANALYST)
    assert changes[0].concept_id == concept_id
