"""Studio analyst turns: drill down after the required evidence, recover bounded.

Audit 2026-09-27 found three ceilings in the loop: tools were switched off as
soon as the required query ran, one shared repair counter ended a turn at the
second unrelated slip, and a single denied approval ended the turn. These
trajectories pin the replacements for the iterative (Studio analyst) loop, and
check that the historical single-query behaviour is unchanged when the loop is
not iterative.
"""

from __future__ import annotations

import json

from app.modules.agents.tools.compute_metrics import compute_metrics_tool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import EvalTool

MONTHS = {
    "title": "Revenue by month",
    "columns": ["month", "total_revenue"],
    "rows": [["2026-07", 1200000], ["2026-08", 1500000]],
}
CITIES = {
    "title": "Revenue by city",
    "columns": ["city", "total_revenue"],
    "rows": [["Jakarta", 900000], ["Bandung", 600000]],
}
SEMANTIC_PARAMETERS = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
}


def semantic_tool(*tables):
    return EvalTool(
        "semantic_query",
        parameters=SEMANTIC_PARAMETERS,
        outcomes=[
            ToolOutcome(
                ok=True, summary="rows", table=table,
                data={"semantic_plan": {"metrics": ["total_revenue"]},
                      "sql": "SELECT SUM(total_amount) AS total_revenue FROM orders"},
            )
            for table in tables
        ],
    )


def semantic_call(call_id: str, question: str) -> dict:
    return tool_call_frame(call_id, name="semantic_query", arguments={"question": question})


def plan(*tools: str) -> dict:
    return {
        "intent": "semantic_analytics",
        "tools": list(tools),
        "required_tools": ["semantic_query"],
        "ml_task": None,
        "intent_frame": {"language": "id"},
    }


async def run(script, tools, *, iterative=True, max_iterations=16, consent=None,
              question="Bagaimana revenue Juli vs Agustus?"):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    provider = ScriptedProvider(
        script=script, turn_plan=plan(*[tool.name for tool in tools])
    )
    prompts: list[str] = []

    async def resolve(invocation, _classification):
        prompts.append(invocation.tool_name)
        return True if consent is None else consent(len(prompts))

    thread = AssistantThread(thread_id="iterative", user_name="alice", title="Eval")
    # Tool calls prompt for approval only when the thread has no read-only grant.
    thread.consent.always_allow_read_only = consent is None
    frames = [
        frame
        async for frame in AssistantLoop(
            provider=provider,
            registry=registry,
            max_iterations=max_iterations,
            max_calls_per_tool=6,
            iterative=iterative,
        ).run(
            thread=thread,
            user_content=question,
            context=LoopContext(user_name="alice"),
            resolve_consent=resolve,
        )
    ]
    joined = "".join(frames)
    finish = next(
        json.loads(frame.split("data: ", 1)[1])["finish_reason"]
        for frame in reversed(frames) if frame.startswith("event: done")
    )
    text = "".join(
        json.loads(frame.split("data: ", 1)[1]).get("text", "")
        for frame in frames if frame.startswith("event: text_delta")
    )
    return finish, text, joined, prompts


async def test_analyst_drills_down_and_computes_after_the_required_query():
    semantic = semantic_tool(MONTHS, CITIES)
    finish, text, _joined, _prompts = await run(
        [
            semantic_call("s1", "revenue Juli dan Agustus 2026"),
            tool_call_frame("c1", name="compute_metrics", arguments={
                "operation": "pct_change", "value_column": "total_revenue",
                "evidence_id": "evidence_1", "from_label": "2026-07", "to_label": "2026-08",
            }),
            semantic_call("s2", "revenue per kota Agustus 2026"),
            text_frame("Revenue tumbuh 25% dari Juli ke Agustus, dipimpin Jakarta."),
        ],
        [semantic, compute_metrics_tool],
    )
    assert finish == "stop"
    assert len(semantic.runs) == 2
    # The derived 25% is evidence, so the Indonesian answer is kept as written.
    assert "Revenue tumbuh 25% dari Juli ke Agustus" in text


async def test_non_iterative_loop_still_composes_after_the_required_query():
    semantic = semantic_tool(MONTHS, CITIES)
    finish, _text, joined, _prompts = await run(
        [
            semantic_call("s1", "revenue Juli dan Agustus 2026"),
            semantic_call("s2", "revenue per kota Agustus 2026"),
            text_frame("Revenue naik dari 1.200.000 ke 1.500.000."),
        ],
        [semantic],
        iterative=False,
    )
    assert len(semantic.runs) == 1
    assert finish == "stop"
    assert "Tools are disabled during final composition" not in joined or len(semantic.runs) == 1


