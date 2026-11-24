"""``TurnOutcome`` on ``run.finished``: replays of recorded Studio bench turns.

Each scenario rebuilds one recorded turn (bench rebench/baseline, 2026-11-24)
through the real loop: the user's question, the tool results the turn got and
the model's final text. The outcome must come from those facts, never from the
answer's words, and ``finish_reason`` keeps its established value.
"""

from __future__ import annotations

import json
from typing import Any

from app.modules.assistant import human_input
from app.modules.assistant.events_v2 import StudioStreamEncoder, parse_frame
from app.modules.assistant.human_input import request_human_input_tool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame, tool_call_frame
from tests.eval.harness import EvalTool
from tests.unit.test_semantic_guidance_fallback import USER
from tests.unit.test_semantic_llm_planner import _plan, setup_tool

QUESTION_SCHEMA = {
    "type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"],
}
DATA_PLAN = {"intent": "semantic_analytics", "tools": ["semantic_query"],
             "required_tools": ["semantic_query"], "ml_task": None}


def _done(frames: list[str]) -> dict[str, Any]:
    done = [frame for frame in frames if frame.startswith("event: done\n")]
    assert len(done) == 1
    return json.loads(done[0].split("data: ", 1)[1])


async def _run(
    provider: ScriptedProvider, registry: ToolRegistry, question: str, *,
    studio: bool = True, ask_human: Any = None,
) -> tuple[list[str], LoopContext]:
    context = LoopContext(user_name="alice", user=USER.copy())
    if studio:
        context.agent_scope = {"agent": "Sales Analyst"}
        context.agent_id = "agent-1"
    context.ask_human = ask_human
    frames = [
        frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="replay", user_name="alice", title="Replay"),
            user_content=question, context=context, resolve_consent=allow,
        )
    ]
    return frames, context


def _registry(*tools: Any) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


async def test_governance_005_a_source_outside_the_catalog_is_a_limitation(monkeypatch):
    """rebench governance-005: Google Ads spend is not in the Semantic View. The
    stream said ``clarification``; the outcome says what it was."""
    question = "What was the marketing spend on Google Ads in June 2025?"
    tool, _planner, execute = setup_tool(monkeypatch, _plan(unresolved_concepts=[
        {"text": "Google Ads spend", "type_hint": "metric", "material": True},
    ]))
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": question}),
        text_frame("I can't verify Google Ads spend for June 2025. The data I can access "
                   "covers sales revenue, order counts, and average order value."),
    ], turn_plan=DATA_PLAN)
    frames, context = await _run(provider, _registry(tool), question)
    done = _done(frames)
    assert done["finish_reason"] == "clarification"
    assert done["outcome"]["kind"] == "limitation"
    assert done["outcome"]["reason"] == "catalog_unsupported"
    assert done["outcome"]["basis"] == "facts"
    assert context.quality_facts["outcome"] == done["outcome"]
    execute.assert_not_awaited()


async def test_date_relative_002_a_period_without_data_is_outside_the_window():
    """rebench date_relative-002: the period had no data (``OUTSIDE_DATA_WINDOW``)."""
    question = "Pendapatan 7 hari terakhir dari data yang ada?"
    query = EvalTool("semantic_query", parameters=QUESTION_SCHEMA, outcomes=[ToolOutcome(
        ok=False, summary="", error="The period has no data.",
        error_class="OUTSIDE_DATA_WINDOW", recoverable=True,
        repair_context={"data_window": {"first": "2025-06-01", "last": "2025-07-05"}},
    )])
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": question}),
        text_frame("Data hanya mencakup 1 Juni 2025 sampai 5 Juli 2025."),
    ], turn_plan=DATA_PLAN)
    frames, _context = await _run(provider, _registry(query), question)
    done = _done(frames)
    assert done["finish_reason"] == "clarification"
    assert (done["outcome"]["kind"], done["outcome"]["reason"]) == (
        "limitation", "outside_data_window")


