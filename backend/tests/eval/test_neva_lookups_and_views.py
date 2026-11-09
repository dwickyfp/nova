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


# ── Checks the model skipped ─────────────────────────────────────────────────


def _plan(required):
    return {"intent": "sql_authoring", "tools": list(required), "required_tools": list(required),
            "skills": [], "ml_task": None}


async def _run_plan(tools, script, required, *, agent=False):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    context = LoopContext(user_name="alice")
    if agent:
        context.agent_scope = {"agent": "Sales"}
    frames = [
        frame async for frame in AssistantLoop(
            provider=ScriptedProvider(script=script, turn_plan=_plan(required)),
            registry=registry,
        ).run(
            thread=AssistantThread(thread_id="checks", user_name="alice", title="Eval"),
            user_content="Write me a query for total sales per month",
            context=context, resolve_consent=allow,
        )
    ]
    return TurnResult(frames=frames)


SQL_DRAFT = "Use this:\n```sql\nSELECT 1\n```"


async def test_neva_checks_the_sql_its_draft_proposes():
    validate = EvalTool("validate_sql", parameters={
        "type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"],
    })
    result = await _run_plan([validate], [
        text_frame(SQL_DRAFT), text_frame(SQL_DRAFT), text_frame(SQL_DRAFT),
    ], ["validate_sql"])
    assert [run.arguments for run in validate.runs] == [{"sql": "SELECT 1"}]
    assert result.finish_reason == "stop"
    assert "SELECT 1" in "".join(result.frames)


async def test_neva_looks_up_the_guide_the_model_skipped():
    guide = EvalTool("search_knowledge", parameters={
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
    })
    result = await _run_plan([guide], [
        text_frame("ACCOUNTADMIN is protected."), text_frame("ACCOUNTADMIN is protected."),
        text_frame("ACCOUNTADMIN is protected and cannot be deleted."),
    ], ["search_knowledge"])
    assert len(guide.runs) == 1
    assert guide.runs[0].arguments["query"].startswith("Write me a query")
    assert result.finish_reason == "stop"


async def test_a_draft_without_sql_has_nothing_to_check():
    validate = EvalTool("validate_sql")
    result = await _run_plan([validate], [
        text_frame("Which table holds your sales?"), text_frame("Which table holds your sales?"),
        text_frame("Which table holds your sales?"),
    ], ["validate_sql"])
    assert validate.runs == []
    assert result.finish_reason == "stop"


async def test_studio_agents_still_stop_when_a_check_is_skipped():
    validate = EvalTool("validate_sql")
    result = await _run_plan([validate], [
        text_frame(SQL_DRAFT), text_frame(SQL_DRAFT), text_frame(SQL_DRAFT),
    ], ["validate_sql"], agent=True)
    assert validate.runs == []
    assert result.finish_reason == "required_capability_incomplete"


# ── Budget follows the kind of turn ─────────────────────────────────────────


async def _looping(intent, scale):
    tool = EvalTool("search_knowledge", parameters={
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
    })
    registry = ToolRegistry()
    registry.register(tool)
    context = LoopContext(user_name="alice")
    context.scale_budget = scale
    script = [tool_call_frame(f"k{index}", name="search_knowledge",
                              arguments={"query": f"guide {index}"}) for index in range(12)]
    frames = [frame async for frame in AssistantLoop(
        provider=ScriptedProvider(script=script, turn_plan={
            "intent": intent, "tools": ["search_knowledge"], "required_tools": [],
            "skills": [], "ml_task": None,
        }),
        registry=registry, max_iterations=10, max_calls_per_tool=20,
    ).run(
        thread=AssistantThread(thread_id="budget", user_name="alice", title="Eval"),
        user_content="Explain stages", context=context, resolve_consent=allow,
    )]
    return tool, TurnResult(frames=frames), context


async def test_a_quick_turn_gets_the_smaller_ceiling():
    from app.modules.assistant.router import _neva_budget

    tool, _result, context = await _looping("capability_help", _neva_budget)
    assert len(tool.runs) <= 8
    assert any(step.get("budget") == "scaled" for step in context.steps or [])


async def test_an_sql_turn_keeps_the_loop_s_own_ceiling():
    from app.modules.assistant.router import _neva_budget

    tool, _result, context = await _looping("sql_authoring", _neva_budget)
    assert len(tool.runs) == 10
    assert not any(step.get("budget") == "scaled" for step in context.steps or [])


async def test_scaling_can_never_raise_a_limit():
    tool, _result, _context = await _looping("capability_help", lambda _route: (40, 600.0))
    assert len(tool.runs) == 10


