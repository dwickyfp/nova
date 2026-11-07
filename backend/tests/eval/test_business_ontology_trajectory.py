"""Studio uses the organisation's approved business definitions, end to end.

Scripted provider frames drive the shared loop. A term with two approved
definitions ends the turn as a question back; an approved concept without a
usable binding is never answered with a similar metric; a grounded question is
answered with the official metric and carries the ontology release; the
ontology tools stay behind consent.
"""

from __future__ import annotations

import pytest

from app.modules.agents.tools.ontology import ontology_explain_tool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from app.modules.ontology import grounding as grounding_module
from app.modules.ontology import resolver as resolver_module
from app.modules.ontology import service as service_module
from app.modules.ontology.contracts import RepresentationChange
from app.modules.ontology.resolver import ConceptResolver
from tests.benchmark.harness import ScriptedProvider, allow, deny, text_frame, tool_call_frame
from tests.unit._ontology import ADMIN, FINANCE_STEWARD, SALES_STEWARD
from tests.unit.test_business_ontology import World
from tests.unit.test_ontology_grounding import _plan, sales_definition, setup_tool
from tests.unit.test_semantic_guidance_fallback import USER


@pytest.fixture
async def world(monkeypatch):
    world = World(monkeypatch)
    await world.domains()
    world.catalog.publish("sales", sales_definition())
    from app.modules.ontology.service import DomainUpdate

    finance = next(
        item for item in await world.reads.list_domains(ADMIN) if item["id"] == world.finance
    )
    await world.reads.update_domain(
        world.finance,
        DomainUpdate(
            steward_roles=["finance_steward"],
            visibility_roles=["finance_analyst", USER["active_role"], "sales_steward"],
            expected_revision=finance["revision"],
        ),
        ADMIN,
    )
    resolver = ConceptResolver(world.reads)
    monkeypatch.setattr(grounding_module.concept_resolver, "resolve", resolver.resolve)
    monkeypatch.setattr(service_module, "ontology_service", world.reads)
    monkeypatch.setattr(resolver_module, "concept_resolver", resolver)
    return world


async def bind(world, domain, slug, metric, reviewer, **extra):
    created = await world.approved(
        [
            world.concept(domain, slug, **extra),
            RepresentationChange(op="create", concept_id="@0", view_id="sales", metric=metric),
        ],
        reviewers=(reviewer or FINANCE_STEWARD,),
    )
    return world.concept_id(created)


async def run(provider, registry, question, *, consent=allow):
    context = LoopContext(user_name="alice", user=USER.copy())
    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="ontology", user_name="alice", title="Eval"),
            user_content=question,
            context=context,
            resolve_consent=consent,
        )
    ]
    return "".join(frames).replace(" ", ""), context


async def test_a_term_with_two_definitions_finishes_as_a_question_back(world, monkeypatch):
    await bind(world, world.finance, "Revenue", "total_revenue", None)
    await bind(world, world.sales, "Revenue", "order_count", SALES_STEWARD)
    await world.release()
    tool, planner, execute = setup_tool(monkeypatch, world)
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": "revenue per city"}),
        tool_call_frame("s2", name="semantic_query", arguments={"question": "sales revenue"}),
        text_frame("Maksudnya Finance.Revenue atau Sales.Revenue?"),
    ])
    joined, context = await run(provider, registry, "revenue per city")
    assert '"finish_reason":"clarification"' in joined
    assert "Finance.Revenue" in joined and "Sales.Revenue" in joined
    planner.complete.assert_not_awaited()
    execute.assert_not_awaited()
    assert context.quality_facts["concept_grounding"]["status"] == "ambiguous"


async def test_an_unbound_concept_is_not_answered_with_a_similar_metric(world, monkeypatch):
    await world.approved([world.concept(world.finance, "Revenue")])
    await world.release()
    tool, planner, execute = setup_tool(monkeypatch, world, _plan())
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": "revenue by city"}),
        text_frame("Finance.Revenue cannot be computed with the data available here."),
    ])
    joined, _context = await run(provider, registry, "revenue by city")
    # The turn stops: no other data path may answer with a look-alike metric.
    assert '"finish_reason":"error"' in joined
    assert "Finance.Revenuehasanapprovedbusinessdefinition" in joined
    assert "cannotbeansweredhere" in joined
    planner.complete.assert_not_awaited()
    execute.assert_not_awaited()


async def test_a_grounded_question_is_answered_with_the_official_metric(world, monkeypatch):
    await bind(world, world.finance, "NetRevenue", "total_revenue", None, aliases=["omzet"])
    release = await world.release()
    tool, planner, execute = setup_tool(monkeypatch, world, _plan(dimensions=["city"]))
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": "omzet per kota"}),
        text_frame("Omzet (Finance.NetRevenue) Jakarta: 100."),
    ])
    joined, context = await run(provider, registry, "omzet per kota")
    assert '"finish_reason":"stop"' in joined
    execute.assert_awaited_once()
    assert context.ontology_release_id == release.id
    assert context.quality_facts["concept_grounding"]["concepts"] == ["Finance.NetRevenue"]
    assert context.quality_facts["semantic_selection"]["metrics"] == ["total_revenue"]