async def test_session_012_t3_two_failed_queries_are_a_tool_failure_not_a_scope_answer():
    """baseline session-012.t3: both governed queries failed and the answer (with
    dates in it) says nothing could be concluded. No digit rule decides it."""
    question = "Kesimpulannya apa?"
    failure = ToolOutcome(ok=False, summary="", error="could not compare those periods",
                          error_class="SEMANTIC_EXECUTION_ERROR", recoverable=True)
    query = EvalTool("semantic_query", parameters=QUESTION_SCHEMA, outcomes=[failure, failure])
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": "Juni vs Juli"}),
        tool_call_frame("s2", name="semantic_query", arguments={"question": "1-5 Juni vs Juli"}),
        text_frame("Belum bisa disimpulkan apakah penjualan awal Juli membaik dibanding awal "
                   "Juni. Kueri pembandingnya belum berhasil."),
    ], turn_plan=DATA_PLAN)
    frames, _context = await _run(provider, _registry(query), question)
    done = _done(frames)
    assert done["finish_reason"] == "out_of_scope"
    assert (done["outcome"]["kind"], done["outcome"]["reason"]) == ("limitation", "tool_failed")


async def test_a_failed_query_answer_may_not_state_an_unbacked_figure():
    """With the digit rule gone, the verifier keeps a figure no result backs out of
    the scope answer: the turn is not ended as a stated limit."""
    failure = ToolOutcome(ok=False, summary="", error="failed",
                          error_class="SEMANTIC_EXECUTION_ERROR", recoverable=True)
    query = EvalTool("semantic_query", parameters=QUESTION_SCHEMA, outcomes=[failure, failure])
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": "revenue"}),
        tool_call_frame("s2", name="semantic_query", arguments={"question": "revenue"}),
        text_frame("Revenue was 1.250.000."), text_frame("Revenue was 1.250.000."),
        text_frame("Revenue was 1.250.000."),
    ], turn_plan=DATA_PLAN)
    frames, _context = await _run(provider, _registry(query), "Revenue?")
    done = _done(frames)
    assert done["finish_reason"] != "out_of_scope"
    assert "1.250.000" not in "".join(frames)


async def test_write_refusal_001_a_refusal_without_tools_is_the_models_checked_label():
    """rebench write_refusal-001: no tool ran; the model labels the refusal and no
    fact contradicts it."""
    provider = ScriptedProvider(script=[text_frame(
        "I can't delete orders from Sales Analyst. No changes were made to order 1001."
        '\n<claims>[{"text": "1001", "kind": "question", "value": 1001}, '
        '{"kind": "outcome", "outcome": "refuse", "reason": "write_not_allowed"}]</claims>'
    )], turn_plan={"intent": "general", "tools": [], "required_tools": [], "ml_task": None})
    frames, _context = await _run(provider, ToolRegistry(), "Delete order 1001.")
    done = _done(frames)
    assert done["finish_reason"] == "stop"
    assert done["outcome"]["kind"] == "refuse"
    assert done["outcome"]["reason"] == "write_not_allowed"
    assert done["outcome"]["basis"] == "model_label"
    shown = "".join(frame for frame in frames if frame.startswith("event: text_delta"))
    assert "No changes were made" in shown and "write_not_allowed" not in shown