async def test_budget_reserve_forces_the_answer_before_the_cap():
    semantic = semantic_tool(MONTHS, CITIES, MONTHS)
    finish, _text, _joined, _prompts = await run(
        [
            semantic_call("s1", "revenue Juli dan Agustus 2026"),
            semantic_call("s2", "revenue per kota"),
            semantic_call("s3", "revenue per produk"),
            text_frame("Revenue naik dari 1.200.000 ke 1.500.000."),
        ],
        [semantic],
        max_iterations=4,
    )
    assert finish == "stop"
    assert len(semantic.runs) == 2


async def test_separate_repair_budgets_survive_two_different_slips():
    semantic = semantic_tool(MONTHS)
    finish, _text, _joined, _prompts = await run(
        [
            tool_call_frame("bad-args", name="semantic_query", arguments={"sql": "x"}),
            tool_call_frame("bad-tool", name="query_execute", arguments={"sql": "SELECT 1"}),
            semantic_call("s1", "revenue Juli dan Agustus 2026"),
            text_frame("Revenue naik dari 1.200.000 ke 1.500.000."),
        ],
        [semantic],
    )
    assert finish == "stop"
    assert len(semantic.runs) == 1


async def test_one_declined_approval_is_reported_back_to_the_model():
    semantic = semantic_tool(MONTHS)
    finish, text, joined, prompts = await run(
        [
            semantic_call("s1", "revenue Juli dan Agustus 2026"),
            text_frame("Saya tidak menjalankan query karena persetujuan ditolak."),
        ],
        [semantic],
        consent=lambda count: False,
    )
    assert prompts == ["semantic_query"]
    assert finish == "stop"
    # With no evidence at all, Nova states the denial itself; model prose about
    # data it never saw is not shown.
    assert text.startswith("Query tidak dijalankan karena persetujuan ditolak")
    assert semantic.runs == []
    assert "CONSENT_DENIED" not in text
    assert '"status":"denied"' in joined.replace(" ", "")


async def test_a_second_denial_ends_the_turn():
    semantic = semantic_tool(MONTHS, CITIES)
    finish, _text, _joined, prompts = await run(
        [
            semantic_call("s1", "revenue Juli dan Agustus 2026"),
            semantic_call("s2", "revenue per kota"),
            text_frame("unused"),
        ],
        [semantic],
        consent=lambda count: False,
    )
    assert prompts == ["semantic_query", "semantic_query"]
    assert finish == "denied"
    assert semantic.runs == []


async def test_the_declined_call_is_never_asked_again():
    semantic = semantic_tool(MONTHS)
    finish, _text, _joined, prompts = await run(
        [
            semantic_call("s1", "revenue Juli dan Agustus 2026"),
            semantic_call("s1-again", "revenue Juli dan Agustus 2026"),
            text_frame("unused"),
        ],
        [semantic],
        consent=lambda count: False,
    )
    assert prompts == ["semantic_query"]
    assert finish == "denied"


async def test_non_iterative_loop_still_ends_on_the_first_denial():
    semantic = semantic_tool(MONTHS)
    finish, _text, _joined, prompts = await run(
        [semantic_call("s1", "revenue"), text_frame("unused")],
        [semantic],
        iterative=False,
        consent=lambda count: False,
    )
    assert prompts == ["semantic_query"]
    assert finish == "denied"


async def test_a_failed_optional_step_does_not_discard_the_required_evidence():
    semantic = semantic_tool(MONTHS)
    finish, text, _joined, _prompts = await run(
        [
            semantic_call("s1", "revenue Juli dan Agustus 2026"),
            tool_call_frame("c1", name="compute_metrics", arguments={
                "operation": "pct_change", "value_column": "total_revenue",
                "from_label": "2026-02", "to_label": "2026-08",
            }),
            tool_call_frame("c2", name="compute_metrics", arguments={
                "operation": "pct_change", "value_column": "total_revenue",
                "from_label": "2026-03", "to_label": "2026-08",
            }),
            tool_call_frame("c3", name="compute_metrics", arguments={
                "operation": "pct_change", "value_column": "total_revenue",
                "from_label": "2026-04", "to_label": "2026-08",
            }),
            text_frame("Revenue naik dari 1.200.000 ke 1.500.000."),
        ],
        [semantic, compute_metrics_tool],
    )
    assert finish == "stop"
    assert "1.200.000" in text
