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
