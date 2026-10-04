"""Scripted semantic and Smart turns retain the runtime business calendar."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.agents import harness_worker, turns
from app.modules.agents.semantic.time_ranges import resolve_execution_time
from app.modules.agents.service import agent_service
from app.modules.assistant import provider as provider_module
from app.modules.assistant import service as assistant_service
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import EvalTool
from tests.unit.test_semantic_guidance_fallback import USER
from tests.unit.test_semantic_llm_planner import _plan, setup_tool
from tests.unit.test_smart_collaboration import USER as SMART_USER
from tests.unit.test_smart_collaboration import collaboration as collaboration

NOW = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)


async def test_configured_utc_semantic_turn_uses_injected_clock(monkeypatch):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", " UTC ")
    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    monkeypatch.setattr(assistant_service, "_now", lambda: NOW)
    plan = _plan(time={
        "dimension": "order_date", "grain": None, "range": "today", "compare": None,
    })
    tool, _, execute = setup_tool(monkeypatch, plan)
    tool._resolve_model.return_value.update(status="ACTIVE", version=1, fingerprint="published")
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider([
        tool_call_frame("query-today", name="semantic_query", arguments={
            "question": "Revenue today",
        }),
        text_frame("Revenue today is 100."),
    ], turn_plan={
        "intent": "semantic_analytics", "tools": ["semantic_query"],
        "required_tools": ["semantic_query"], "skills": [], "ml_task": None,
    })
    monkeypatch.setattr(provider_module, "assistant_provider", provider)
    monkeypatch.setattr(agent_service, "build_loop_inputs", AsyncMock(
        return_value=(registry, "Use governed semantic evidence.", 60, 4096),
    ))
    output = await turns.run_agent_turn({"agent_id": "finance", "owner_name": "alice"},
                                       user=USER.copy(), question="Revenue today",
                                       thread_id="utc-eval")
    assert output.finish_reason == "stop"
    assert "100" in output.text and "enc" not in output.text
    execute.assert_awaited_once()
    sql = execute.await_args.kwargs["sql"]
    assert "'2026-01-01 00:00:00.000000'" in sql
    assert "'2026-01-01 00:30:00.000000'" in sql
    assert "CURRENT_DATE" not in sql
    detail = next(step["trace_detail"] for step in output.steps if step.get("kind") == "tool")
    fixed = detail["execution_time_context"]
    assert fixed["timezone"] == "UTC" and fixed["now"] == NOW.isoformat()
    assert fixed["current"] == {
        "start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T00:30:00+00:00",
    }


@pytest.mark.parametrize("zone", ("Asia/Jakarta", "UTC", "Europe/Berlin", "America/New_York"))
async def test_smart_specialist_observes_configured_calendar(collaboration, monkeypatch, zone):
    repo, control = collaboration
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", f" {zone} ")
    spawned = await control.spawn_agent(
        agent="finance", task_name="calendar", objective="Revenue today", operation_id="calendar",
    )
    calendars = []

    class CalendarQuery(EvalTool):
        async def run(self, invocation, context):
            calendars.append(resolve_execution_time("today", now=NOW,
                                                     timezone=context.execution_timezone))
            assert context.role == SMART_USER["active_role"]
            return await super().run(invocation, context)

    query = CalendarQuery(
        "semantic_query", parameters={
            "type": "object", "properties": {"question": {"type": "string"}},
            "required": ["question"],
        }, table={"columns": ["revenue"], "rows": [[100]]}, data={
            "semantic_plan": {"metrics": ["revenue"]}, "sql": "SELECT revenue FROM sales",
        },
    )
    registry = ToolRegistry()
    registry.register(query)
    monkeypatch.setattr(harness_worker.agent_service, "build_loop_inputs", AsyncMock(
        return_value=(registry, "Use the semantic query for revenue.", 30, 24000),
    ))
    monkeypatch.setattr(harness_worker.agent_repository, "get_agent", AsyncMock(return_value={
        "agent_id": "finance", "owner_name": "alice", "policy": "auto_read_only",
    }))
    monkeypatch.setattr(harness_worker, "has_verified_access", AsyncMock(return_value=True))
    monkeypatch.setattr(harness_worker.memory_repository, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(harness_worker, "assistant_provider", ScriptedProvider([
        tool_call_frame("query-calendar", name="semantic_query", arguments={"question": "Revenue"}),
        text_frame("Revenue is 100."),
    ]))
    worker = harness_worker.AgentHarnessWorker(repo)
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value=SMART_USER))
    await worker.process(spawned["current_turn_id"], "calendar-worker")
    result = await repo.get(spawned["current_turn_id"])
    assert result["status"] == "completed", (result.get("error_class"), repo.events[-6:])
    assert len(calendars) == 1 and calendars[0].timezone == zone
    assert calendars[0].as_dict()["now"] == NOW.isoformat()
