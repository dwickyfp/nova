"""A selected outcome cohort cannot become causal evidence in a Studio tool turn."""

import json
from unittest.mock import AsyncMock

from app.modules.agents.tools.intelligence import DecisionLabTool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from app.modules.intelligence import decision_lab
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import TurnResult


async def test_post_outcome_selection_stops_before_query_and_success_claim(monkeypatch):
    user = {
        "username": "analyst",
        "active_role": "ANALYST",
        "assigned_roles": ["ANALYST"],
        "session_id": "causal-session",
        "security_context_version": 4,
        "encrypted_password": "sealed-private-credential",
    }
    authorization = AsyncMock(return_value={"definition": {"ai_context": {"experiments": {
        "assignment": {
            "design": "randomized",
            "assignment": "independent",
            "protocol_reference": "reviewed-protocol",
            "unit": "assigned.unit",
            "arm": "assigned.arm",
        },
    }}}})
    query, numerical = AsyncMock(), AsyncMock()
    monkeypatch.setattr(decision_lab.intelligence_service, "authorize_semantic", authorization)
    monkeypatch.setattr(decision_lab.intelligence_service, "query", query)
    monkeypatch.setattr(decision_lab, "run_numerical", numerical)
    arguments = {
        "operation_id": "selected-outcome-cohort",
        "method": "causal_effect",
        "semantic": {"view_id": "published", "version": 2, "fingerprint": "reviewed"},
        "plan": {
            "metrics": ["outcome"],
            "dimensions": ["assigned.unit", "assigned.arm"],
            "having": [{"metric": "outcome", "operator": ">", "value": 10}],
            "limit": 100,
        },
        "value_column": "outcome",
        "experiment": "assignment",
    }
    call = tool_call_frame("causal-1", name="decision_lab", arguments=arguments)
    call["tool_calls"][0]["function"]["arguments"] = json.dumps(arguments)
    provider = ScriptedProvider([
        call,
        text_frame("The experiment establishes a supported causal effect."),
    ])
    registry = ToolRegistry()
    registry.register(DecisionLabTool())
    approvals = []

    async def consent(invocation, classification):
        approvals.append((invocation.tool_name, classification))
        return True

    context = LoopContext(user_name="analyst", user=user, role="ANALYST")
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=registry, system_prompt="test"
    ).run(
        thread=thread(), user_content="Estimate the effect for units with outcome above 10",
        context=context, resolve_consent=consent,
    )]
    result = TurnResult(frames=frames)
    assert approvals == [("decision_lab", "read_only")]
    authorization.assert_awaited_once()
    assert authorization.await_args.args[1]["session_id"] == "causal-session"
    query.assert_not_awaited()
    numerical.assert_not_awaited()
    assert result.finish_reason == "error"
    public = "".join(frames)
    assert "supported causal effect" not in public
    assert "sealed-private-credential" not in public
    assert "complete unfiltered assignment cohort" in public
