"""Neva corrects a failed lookup once, names a governed view, and never guesses one.

Found in the phase 2 live run (docs/testing/neva/phase-2-checkpoint.md): data
questions were answered from the first readable view (deliveries) and a
misspelled lookup ended the turn with the engine's message.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.messages import say
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult


def _query(outcomes):
    return EvalTool("query_execute", classification="read_only", outcomes=outcomes)


async def _run(tool, script, *, agent=False, content="Which users exist?"):
    registry = ToolRegistry()
    registry.register(tool)
    context = LoopContext(user_name="alice")
    if agent:
        context.agent_scope = {"agent": "Sales"}
    thread = AssistantThread(thread_id="lookup", user_name="alice", title="Eval")
    thread.consent.always_allow_read_only = True
    frames = [
        frame async for frame in AssistantLoop(
            provider=ScriptedProvider(script=script), registry=registry
        ).run(thread=thread, user_content=content, context=context, resolve_consent=allow)
    ]
    return TurnResult(frames=frames)


ENGINE_ERROR = "SQL error: (5502) Unknown table 'information_schema.users'."


async def test_a_failed_lookup_is_corrected_once():
    tool = _query([
        ToolOutcome(ok=False, summary="", error=ENGINE_ERROR),
        ToolOutcome(ok=True, summary="2 rows",
                    table={"columns": ["user"], "rows": [["ana"], ["bob"]]}),
    ])
    result = await _run(tool, [
        tool_call_frame("q1", sql="SELECT * FROM information_schema.users"),
        tool_call_frame("q2", sql="SHOW USERS"),
        text_frame("Nova has two accounts: ana and bob."),
    ])
    assert len(tool.runs) == 2
    assert result.finish_reason == "stop"
    assert "5502" not in result.text


async def test_a_lookup_that_keeps_failing_ends_in_plain_words():
    tool = _query([ToolOutcome(ok=False, summary="", error=ENGINE_ERROR)] * 3)
    result = await _run(tool, [
        tool_call_frame("q1", sql="SELECT * FROM information_schema.users"),
        tool_call_frame("q2", sql="SELECT * FROM mysql.user"),
        tool_call_frame("q3", sql="SELECT * FROM sys.users"),
        text_frame("unused"),
    ])
    assert len(tool.runs) <= 3
    joined = "".join(result.frames)
    assert "5502" not in joined
    assert "won't guess" in joined and say("loop.lookup_failed", "en")[:30] in joined
    assert result.finish_reason == "error"


@pytest.mark.parametrize("error", [
    "Access denied for user alice", "permission denied", "Missing privilege SELECT",
])
async def test_an_access_refusal_is_never_retried(error):
    tool = _query([ToolOutcome(ok=False, summary="", error=error)] * 2)
    result = await _run(tool, [
        tool_call_frame("q1", sql="SELECT * FROM secret"),
        tool_call_frame("q2", sql="SELECT * FROM secret_copy"),
        text_frame("unused"),
    ])
    assert len(tool.runs) == 1
    assert result.finish_reason == "error"


async def test_studio_agents_keep_their_failure_contract():
    tool = _query([ToolOutcome(ok=False, summary="", error=ENGINE_ERROR)] * 2)
    result = await _run(tool, [
        tool_call_frame("q1", sql="SELECT * FROM information_schema.users"),
        tool_call_frame("q2", sql="SHOW USERS"),
        text_frame("unused"),
    ], agent=True)
    assert len(tool.runs) == 1
    assert "tool_failed" in result.error_codes


# ── Governed views ───────────────────────────────────────────────────────────


def _models():
    return [
        {"semantic_model_id": "v-deliveries", "name": "delivery_routes", "definition": {}},
        {"semantic_model_id": "v-sales", "name": "retail_sales", "definition": {}},
    ]


@pytest.fixture
def two_views(monkeypatch):
    import app.modules.agents.semantic.access as access

    monkeypatch.setattr(access, "load_authorized_models", AsyncMock(return_value=_models()))
    import app.modules.agents.tools.semantic_query as semantic

    monkeypatch.setattr(semantic, "readable_view_summaries", lambda models, limit=16: [
        {"name": model["name"], "about": "", "metrics": [], "dimensions": []}
        for model in models
    ])


async def test_neva_does_not_guess_a_view(two_views):
    from app.modules.agents.tools.semantic_query import SemanticQueryTool, _view_choice

    tool = SemanticQueryTool()
    context = LoopContext(user_name="alice")
    assert await tool._resolve_model(context, "best month for sales", guess=False) is None
    choice = await _view_choice(context)
    assert choice is not None and choice.recoverable
    assert choice.error_class == "VIEW_CHOICE_REQUIRED"
    assert [view["name"] for view in choice.repair_context["semantic_views"]] == [
        "delivery_routes", "retail_sales",
    ]


async def test_a_named_view_is_used(two_views):
    from app.modules.agents.tools.semantic_query import SemanticQueryTool

    context = LoopContext(user_name="alice")
    context.requested_semantic_view = "retail_sales"
    model = await SemanticQueryTool()._resolve_model(context, "best month", guess=False)
    assert model["semantic_model_id"] == "v-sales"


async def test_a_studio_agent_keeps_its_first_view(two_views):
    from app.modules.agents.tools.semantic_query import SemanticQueryTool

    context = LoopContext(user_name="alice")
    context.agent_scope = {"agent": "Sales"}
    model = await SemanticQueryTool()._resolve_model(context, "best month")
    assert model["semantic_model_id"] == "v-deliveries"


def test_neva_sees_the_views_it_can_name(monkeypatch):
    import app.modules.assistant.service as service

    monkeypatch.setattr(service, "_readable_view_summaries", lambda context: [
        {"name": "retail_sales", "about": "Sales", "metrics": ["net_revenue"], "dimensions": []}
    ])
    loop = AssistantLoop(provider=ScriptedProvider(script=[]), registry=ToolRegistry())
    context = LoopContext(user_name="alice")
    context.authorized_semantic_models = [{"semantic_model_id": "v-sales"}]
    thread = AssistantThread(thread_id="views", user_name="alice", title="Eval")
    workspace = next(
        message["content"] for message in loop._build_messages(thread, "sales?", context)
        if "<WORKSPACE_CONTEXT>" in str(message.get("content"))
    )
    assert "retail_sales" in workspace and "net_revenue" in workspace
    context.agent_scope = {"agent": "Sales"}
    workspace = next(
        message["content"] for message in loop._build_messages(thread, "sales?", context)
        if "<WORKSPACE_CONTEXT>" in str(message.get("content"))
    )
    assert "retail_sales" not in workspace