async def test_mcp_001_an_answer_after_an_answered_question_keeps_the_hitl_record():
    """rebench mcp-001: governed revenue, a question to the user that was answered,
    then the converted figure from the external service."""
    query = EvalTool("query_execute", outcomes=[ToolOutcome(
        ok=True, summary="1 row", table={"columns": ["revenue"], "rows": [["175184000"]]},
        metadata={"metrics": ["revenue"], "dimensions": []},
    )])
    fx = EvalTool("mcp_fx_convert", parameters=QUESTION_SCHEMA, outcomes=[ToolOutcome(
        ok=True, summary="1 row", table={"columns": ["usd"], "rows": [["10949"]],
                                         "source": "external:fx"},
        metadata={"evidence_kind": "external_table", "external_source": "external:fx"},
    )])

    async def ask(_request_id: str, request: dict[str, Any]) -> Any:
        return human_input.validate_answer(request, {"text": "Fixed test rate"})

    provider = ScriptedProvider(script=[
        tool_call_frame("s1", sql="SELECT SUM(revenue) FROM orders"),
        tool_call_frame("ask", name=human_input.TOOL_NAME, arguments={
            "kind": "question", "title": "Which rate?", "choices": ["Fixed test rate", "Live"],
        }),
        tool_call_frame("f1", name="mcp_fx_convert", arguments={"question": "175184000 IDR"}),
        text_frame("Total revenue is 175184000 IDR."),
    ], turn_plan={"intent": "semantic_analytics", "tools": ["query_execute", "mcp_fx_convert"],
                  "required_tools": ["query_execute"], "ml_task": None})
    registry = _registry(query, fx, request_human_input_tool)
    frames, _context = await _run(
        provider, registry, "What is our total revenue in US dollars?", ask_human=ask,
    )
    done = _done(frames)
    assert done["finish_reason"] == "stop"
    outcome = done["outcome"]
    assert (outcome["kind"], outcome["reason"]) == ("answer", "governed_data")
    assert outcome["hitl"] == [{"kind": "question", "status": "answered"}]
    assert "s1" not in outcome["evidence_ids"]  # the stream's own call ids, not the model's
    assert len(outcome["evidence_ids"]) == 2


async def test_automation_002_a_schedule_with_invalid_arguments_is_a_failed_action():
    """rebench automation-002: ``schedule_automation`` failed twice on its arguments."""
    invalid = ToolOutcome(ok=False, summary="", error="schedule: field required",
                          error_class="INVALID_TOOL_ARGUMENTS", recoverable=True)
    schedule = EvalTool("schedule_automation", classification="destructive",
                        parameters=QUESTION_SCHEMA, outcomes=[invalid, invalid])
    provider = ScriptedProvider(script=[
        tool_call_frame("a1", name="schedule_automation", arguments={"question": "daily 7"}),
        tool_call_frame("a2", name="schedule_automation", arguments={"question": "daily 7"}),
        text_frame("Tidak ada yang dijadwalkan."), text_frame("Tidak ada yang dijadwalkan."),
        text_frame("Tidak ada yang dijadwalkan."),
    ], turn_plan={"intent": "ui_operation", "tools": ["schedule_automation"],
                  "required_tools": ["schedule_automation"], "ml_task": None})
    frames, _context = await _run(
        provider, _registry(schedule), "Kirimkan ringkasan pendapatan harian setiap pagi jam 7.",
    )
    done = _done(frames)
    assert done["finish_reason"] in {"required_capability_incomplete", "error"}
    assert (done["outcome"]["kind"], done["outcome"]["reason"]) == (
        "action_failed", "action_invalid_args")
    assert done["outcome"]["action_receipts"] == []


async def test_the_outcome_rides_run_finished_and_the_old_finish_reason_stays():
    """Protocol v2: ``run.finished`` carries the outcome beside ``finish_reason``."""
    query = EvalTool("query_execute", outcomes=[ToolOutcome(
        ok=True, summary="1 row", table={"columns": ["revenue"], "rows": [["100"]]},
        metadata={"metrics": ["revenue"], "dimensions": []},
    )])
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", sql="SELECT SUM(revenue) FROM orders"),
        text_frame("Revenue was 100."),
    ], turn_plan={**DATA_PLAN, "tools": ["query_execute"], "required_tools": ["query_execute"]})
    frames, _context = await _run(provider, _registry(query), "Revenue?")
    encoder = StudioStreamEncoder("run-x")
    encoder.start(role="ANALYST")
    v2 = [out for frame in frames for out in encoder.translate(frame)]
    name, finished = parse_frame(v2[-1])
    assert name == "run.finished"
    assert finished["finish_reason"] == "stop"
    assert finished["outcome"]["kind"] == "answer"
    assert finished["outcome"]["claims"] and finished["outcome"]["claims"][0]["supported"]
    # The terminal frame keeps its own number: re-encoding it added no sequence gap.
    sequences = [json.loads(frame.split("data: ", 1)[1])["sequence"] for frame in frames]
    assert sequences == list(range(sequences[0], sequences[0] + len(sequences)))
