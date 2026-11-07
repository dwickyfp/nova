"""semantic_query plans every question with the model, in any language.

The model returns a SemanticPlan from the catalog; Nova validates and compiles
it. The user's own constraints come from the turn's IntentFrame, never from
parsing the question text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.agents.semantic.runtime import semantic_ir_to_definition
from app.modules.agents.tools.semantic_query import SemanticQueryTool
from app.modules.assistant.intent import IntentFrame, Threshold
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from tests.unit.test_semantic_guidance_fallback import USER
from tests.unit.test_semantic_intelligence import sales_model


def _plan(**overrides):
    plan = {
        "metrics": ["total_revenue"], "dimensions": [], "filters": [], "named_filters": [],
        "time": None, "order_by": [], "limit": None, "unresolved_concepts": [],
    }
    plan.update(overrides)
    return plan


def setup_tool(monkeypatch, *responses):
    from app.modules.agents.repository import agent_repository
    from app.modules.query.service import query_service

    provider = SimpleNamespace(
        resolve=AsyncMock(return_value=SimpleNamespace(
            capabilities=SimpleNamespace(supports_json_schema=True),
        )),
        complete=AsyncMock(side_effect=[{"content": json.dumps(item)} for item in responses]),
    )
    tool = SemanticQueryTool(provider=provider)
    monkeypatch.setattr(tool, "_resolve_model", AsyncMock(return_value={
        "semantic_model_id": "sales", "definition": semantic_ir_to_definition(sales_model()),
    }))
    monkeypatch.setattr(agent_repository, "list_verified_queries", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_repository, "record_semantic_usage", AsyncMock())
    import app.modules.agents.semantic.literals as literals

    monkeypatch.setattr(literals, "search_literal_candidates", AsyncMock(return_value={}))
    execute = AsyncMock(return_value=[SimpleNamespace(
        columns=["city", "total_revenue"], rows=[["Jakarta", 100]], row_count=1, error=None,
    )])
    monkeypatch.setattr(query_service, "execute_statements", execute)
    return tool, provider, execute


async def _run(tool, question, *, active_state=None):
    context = LoopContext(user_name="alice", user=USER.copy(),
                          execution_now=datetime(2026, 10, 22, 12, tzinfo=UTC))
    context.active_state = active_state
    return await tool.run(
        ToolInvocation(tool_call_id="s1", tool_name="semantic_query",
                       arguments={"question": question}),
        context,
    )


async def test_every_question_is_planned_by_the_model(monkeypatch):
    tool, provider, execute = setup_tool(
        monkeypatch, _plan(dimensions=["city"], time={
            "dimension": "order_date", "grain": None, "range": "previous_month", "compare": None,
        }),
    )
    outcome = await _run(tool, "Omzet per kota bulan lalu")
    assert outcome.ok, outcome.safe_detail
    provider.complete.assert_awaited_once()
    sql = execute.call_args.kwargs["sql"]
    assert "`orders`.`city`" in sql
    assert ">= '2026-09-01 00:00:00.000000'" in sql
    assert "< '2026-10-01 00:00:00.000000'" in sql
    payload = json.loads(provider.complete.call_args.kwargs["messages"][1]["content"])
    # The whole catalog, not a slice chosen by shared words.
    assert {item["name"] for item in payload["catalog"]["metrics"]} >= {"total_revenue"}


async def test_follow_up_without_keyword_receives_the_previous_plan(monkeypatch):
    prior = _plan(time={
        "dimension": "order_date", "grain": None, "range": "current_year", "compare": None,
    })
    tool, provider, _execute = setup_tool(monkeypatch, {**prior, "dimensions": ["city"]})
    outcome = await _run(tool, "breakdown per kota dong", active_state={"semantic_plan": prior})
    assert outcome.ok, outcome.safe_detail
    payload = json.loads(provider.complete.call_args.kwargs["messages"][1]["content"])
    assert payload["previous_plan"]["metrics"] == ["total_revenue"]
    assert payload["previous_plan"]["time"]["range"] == "current_year"


async def test_invalid_previous_plan_is_ignored(monkeypatch):
    tool, provider, _execute = setup_tool(monkeypatch, _plan(dimensions=["city"]))
    await _run(tool, "breakdown per kota dong",
               active_state={"semantic_plan": {"metrics": ["deleted_metric"]}})
    payload = json.loads(provider.complete.call_args.kwargs["messages"][1]["content"])
    assert payload["previous_plan"] is None


async def test_unresolved_concept_becomes_a_clarification_not_a_query(monkeypatch):
    tool, _provider, execute = setup_tool(monkeypatch, _plan(unresolved_concepts=[
        {"text": "payroll", "type_hint": "metric", "material": True},
    ]))
    outcome = await _run(tool, "Berapa biaya payroll tim sales?")
    assert not outcome.ok
    assert outcome.error_class == "CLARIFICATION_REQUIRED"
    assert outcome.recoverable
    assert "payroll" in outcome.safe_detail
    assert "total_revenue" in outcome.repair_context["available_metrics"]
    execute.assert_not_awaited()


async def test_a_plan_that_names_nothing_asks_instead_of_failing(monkeypatch):
    # Seen live: for "show sales by supplier name" the model left every field empty.
    tool, provider, execute = setup_tool(monkeypatch, _plan(metrics=[]))
    outcome = await _run(tool, "Tampilkan penjualan berdasarkan nama pemasok.")
    assert outcome.error_class == "CLARIFICATION_REQUIRED"
    assert "total_revenue" in outcome.repair_context["available_metrics"]
    provider.complete.assert_awaited_once()  # not a repair target
    execute.assert_not_awaited()


def test_the_planner_rules_ask_about_a_measure_word_several_metrics_fit():
    from app.modules.agents.semantic.model_planner import PLANNER_RULES

    assert "two or more catalog metrics fit about equally" in PLANNER_RULES
    assert "a supplied business definition names, is resolved" in PLANNER_RULES


async def test_invalid_model_plan_gets_one_internal_repair(monkeypatch):
    tool, provider, execute = setup_tool(
        monkeypatch, _plan(metrics=["gross_merchandise"]), _plan(dimensions=["city"]),
    )
    outcome = await _run(tool, "GMV per kota")
    assert outcome.ok, outcome.safe_detail
    assert provider.complete.await_count == 2
    repair = provider.complete.call_args.kwargs["messages"][-1]["content"]
    assert "gross_merchandise" in repair
    execute.assert_awaited_once()


async def test_second_invalid_model_plan_is_a_recoverable_plan_error(monkeypatch):
    bad = _plan(metrics=["gross_merchandise"])
    tool, provider, execute = setup_tool(monkeypatch, bad, bad)
    outcome = await _run(tool, "GMV per kota")
    assert not outcome.ok
    assert outcome.error_class == "INVALID_SEMANTIC_PLAN"
    assert outcome.recoverable
    execute.assert_not_awaited()


# ── the turn planner's primary plan ─────────────────────────────────────────

def _context(frame=None, *, question="x", primary=None):
    context = LoopContext(user_name="alice", user=USER.copy(),
                          execution_now=datetime(2026, 10, 22, 12, tzinfo=UTC))
    context.user_question = question
    context.intent_frame = frame
    context.primary_plan = primary
    return context


async def _ask(tool, context, question="the model's rewrite of the question"):
    return await tool.run(ToolInvocation("s1", "semantic_query", {"question": question}), context)


async def test_the_turn_planners_plan_runs_without_another_model_call(monkeypatch):
    tool, provider, execute = setup_tool(monkeypatch)
    context = _context(primary={"plan": _plan(dimensions=["city"]), "view": None})
    outcome = await _ask(tool, context)
    assert outcome.ok, outcome.safe_detail
    provider.complete.assert_not_awaited()
    assert "`orders`.`city`" in execute.call_args.kwargs["sql"]
    assert outcome.metadata["plan_source"] == "turn_planner"


async def test_an_invalid_turn_plan_is_planned_again(monkeypatch):
    tool, provider, execute = setup_tool(monkeypatch, _plan(dimensions=["city"]))
    context = _context(primary={"plan": _plan(metrics=["gross_merchandise"]), "view": None})
    outcome = await _ask(tool, context)
    assert outcome.ok, outcome.safe_detail
    provider.complete.assert_awaited_once()
    assert outcome.metadata["plan_source"] == "model_planner"


async def test_later_queries_plan_for_themselves(monkeypatch):
    tool, provider, _execute = setup_tool(monkeypatch, _plan(dimensions=["city"]))
    context = _context(primary={"plan": _plan(), "view": None})
    context.primary_query_done = True
    outcome = await _ask(tool, context)
    assert outcome.ok, outcome.safe_detail
    provider.complete.assert_awaited_once()


# ── the user's constraints, from the intent frame ────────────────────────────

async def test_the_users_period_wins_over_the_models_rewrite(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.NOVA_TIMEZONE", "UTC")
    tool, _provider, execute = setup_tool(monkeypatch, _plan(time={
        "dimension": "order_date", "grain": "month", "range": "last_3_months", "compare": None,
    }))
    frame = IntentFrame(language="ja", range="current_month", compare="month_over_month")
    outcome = await _ask(tool, _context(frame, question="今月の売上を先月と比較して"))
    assert outcome.ok, outcome.safe_detail
    sql = execute.call_args.kwargs["sql"]
    assert "comparison_period" in sql
    assert ">= '2026-10-01 00:00:00.000000'" in sql
    assert "< '2026-10-22 12:00:00.000000'" in sql
    assert ">= '2026-09-01 00:00:00.000000'" in sql
    assert "< '2026-09-22 12:00:00.000000'" in sql
    assert outcome.metadata["period_from_user"].startswith("Used the period the user asked for")


async def test_later_analysis_queries_keep_their_own_period(monkeypatch):
    plan = _plan(time={
        "dimension": "order_date", "grain": None, "range": "previous_month", "compare": None,
    })
    tool, _provider, execute = setup_tool(monkeypatch, plan)
    context = _context(IntentFrame(range="current_month"))
    context.primary_query_done = True
    outcome = await _ask(tool, context)
    assert outcome.ok, outcome.safe_detail
    assert outcome.metadata["period_from_user"] is None
    assert ">= '2026-09-01 00:00:00.000000'" in execute.call_args.kwargs["sql"]
    assert "< '2026-10-01 00:00:00.000000'" in execute.call_args.kwargs["sql"]


async def test_the_users_top_n_survives_a_rewrite_that_drops_it(monkeypatch):
    tool, _provider, execute = setup_tool(monkeypatch, _plan(dimensions=["city"]))
    outcome = await _ask(tool, _context(IntentFrame(top_n=2, order="desc")))
    assert outcome.ok, outcome.safe_detail
    sql = execute.call_args.kwargs["sql"]
    assert "LIMIT 2" in sql and "DESC" in sql
    assert "top 2" in outcome.metadata["period_from_user"]


async def test_the_users_threshold_survives_a_rewrite_that_drops_it(monkeypatch):
    tool, _provider, execute = setup_tool(monkeypatch, _plan(dimensions=["city"]))
    frame = IntentFrame(threshold=Threshold(">", 1_000_000_000.0, "total_revenue"))
    outcome = await _ask(tool, _context(frame))
    assert outcome.ok, outcome.safe_detail
    assert "`total_revenue` > 1000000000" in execute.call_args.kwargs["sql"]


async def test_a_period_total_is_not_split_by_month_unless_asked(monkeypatch):
    by_month = _plan(time={
        "dimension": "order_date", "grain": "month", "range": "last_3_months", "compare": None,
    })
    tool, _provider, execute = setup_tool(monkeypatch, by_month)
    outcome = await _ask(tool, _context(IntentFrame(range="last_3_months")))
    assert outcome.ok, outcome.safe_detail
    assert "DATE_TRUNC" not in execute.call_args.kwargs["sql"].split("WHERE")[0]

    tool, _provider, execute = setup_tool(monkeypatch, by_month)
    outcome = await _ask(tool, _context(IntentFrame(range="last_3_months", asks_series=True)))
    assert outcome.ok, outcome.safe_detail
    assert "DATE_TRUNC('month'" in execute.call_args.kwargs["sql"]


async def test_a_failed_first_query_leaves_the_users_wording_for_the_retry(monkeypatch):
    by_day = _plan(time={
        "dimension": "order_date", "grain": "day", "range": "current_month",
        "compare": "month_over_month",
    })
    tool, _provider, execute = setup_tool(monkeypatch, by_day, by_day)
    execute.side_effect = [RuntimeError("engine busy"), execute.return_value]
    context = _context(IntentFrame(range="current_month", compare="month_over_month"))
    first = await _ask(tool, context)
    assert not first.ok and not context.primary_query_done
    retry = await _ask(tool, context)
    assert retry.ok, retry.safe_detail
    assert "DATE_TRUNC('day'" not in execute.call_args.kwargs["sql"]
    assert context.primary_query_done


def test_top_n_in_each_group_is_a_per_group_ranking_not_a_flat_limit():
    from app.modules.agents.semantic.planning import SemanticPlan
    from app.modules.agents.tools.semantic_query import reconcile_with_frame
    from tests.benchmark.studio_accuracy.model import bench_model

    plan = SemanticPlan(metrics=("product_revenue",), dimensions=("category", "city"))
    frame = IntentFrame(top_n=2, order="desc", per_group_dimension="city")
    ranked, notes = reconcile_with_frame(plan, frame, bench_model())
    assert ranked.limit is None
    assert ranked.top_n_per_group.n == 2
    assert ranked.top_n_per_group.partition_by == ("city",)
    assert any("per city" in note for note in notes)
    unclear, _ = reconcile_with_frame(
        plan, IntentFrame(top_n=2, per_group_dimension="galaxy"), bench_model()
    )
    assert unclear == plan


def test_a_raw_date_column_is_dropped_when_the_user_asked_for_a_total():
    from app.modules.agents.semantic.planning import SemanticPlan, SemanticTime
    from app.modules.agents.tools.semantic_query import reconcile_with_frame
    from tests.benchmark.studio_accuracy.model import bench_model

    daily = SemanticPlan(
        metrics=("total_revenue",), dimensions=("order_date",),
        time=SemanticTime("order_date", range="previous_month", compare="month_over_month"),
    )
    frame = IntentFrame(range="previous_month", compare="month_over_month")
    fixed, _ = reconcile_with_frame(daily, frame, bench_model())
    assert fixed.dimensions == ()
    kept, _ = reconcile_with_frame(
        daily, IntentFrame(range="previous_month", compare="month_over_month", asks_series=True),
        bench_model(),
    )
    assert kept.dimensions == ("order_date",)


def test_a_superlative_ranks_every_group_without_a_limit():
    from app.modules.agents.semantic.planning import SemanticPlan
    from app.modules.agents.tools.semantic_query import reconcile_with_frame
    from tests.benchmark.studio_accuracy.model import bench_model

    plan = SemanticPlan(metrics=("total_revenue",), dimensions=("sales_channel",))
    ranked, notes = reconcile_with_frame(plan, IntentFrame(order="desc"), bench_model())
    assert ranked.limit is None
    assert [(item.field, item.direction) for item in ranked.order_by] == [("total_revenue", "desc")]
    assert notes == ["Ranked by the user's superlative"]


def test_a_limit_without_an_order_ranks_by_the_first_metric():
    from app.modules.agents.semantic.compiler import SemanticCompiler
    from app.modules.agents.semantic.planning import SemanticPlan
    from tests.benchmark.studio_accuracy.model import bench_model

    sql = SemanticCompiler().compile(
        bench_model(), SemanticPlan(metrics=("total_revenue",), dimensions=("city",), limit=3)
    ).sql
    assert "ORDER BY `total_revenue` DESC\nLIMIT 3" in sql


def test_a_malformed_frame_keeps_only_valid_fields():
    frame = IntentFrame.from_dict({
        "language": "ja", "range": "the month before", "compare": "month_over_month",
        "grain": "fortnight", "top_n": 0, "order": "most",
        "threshold": {"operator": "above", "value": 10, "metric": None},
    })
    assert frame.language == "ja"
    assert frame.range is None and frame.compare is None and frame.grain is None
    assert frame.top_n is None and frame.order is None and frame.threshold is None


def test_a_plan_that_would_multiply_its_metric_is_sent_back_for_repair():
    import pytest

    from app.modules.agents.semantic.model_planner import _check_grain, primary_plan_from
    from app.modules.agents.semantic.planning import SemanticPlan, SemanticPlanError

    model = sales_model(with_items=True)
    # Order revenue grouped by a product of its items counts each order once per item.
    fanout = _plan(dimensions=["product_name"])
    with pytest.raises(SemanticPlanError, match="metric's own dataset"):
        _check_grain(model, SemanticPlan.from_dict(fanout))
    # The turn planner's plan is then planned again rather than refused at compile.
    assert primary_plan_from(fanout, model) is None
    assert primary_plan_from(_plan(dimensions=["city"]), model) is not None


def test_the_catalog_tells_the_planner_where_a_metric_can_be_grouped():
    from app.modules.agents.semantic.model_planner import compact_catalog

    catalog = compact_catalog(sales_model(with_items=True))
    revenue = next(item for item in catalog["metrics"] if item["name"] == "total_revenue")
    # Orders reach their region many-to-one; their items are one-to-many and are not listed.
    assert revenue["groupable_by_datasets"] == ["orders", "regions"]


async def test_a_multi_step_turn_runs_the_resolved_data_and_names_the_rest(monkeypatch):
    with_steps = _plan(dimensions=["city"], unresolved_concepts=[
        {"text": "optimise the visit order and show the route map", "type_hint": "route",
         "material": True},
    ])
    tool, _provider, execute = setup_tool(monkeypatch, with_steps)
    context = LoopContext(user_name="alice", user=USER.copy(),
                          execution_now=datetime(2026, 10, 22, 12, tzinfo=UTC))
    context.route = {"intent": "machine_learning", "ml_task": "route_optimization"}
    outcome = await tool.run(
        ToolInvocation("s1", "semantic_query", {"question": "revenue by city, then a route"}),
        context,
    )
    assert outcome.ok, outcome.safe_detail
    execute.assert_awaited_once()
    assert "Not covered by it: optimise the visit order" in outcome.summary

    # A plain data question with an unresolved constraint still asks back.
    tool, _provider, execute = setup_tool(monkeypatch, with_steps)
    asked = await _run(tool, "revenue by city, then a route")
    assert not asked.ok and asked.error_class == "CLARIFICATION_REQUIRED"
    execute.assert_not_awaited()


async def test_a_question_naming_one_views_metric_queries_that_view(monkeypatch):
    from app.modules.agents.semantic import access
    from app.modules.agents.tools.semantic_query import SemanticQueryTool
    from tests.benchmark.retail_field.views import VIEWS

    models = [{"semantic_model_id": name, "name": name, "definition": json.dumps(view)}
              for name, view in VIEWS.items()]
    monkeypatch.setattr(access, "load_authorized_models", AsyncMock(return_value=models))
    tool = SemanticQueryTool(provider=SimpleNamespace())
    question = "Bandingkan planned_distance_km dan actual_distance_km per driver di Medan."
    # A made-up view name does not send the query to the first view.
    context = SimpleNamespace(requested_semantic_view="retail_operations", primary_plan={
        "view": "retail_sales",
    })
    chosen = await tool._resolve_model(context, question)
    assert chosen["semantic_model_id"] == "delivery_routes"
    # A real named view still wins.
    context.requested_semantic_view = "outlet_stock"
    assert (await tool._resolve_model(context, question))["semantic_model_id"] == "outlet_stock"


async def test_the_first_query_runs_early_only_from_a_usable_turn_plan(monkeypatch):
    from app.modules.agents.semantic import access

    models = [{"semantic_model_id": "sales",
               "definition": json.loads(json.dumps(semantic_ir_to_definition(sales_model())))}]
    monkeypatch.setattr(access, "load_authorized_models", AsyncMock(return_value=models))
    tool = SemanticQueryTool(provider=SimpleNamespace())
    context_defaults = {"requested_semantic_view": None}
    context = SimpleNamespace(primary_plan={"plan": _plan(dimensions=["city"]), "view": "sales"},
                              **context_defaults)
    assert await tool.primary_plan_usable(context)
    # A plan naming what the view does not have is not run early; the model asks.
    context.primary_plan = {"plan": _plan(metrics=["gross_merchandise"]), "view": "sales"}
    assert not await tool.primary_plan_usable(context)
    context.primary_plan = None
    assert not await tool.primary_plan_usable(context)
