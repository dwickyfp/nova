"""Reject the catalog-as-revenue failure through the shared production loop."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.collaboration_tools import COLLABORATION_TOOLS
from app.modules.assistant.intelligence import EvidenceTracker
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult

QUESTION = "Bandingkan recognized revenue, gross margin, dan order count per channel untuk 2025."
METRICS = ["recognized_revenue", "gross_margin_pct", "order_count"]
TABLE = {"columns": ["sales_channel", *METRICS],
         "rows": [["Mobile App", "3368049065451.00", "0.36962540", 177450]]}
OWNER = {"agent_id": "configured-owner", "name": "Renamed specialist",
         "dimension_matches": ["sales_channel"],
         "semantic_matches": [{"metric": name, "matched_alias": name} for name in METRICS]}


def call(name: str, **arguments) -> dict:
    return tool_call_frame(name, name=name, arguments=arguments or {"sql": "SELECT 1"})


def participant() -> dict:
    evidence = EvidenceTracker()
    evidence.add("semantic_query", "Authorized query", table=copy.deepcopy(TABLE), metadata={
        "metrics": METRICS, "dimensions": ["sales_channel"],
        "semantic_model_id": "sales-model",
        "semantic_plan": {"time": {"range": "2025"}},
    })
    return {"agent_id": OWNER["agent_id"], "current_turn_id": "child", "depth": 1,
            "status": "idle", "evidence": evidence.snapshot()}


async def run(script: list[dict], *, result: dict | None = None, owners: bool = True):
    discovery = EvalTool("discover_agents", parameters=COLLABORATION_TOOLS["discover_agents"][1],
                         data={"agents": [OWNER] if owners else []})
    spawn = EvalTool("spawn_agent", parameters=COLLABORATION_TOOLS["spawn_agent"][1], data=OWNER)
    wait = EvalTool("wait_agent", parameters=COLLABORATION_TOOLS["wait_agent"][1],
                    data={"agents": [result or participant()]})
    query = EvalTool("query_execute", table={"columns": ["Database"], "rows": [["NOVA_SALES"]]})
    registry = ToolRegistry()
    for tool in (discovery, spawn, wait, query):
        registry.register(tool)
    provider = ScriptedProvider(script, turn_plan={
        "intent": "semantic_analytics", "tools": registry.names(),
        "required_tools": ["query_execute"],
        # What the planner reads from the question: the year 2025, no series.
        "intent_frame": {"language": "en", "range": "2025", "compares_groups": True},
    })
    context = LoopContext(user_name="alice", collaboration_root=True,
                          collaboration_tools=tuple(COLLABORATION_TOOLS))
    consent = AsyncMock(return_value=False)
    loop = AssistantLoop(provider=provider, registry=registry, max_iterations=12)
    frames = [frame async for frame in loop.run(
        thread=thread(read_only_grant=True), user_content=QUESTION, context=context,
        resolve_consent=consent,
    )]
    return TurnResult(frames=frames), query, context, consent


@pytest.mark.asyncio
async def test_root_cannot_bypass_discovery_or_metric_owner():
    result, query, context, consent = await run([
        call("query_execute", sql="SHOW DATABASES"),
        call("discover_agents", capability=QUESTION),
        call("query_execute", sql="SHOW TABLES IN NOVA_SALES"),
        call("spawn_agent", agent=OWNER["agent_id"], task_name="data", objective=QUESTION),
        call("wait_agent", targets=["/root/data"]),
        text_frame("Complete"),
    ])
    assert result.finish_reason == "stop", result.error_codes
    assert not query.runs
    assert not consent.called
    assert context.verified_evidence["tables"]
    assert any(item["metadata"].get("source_agent_id") == OWNER["agent_id"]
               for item in context.verified_evidence["items"])






@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [
    "metric", "grouping", "period", "grain", "catalog", "failed", "message",
])
async def test_incomplete_specialist_evidence_cannot_complete(defect):
    child = participant()
    evidence = child["evidence"]
    metadata = evidence["items"][0]["metadata"]
    if defect == "metric":
        metadata["metrics"] = METRICS[:1]
    elif defect == "grouping":
        metadata["dimensions"] = []
    elif defect == "period":
        metadata["semantic_plan"]["time"]["range"] = "2024"
    elif defect == "grain":
        metadata["semantic_plan"]["time"]["grain"] = "month"
    elif defect == "catalog":
        evidence["tables"]["evidence_1"] = {"columns": ["Database"], "rows": [["NOVA_SALES"]]}
    elif defect == "failed":
        child["status"] = "failed"
    elif defect == "message":
        child["evidence"] = {}
        child["summary"] = "All requested metrics are complete."
    result, _, _, _ = await run([
        call("discover_agents", capability=QUESTION),
        call("spawn_agent", agent=OWNER["agent_id"], task_name="data", objective=QUESTION),
        call("wait_agent", targets=["/root/data"]),
        text_frame("Revenue is 999 and the task is complete."),
    ], result=child)
    assert result.finish_reason == "data_evidence_incomplete", result.error_codes
    assert not any(frame.startswith("event: text_delta") for frame in result.frames)


@pytest.mark.asyncio
async def test_metadata_alone_cannot_answer_when_no_specialist_matches():
    result, query, _, _ = await run([
        call("discover_agents", capability=QUESTION),
        call("query_execute", sql="SHOW DATABASES"),
        text_frame("Here is the comparison."),
    ], owners=False)
    assert len(query.runs) == 1
    assert result.finish_reason == "data_evidence_incomplete"


@pytest.mark.asyncio
async def test_repeated_specialist_projection_is_not_rendered_twice():
    child = participant()
    rows = child["evidence"]["tables"]["evidence_1"]["rows"]
    rows.append(["Store", "100.00", "0.30", 2])
    duplicate = copy.deepcopy(child["evidence"]["items"][0])
    duplicate["evidence_id"] = "evidence_2"
    duplicate["metadata"]["metrics"] = METRICS[:1]
    child["evidence"]["items"].append(duplicate)
    child["evidence"]["tables"]["evidence_2"] = {
        "columns": ["sales_channel", METRICS[0]], "rows": [row[:2] for row in reversed(rows)],
    }
    result, _, context, _ = await run([
        call("discover_agents", capability=QUESTION),
        call("spawn_agent", agent=OWNER["agent_id"], task_name="data", objective=QUESTION),
        call("wait_agent", targets=["/root/data"]),
        text_frame("Complete"),
    ], result=child)
    assert result.finish_reason == "stop", result.error_codes
    assert len(context.verified_evidence["tables"]) == 2
    assert "".join(result.frames).count("| sales_channel |") == 1


@pytest.mark.parametrize("difference", ["period", "source", "model", "value", "truncated"])
def test_projection_with_different_evidence_is_preserved(difference):
    from app.modules.assistant.data_evidence import nonredundant_business_tables

    tracker = EvidenceTracker()
    child = participant()
    tracker.import_results(child)
    metadata = copy.deepcopy(tracker.items[0].metadata)
    table = {"columns": ["sales_channel", METRICS[0]], "rows": [TABLE["rows"][0][:2]]}
    if difference == "period":
        metadata["semantic_plan"]["time"]["range"] = "2024"
    elif difference == "source":
        metadata["source_agent_id"] = "another-owner"
    elif difference == "model":
        metadata["semantic_model_id"] = "another-model"
    elif difference == "value":
        table["rows"][0][1] = "1.00"
    else:
        table["truncated"] = True
    tracker.add("semantic_query", "Another result", table=table, metadata=metadata)
    assert len(nonredundant_business_tables(tracker)) == 2


@pytest.mark.asyncio
async def test_catalog_inspection_does_not_exhaust_data_query_budget():
    query = EvalTool("query_execute", outcomes=[
        ToolOutcome(ok=True, summary="Catalog", table={"columns": ["Database"], "rows": [["db"]]}),
        ToolOutcome(ok=True, summary="Schema",
                    table={"columns": ["Tables_in_db"], "rows": [["t"]]}),
        ToolOutcome(ok=True, summary="Data", table=TABLE),
    ])
    registry = ToolRegistry()
    registry.register(query)
    script = [call("query_execute", sql=sql) for sql in
              ["SHOW DATABASES", "SHOW TABLES IN db", "SELECT * FROM db.t"]]
    provider = ScriptedProvider([*script, text_frame("Complete")], turn_plan={
        "intent": "semantic_analytics", "tools": ["query_execute"],
        "required_tools": ["query_execute"],
    })
    loop = AssistantLoop(provider=provider, registry=registry)
    frames = [frame async for frame in loop.run(
        thread=thread(read_only_grant=True), user_content=QUESTION,
        context=LoopContext(user_name="alice"), resolve_consent=AsyncMock(return_value=False),
    )]
    assert len(query.runs) == 3
    assert TurnResult(frames=frames).finish_reason == "stop"


async def uncovered(answer: str) -> TurnResult:
    """Smart discovers no owner and answers; a describe_agent tool makes it a Studio turn."""
    from app.modules.agents.tools.describe_agent import DescribeAgentTool

    discovery = EvalTool("discover_agents", parameters=COLLABORATION_TOOLS["discover_agents"][1],
                         data={"agents": []})
    registry = ToolRegistry()
    registry.register(discovery)
    registry.register(DescribeAgentTool(registry, name="Smart"))
    provider = ScriptedProvider([
        call("discover_agents", capability="Berapa stok gudang?"), text_frame(answer),
    ], turn_plan={"intent": "semantic_analytics", "tools": ["discover_agents"],
                  "required_tools": ["discover_agents"]})
    context = LoopContext(user_name="alice", agent_id="__smart__", collaboration_root=True,
                          collaboration_tools=tuple(COLLABORATION_TOOLS))
    loop = AssistantLoop(provider=provider, registry=registry, max_iterations=6)
    return TurnResult(frames=[frame async for frame in loop.run(
        thread=thread(read_only_grant=True), user_content="Berapa stok gudang?",
        context=context,
        resolve_consent=AsyncMock(return_value=False),
    )])


@pytest.mark.asyncio
async def test_smart_states_the_gap_when_no_specialist_owns_the_request():
    result = await uncovered("Saya tidak punya data stok gudang. Saya punya keuangan dan HR.")
    assert result.finish_reason == "out_of_scope", result.error_codes
    assert any(frame.startswith("event: text_delta") for frame in result.frames)


@pytest.mark.asyncio
async def test_smart_cannot_state_a_value_when_no_specialist_owns_the_request():
    result = await uncovered("Stok gudang saat ini 1200 unit.")
    assert result.finish_reason == "data_evidence_incomplete", result.error_codes
    assert not any(frame.startswith("event: text_delta") for frame in result.frames)


@pytest.mark.asyncio
async def test_an_unsupported_number_gets_one_rewrite_before_anything_is_removed():
    result, _, context, _ = await run([
        call("discover_agents", capability=QUESTION),
        call("spawn_agent", agent=OWNER["agent_id"], task_name="data", objective=QUESTION),
        call("wait_agent", targets=["/root/data"]),
        text_frame("Mobile App recognized revenue is 3368049065451.00, about 9 times Store."),
        text_frame("Mobile App recognized revenue is 3368049065451.00."),
    ])
    assert result.finish_reason == "stop", result.error_codes
    text = "".join(
        frame for frame in result.frames if frame.startswith("event: text_delta")
    )
    assert "3368049065451.00" in text and "9 times" not in text
    repairs = [step for step in context.steps if step.get("answer_repair")]
    assert [step["unsupported"] for step in repairs] == [["9"]]


@pytest.mark.asyncio
async def test_a_model_that_repeats_an_unsupported_number_still_cannot_show_it():
    result, _, _, _ = await run([
        call("discover_agents", capability=QUESTION),
        call("spawn_agent", agent=OWNER["agent_id"], task_name="data", objective=QUESTION),
        call("wait_agent", targets=["/root/data"]),
        text_frame("Mobile App recognized revenue is 3368049065451.00, about 9 times Store."),
    ])
    assert result.finish_reason == "stop", result.error_codes
    text = "".join(
        frame for frame in result.frames if frame.startswith("event: text_delta")
    )
    assert "9 times" not in text and "unverified" not in text


def specialist(turn: str, agent_id: str, table: dict, metric: str) -> dict:
    evidence = EvidenceTracker()
    evidence.add("semantic_query", "Authorized query", table=copy.deepcopy(table), metadata={
        "metrics": [metric], "dimensions": ["department"], "semantic_model_id": f"{agent_id}-model",
        "semantic_plan": {"time": {"range": "2025"}},
    })
    return {"agent_id": agent_id, "current_turn_id": turn, "depth": 1, "status": "idle",
            "evidence": evidence.snapshot()}


@pytest.mark.asyncio
async def test_root_relates_two_specialists_results_with_verified_arithmetic():
    from app.modules.agents.tools.compute_metrics import compute_metrics_tool

    question = "Expense per active employee by department for 2025?"
    owners = [
        {"agent_id": "finance", "name": "Finance", "dimension_matches": ["department"],
         "semantic_matches": [{"metric": "total_expense", "matched_alias": "expense"}]},
        {"agent_id": "hr", "name": "HR", "dimension_matches": ["department"],
         "semantic_matches": [{"metric": "active_headcount", "matched_alias": "active employee"}]},
    ]
    expense = {"columns": ["department", "total_expense"],
               "rows": [["Sales", "1200.00"], ["Finance", "900.00"]]}
    headcount = {"columns": ["department", "active_headcount"],
                 "rows": [["Finance", 3], ["Sales", 4]]}
    registry = ToolRegistry()
    for name, data in (
        ("discover_agents", {"agents": owners}), ("spawn_agent", owners[0]),
        ("wait_agent", {"agents": [specialist("t1", "finance", expense, "total_expense"),
                                   specialist("t2", "hr", headcount, "active_headcount")]}),
    ):
        registry.register(EvalTool(name, parameters=COLLABORATION_TOOLS[name][1], data=data))
    registry.register(compute_metrics_tool)
    script = [
        call("discover_agents", capability=question),
        call("spawn_agent", agent="finance", task_name="expense", objective=question),
        call("wait_agent", targets=["/root/expense"]),
        tool_call_frame("combine", name="compute_metrics", arguments={
            "operation": "combine", "evidence_id": "evidence_4",
            "with_evidence_id": "evidence_5"}),
        tool_call_frame("ratio", name="compute_metrics", arguments={
            "operation": "ratio", "value_column": "total_expense",
            "compare_column": "active_headcount"}),
        text_frame("Sales spends 300 per active employee and Finance 300, about 77 in savings."),
        text_frame("Sales spends 300 per active employee and Finance 300."),
    ]
    provider = ScriptedProvider(script, turn_plan={
        "intent": "semantic_analytics", "tools": ["discover_agents"],
        "required_tools": ["discover_agents"],
        "intent_frame": {"language": "en", "range": "2025", "compares_groups": True},
    })
    context = LoopContext(user_name="alice", collaboration_root=True,
                          collaboration_tools=tuple(COLLABORATION_TOOLS))
    result = TurnResult(frames=[frame async for frame in AssistantLoop(
        provider=provider, registry=registry, max_iterations=12,
    ).run(thread=thread(read_only_grant=True), user_content=question, context=context,
          resolve_consent=AsyncMock(return_value=False))])
    assert result.finish_reason == "stop", result.error_codes
    assert {"compute_metrics"} <= set(context.selected_tools)
    text = "".join(frame for frame in result.frames if frame.startswith("event: text_delta"))
    assert "300 per active employee" in text and "77" not in text
    assert context.last_result["columns"][-1] == "total_expense_per_active_headcount"
