"""Thread learning controls apply to successful and failed semantic usage."""

from unittest.mock import AsyncMock

import pytest

from app.modules.agents.repository import agent_repository
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.query.repository import QueryResult
from tests.unit.test_semantic_guidance_fallback import USER
from tests.unit.test_semantic_llm_planner import _plan, setup_tool


@pytest.mark.parametrize("failure", [None, "result", "exception"])
async def test_current_thread_opt_out_suppresses_all_execution_telemetry(monkeypatch, failure):
    tool, provider, execute = setup_tool(monkeypatch, _plan())
    if failure == "result":
        execute.return_value = [QueryResult(error="private engine detail")]
    elif failure == "exception":
        execute.side_effect = RuntimeError("private engine detail")
    learning = AsyncMock(return_value=False)
    monkeypatch.setattr(assistant_repository, "learning_enabled", learning)
    context = LoopContext(user_name="alice", user=USER.copy(), thread_id="thread")
    outcome = await tool.run(
        ToolInvocation("usage", "semantic_query", {"question": "Revenue"}), context
    )
    assert outcome.ok == (failure is None)
    execute.assert_awaited_once()
    provider.complete.assert_awaited_once()
    learning.assert_awaited_once_with("thread", user_name="alice")
    agent_repository.record_semantic_usage.assert_not_awaited()


async def test_explicit_turn_opt_out_does_not_read_or_record_learning(monkeypatch):
    tool, _, _ = setup_tool(monkeypatch, _plan())
    learning = AsyncMock(return_value=True)
    monkeypatch.setattr(assistant_repository, "learning_enabled", learning)
    context = LoopContext(user_name="alice", user=USER.copy(), thread_id="thread")
    context.learning_enabled = False
    outcome = await tool.run(
        ToolInvocation("usage", "semantic_query", {"question": "Revenue"}), context
    )
    assert outcome.ok
    learning.assert_not_awaited()
    agent_repository.record_semantic_usage.assert_not_awaited()


@pytest.mark.parametrize("enabled", [True, RuntimeError("metadata unavailable")])
async def test_opt_in_records_redacted_shape_and_unavailable_setting_fails_closed(
    monkeypatch,
    enabled,
):
    tool, _, _ = setup_tool(
        monkeypatch,
        _plan(
            filters=[
                {
                    "field": "city",
                    "operator": "=",
                    "value": "private-literal",
                }
            ]
        ),
    )
    learning = AsyncMock(
        return_value=enabled if enabled is True else None,
        side_effect=None if enabled is True else enabled,
    )
    monkeypatch.setattr(assistant_repository, "learning_enabled", learning)
    context = LoopContext(user_name="alice", user=USER.copy(), thread_id="thread")
    outcome = await tool.run(
        ToolInvocation("usage", "semantic_query", {"question": "Revenue"}), context
    )
    assert outcome.ok
    if enabled is True:
        assert agent_repository.record_semantic_usage.await_args.kwargs["filter_shape"] == [
            {"field": "city", "operator": "="},
        ]
        assert "private-literal" not in str(agent_repository.record_semantic_usage.await_args)
    else:
        agent_repository.record_semantic_usage.assert_not_awaited()
