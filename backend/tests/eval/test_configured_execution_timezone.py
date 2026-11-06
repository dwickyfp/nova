"""Scripted semantic turns retain the runtime business calendar."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from app.core.config import settings
from app.modules.agents import turns
from app.modules.agents.service import agent_service
from app.modules.assistant import provider as provider_module
from app.modules.assistant import service as assistant_service
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.unit.test_semantic_guidance_fallback import USER
from tests.unit.test_semantic_llm_planner import _plan, setup_tool

NOW = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)


async def test_configured_utc_semantic_turn_uses_injected_clock(monkeypatch):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", " UTC ")
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
