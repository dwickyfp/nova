"""Learning without mutation: agent proposals, News feedback and significance."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agents.tools.ontology import knowledge_propose_tool
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.intelligence import business_events
from app.modules.intelligence.newsroom_feedback import LARGEST, rank
from app.modules.ontology import service as service_module
from app.modules.ontology import stewardship as stewardship_module
from app.modules.ontology.contracts import (
    Concept,
    Proposal,
    RelationChange,
    RepresentationChange,
)
from tests.unit._ontology import ANALYST, FINANCE_STEWARD, OUTSIDER
from tests.unit.test_business_events import Hub, story
from tests.unit.test_business_ontology import World

#: Proposals are read back as a steward who can see the Finance domain.
ADMIN_VIEW = {**FINANCE_STEWARD}


@pytest.fixture
async def world(monkeypatch):
    world = World(monkeypatch)
    await world.domains()
    created = await world.approved(
        [world.concept(world.finance, "NetRevenue", aliases=["omzet"])]
    )
    world.revenue = world.concept_id(created)
    await world.release()
    monkeypatch.setattr(service_module, "ontology_service", world.reads)
    monkeypatch.setattr(stewardship_module, "stewardship_service", world.stewardship)
    return world


async def propose(arguments, user=ANALYST, call="call-1"):
    context = LoopContext(user_name=user["username"], user=user, run_id="run-1")
    return await knowledge_propose_tool.run(
        ToolInvocation(call, "knowledge_propose", arguments), context
    )


# --- agent proposals -----------------------------------------------------------------------


async def test_a_users_correction_becomes_a_proposal_not_a_change(world):
    outcome = await propose({
        "change": "add_aliases", "concept": "finance.netrevenue", "aliases": ["GMV bersih"],
        "rationale": "The user calls net revenue 'GMV bersih'",
    })
    assert outcome.ok, outcome.error
    assert "Nothing changes until a steward approves" in outcome.summary
    (proposal,) = [
        item for item in await world.repository.current("proposals", Proposal)
        if item.source == "AGENT"
    ]
    assert proposal.status == "PENDING"
    assert proposal.submitter == ANALYST["username"]
    assert proposal.changes[0].body.aliases == ["GMV bersih", "omzet"]
    concept = await world.repository.latest("concepts", world.revenue, Concept)
    assert concept.aliases == ["omzet"] and concept.revision == 1  # unchanged
    replay = await propose({
        "change": "add_aliases", "concept": "finance.netrevenue", "aliases": ["GMV bersih"],
        "rationale": "The user calls net revenue 'GMV bersih'",
    })
    assert replay.data["proposal_id"] == outcome.data["proposal_id"]


async def test_a_proposed_formula_change_waits_for_a_steward_and_a_release(world):
    outcome = await propose({
        "change": "revise_definition", "concept": world.revenue,
        "definition": "Revenue after discounts, refunds and chargebacks.",
        "rationale": "Chargebacks were missing",
    })
    assert outcome.ok, outcome.error
    proposal_id = outcome.data["proposal_id"]
    # The agent acted for the analyst, so the analyst cannot approve it.
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await world.review(await world.stewardship.get(proposal_id, ADMIN_VIEW), ANALYST)
    applied = await world.review(await world.stewardship.get(proposal_id, ADMIN_VIEW),
                                 FINANCE_STEWARD)
    assert applied.status == "APPLIED"
    # Studio still answers with the published release until a new one is published.
    published = await world.reads.get_concept(world.revenue, ANALYST)
    assert published["concept"]["definition"] == "The official meaning of NetRevenue."
    await world.release()
    assert (await world.reads.get_concept(world.revenue, ANALYST))["concept"]["definition"] == (
        "Revenue after discounts, refunds and chargebacks."
    )


async def test_new_concepts_and_refusals(world):
    created = await propose({
        "change": "new_concept",
        "new_concept": {"domain": "finance", "slug": "Chargebacks", "label": "Chargebacks",
                        "definition": "Card payments reversed by the issuer.", "unit": "IDR"},
        "aliases": ["chargeback"], "rationale": "Asked about chargebacks twice",
    })
    assert created.ok and created.data["title"] == "Add Finance.Chargebacks"
    for arguments, user in (
        ({"change": "add_aliases", "concept": "Finance.NetRevenue", "aliases": ["x"],
          "rationale": "r"}, OUTSIDER),
        ({"change": "add_aliases", "concept": "Finance.NetRevenue", "rationale": "r"}, ANALYST),
        ({"change": "revise_definition", "concept": "Finance.NetRevenue", "rationale": "r"},
         ANALYST),
        ({"change": "new_concept", "rationale": "r"}, ANALYST),
        ({"change": "new_concept", "rationale": "r", "new_concept": {
            "domain": "Nowhere", "slug": "X", "label": "X", "definition": "x"}}, ANALYST),
        ({"change": "add_aliases", "aliases": ["x"], "rationale": "r"}, ANALYST),
        ({"change": "bogus", "rationale": "r"}, ANALYST),
    ):
        outcome = await propose(arguments, user, call=str(arguments))
        assert not outcome.ok, arguments
    hidden = await propose({"change": "add_aliases", "concept": "Finance.NetRevenue",
                            "aliases": ["x"], "rationale": "r"}, OUTSIDER, call="hidden")
    assert hidden.error == "Ontology record not found"


def test_the_tool_files_proposals_behind_consent():
    from app.modules.agents.harness_worker import SMART_ROOT_TOOLS
    from app.modules.agents.registry import KNOWN_TOOLS, build_registry
    from app.modules.agents.tool_catalog import AGENT_BUNDLEABLE_TOOLS, BUILTIN_TOOLS

    assert knowledge_propose_tool.classification == "session_change"
    assert knowledge_propose_tool.requires_consent and knowledge_propose_tool.preview(None)
    for registry_names in (KNOWN_TOOLS, AGENT_BUNDLEABLE_TOOLS, BUILTIN_TOOLS):
        assert "knowledge_propose" in registry_names
    # A Smart turn cannot ask consent: it drafts, and the user files the proposal.
    assert "knowledge_propose" not in SMART_ROOT_TOOLS
    assert "knowledge_propose" in build_registry({"default_tools": ["knowledge_propose"]}).names()


# --- News feedback never mutates meaning -------------------------------------------------------


def public(story_id, *, severity="warning", relative_change=-0.1, rank=1, metric="revenue",
           significance=None):
    story = {
        "id": story_id, "view_id": "sales", "metric": metric, "change": -1.0, "slice": None,
        "severity": severity, "relative_change": relative_change, "rank": rank,
    }
    return story | ({"significance": significance} if significance is not None else {})


async def test_disliking_a_story_changes_its_order_not_the_ontology(world):
    before = await world.repository.current("proposals", Proposal)
    stories = [
        public("a", severity="critical", relative_change=-0.3, rank=1, metric="revenue"),
        public("b", relative_change=-0.1, rank=2, metric="orders"),
    ]
    ranked = rank(stories, [{"story_id": "a", "reaction": "dislike", "features": {}}])
    assert [item["id"] for item in ranked] == ["b", "a"]
    # A dislike is a reader's preference, never evidence that a definition is wrong.
    assert await world.repository.current("proposals", Proposal) == before


# --- significance ----------------------------------------------------------------------------


async def test_significance_uses_goals_corroboration_and_official_definitions(monkeypatch):
    world = World(monkeypatch)
    await world.domains()
    await world.let_sales_steward_read_finance()
    created = await world.approved(
        [
            world.concept(world.finance, "NetRevenue"),
            RepresentationChange(op="create", concept_id="@0", view_id="sales_sv",
                                 metric="revenue"),
            world.concept(world.finance, "GrowthTarget", kind="GOAL"),
            RelationChange(op="create", source_id="@0", type="AFFECTS_GOAL", target_id="@2",
                           justification="Revenue growth is the annual goal"),
            world.concept(world.finance, "RefundRate"),
        ]
    )
    revenue, refunds = world.concept_id(created, 0), world.concept_id(created, 4)
    await world.approved([RelationChange(
        op="create", source_id=revenue, type="RELATED_TO", target_id=refunds,
        justification="Refunds reduce net revenue",
    )])
    await world.release()

    hub = Hub(monkeypatch)
    concepts = {("sales", "net_revenue"): revenue, ("sales", "refund_rate"): refunds}

    async def bind(semantic, metric, definition):
        from app.modules.ontology.binding import MetricBinding

        concept = concepts.get((semantic.view_id, metric))
        if concept is None:
            return MetricBinding(status="ONTOLOGY_UNBOUND", release_id="r")
        return MetricBinding(status="BOUND", release_id="r", concept_id=concept,
                             concept_revision=1, representation_id="rep")

    async def link(a, b):
        from app.modules.ontology.binding import linked

        return await linked(a, b, service=world.reads)

    hub.service.bind, hub.service.link = bind, link
    revenue_story = story("rev", "net_revenue")
    refund_story = story("ref", "refund_rate", digest="b")
    lone = story("lone", "units", digest="c", city="SMB")
    await hub.record(revenue_story, refund_story, lone)

    scores = await business_events.significance(
        [revenue_story, refund_story, lone], ANALYST, service=hub.service, ontology=world.reads,
    )
    assert scores["rev"]["boost"] == 1.5
    assert scores["rev"]["reasons"] == [
        "Affects goal Finance.GrowthTarget",
        "Moved with 1 related story here",
        "Official definition: Finance.NetRevenue",
    ]
    assert scores["ref"]["reasons"] == [
        "Moved with 1 related story here", "Official definition: Finance.RefundRate",
    ]
    assert scores["lone"] == {"boost": 0.0, "reasons": []}

    # A reader who sees only one story of the incident gets no corroboration, and a
    # reader who cannot see the Finance domain gets no concept names at all.
    alone = await business_events.significance(
        [revenue_story], OUTSIDER, service=hub.service, ontology=world.reads,
    )
    assert alone["rev"] == {"boost": 0.0, "reasons": []}

    ranked = rank(
        [
            public("lone", relative_change=-0.4, rank=1, metric="units",
                   significance=scores["lone"]),
            public("rev", relative_change=-0.2, rank=2, significance=scores["rev"]),
        ],
        [],
    )
    assert [item["id"] for item in ranked] == ["rev", "lone"]
    assert ranked[0]["reason"] == "Affects goal Finance.GrowthTarget"
    assert ranked[1]["reason"] == LARGEST


async def test_newspaper_ranking_survives_a_significance_failure(monkeypatch):
    from app.modules.intelligence import newsroom

    async def boom(*_args, **_kwargs):
        raise RuntimeError("metadata down")

    monkeypatch.setattr(business_events, "significance", boom)
    assert await newsroom._significance([SimpleNamespace(id="s")], ANALYST) == {}
    assert await newsroom._significance([], ANALYST) == {}


def test_smart_reads_approved_meaning_itself():
    from app.modules.agents.harness_worker import SMART_ROOT_TOOLS
    from app.modules.agents.registry import build_registry

    names = build_registry({"default_tools": list(SMART_ROOT_TOOLS)}).names()
    assert {"ontology_resolve", "ontology_explain"} <= set(names)


async def test_smart_drafts_a_proposal_that_only_the_user_files(world):
    from app.modules.agents.tools.ontology import draft_knowledge_proposal_tool
    from app.modules.assistant.service import SMART_DRAFT_TOOLS
    from app.modules.ontology.stewardship import ProposalCreate

    tool = draft_knowledge_proposal_tool
    assert tool.name in SMART_DRAFT_TOOLS
    assert tool.classification == "read_only" and not tool.requires_consent
    context = LoopContext(user_name=ANALYST["username"], user=ANALYST, run_id="run-1")
    args = {"change": "add_aliases", "concept": "Finance.NetRevenue",
            "aliases": ["revenue bersih"], "rationale": "Finance says revenue bersih"}
    before = await world.repository.current("proposals", Proposal)
    outcome = await tool.run(ToolInvocation("d1", tool.name, args), context)
    assert outcome.ok and outcome.data == {"title": "Add other names to Finance.NetRevenue",
                                           "filed": False}
    # Drafting files nothing; the draft is exactly what the user's click submits.
    assert await world.repository.current("proposals", Proposal) == before
    (draft,) = context.smart_drafts
    assert draft["draft_kind"] == "knowledge_proposal"
    assert draft["detail"] == "Other names: revenue bersih"
    assert "operation_id" not in draft["payload"]
    payload = ProposalCreate.model_validate({**draft["payload"], "operation_id": "card-12345678"})
    filed = await world.stewardship.create(payload, ANALYST)
    assert filed.status == "PENDING" and filed.changes[0].body.aliases == [
        "omzet", "revenue bersih",
    ]
    hidden = await tool.run(ToolInvocation("d2", tool.name, args),
                            LoopContext(user_name="x", user=OUTSIDER, run_id="run-2"))
    assert not hidden.ok and hidden.error == "Ontology record not found"