async def test_a_word_describing_the_asked_metric_does_not_block_the_answer(world, monkeypatch):
    # "completed order" names OrderCount, but here it only describes the revenue asked for.
    await bind(world, world.finance, "NetRevenue", "total_revenue", None, aliases=["omzet"])
    await bind(world, world.finance, "OrderCount", "order_count", None,
               aliases=["completed order"])
    await world.release()
    tool, planner, execute = setup_tool(monkeypatch, world, _plan(dimensions=["city"]))
    registry = ToolRegistry()
    registry.register(tool)
    question = "omzet dari completed order per kota"
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": question}),
        text_frame("Omzet (Finance.NetRevenue) Jakarta: 100."),
    ])
    joined, context = await run(provider, registry, question)
    assert '"finish_reason":"stop"' in joined
    execute.assert_awaited_once()
    grounding = context.quality_facts["concept_grounding"]
    assert grounding["status"] == "grounded"
    assert grounding["concepts"] == ["Finance.NetRevenue", "Finance.OrderCount"]
    assert context.quality_facts["semantic_selection"]["metrics"] == ["total_revenue"]


async def test_a_second_metric_the_question_asks_for_is_still_required(world, monkeypatch):
    await bind(world, world.finance, "NetRevenue", "total_revenue", None, aliases=["omzet"])
    await bind(world, world.finance, "OrderCount", "order_count", None, aliases=["jumlah order"])
    await world.release()
    tool, planner, execute = setup_tool(
        monkeypatch, world, _plan(dimensions=["city"]), _plan(dimensions=["city"]))
    registry = ToolRegistry()
    registry.register(tool)
    question = "omzet dan jumlah order per kota"
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": question}),
        text_frame("Order count could not be planned."),
    ])
    joined, _context = await run(provider, registry, question)
    # The plan left out a metric the question asked for: no data is returned.
    execute.assert_not_awaited()
    assert '"finish_reason":"required_capability_incomplete"' in joined


async def test_the_explain_tool_stays_behind_consent(world):
    await bind(world, world.finance, "NetRevenue", "total_revenue", None)
    await world.release()
    registry = ToolRegistry()
    registry.register(ontology_explain_tool)
    script = [
        tool_call_frame("e1", name="ontology_explain", arguments={"concept": "Finance.NetRevenue"}),
        text_frame("Net revenue is defined by Finance."),
    ]
    denied, _ = await run(ScriptedProvider(script=list(script)), registry, "define net revenue",
                          consent=deny)
    assert "Theofficialmeaningof" not in denied
    assert "officialdefinitionoffinance.netrevenue" not in denied.lower()
    allowed, context = await run(ScriptedProvider(script=list(script)), registry,
                                 "define net revenue")
    assert "OfficialdefinitionofFinance.NetRevenue" in allowed
    assert context.ontology_release_id is not None


async def test_a_users_correction_is_filed_for_review_behind_consent(world, monkeypatch):
    from app.modules.agents.tools.ontology import knowledge_propose_tool
    from app.modules.ontology import stewardship as stewardship_module
    from app.modules.ontology.contracts import Concept, Proposal

    monkeypatch.setattr(stewardship_module, "stewardship_service", world.stewardship)
    concept_id = await bind(world, world.finance, "NetRevenue", "total_revenue", None)
    await world.release()
    registry = ToolRegistry()
    registry.register(knowledge_propose_tool)
    script = [
        tool_call_frame("k1", name="knowledge_propose", arguments={
            "change": "add_aliases", "concept": "Finance.NetRevenue",
            "aliases": ["GMV bersih"], "rationale": "The user calls it GMV bersih",
        }),
        text_frame("I filed that for a Finance steward to review."),
    ]
    agent_proposals = lambda rows: [item for item in rows if item.source == "AGENT"]  # noqa: E731

    question = "GMV bersih = net revenue"
    denied, _ = await run(
        ScriptedProvider(script=list(script)), registry, question, consent=deny
    )
    assert '"finish_reason":"denied"' in denied
    assert agent_proposals(await world.repository.current("proposals", Proposal)) == []

    allowed, _ = await run(ScriptedProvider(script=list(script)), registry, question)
    assert '"finish_reason":"stop"' in allowed
    (proposal,) = agent_proposals(await world.repository.current("proposals", Proposal))
    assert proposal.status == "PENDING"
    concept = await world.repository.latest("concepts", concept_id, Concept)
    assert "GMV bersih" not in concept.aliases  # nothing changes before review and release
