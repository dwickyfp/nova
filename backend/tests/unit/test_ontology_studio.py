"""Ontology tools, release pins and Mission business anchors in Studio."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import business_results, releases
from app.modules.agents.mission_schema import BusinessContextAnchor, ConceptPin, Mission
from app.modules.agents.registry import KNOWN_TOOLS, build_registry
from app.modules.agents.router import KNOWN_TOOLS as ROUTER_TOOLS
from app.modules.agents.tool_catalog import AGENT_BUNDLEABLE_TOOLS, BUILTIN_TOOLS
from app.modules.agents.tools import ontology as ontology_tools
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.ontology import resolver as resolver_module
from app.modules.ontology import service as service_module
from app.modules.ontology.contracts import RepresentationChange
from app.modules.ontology.resolver import ConceptResolver
from tests.unit._ontology import ANALYST, FINANCE_STEWARD, SALES_STEWARD
from tests.unit.test_automatic_business_hook import governed_outcome
from tests.unit.test_business_ontology import World
from tests.unit.test_intelligence_engine import USER
from tests.unit.test_ontology_grounding import sales_definition
from tests.unit.test_studio_missions import USER as MISSION_USER
from tests.unit.test_studio_missions import create
from tests.unit.test_studio_missions import mission_io as mission_io


@pytest.fixture
async def world(monkeypatch):
    world = World(monkeypatch)
    await world.domains()
    world.catalog.publish("sales", sales_definition())
    monkeypatch.setattr(service_module, "ontology_service", world.reads)
    monkeypatch.setattr(resolver_module, "concept_resolver", ConceptResolver(world.reads))
    created = await world.approved(
        [
            world.concept(world.finance, "NetRevenue", aliases=["omzet"]),
            RepresentationChange(
                op="create", concept_id="@0", view_id="sales", metric="total_revenue"
            ),
            world.concept(world.finance, "Customer", kind="BUSINESS_ENTITY"),
        ]
    )
    world.net_revenue = world.concept_id(created, 0)
    world.customer = world.concept_id(created, 2)
    from app.modules.ontology.contracts import RelationChange

    await world.approved(
        [
            RelationChange(
                op="create", source_id=world.net_revenue, type="MEASURES",
                target_id=world.customer, justification="Revenue is earned from customers",
            )
        ]
    )
    world.first = await world.release()
    return world


def tool_context(user=ANALYST, **extra):
    values = {
        "user": user, "release_manifest": None, "ontology_release_id": None, "agent_id": None,
        **extra,
    }
    return SimpleNamespace(**values)


async def call(tool, arguments, context):
    return await tool.run(ToolInvocation("call", tool.name, arguments), context)


# --- tools ---------------------------------------------------------------------------


def test_the_tools_are_known_bundleable_and_cataloged():
    for name in ("ontology_resolve", "ontology_explain"):
        assert name in KNOWN_TOOLS and name in ROUTER_TOOLS
        assert name in AGENT_BUNDLEABLE_TOOLS and name in BUILTIN_TOOLS
        assert BUILTIN_TOOLS[name][1]["type"] == "object"
    registry = build_registry({"default_tools": ["ontology_explain"]})
    assert "ontology_explain" in registry.names()
    assert "ontology_resolve" not in registry.names()
    for tool in (ontology_tools.ontology_resolve_tool, ontology_tools.ontology_explain_tool):
        assert tool.classification == "read_only" and tool.requires_consent
        assert tool.preview(None)


async def test_resolve_tool_returns_definitions_and_remembers_the_release(world):
    context = tool_context()
    outcome = await call(ontology_tools.ontology_resolve_tool, {"text": "omzet per kota"}, context)
    assert outcome.ok
    concept = outcome.data["concepts"][0]
    assert concept["qualified_name"] == "Finance.NetRevenue"
    assert concept["representations"][0]["metric"] == "total_revenue"
    assert context.ontology_release_id == world.first.id
    assert outcome.evidence == {"source": "business_ontology", "release_id": world.first.id}


async def test_resolve_tool_reports_status_and_limits_bindings_to_agent_views(world, monkeypatch):
    from app.modules.agents.semantic import access as semantic_access

    monkeypatch.setattr(semantic_access, "_context_ids", lambda context: ["other_view"])
    outcome = await call(
        ontology_tools.ontology_resolve_tool, {"text": "omzet"}, tool_context(agent_id="agent")
    )
    assert outcome.data["concepts"][0]["representations"] == []
    none = await call(ontology_tools.ontology_resolve_tool, {"text": "weather"}, tool_context())
    assert none.summary == "The text names no approved business concept."
    bad = await call(ontology_tools.ontology_resolve_tool, {}, tool_context())
    assert not bad.ok


async def test_explain_tool_by_qualified_name_with_related_concepts(world):
    outcome = await call(
        ontology_tools.ontology_explain_tool, {"concept": "finance.netrevenue"}, tool_context()
    )
    assert outcome.ok, outcome.error
    assert outcome.data["concept"]["id"] == world.net_revenue
    assert outcome.data["representations"][0]["metric"] == "total_revenue"
    assert outcome.data["related"]["concepts"][0]["qualified_name"] == "Finance.Customer"
    assert outcome.data["related"]["relations"][0]["type"] == "MEASURES"
    by_id = await call(
        ontology_tools.ontology_explain_tool,
        {"concept": world.net_revenue, "include_related": False},
        tool_context(),
    )
    assert by_id.data["related"] is None


async def test_explain_tool_hides_concepts_the_role_cannot_see(world):
    outcome = await call(
        ontology_tools.ontology_explain_tool, {"concept": "Finance.NetRevenue"},
        tool_context(user=SALES_STEWARD),
    )
    assert not outcome.ok
    assert outcome.error == "Ontology record not found"


async def test_explain_tool_follows_the_pinned_release(world):
    await world.approved([world.concept(world.finance, "Margin")])
    await world.release()
    pinned = tool_context(ontology_release_id=world.first.id)
    outcome = await call(
        ontology_tools.ontology_explain_tool, {"concept": "Finance.Margin"}, pinned
    )
    assert not outcome.ok
    current = await call(
        ontology_tools.ontology_explain_tool, {"concept": "Finance.Margin"}, tool_context()
    )
    assert current.ok


async def test_ontology_resolve_route(world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_user
    from app.modules.ontology import router as router_module

    monkeypatch.setattr(router_module, "ontology_service", world.reads)
    app = FastAPI()
    app.include_router(router_module.router, prefix="/ontology")
    app.dependency_overrides[get_current_user] = lambda: FINANCE_STEWARD
    body = TestClient(app).get("/ontology/resolve", params={"q": "omzet"}).json()
    assert body["status"] == "resolved"
    assert body["concepts"][0]["qualified_name"] == "Finance.NetRevenue"


# --- release pins ------------------------------------------------------------------------


async def test_new_agent_versions_pin_the_published_ontology_release(world, monkeypatch):
    from app.modules.ontology import releases as ontology_releases

    monkeypatch.setattr(ontology_releases, "release_service", world.releases)
    pin = await releases.ontology_release_pin()
    assert pin == {"release_id": world.first.id, "manifest_hash": world.first.manifest_hash}
    await releases.authorize_ontology_pin({"ontology_release": pin})
    await releases.authorize_ontology_pin({})  # older manifests pin nothing
    with pytest.raises(HTTPException) as changed:
        await releases.authorize_ontology_pin(
            {"ontology_release": {**pin, "manifest_hash": "0" * 64}}
        )
    assert changed.value.status_code == 409
    with pytest.raises(HTTPException) as missing:
        await releases.authorize_ontology_release("ont_rel_9999", "0" * 64)
    assert missing.value.status_code == 409


async def test_without_a_published_release_nothing_is_pinned(monkeypatch):
    from app.modules.ontology import releases as ontology_releases

    monkeypatch.setattr(
        ontology_releases.release_service, "alias", AsyncMock(return_value=None)
    )
    assert await releases.ontology_release_pin() is None


# --- mission anchors ---------------------------------------------------------------------


ANCHOR = BusinessContextAnchor(
    release_id="ont_rel_0001", manifest_hash="a" * 64,
    concepts=[ConceptPin(id="con_b", revision=2), ConceptPin(id="con_a", revision=1),
              ConceptPin(id="con_a", revision=1)],
)


def test_business_anchors_are_normalised_and_old_missions_still_load():
    assert [(item.id, item.revision) for item in ANCHOR.concepts] == [("con_a", 1), ("con_b", 2)]
    assert "business_anchors" in Mission.model_fields
    assert Mission.model_fields["business_anchors"].default_factory() == []


async def test_missions_record_business_anchors_once(mission_io):
    _, service = mission_io
    mission = await create(service)
    anchored = await service.record_business_anchor(mission.mission_id, ANCHOR, MISSION_USER)
    assert anchored.business_anchors == [ANCHOR]
    again = await service.record_business_anchor(mission.mission_id, ANCHOR, MISSION_USER)
    assert again.revision == anchored.revision


async def test_a_governed_result_with_business_context_anchors_the_mission(monkeypatch):
    monkeypatch.setattr(business_results.mission_service, "record_anchor", AsyncMock())
    record = AsyncMock()
    monkeypatch.setattr(business_results.mission_service, "record_business_anchor", record)
    outcome = governed_outcome()
    outcome = replace(outcome, evidence={"business_context": {
        "ontology_release_id": "ont_rel_0001", "manifest_hash": "a" * 64,
        "concepts": [{"id": "con_a", "revision": 1, "qualified_name": "Finance.NetRevenue"}],
    }})
    context = LoopContext(user_name="alice", user=USER, mission_id="mission", run_id="run",
                          effective_work_intent="ANSWER")
    await business_results.governed_result(
        ToolInvocation("call", "semantic_query", {}), outcome, context
    )
    anchor = record.await_args.args[1]
    assert anchor.release_id == "ont_rel_0001"
    assert anchor.concepts == [ConceptPin(id="con_a", revision=1)]


def test_a_continued_mission_keeps_its_ontology_release():
    context = LoopContext(user_name="alice", user=USER, mission_id="mission")
    business_results.bind_business_hooks(
        context, {"business_anchors": [ANCHOR.model_dump(mode="json")]}
    )
    assert context.ontology_release_id == "ont_rel_0001"
    later = LoopContext(user_name="alice", user=USER, mission_id="mission")
    business_results.bind_business_hooks(later, SimpleNamespace(business_anchors=[ANCHOR]))
    assert later.ontology_release_id == "ont_rel_0001"
    fresh = LoopContext(user_name="alice", user=USER, mission_id=None)
    business_results.bind_business_hooks(fresh)
    assert fresh.ontology_release_id is None


def test_concept_grounding_is_scored_from_recorded_facts():
    from app.modules.agents.quality_scoring import Assertion
    from app.modules.agents.quality_scoring import score_assertion as score

    expected = Assertion(
        scorer="concept_grounding",
        expected={"status": "grounded", "concepts": ["Finance.NetRevenue"]},
    )
    recorded = {"facts": {"concept_grounding": {
        "status": "grounded", "concepts": ["Finance.NetRevenue"], "metrics": ["total_revenue"],
    }}}
    assert score(expected, recorded).status == "pass"
    wrong = {"facts": {"concept_grounding": {"status": "grounded", "concepts": ["Sales.Revenue"]}}}
    failed = score(expected, wrong)
    assert failed.status == "fail" and failed.failure_taxonomy == "WRONG_BUSINESS_CONCEPT"
    assert score(expected, {"facts": {}}).status == "unavailable"


async def test_resolve_tool_reads_a_shared_term_in_the_agents_own_business(world, monkeypatch):
    """Sales defines its own NetRevenue ("omzet") on another view: each agent
    gets its business's definition, never the other's."""
    from app.modules.agents.semantic import access as semantic_access

    await world.approved(
        [
            world.concept(
                world.sales, "NetRevenue", aliases=["omzet"],
                definition="Sales omzet: invoiced sell-in after returns.",
            ),
            RepresentationChange(
                op="create", concept_id="@0", view_id="sales_sv", metric="revenue"
            ),
        ],
        reviewers=(SALES_STEWARD,),
    )
    await world.release()
    unscoped = await call(ontology_tools.ontology_resolve_tool, {"text": "omzet"}, tool_context())
    assert unscoped.summary.startswith("A term has several approved definitions")

    for views, expected, foreign in (
        (["sales_sv"], "Sales.NetRevenue", "Finance.NetRevenue"),
        (["sales"], "Finance.NetRevenue", "Sales.NetRevenue"),
    ):
        monkeypatch.setattr(semantic_access, "_context_ids", lambda context, v=views: v)
        outcome = await call(
            ontology_tools.ontology_resolve_tool, {"text": "omzet per kota"},
            tool_context(agent_id="agent"),
        )
        assert [item["qualified_name"] for item in outcome.data["concepts"]] == [expected]
        assert foreign not in str(outcome.data)


async def test_an_agent_that_spans_two_meanings_is_told_to_ask_which(world, monkeypatch):
    """Read in the agent's own views: a term two of its businesses define is named
    with both choices; a term only one of them defines adds nothing."""
    from app.modules.agents import router as agent_router

    await world.approved(
        [
            world.concept(world.sales, "NetRevenue", aliases=["omzet"],
                          definition="Sales omzet: invoiced sell-in after returns."),
            RepresentationChange(
                op="create", concept_id="@0", view_id="sales_sv", metric="revenue"
            ),
        ],
        reviewers=(SALES_STEWARD,),
    )
    await world.release()
    both = await agent_router._business_terms_note("omzet per kota", ANALYST, ["sales", "sales_sv"])
    assert "Finance.NetRevenue" in both and "Sales.NetRevenue" in both
    assert "Ask the user which one they mean" in both
    assert await agent_router._business_terms_note("omzet per kota", ANALYST, ["sales"]) == ""
    assert await agent_router._business_terms_note("omzet", ANALYST, []) == ""