# ── Page work: SQL for the person's editor ──────────────────────────────────


def _workspace(text="SELECT order_date FROM NOVA_RETAIL_FIELD.sales_orders"):
    from app.modules.assistant.app_context import NevaAppContext

    return NevaAppContext.model_validate({
        "version": 1,
        "surface": {"id": "workspace.sql", "route": "/workspaces"},
        "capabilities": ["editor.propose_sql"],
        "editor": {"language": "sql", "text": text},
    })


async def _run_page(tools, script, required, app):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    context = LoopContext(user_name="alice")
    context.app_context = app
    frames = [frame async for frame in AssistantLoop(
        provider=ScriptedProvider(script=script, turn_plan=_plan(required)), registry=registry,
    ).run(
        thread=AssistantThread(thread_id="page", user_name="alice", title="Eval"),
        user_content="Make this query faster and put it in my editor",
        context=context, resolve_consent=allow,
    )]
    return TurnResult(frames=frames)


def _page_tools():
    validate = EvalTool("validate_sql", parameters={
        "type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"],
    })
    propose = EvalTool("invoke_client_capability", parameters={
        "type": "object",
        "properties": {"capability": {"type": "string"},
                       "args": {"type": "object", "additionalProperties": True}},
        "required": ["capability", "args"],
    })
    catalog = EvalTool("catalog_search")
    return validate, propose, catalog


async def test_checked_sql_goes_into_the_editor_as_an_added_suggestion():
    validate, propose, catalog = _page_tools()
    draft = "Faster:\n```sql\nSELECT order_date FROM NOVA_RETAIL_FIELD.sales_orders LIMIT 100\n```"
    await _run_page([validate, propose, catalog], [text_frame(draft)] * 6,
                    ["catalog_search", "validate_sql", "invoke_client_capability"], _workspace())
    assert catalog.runs == []  # the SQL being changed is on screen
    assert [run.arguments["sql"] for run in validate.runs] == [
        "SELECT order_date FROM NOVA_RETAIL_FIELD.sales_orders LIMIT 100"]
    assert len(propose.runs) == 1
    assert propose.runs[0].arguments == {
        "capability": "editor.propose_sql",
        "args": {"sql": "SELECT order_date FROM NOVA_RETAIL_FIELD.sales_orders LIMIT 100",
                 "mode": "insert"},
    }


async def test_a_question_back_is_an_answer_not_a_refusal():
    validate, propose, catalog = _page_tools()
    result = await _run_page([validate, propose, catalog],
                             [text_frame("Which date range should it cover?")] * 4,
                             ["catalog_search", "validate_sql", "invoke_client_capability"],
                             _workspace())
    assert result.finish_reason == "stop"
    assert propose.runs == [] and validate.runs == []
    assert "won't guess" not in "".join(result.frames)


async def test_a_page_without_the_editor_action_gets_no_proposal():
    from app.modules.assistant.app_context import NevaAppContext

    validate, propose, catalog = _page_tools()
    app = NevaAppContext.model_validate({
        "version": 1, "surface": {"id": "roles.list", "route": "/roles"},
        "capabilities": ["surface.refresh"],
    })
    draft = "```sql\nSELECT 1\n```"
    await _run_page([validate, propose, catalog], [text_frame(draft)] * 6,
                    ["validate_sql", "invoke_client_capability"], app)
    assert propose.runs == []


async def test_a_suggested_edit_is_explained_in_the_person_s_words():
    from app.modules.assistant.tools.client_capability import InvokeClientCapabilityTool

    validate, _propose, catalog = _page_tools()
    sql = "SELECT order_date FROM NOVA_RETAIL_FIELD.sales_orders LIMIT 100"
    explanation = "Saya membatasi hasilnya ke 100 baris; tinjau perubahannya di editor."
    result = await _run_page(
        [validate, InvokeClientCapabilityTool(), catalog],
        [tool_call_frame("v1", name="validate_sql", arguments={"sql": sql}),
         tool_call_frame("p1", name="invoke_client_capability", arguments={
             "capability": "editor.propose_sql", "args": {"sql": sql, "mode": "insert"}}),
         text_frame(explanation)],
        ["validate_sql", "invoke_client_capability"], _workspace(),
    )
    events = [frame.split("\n", 1)[0] for frame in result.frames]
    assert "event: client_action" in events
    text = "".join(frame for frame in result.frames if frame.startswith("event: text_delta"))
    assert "membatasi" in text
    # Neva's own instruction to the model never reaches the person.
    assert "person's language" not in text and "nothing has run" not in text
    assert result.finish_reason == "stop"
