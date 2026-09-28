"""semantic_query plans with the model unless the lexical fast path is certain.

Audit 2026-09-27: one unrecognised word made the lexical planner raise before
the constrained model planner could run, and follow-ups were recognised only
when the question started with "now" or "sekarang".
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic.runtime import semantic_ir_to_definition
from app.modules.agents.tools.semantic_query import SemanticQueryTool
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
    context = LoopContext(user_name="alice", user=USER.copy())
    context.active_state = active_state
    return await tool.run(
        ToolInvocation(tool_call_id="s1", tool_name="semantic_query",
                       arguments={"question": question}),
        context,
    )


async def test_confident_lexical_plan_skips_the_model(monkeypatch):
    tool, provider, execute = setup_tool(monkeypatch)
    outcome = await _run(tool, "Revenue by city last month")
    assert outcome.ok, outcome.safe_detail
    provider.complete.assert_not_awaited()
    assert "`orders`.`city`" in execute.call_args.kwargs["sql"]


async def test_unrecognised_word_goes_to_the_model_instead_of_failing(monkeypatch):
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
    assert "DATE_SUB(DATE_TRUNC('month', CURRENT_DATE()), INTERVAL 1 MONTH)" in sql


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


@pytest.mark.parametrize("question", ["Revenue in Q2 2025", "Revenue last 7 days"])
async def test_audit_time_questions_run_without_the_model(monkeypatch, question):
    tool, provider, execute = setup_tool(monkeypatch)
    outcome = await _run(tool, question)
    assert outcome.ok, outcome.safe_detail
    provider.complete.assert_not_awaited()
    execute.assert_awaited_once()


async def test_the_users_explicit_period_wins_over_the_models_rewrite(monkeypatch):
    tool, _provider, execute = setup_tool(monkeypatch, _plan(time={
        "dimension": "order_date", "grain": "month", "range": "last_3_months", "compare": None,
    }))
    context = LoopContext(user_name="alice", user=USER.copy())
    context.user_question = "Revenue this month vs last month"
    outcome = await tool.run(
        ToolInvocation("s1", "semantic_query",
                       {"question": "revenue by month for the last 3 months"}),
        context,
    )
    assert outcome.ok, outcome.safe_detail
    sql = execute.call_args.kwargs["sql"]
    assert "comparison_period" in sql
    assert "DATE_TRUNC('month', CURRENT_DATE())" in sql
    assert outcome.metadata["period_from_user"].startswith("Used the period the user asked for")


async def test_later_analysis_queries_keep_their_own_period(monkeypatch):
    plan = _plan(time={
        "dimension": "order_date", "grain": None, "range": "previous_month", "compare": None,
    })
    tool, _provider, execute = setup_tool(monkeypatch, plan, plan)
    context = LoopContext(user_name="alice", user=USER.copy())
    context.user_question = "Kenapa penjualan bulan ini turun?"
    context.primary_query_done = True
    outcome = await tool.run(
        ToolInvocation("s2", "semantic_query", {"question": "penjualan per kota periode lalu x"}),
        context,
    )
    assert outcome.ok, outcome.safe_detail
    assert outcome.metadata["period_from_user"] is None
    assert "INTERVAL 1 MONTH)" in execute.call_args.kwargs["sql"]


async def test_the_users_top_n_survives_a_rewrite_that_drops_it(monkeypatch):
    tool, _provider, execute = setup_tool(monkeypatch, _plan(dimensions=["city"]))
    context = LoopContext(user_name="alice", user=USER.copy())
    context.user_question = "Top 2 kota berdasarkan penjualan"
    outcome = await tool.run(
        ToolInvocation("s1", "semantic_query", {"question": "total revenue by city overall x"}),
        context,
    )
    assert outcome.ok, outcome.safe_detail
    sql = execute.call_args.kwargs["sql"]
    assert "LIMIT 2" in sql and "DESC" in sql
    assert "top 2" in outcome.metadata["period_from_user"]


@pytest.mark.parametrize(("question", "expected"), [
    ("Top 2 kanal berdasarkan penjualan", (2, "desc")),
    ("5 kota teratas bulan ini", (5, "desc")),
    ("3 produk terlaris", (3, "desc")),
    ("bottom 3 cities by revenue", (3, "asc")),
    ("3 kanal terendah", (3, "asc")),
    ("penjualan tahun 2025 terbesar", None),
    ("pesanan di atas 10 juta terbesar", None),
])
def test_rank_phrases_in_english_and_indonesian(question, expected):
    from app.modules.agents.semantic.planning import requested_rank

    assert requested_rank(question) == expected


async def test_the_users_threshold_survives_a_rewrite_that_drops_it(monkeypatch):
    tool, _provider, execute = setup_tool(monkeypatch, _plan(dimensions=["city"]))
    context = LoopContext(user_name="alice", user=USER.copy())
    context.user_question = "Kota dengan penjualan di atas 1 miliar"
    outcome = await tool.run(
        ToolInvocation("s1", "semantic_query", {"question": "total revenue by city overall x"}),
        context,
    )
    assert outcome.ok, outcome.safe_detail
    assert "`total_revenue` > 1000000000" in execute.call_args.kwargs["sql"]


@pytest.mark.parametrize(("question", "expected"), [
    ("Kota dengan penjualan di atas 1 miliar tahun ini", (">", 1_000_000_000)),
    ("cities with revenue over 500k", (">", 500_000)),
    ("kategori dengan omzet kurang dari 1,5 miliar", ("<", 1_500_000_000)),
    ("channels above Rp 100.000.000", (">", 100_000_000)),
    ("penjualan minimal 50 juta per kota", (">=", 50_000_000)),
    ("orders over 3 months", None),
    ("growth above 10%", None),
    ("revenue by city", None),
])
def test_threshold_phrases(question, expected):
    from app.modules.agents.semantic.planning import requested_threshold

    assert requested_threshold(question) == expected


async def test_a_period_total_is_not_split_by_month_unless_asked(monkeypatch):
    by_month = _plan(time={
        "dimension": "order_date", "grain": "month", "range": "last_3_months", "compare": None,
    })
    tool, _provider, execute = setup_tool(monkeypatch, by_month, by_month)
    context = LoopContext(user_name="alice", user=USER.copy())
    context.user_question = "Berapa penjualan 3 bulan terakhir?"
    outcome = await tool.run(
        ToolInvocation("s1", "semantic_query", {"question": "penjualan per bulan 3 bulan x"}),
        context,
    )
    assert outcome.ok, outcome.safe_detail
    assert "DATE_TRUNC" not in execute.call_args.kwargs["sql"].split("WHERE")[0]

    tool, _provider, execute = setup_tool(monkeypatch, by_month)
    context = LoopContext(user_name="alice", user=USER.copy())
    context.user_question = "Tren penjualan 3 bulan terakhir"
    outcome = await tool.run(
        ToolInvocation("s1", "semantic_query", {"question": "penjualan per bulan 3 bulan x"}),
        context,
    )
    assert outcome.ok, outcome.safe_detail
    assert "DATE_TRUNC('month'" in execute.call_args.kwargs["sql"]


async def test_a_failed_first_query_leaves_the_users_wording_for_the_retry(monkeypatch):
    by_day = _plan(time={
        "dimension": "order_date", "grain": "day", "range": "current_month",
        "compare": "month_over_month",
    })
    tool, _provider, execute = setup_tool(monkeypatch, by_day, by_day)
    execute.side_effect = [RuntimeError("engine busy"), execute.return_value]
    context = LoopContext(user_name="alice", user=USER.copy())
    context.user_question = "Revenue this month vs last month"
    first = await tool.run(
        ToolInvocation("s1", "semantic_query", {"question": "daily revenue this month x"}), context
    )
    assert not first.ok and not context.primary_query_done
    retry = await tool.run(
        ToolInvocation("s2", "semantic_query", {"question": "daily revenue this month x"}), context
    )
    assert retry.ok, retry.safe_detail
    assert "DATE_TRUNC('day'" not in execute.call_args.kwargs["sql"]
    assert context.primary_query_done


async def test_the_users_own_words_still_get_their_limit(monkeypatch):
    tool, _provider, execute = setup_tool(monkeypatch, _plan(dimensions=["city"]))
    context = LoopContext(user_name="alice", user=USER.copy())
    context.user_question = "Top 2 kota berdasarkan penjualan x"
    outcome = await tool.run(
        ToolInvocation("s1", "semantic_query", {"question": "Top 2 kota berdasarkan penjualan x"}),
        context,
    )
    assert outcome.ok, outcome.safe_detail
    assert "LIMIT 2" in execute.call_args.kwargs["sql"]
