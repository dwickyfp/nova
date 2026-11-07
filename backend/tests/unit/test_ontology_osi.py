"""A release exchanged as Ossie documents is read back without losing anything."""

from __future__ import annotations

import copy
import json

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.ontology.osi import EXTENSION, export_release, read_documents
from tests.unit._ontology import ADMIN, ANALYST, OUTSIDER
from tests.unit.test_ontology_grounding import bind


@pytest.fixture
async def world(monkeypatch):
    """The grounding test world: domains, a published sales view and the resolver."""
    from app.modules.ontology import grounding as grounding_module
    from app.modules.ontology.resolver import ConceptResolver
    from tests.unit.test_business_ontology import World
    from tests.unit.test_ontology_grounding import sales_definition

    built = World(monkeypatch)
    await built.domains()
    built.catalog.publish("sales", sales_definition())
    built.resolver = ConceptResolver(built.reads)
    monkeypatch.setattr(grounding_module.concept_resolver, "resolve", built.resolver.resolve)
    return built


async def published(world):
    concept = await bind(world, world.finance, "NetRevenue", label="Net revenue",
                         aliases=["omzet bersih", "penjualan bersih"])
    await world.release()
    return concept


async def test_every_bound_metric_carries_its_official_meaning(world):
    await published(world)
    exported = await export_release(ADMIN, service=world.reads)
    assert exported["complete"] is True and len(exported["documents"]) == 1
    document = exported["documents"][0]["document"]
    # A standard Ossie reader accepts the document.
    assert parse_ossie(json.dumps(document), raise_on_error=False).valid
    revenue = next(item for item in document["metrics"] if item["name"] == "total_revenue")
    assert {"omzet bersih", "penjualan bersih"} <= set(revenue["synonyms"])
    assert revenue["description"] == "The official meaning of NetRevenue."
    assert revenue["ai_context"]["nova_concept"]["label"] == "Net revenue"


async def test_reading_the_documents_back_reproduces_the_release_manifest(world):
    await published(world)
    release = await world.releases.alias()
    exported = await export_release(ADMIN, service=world.reads)
    read = read_documents([json.dumps(item["document"]) for item in exported["documents"]])
    assert read["lossless"] is True and read["complete"] is True
    current = await world.releases.get(release.release_id)
    assert read["manifest_hash"] == current.manifest_hash
    assert read["records"]["concept"] == 1 and read["records"]["representation"] == 1


async def test_a_changed_record_is_detected_on_import(world):
    await published(world)
    exported = await export_release(ADMIN, service=world.reads)
    document = copy.deepcopy(exported["documents"][0]["document"])
    concept = document["ai_context"][EXTENSION]["records"]["concept"][0]
    concept["definition"] = "Something else."
    read = read_documents([json.dumps(document)])
    assert read["lossless"] is False


async def test_only_what_the_caller_may_see_is_exported(world):
    await published(world)
    # An outsider sees no finance domain: nothing of it leaves, and the export says so.
    exported = await export_release(OUTSIDER, service=world.reads)
    assert exported["complete"] is False
    assert all(not item["document"]["ai_context"][EXTENSION]["records"]["concept"]
               for item in exported["documents"])
    analyst = await export_release(ANALYST, service=world.reads)
    assert analyst["documents"]


def test_documents_without_the_extension_or_from_two_releases_are_refused():
    plain = {"version": "0.1.1", "name": "m", "datasets": [{"name": "d", "source": "db.t",
                                                            "fields": []}]}
    with pytest.raises(HTTPException) as missing:
        read_documents([json.dumps(plain)])
    assert missing.value.status_code == 422
    first = {**plain, "ai_context": {EXTENSION: {"release_id": "r1", "records": {}}}}
    second = {**plain, "ai_context": {EXTENSION: {"release_id": "r2", "records": {}}}}
    with pytest.raises(HTTPException):
        read_documents([json.dumps(first), json.dumps(second)])
