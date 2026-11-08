"""Studio answers business terms with the organisation's approved definitions."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic.runtime import semantic_ir_to_definition
from app.modules.agents.tools.semantic_query import SemanticQueryTool
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.ontology import grounding as grounding_module
from app.modules.ontology.contracts import RepresentationChange
from app.modules.ontology.grounding import ground, missing_metrics, never_created, pinned_release
from app.modules.ontology.resolver import ConceptResolver, normalise
from tests.unit._ontology import ADMIN, ANALYST, FINANCE_STEWARD, OUTSIDER, SALES_STEWARD
from tests.unit.test_business_ontology import World
from tests.unit.test_semantic_guidance_fallback import USER
from tests.unit.test_semantic_intelligence import sales_model


def sales_definition() -> dict:
    definition = semantic_ir_to_definition(sales_model())
    revenue = definition["metrics"][0]
    orders = copy.deepcopy(revenue)
    orders.update(name="order_count", expression="COUNT(orders.order_id)", synonyms=[])
    definition["metrics"].append(orders)
    return definition


@pytest.fixture
async def world(monkeypatch):
    world = World(monkeypatch)
    await world.domains()
    world.catalog.publish("sales", sales_definition())
    world.resolver = ConceptResolver(world.reads)
    monkeypatch.setattr(grounding_module.concept_resolver, "resolve", world.resolver.resolve)
    return world


async def bind(world, domain, slug, metric="total_revenue", *, view="sales", reviewer=None,
               **extra):
    created = await world.approved(
        [
            world.concept(domain, slug, **extra),
            RepresentationChange(op="create", concept_id="@0", view_id=view, metric=metric),
        ],
        reviewers=(reviewer or FINANCE_STEWARD,),
    )
    return world.concept_id(created)


def models(world, view="sales"):
    published = world.catalog.views[view]

    async def load():
        return [{"semantic_model_id": view, "name": view, "definition": published.definition}]

    return load


def context(user=ANALYST, **extra):
    values = {
        "user": user, "release_manifest": None, "ontology_release_id": None,
        "requested_semantic_view": None, **extra,
    }
    return SimpleNamespace(**values)


# --- resolver ------------------------------------------------------------------------


async def test_names_match_as_whole_words_and_the_longest_name_wins(world):
    await world.approved(
        [
            world.concept(world.finance, "Revenue", aliases=["omzet"]),
            world.concept(world.finance, "NetRevenue", aliases=["omzet bersih"]),
        ]
    )
    await world.release()
    result = await world.resolver.resolve("Berapa omzet bersih per kota?", ANALYST)
    assert [item["qualified_name"] for item in result.concepts] == ["Finance.NetRevenue"]
    camel = await world.resolver.resolve("show net revenue by city", ANALYST)
    assert [item["qualified_name"] for item in camel.concepts] == ["Finance.NetRevenue"]
    qualified = await world.resolver.resolve("Finance.Revenue last month", ANALYST)
    assert [item["qualified_name"] for item in qualified.concepts] == ["Finance.Revenue"]
    assert (await world.resolver.resolve("revenues", ANALYST)).status == "none"
    assert normalise("  Omzet-Bersih!! ") == "omzet bersih"


async def test_a_shared_name_is_ambiguous_unless_a_domain_is_named(world):
    await world.let_sales_steward_read_finance()
    await world.approved([world.concept(world.finance, "Revenue")])
    await world.approved([world.concept(world.sales, "Revenue")], reviewers=(SALES_STEWARD,))
    await world.release()
    ambiguous = await world.resolver.resolve("revenue by region", SALES_STEWARD)
    assert ambiguous.status == "ambiguous"
    public = ambiguous.public()
    assert [item["qualified_name"] for item in public["ambiguities"][0]["candidates"]] == [
        "Finance.Revenue", "Sales.Revenue",
    ] or sorted(
        item["qualified_name"] for item in public["ambiguities"][0]["candidates"]
    ) == ["Finance.Revenue", "Sales.Revenue"]
    assert "Finance.Revenue or Sales.Revenue" in public["ambiguities"][0]["question"] or (
        "Sales.Revenue or Finance.Revenue" in public["ambiguities"][0]["question"]
    )
    hinted = await world.resolver.resolve("sales revenue by region", SALES_STEWARD)
    assert hinted.status == "resolved"
    assert [item["qualified_name"] for item in hinted.concepts] == ["Sales.Revenue"]


async def test_hidden_definitions_never_create_an_ambiguity(world):
    await world.approved([world.concept(world.finance, "Revenue")])
    await world.approved([world.concept(world.sales, "Revenue")], reviewers=(SALES_STEWARD,))
    await world.release()
    result = await world.resolver.resolve("revenue by region", OUTSIDER)
    assert result.status == "resolved"
    assert [item["qualified_name"] for item in result.concepts] == ["Sales.Revenue"]
    assert "Finance" not in json.dumps(result.public())


async def test_without_a_release_nothing_resolves(world):
    await world.approved([world.concept(world.finance, "Revenue")])
    assert (await world.resolver.resolve("revenue", ANALYST)).status == "no_release"


async def test_an_earlier_release_can_be_named(world):
    await world.approved([world.concept(world.finance, "Revenue")])
    first = await world.release()
    await world.approved([world.concept(world.finance, "Margin")])
    await world.release()
    pinned = await world.resolver.resolve("margin and revenue", ANALYST, release_id=first.id)
    assert [item["qualified_name"] for item in pinned.concepts] == ["Finance.Revenue"]


async def test_only_usable_bindings_are_offered(world):
    concept_id = await bind(world, world.finance, "Revenue")
    await world.release()
    result = await world.resolver.resolve("revenue", ANALYST)
    assert [item.metric for item in result.bindings[concept_id]] == ["total_revenue"]
    world.catalog.hidden["sales"] = {ANALYST["username"]}
    assert (await world.resolver.resolve("revenue", ANALYST)).bindings[concept_id] == []
    world.catalog.hidden = {}
    changed = sales_definition()
    changed["metrics"][0]["expression"] = "SUM(orders.gross)"
    world.catalog.publish("sales", changed)
    assert (await world.resolver.resolve("revenue", ANALYST)).bindings[concept_id] == []


# --- grounding -------------------------------------------------------------------------


async def test_grounding_requires_the_bound_metric_in_its_view(world):
    await bind(world, world.finance, "NetRevenue", aliases=["omzet"], unit=None)
    await world.release()
    ctx = context()
    result = await ground("omzet per kota", ctx, models(world))
    assert result.status == "grounded" and result.view_id == "sales"
    assert list(result.required) == ["total_revenue"]
    assert ctx.ontology_release_id == result.resolution.release_id
    assert result.planner_hints()[0]["concept"] == "Finance.NetRevenue"
    assert result.provenance()["concepts"][0]["metric"] == "total_revenue"
    assert missing_metrics(result, ("order_count",)) == [
        "Finance.NetRevenue must use metric total_revenue"
    ]
    assert missing_metrics(result, ("total_revenue",)) == []
    assert result.required["total_revenue"]["matched_terms"] == ["omzet"]


async def test_an_agent_on_a_different_definition_cannot_compute_the_concept(world):
    await bind(world, world.finance, "NetRevenue")
    await world.release()
    older = sales_definition()
    older["metrics"][0]["expression"] = "SUM(orders.gross)"

    async def pinned_old_version():
        return [{"semantic_model_id": "sales", "name": "sales", "definition": older}]

    result = await ground("net revenue", context(), pinned_old_version)
    assert result.error_class == "NO_AUTHORIZED_REPRESENTATION"
    assert "Finance.NetRevenue" in result.detail
    assert "do not substitute" in result.detail


async def test_an_unbound_concept_is_not_answered_with_a_similar_metric(world):
    await world.approved([world.concept(world.finance, "Churn")])
    await world.release()
    result = await ground("churn by city", context(), models(world))
    assert result.error_class == "NO_AUTHORIZED_REPRESENTATION"


async def test_concepts_in_different_views_are_asked_one_at_a_time(world):
    await bind(world, world.finance, "NetRevenue")
    await bind(world, world.finance, "Revenue", metric="revenue", view="sales_sv", unit="IDR")
    await world.release()

    async def both():
        return [
            {"semantic_model_id": view, "name": view,
             "definition": world.catalog.views[view].definition}
            for view in ("sales", "sales_sv")
        ]

    result = await ground("net revenue and revenue", context(), both)
    assert result.status == "split"
    assert result.error_class == "CLARIFICATION_REQUIRED"


async def test_grounding_passes_through_without_a_release_or_a_metric_term(world):
    assert (await ground("revenue", context(), models(world))).status == "no_release"
    await world.approved([world.concept(world.finance, "Customer", kind="BUSINESS_ENTITY")])
    await world.release()
    result = await ground("customer list", context(), models(world))
    assert result.status == "none" and not result.blocking
    assert result.provenance() is None


async def test_an_unreadable_ontology_fails_closed_but_missing_tables_mean_none(world, monkeypatch):
    async def broken(*_args, **_kwargs):
        raise RuntimeError("metadata store down")

    monkeypatch.setattr(grounding_module.concept_resolver, "resolve", broken)
    failed = await ground("revenue", context(), models(world))
    assert failed.error_class == "ONTOLOGY_UNAVAILABLE"

    class Missing(Exception):
        pass

    async def missing(*_args, **_kwargs):
        raise Missing(5502, "Getting analyzing error. Detail message: Unknown table "
                            "'NOVA_SYSTEM.CONFIG_ONTOLOGY_ALIASES'.")

    monkeypatch.setattr(grounding_module.concept_resolver, "resolve", missing)
    assert (await ground("revenue", context(), models(world))).status == "no_release"
    assert never_created(Missing(5502, "Unknown table 'NOVA_SYSTEM.CONFIG_AGENTS'")) is False


def test_the_release_pin_comes_from_the_agent_manifest_first():
    manifest = {"dependencies": {"ontology_release": {"release_id": "ont_rel_0002"}}}
    assert pinned_release(context(release_manifest=manifest)) == "ont_rel_0002"
    assert pinned_release(
        SimpleNamespace(release_manifest=None, ontology_release_id="ont_rel_0001")
    ) == "ont_rel_0001"
    assert pinned_release(SimpleNamespace()) is None


# --- semantic_query ------------------------------------------------------------------------


def _plan(**overrides):
    plan = {
        "metrics": ["total_revenue"], "dimensions": [], "filters": [], "named_filters": [],
        "time": None, "order_by": [], "limit": None, "unresolved_concepts": [],
    }
    plan.update(overrides)
    return plan


def setup_tool(monkeypatch, world, *responses):
    from app.modules.agents.repository import agent_repository
    from app.modules.agents.semantic import access as semantic_access
    from app.modules.query.service import query_service

    provider = SimpleNamespace(
        resolve=AsyncMock(return_value=SimpleNamespace(
            capabilities=SimpleNamespace(supports_json_schema=True),
        )),
        complete=AsyncMock(side_effect=[{"content": json.dumps(item)} for item in responses]),
    )
    tool = SemanticQueryTool(provider=provider)
    model = {"semantic_model_id": "sales", "name": "sales", "definition": sales_definition()}
    monkeypatch.setattr(semantic_access, "load_authorized_models", AsyncMock(return_value=[model]))
    monkeypatch.setattr(agent_repository, "list_verified_queries", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_repository, "record_semantic_usage", AsyncMock())
    import app.modules.agents.semantic.literals as literals

    monkeypatch.setattr(literals, "search_literal_candidates", AsyncMock(return_value={}))
    execute = AsyncMock(return_value=[SimpleNamespace(
        columns=["city", "total_revenue"], rows=[["Jakarta", 100]], row_count=1, error=None,
    )])
    monkeypatch.setattr(query_service, "execute_statements", execute)
    return tool, provider, execute


async def _run(tool, question, *, primary_plan=None):
    ctx = LoopContext(user_name="alice", user=USER.copy(),
                      execution_now=datetime(2026, 10, 22, 12, tzinfo=UTC))
    ctx.quality_facts = {}
    ctx.primary_plan = primary_plan
    outcome = await tool.run(
        ToolInvocation(tool_call_id="s1", tool_name="semantic_query",
                       arguments={"question": question}),
        ctx,
    )
    return outcome, ctx


@pytest.fixture
async def alice_world(world, monkeypatch):
    """``USER`` (alice, ANALYST role) reads Finance; bindings use her views."""
    finance = next(
        item for item in await world.reads.list_domains(ADMIN) if item["id"] == world.finance
    )
    from app.modules.ontology.service import DomainUpdate

    await world.reads.update_domain(
        world.finance,
        DomainUpdate(
            steward_roles=["finance_steward"],
            visibility_roles=["finance_analyst", USER["active_role"]],
            expected_revision=finance["revision"],
        ),
        ADMIN,
    )
    return world


async def test_a_grounded_question_plans_and_answers_with_the_official_metric(
    alice_world, monkeypatch,
):
    world = alice_world
    await bind(world, world.finance, "NetRevenue", aliases=["omzet"])
    await world.release()
    tool, provider, execute = setup_tool(monkeypatch, world, _plan(dimensions=["city"]))
    outcome, ctx = await _run(tool, "Omzet per kota")
    assert outcome.ok, outcome.safe_detail
    payload = json.loads(provider.complete.call_args.kwargs["messages"][1]["content"])
    assert payload["business_concepts"][0]["concept"] == "Finance.NetRevenue"
    assert payload["business_concepts"][0]["metric"] == "total_revenue"
    business = outcome.evidence["business_context"]
    assert business["ontology_release_id"] == ctx.ontology_release_id
    assert business["concepts"][0]["qualified_name"] == "Finance.NetRevenue"
    assert outcome.data["business_concepts"][0]["definition"]
    assert ctx.quality_facts["concept_grounding"] == {
        "status": "grounded", "release_id": ctx.ontology_release_id,
        "concepts": ["Finance.NetRevenue"], "ambiguous_terms": [], "metrics": ["total_revenue"],
    }
    execute.assert_awaited_once()


async def test_a_plan_that_skips_the_official_metric_is_refused(alice_world, monkeypatch):
    world = alice_world
    await bind(world, world.finance, "NetRevenue", aliases=["omzet"])
    await world.release()
    wrong = _plan(metrics=["order_count"])
    tool, provider, execute = setup_tool(monkeypatch, world, wrong)
    outcome, _ctx = await _run(tool, "Omzet per kota")
    assert outcome.error_class == "INVALID_SEMANTIC_PLAN"
    assert "must use metric total_revenue" in outcome.safe_detail
    execute.assert_not_awaited()


async def test_the_turn_planner_plan_is_replanned_when_it_misses_the_concept(
    alice_world, monkeypatch,
):
    world = alice_world
    await bind(world, world.finance, "NetRevenue", aliases=["omzet"])
    await world.release()
    tool, provider, execute = setup_tool(monkeypatch, world, _plan())
    outcome, _ctx = await _run(
        tool, "Omzet", primary_plan={"view": "sales", "plan": _plan(metrics=["order_count"])},
    )
    assert outcome.ok, outcome.safe_detail
    provider.complete.assert_awaited_once()
    assert outcome.metadata["plan_source"] == "model_planner"


async def test_an_ambiguous_term_is_a_question_back_before_any_planning(
    alice_world, monkeypatch,
):
    world = alice_world
    await world.let_sales_steward_read_finance()
    await bind(world, world.finance, "Revenue")
    await bind(world, world.sales, "Revenue", metric="order_count", reviewer=SALES_STEWARD)
    await world.release()
    tool, provider, execute = setup_tool(monkeypatch, world)
    outcome, ctx = await _run(tool, "revenue per city")
    assert outcome.error_class == "CLARIFICATION_REQUIRED" and outcome.recoverable
    assert "Finance.Revenue" in outcome.safe_detail and "Sales.Revenue" in outcome.safe_detail
    provider.complete.assert_not_awaited()
    execute.assert_not_awaited()
    assert ctx.quality_facts["concept_grounding"]["status"] == "ambiguous"
    assert ctx.quality_facts["concept_grounding"]["ambiguous_terms"] == ["revenue"]


async def test_an_unbound_concept_stops_before_planning(alice_world, monkeypatch):
    world = alice_world
    await world.approved([world.concept(world.finance, "Churn")])
    await world.release()
    tool, provider, execute = setup_tool(monkeypatch, world)
    outcome, _ctx = await _run(tool, "churn per city")
    assert outcome.error_class == "NO_AUTHORIZED_REPRESENTATION"
    assert outcome.recoverable is False
    provider.complete.assert_not_awaited()


async def test_without_a_release_semantic_query_is_unchanged(alice_world, monkeypatch):
    tool, provider, execute = setup_tool(monkeypatch, alice_world, _plan())
    outcome, ctx = await _run(tool, "revenue")
    assert outcome.ok
    payload = json.loads(provider.complete.call_args.kwargs["messages"][1]["content"])
    assert "business_concepts" not in payload
    assert outcome.evidence["business_context"] is None
    assert ctx.quality_facts["concept_grounding"]["status"] == "no_release"


# --- mentions a planned metric explains -------------------------------------------------


def _retail_ir():
    import json

    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.agents.semantic.ossie import parse_ossie
    from tests.benchmark.retail_field.views import VIEWS

    return SemanticModelIR.from_ossie(parse_ossie(json.dumps(VIEWS["retail_sales"])).model)


def _required(**concepts):
    from app.modules.ontology.grounding import Grounding

    return Grounding(status="grounded", view_id="retail_sales", required={
        metric: {"qualified_name": f"Penjualan.{name}", "label": name, "definition": "",
                 "aliases": [], "matched_terms": terms}
        for metric, (name, terms) in concepts.items()
    })


def test_a_word_that_describes_the_planned_metric_is_not_a_second_request():
    from app.modules.ontology.grounding import grounding_gaps

    ir = _retail_ir()
    # "penjualan kotor sebelum diskon": discount describes gross revenue.
    grounding = _required(discount_amount=("DiscountAmount", ["diskon"]))
    missing, explained = grounding_gaps(grounding, ("gross_revenue",), ir)
    assert missing == []
    assert explained == [
        "Penjualan.DiscountAmount: 'diskon' describes planned metric gross_revenue"
    ]
    # "margin kotor, yaitu omzet bersih ...": the margin is defined from net revenue.
    grounding = _required(net_revenue=("NetRevenue", ["omzet bersih"]))
    assert grounding_gaps(grounding, ("gross_margin",), ir)[0] == []


def test_an_input_of_the_planned_metric_is_explained_by_its_formula():
    from app.modules.ontology.grounding import grounding_gaps

    grounding = _required(sales_before_returns=("SalesBeforeReturns", ["nilai order"]))
    missing, explained = grounding_gaps(grounding, ("net_revenue",), _retail_ir())
    assert missing == []
    assert explained == ["Penjualan.SalesBeforeReturns: an input of planned metric net_revenue"]


def test_a_mention_after_per_that_the_planned_metric_counts_by_is_its_unit():
    from dataclasses import replace as copy

    from app.modules.ontology.grounding import grounding_gaps

    ir = _retail_ir()
    question = "Berapa rata-rata persentase diskon per baris pesanan setiap bulan?"
    # Average discount is defined "per baris order": the words are its unit.
    grounding = copy(_required(order_lines=("OrderLines", ["baris pesanan"])),
                     question=question)
    missing, explained = grounding_gaps(grounding, ("avg_discount_pct",), ir)
    assert missing == []
    assert explained == [
        "Penjualan.OrderLines: 'per baris pesanan' is the unit of planned metric "
        "avg_discount_pct"
    ]
    # Without 'per' in the question the mention asks for the metric itself.
    asked = copy(grounding, question="Berapa baris pesanan dan rata-rata diskon?")
    assert grounding_gaps(asked, ("avg_discount_pct",), ir)[0] == [
        "Penjualan.OrderLines must use metric order_lines"
    ]
    # A planned metric not defined per that unit does not explain it.
    assert grounding_gaps(grounding, ("net_revenue",), ir)[0] == [
        "Penjualan.OrderLines must use metric order_lines"
    ]


def test_substitution_is_still_refused():
    from app.modules.ontology.grounding import grounding_gaps

    ir = _retail_ir()
    # "omzet" asked, gross revenue planned: its own words never say omzet.
    grounding = _required(net_revenue=("NetRevenue", ["omzet"]))
    assert grounding_gaps(grounding, ("gross_revenue",), ir)[0] == [
        "Penjualan.NetRevenue must use metric net_revenue"
    ]
    # Two measures asked, one planned: the other is still required.
    grounding = _required(order_count=("OrderCount", ["jumlah order"]))
    assert grounding_gaps(grounding, ("net_revenue",), ir)[0] == [
        "Penjualan.OrderCount must use metric order_count"
    ]
    # Without the view's model the strict rule applies.
    grounding = _required(discount_amount=("DiscountAmount", ["diskon"]))
    assert missing_metrics(grounding, ("gross_revenue",)) == [
        "Penjualan.DiscountAmount must use metric discount_amount"
    ]
