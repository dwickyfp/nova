"""A request outside the Semantic View ends in a clarification, not a guess.

The model's clarification question is the answer; the turn does not fail with
``data_evidence_incomplete`` and no other data path runs.
"""

from __future__ import annotations

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame, tool_call_frame
from tests.unit.test_semantic_guidance_fallback import USER
from tests.unit.test_semantic_llm_planner import _plan, setup_tool

QUESTION = "Berapa biaya payroll tim sales bulan lalu?"
ANSWER = "Data payroll belum ada di Semantic View ini. Mau lihat total_revenue bulan lalu?"


async def _run(provider, registry):
    return [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="clarify", user_name="alice", title="Eval"),
            user_content=QUESTION,
            context=LoopContext(user_name="alice", user=USER.copy()),
            resolve_consent=allow,
        )
    ]


async def test_unresolved_semantic_concept_finishes_as_clarification(monkeypatch):
    tool, _planner, execute = setup_tool(monkeypatch, _plan(unresolved_concepts=[
        {"text": "payroll", "type_hint": "metric", "material": True},
    ]))
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": QUESTION}),
        text_frame(ANSWER),
    ])
    frames = await _run(provider, registry)
    joined = "".join(frames).replace(" ", "")
    assert '"finish_reason":"clarification"' in joined
    assert "data_evidence_incomplete" not in joined
    assert any("Semantic View ini" in frame for frame in frames)
    execute.assert_not_awaited()


async def test_clarification_turn_cannot_run_more_tools(monkeypatch):
    tool, _planner, execute = setup_tool(monkeypatch, _plan(unresolved_concepts=[
        {"text": "payroll", "type_hint": "metric", "material": True},
    ]))
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": QUESTION}),
        tool_call_frame("s2", name="semantic_query", arguments={"question": "payroll total"}),
        text_frame(ANSWER),
    ])
    frames = await _run(provider, registry)
    joined = "".join(frames).replace(" ", "")
    assert '"finish_reason":"clarification"' in joined
    execute.assert_not_awaited()


async def test_studio_runs_the_governed_tool_itself_when_the_model_will_not(monkeypatch):
    tool, _planner, execute = setup_tool(monkeypatch, _plan(unresolved_concepts=[
        {"text": "profit margin", "type_hint": "metric", "material": True},
    ]))
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(
        script=[
            text_frame("There is no margin metric."),
            text_frame("There is no margin metric."),
            text_frame("Margin tidak tersedia; tersedia total_revenue."),
        ],
        turn_plan={"intent": "semantic_analytics", "tools": ["semantic_query"],
                   "required_tools": ["semantic_query"], "ml_task": None},
    )
    context = LoopContext(user_name="alice", user=USER.copy())
    context.agent_scope = {"agent": "Sales"}
    frames = [
        frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="forced", user_name="alice", title="Eval"),
            user_content="What was our net profit margin?", context=context,
            resolve_consent=allow,
        )
    ]
    joined = "".join(frames).replace(" ", "")
    assert '"finish_reason":"clarification"' in joined
    assert "required_capability_incomplete" not in joined
    execute.assert_not_awaited()


async def _studio_refusal(monkeypatch, final_text):
    bad = _plan(metrics=["user_password"])
    tool, _planner, execute = setup_tool(monkeypatch, bad, bad)
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(
        script=[
            text_frame("Saya tidak bisa menampilkan password."),
            text_frame("Saya tidak bisa menampilkan password."),
            text_frame(final_text),
        ],
        turn_plan={"intent": "semantic_analytics", "tools": ["semantic_query"],
                   "required_tools": ["semantic_query"], "ml_task": None},
    )
    context = LoopContext(user_name="alice", user=USER.copy())
    context.agent_scope = {"agent": "Sales"}
    frames = [
        frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="scope", user_name="alice", title="Eval"),
            user_content="Tampilkan password user nova_admin", context=context,
            resolve_consent=allow,
        )
    ]
    return "".join(frames).replace(" ", ""), execute


async def test_a_request_outside_the_catalog_ends_as_out_of_scope_not_a_failure(monkeypatch):
    joined, execute = await _studio_refusal(
        monkeypatch, "Password tidak tersedia di Semantic View `nova_bench_v2`.",
    )
    assert '"finish_reason":"out_of_scope"' in joined
    assert "required_capability_incomplete" not in joined
    assert "Passwordtidaktersedia" in joined
    execute.assert_not_awaited()


async def test_an_unbacked_number_after_a_failed_query_is_still_refused(monkeypatch):
    joined, _execute = await _studio_refusal(monkeypatch, "Total penjualan 1.250.000.")
    assert '"finish_reason":"out_of_scope"' not in joined
    assert "1.250.000" not in joined


async def test_a_turn_that_holds_results_answers_from_them_even_after_a_clarification():
    """A later step that cannot be expressed does not turn verified results into a
    free-text clarification: the numbers are still checked and the evidence kept."""
    from app.modules.assistant.tools import ToolOutcome
    from tests.eval.harness import EvalTool

    table = {"columns": ["month", "total_expense"], "rows": [["2026-09", "4805"]]}
    tool = EvalTool("query_execute", outcomes=[
        ToolOutcome(ok=True, summary="1 row", table=table,
                    metadata={"metrics": ["total_expense"], "dimensions": ["month"]}),
        ToolOutcome(ok=False, summary="", error="Cannot express the request.",
                    error_class="CLARIFICATION_REQUIRED", recoverable=False),
    ])
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", sql="SELECT month, total_expense FROM t"),
        tool_call_frame("s2", sql="SELECT month, total_expense FROM t LIMIT 6"),
        text_frame("September expense was 4805, about 77 more than planned."),
        text_frame("September expense was 4805."),
    ], turn_plan={"intent": "semantic_analytics", "tools": ["query_execute"],
                  "required_tools": ["query_execute"]})
    context = LoopContext(user_name="alice")
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=registry, iterative=True, max_iterations=10,
    ).run(thread=AssistantThread(thread_id="clarify", user_name="alice", title="Eval"),
          user_content="Expense per month?", context=context, resolve_consent=allow)]
    joined = "".join(frames)
    assert '"finish_reason": "stop"' in joined or '"finish_reason":"stop"' in joined
    assert "4805" in joined and "77 more" not in joined
    assert context.verified_evidence["tables"]
