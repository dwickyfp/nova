"""Human in the loop: the turn pauses for its user and resumes with the answer.

Scripted trajectories through the real loop: what is published, what the model
reads back, what runs, and how the turn finishes for an answered, skipped,
expired, stopped and malformed request, and for Edit then run on an approval.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.modules.assistant import human_input
from app.modules.assistant.consent import ConsentApproval
from app.modules.assistant.human_input import HumanAnswer, request_human_input_tool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult, _data

QUESTION = {
    "kind": "question", "title": "Gross or net revenue?",
    "detail": "Net excludes refunds.", "choices": ["Gross revenue", "Net revenue"],
}
PLAN = {
    "intent": "raw_sql_query", "tools": ["query_execute"],
    "required_tools": [], "ml_task": None,
}


class RecordingProvider(ScriptedProvider):
    """Keeps every message list the loop sent, to read the tool results back."""

    def __init__(self, script: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(script, **kwargs)
        self.sent: list[list[dict[str, Any]]] = []
        self.tools_offered: list[list[str]] = []

    async def stream(self, **kwargs: Any):
        self.sent.append(list(kwargs.get("messages") or []))
        self.tools_offered.append([
            tool["function"]["name"] for tool in kwargs.get("tools") or []
        ])
        async for frame in super().stream(**kwargs):
            yield frame


class SqlTool(EvalTool):
    """Classifies like ``query_execute``: a write is an action, USE ROLE switches role."""

    def classification_for(self, invocation: ToolInvocation) -> str:
        sql = str(invocation.arguments.get("sql") or "").upper()
        if sql.startswith(("DELETE", "INSERT", "UPDATE")):
            return "destructive"
        if sql.startswith("USE ROLE"):
            return "session_change"
        return "read_only"


def _registry(*tools: Any) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    registry.register(request_human_input_tool)
    return registry


def _tool_results(provider: RecordingProvider) -> list[dict[str, Any]]:
    results = []
    for message in provider.sent[-1]:
        if message.get("role") == "tool":
            results.append(json.loads(message["content"]))
    return results


async def _run(
    loop: AssistantLoop,
    *,
    ask_human: Any = None,
    consent: Any = None,
) -> TurnResult:
    context = LoopContext("eval")
    context.ask_human = ask_human

    async def allow(_invocation: ToolInvocation, _classification: str) -> bool:
        return True

    frames = [
        frame
        async for frame in loop.run(
            thread=thread(), user_content="What was revenue last month?",
            context=context, resolve_consent=consent or allow,
        )
    ]
    return TurnResult(frames=frames)


def _named(result: TurnResult, name: str) -> list[dict[str, Any]]:
    return [_data(frame) for frame in result.frames if frame.startswith(f"event: {name}\n")]


@pytest.mark.asyncio
async def test_answered_question_resumes_the_same_turn_with_the_answer() -> None:
    query = EvalTool("query_execute")
    provider = RecordingProvider(
        [
            tool_call_frame("ask", name=human_input.TOOL_NAME, arguments=QUESTION),
            tool_call_frame("q1", sql="SELECT SUM(net) FROM orders"),
            text_frame("Net revenue was 10."),
        ],
        turn_plan=PLAN,
    )
    asked: list[tuple[str, dict[str, Any]]] = []

    async def ask(request_id: str, request: dict[str, Any]) -> HumanAnswer:
        asked.append((request_id, request))
        return human_input.validate_answer(request, {"text": "Net revenue"})

    result = await _run(
        AssistantLoop(provider=provider, registry=_registry(query), system_prompt="eval"),
        ask_human=ask,
    )

    assert result.finish_reason == "stop"
    assert [request["kind"] for _, request in asked] == ["question"]
    assert human_input.TOOL_NAME in provider.tools_offered[0]
    required = _named(result, "input_required")
    request_id = required[0]["request_id"]
    assert asked[0][0] == request_id  # the loop's own call id, not the model's
    assert required[0]["request"]["choices"] == ["Gross revenue", "Net revenue"]
    resolved = [r for r in _named(result, "input_resolved") if r["request_id"] == request_id]
    assert resolved == [{
        **resolved[0], "request_id": request_id, "status": "answered",
        "summary": "You answered: Net revenue", "answer": {"text": "Net revenue"},
    }]
    # The model reads the answer as the result of its own call, then goes on.
    answer = next(r for r in _tool_results(provider) if r.get("tool") == human_input.TOOL_NAME)
    assert answer["data"]["answer"] == {"text": "Net revenue"}
    assert len(query.runs) == 1
    names = result.events
    assert names.index("input_required") < names.index("input_resolved") < names.index(
        "tool_call"
    )


@pytest.mark.asyncio
async def test_without_a_person_the_tool_is_not_offered_and_a_call_is_rejected() -> None:
    provider = RecordingProvider(
        [
            tool_call_frame("ask", name=human_input.TOOL_NAME, arguments=QUESTION),
            text_frame("Assuming net revenue."),
        ],
        turn_plan=PLAN,
    )
    result = await _run(
        AssistantLoop(provider=provider, registry=_registry(EvalTool("query_execute")),
                      system_prompt="eval"),
    )
    assert result.finish_reason == "stop"
    assert human_input.TOOL_NAME not in provider.tools_offered[0]
    assert not _named(result, "input_required")
    rejected = [r for r in _tool_results(provider) if r.get("error_class") == "TOOL_NOT_ALLOWED"]
    assert rejected


@pytest.mark.asyncio
async def test_stop_while_waiting_cancels_the_request_and_the_turn() -> None:
    provider = RecordingProvider(
        [tool_call_frame("ask", name=human_input.TOOL_NAME, arguments=QUESTION), text_frame()],
        turn_plan=PLAN,
    )

    async def ask(_request_id: str, _request: dict[str, Any]) -> None:
        return None  # the broker resolves a stopped run's waiters with None

    query = EvalTool("query_execute")
    result = await _run(
        AssistantLoop(provider=provider, registry=_registry(query), system_prompt="eval"),
        ask_human=ask,
    )
    assert result.finish_reason == "cancelled"
    assert [r["status"] for r in _named(result, "input_resolved")] == ["cancelled"]
    assert query.runs == []


@pytest.mark.asyncio
async def test_an_unanswered_request_expires_and_the_model_states_its_assumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(human_input, "INPUT_TIMEOUT_SECONDS", 0.01)
    provider = RecordingProvider(
        [
            tool_call_frame("ask", name=human_input.TOOL_NAME, arguments=QUESTION),
            text_frame("Assuming net revenue: 10."),
        ],
        turn_plan=PLAN,
    )

    async def ask(_request_id: str, _request: dict[str, Any]) -> None:
        await asyncio.Event().wait()

    result = await _run(
        AssistantLoop(provider=provider, registry=_registry(EvalTool("query_execute")),
                      system_prompt="eval"),
        ask_human=ask,
    )
    assert result.finish_reason == "stop"
    assert [r["status"] for r in _named(result, "input_resolved")] == ["expired"]
    expired = next(r for r in _tool_results(provider) if r.get("tool") == human_input.TOOL_NAME)
    assert expired["data"]["status"] == "expired"


@pytest.mark.asyncio
async def test_a_malformed_request_goes_back_to_the_model_without_asking() -> None:
    provider = RecordingProvider(
        [
            tool_call_frame("ask", name=human_input.TOOL_NAME,
                            arguments={"kind": "choice", "title": "Pick", "options": [
                                {"title": "Only one"},
                            ]}),
            text_frame("Done."),
        ],
        turn_plan=PLAN,
    )
    asked: list[str] = []

    async def ask(request_id: str, _request: dict[str, Any]) -> None:
        asked.append(request_id)

    result = await _run(
        AssistantLoop(provider=provider, registry=_registry(EvalTool("query_execute")),
                      system_prompt="eval"),
        ask_human=ask,
    )
    assert asked == []
    assert not _named(result, "input_required")
    refused = next(r for r in _tool_results(provider) if r.get("tool") == human_input.TOOL_NAME)
    assert refused["error_class"] == "INVALID_HUMAN_INPUT"


@pytest.mark.asyncio
async def test_a_read_only_call_runs_without_an_approval() -> None:
    query = SqlTool("query_execute")
    provider = RecordingProvider(
        [tool_call_frame("q1", sql="SELECT 1"), text_frame("One.")], turn_plan=PLAN,
    )
    asked: list[str] = []

    async def consent(_invocation: ToolInvocation, classification: str) -> bool:
        asked.append(classification)
        return False

    result = await _run(
        AssistantLoop(provider=provider, registry=_registry(query), system_prompt="eval"),
        consent=consent,
    )
    assert result.finish_reason == "stop"
    assert asked == []
    assert not _named(result, "input_required")
    assert [run.arguments["sql"] for run in query.runs] == ["SELECT 1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sql", "classification", "reason", "title"),
    [
        ("DELETE FROM orders WHERE id = 1", "destructive", "changes_data", "Allow this action?"),
        ("USE ROLE finance", "session_change", "changes_role",
         "Allow this change to your session?"),
    ],
)
async def test_an_action_is_published_as_an_approval_and_settles_with_the_decision(
    sql: str, classification: str, reason: str, title: str,
) -> None:
    query = SqlTool("query_execute")
    provider = RecordingProvider(
        [tool_call_frame("q1", sql=sql), text_frame("Nothing changed.")],
        turn_plan=PLAN,
    )

    async def deny(_invocation: ToolInvocation, _classification: str) -> bool:
        return False

    result = await _run(
        AssistantLoop(provider=provider, registry=_registry(query), system_prompt="eval",
                      iterative=True),
        consent=deny,
    )
    approval = _named(result, "input_required")[0]
    assert approval["kind"] == "approval"
    assert approval["tool_call_id"] == approval["request_id"]
    request = approval["request"]
    assert request == {
        "kind": "approval", "title": title, "classification": classification,
        "reason": reason, "role": "", "action": sql,
    }
    assert [r["status"] for r in _named(result, "input_resolved")] == ["denied"]
    assert [r["summary"] for r in _named(result, "input_resolved")] == [
        "You denied this action"
    ]
    assert query.runs == []


@pytest.mark.asyncio
async def test_requests_per_turn_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(human_input, "MAX_REQUESTS_PER_TURN", 1)
    second = {**QUESTION, "title": "And which month?"}
    provider = RecordingProvider(
        [
            tool_call_frame("ask1", name=human_input.TOOL_NAME, arguments=QUESTION),
            tool_call_frame("ask2", name=human_input.TOOL_NAME, arguments=second),
            text_frame("Net revenue for the latest month."),
        ],
        turn_plan=PLAN,
    )

    async def ask(_request_id: str, request: dict[str, Any]) -> HumanAnswer:
        return human_input.validate_answer(request, {"text": "Net revenue"})

    result = await _run(
        AssistantLoop(provider=provider, registry=_registry(EvalTool("query_execute")),
                      system_prompt="eval"),
        ask_human=ask,
    )
    assert len(_named(result, "input_required")) == 1
    limited = [r for r in _tool_results(provider) if r.get("error_class") == "HUMAN_INPUT_LIMIT"]
    assert len(limited) == 1


@pytest.mark.asyncio
async def test_a_clarification_turn_asks_with_the_card_and_then_runs_the_data_step() -> None:
    from app.modules.assistant.tools import ToolOutcome
    from tests.benchmark.studio_accuracy.model import bench_model

    semantic = EvalTool(
        "semantic_query",
        parameters={"type": "object", "properties": {"question": {"type": "string"}},
                    "required": ["question"]},
        outcomes=[ToolOutcome(
            ok=True, summary="1 row",
            table={"columns": ["net_revenue"], "rows": [[10]]},
            data={"semantic_plan": {"metrics": ["net_revenue"]}, "sql": "SELECT 1"},
        )],
    )
    provider = RecordingProvider(
        [
            tool_call_frame("ask", name=human_input.TOOL_NAME, arguments=QUESTION),
            tool_call_frame("s1", name="semantic_query", arguments={"question": "Net revenue"}),
            text_frame("Net revenue was 10."),
        ],
        turn_plan={"intent": "clarification", "tools": [], "required_tools": [], "ml_task": None},
    )

    async def ask(_request_id: str, request: dict[str, Any]) -> HumanAnswer:
        return human_input.validate_answer(request, {"text": "Net revenue"})

    context = LoopContext("alice")
    context.ask_human = ask
    context.agent_scope = {"agent": "Sales"}
    context.authorized_semantic_models = [{"_scoped_ir": bench_model()}]

    async def allow(_invocation: ToolInvocation, _classification: str) -> bool:
        return True

    frames = [
        frame
        async for frame in AssistantLoop(
            provider=provider, registry=_registry(semantic), system_prompt="eval",
        ).run(
            thread=thread(), user_content="Show me revenue, gross or net?",
            context=context, resolve_consent=allow,
        )
    ]
    result = TurnResult(frames=frames)
    # Only the card is offered while the turn is a question back to the user...
    assert provider.tools_offered[0] == [human_input.TOOL_NAME]
    assert _named(result, "input_resolved")[0]["status"] == "answered"
    # ...and once answered the same turn runs the governed data step.
    assert "semantic_query" in provider.tools_offered[1]
    assert len(semantic.runs) == 1
    assert result.finish_reason == "stop"
    assert any(step.get("clarified") == "semantic_analytics" for step in context.steps or [])


@pytest.mark.asyncio
async def test_a_skipped_clarification_stays_a_clarification() -> None:
    provider = RecordingProvider(
        [
            tool_call_frame("ask", name=human_input.TOOL_NAME, arguments=QUESTION),
            text_frame("Tell me whether you mean gross or net revenue."),
        ],
        turn_plan={"intent": "clarification", "tools": [], "required_tools": [], "ml_task": None},
    )

    async def ask(_request_id: str, _request: dict[str, Any]) -> HumanAnswer:
        return human_input.declined()

    async def allow(_invocation: ToolInvocation, _classification: str) -> bool:
        return True

    context = LoopContext("alice")
    context.ask_human = ask
    context.agent_scope = {"agent": "Sales"}
    frames = [
        frame
        async for frame in AssistantLoop(
            provider=provider, registry=_registry(EvalTool("semantic_query")),
            system_prompt="eval",
        ).run(thread=thread(), user_content="Revenue?", context=context, resolve_consent=allow)
    ]
    result = TurnResult(frames=frames)
    assert [r["status"] for r in _named(result, "input_resolved")] == ["declined"]
    assert provider.tools_offered[-1] == [human_input.TOOL_NAME]
