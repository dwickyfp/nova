from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.core.config import settings
from app.modules.agents.quality_scoring import Assertion, score_assertion
from app.modules.assistant import provider as provider_module
from app.modules.assistant.provider import AssistantProviderClient, ProviderConfig
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult

PLAN = {"intent": "direct_answer", "tools": [], "required_tools": [], "ml_task": None}


def observation(context):
    return next(step for step in context.steps if step["kind"] == "quality_observation")


def budget(record, **limits):
    return score_assertion(Assertion(scorer="efficiency", expected=limits), record).status


@pytest.fixture
def quality_enabled(monkeypatch):
    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", True)
    monkeypatch.setattr("app.modules.assistant.decision.decision_session",
                        AsyncMock(return_value=None))


def http_provider(monkeypatch, responses, *, max_attempts=1):
    requests = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        response = responses.pop(0)
        if isinstance(response, httpx.Response):
            return response
        if isinstance(response, int):
            return httpx.Response(response)
        content, usage = response
        if body.get("stream"):
            chunks = [
                {"choices": [{"delta": {"content": content}}]},
                {"choices": [], "usage": usage},
            ]
            return httpx.Response(200, text="".join(
                f"data: {json.dumps(chunk)}\n\n" for chunk in chunks
            ) + "data: [DONE]\n\n")
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": usage,
        })

    @asynccontextmanager
    async def client(**kwargs):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True,
        ) as value:
            yield value

    monkeypatch.setattr(provider_module, "guarded_async_client", client)
    monkeypatch.setattr(provider_module, "resolve_and_validate_url", lambda value: None)
    value = AssistantProviderClient(max_attempts=max_attempts, retry_base_seconds=0)
    value.resolve = AsyncMock(return_value=ProviderConfig(
        provider_id="quality", model="fixture", endpoint="https://fixture.invalid/chat",
        api_key="private-credential",
    ))
    return value, requests


async def run(provider, *, registry=None, context=None, consent=None, **kwargs):
    context = context or LoopContext(user_name="alice", agent_id="agent")
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=registry or ToolRegistry(), max_iterations=4,
        system_prompt="Answer the request.",
    ).run(
        thread=thread(), user_content="Explain the result. private-user-text",
        context=context, resolve_consent=consent or AsyncMock(return_value=True), **kwargs,
    )]
    return context, frames


async def test_real_planner_repair_and_response_are_counted_without_extra_calls(
    monkeypatch, quality_enabled,
):
    provider, requests = http_provider(monkeypatch, [
        ("invalid plan", {"prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 90}),
        (json.dumps(PLAN), {"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140}),
        ("The result is ready.", {"prompt_tokens": 40, "completion_tokens": 10,
                                  "total_tokens": 50}),
    ])
    context, frames = await run(provider)
    assert TurnResult(frames=frames).finish_reason == "stop"
    assert len(requests) == 3
    assert len([step for step in context.steps if step["kind"] == "provider"]) == 1
    record = observation(context)
    assert record["counts"] == {
        "tool_calls": 0, "participants": 1, "provider_calls": 3,
        "total_tokens": 280, "context_tokens": 120, "metadata_reads": 0,
    }
    assert record["measurement"]["provider_invocations"] == 3
    assert record["measurement"]["reported_total_tokens"] == 280
    assert record["measurement"]["reported_peak_prompt_tokens"] == 120
    assert budget(record, provider_calls=2) == "fail"
    assert budget(record, provider_calls=3, total_tokens=280, context_tokens=120) == "pass"
    assert budget(record, tool_calls=0, participants=1) == "pass"
    assert context.usage == {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50}
    assert record["measurement"]["estimated_peak_context_tokens"] > 0
    safe_measurement = json.dumps({"counts": record["counts"],
                                   "measurement": record["measurement"]})
    for private in ("private-credential", "private-user-text", "invalid plan", "Explain"):
        assert private not in safe_measurement


async def test_transport_retry_cannot_pass_a_one_call_budget(monkeypatch, quality_enabled):
    provider, requests = http_provider(monkeypatch, [
        503,
        (json.dumps(PLAN), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        ("Ready.", {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}),
    ], max_attempts=2)
    context, _ = await run(provider)
    assert len(requests) == 3
    record = observation(context)
    assert record["measurement"]["provider_invocations"] == 2
    assert record["measurement"]["reported_total_tokens"] == 40
    assert record["counts"]["provider_calls"] == 3
    assert "total_tokens" not in record["counts"]
    assert budget(record, provider_calls=1) == "fail"
    assert budget(record, total_tokens=40) == "unavailable"
    assert "provider_calls" not in record["measurement"]["unavailable"]


@pytest.mark.parametrize("usage", [None, {}, {"total_tokens": True}, {"total_tokens": -1}])
async def test_missing_or_invalid_usage_is_not_a_zero_token_pass(
    monkeypatch, quality_enabled, usage,
):
    provider, requests = http_provider(monkeypatch, [
        (json.dumps(PLAN), usage),
        ("Ready.", {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}),
    ])
    context, _ = await run(provider)
    record = observation(context)
    assert len(requests) == 2
    assert record["measurement"]["provider_invocations"] == 2
    assert record["measurement"]["reported_total_tokens"] == 25
    assert record["measurement"]["reported_peak_prompt_tokens"] == 20
    assert "provider_usage_missing" in record["measurement"]["unavailable"]["total_tokens"]
    assert budget(record, total_tokens=25) == "unavailable"
    assert budget(record, context_tokens=20) == "unavailable"


async def test_failure_before_http_dispatch_proves_zero_requests(monkeypatch, quality_enabled):
    provider, requests = http_provider(monkeypatch, [])
    provider._validate_endpoint = AsyncMock(side_effect=RuntimeError("private-endpoint"))
    context, frames = await run(provider)
    assert not requests
    assert TurnResult(frames=frames).finish_reason == "planning_failed"
    assert observation(context)["counts"]["provider_calls"] == 0
    assert budget(observation(context), provider_calls=0) == "pass"


async def test_failed_stream_does_not_treat_planner_usage_as_complete(
    monkeypatch, quality_enabled,
):
    provider, requests = http_provider(monkeypatch, [
        (json.dumps(PLAN), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        503,
    ])
    context, frames = await run(provider)
    assert len(requests) == 2
    assert TurnResult(frames=frames).finish_reason == "error"
    record = observation(context)
    assert record["measurement"]["provider_invocations"] == 2
    assert record["measurement"]["reported_total_tokens"] == 15
    assert record["counts"]["provider_calls"] == 2
    assert budget(record, provider_calls=1) == "fail"
    assert budget(record, total_tokens=15) == "unavailable"


async def test_single_attempt_redirect_does_not_claim_one_transport_request(
    monkeypatch, quality_enabled,
):
    provider, requests = http_provider(monkeypatch, [
        httpx.Response(307, headers={"location": "https://fixture.invalid/redirect"}),
        (json.dumps(PLAN), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        ("Ready.", {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}),
    ])
    context, _ = await run(provider)
    assert len(requests) == 3
    record = observation(context)
    assert record["measurement"]["provider_invocations"] == 2
    assert record["counts"]["provider_calls"] == 3
    assert budget(record, provider_calls=2) == "fail"


async def test_resolution_failure_proves_zero_provider_requests(monkeypatch, quality_enabled):
    provider, requests = http_provider(monkeypatch, [])
    provider.resolve = AsyncMock(side_effect=RuntimeError("Unavailable"))
    context, frames = await run(provider)
    assert not requests
    assert TurnResult(frames=frames).finish_reason == "error"
    assert observation(context)["counts"] == {
        "provider_calls": 0, "tool_calls": 0, "total_tokens": 0,
        "context_tokens": 0, "participants": 1, "metadata_reads": 0,
    }
    assert budget(observation(context), provider_calls=0, total_tokens=0) == "pass"


async def test_decision_workload_and_refinement_requests_cannot_be_ignored(
    monkeypatch, quality_enabled,
):
    from app.modules.assistant.decision import DecisionSession
    from tests.unit.test_decision_runtime import settings as decision_settings

    decision = DecisionSession(decision_settings())

    async def judgment(payload, timeout):
        answers = {}
        for key, question in payload["questions"].items():
            criteria = question["criteria"]
            answers[key] = {
                "type": "choice", "choice": "none" if "none" in criteria else "support",
                "confidence": 0.99,
                "probabilities": {
                    label: 1 if label == ("none" if "none" in criteria else "support") else 0
                    for label in criteria
                },
            }
        return {"answers": answers, "model": "fixture"}

    decision._post = AsyncMock(side_effect=judgment)
    monkeypatch.setattr("app.modules.assistant.decision.decision_session",
                        AsyncMock(return_value=decision))
    registry = ToolRegistry()
    registry.register(EvalTool("load_skill"))
    registry.register(EvalTool("search_knowledge"))
    provider = ScriptedProvider([text_frame("Ready.")], turn_plan={
        "intent": "capability_help", "tools": registry.names(), "required_tools": [],
    })
    context, _ = await run(provider, registry=registry)
    assert decision._post.await_count == 2
    assert decision.calls == 2
    assert provider.calls == 1
    record = observation(context)
    assert "decision_requests_unmeasured" in record["measurement"]["unavailable"][
        "provider_calls"
    ]
    assert budget(record, provider_calls=1) == "unavailable"


@pytest.mark.parametrize("allowed", [False, True])
async def test_tool_dispatch_counts_exclude_denials_and_do_not_guess_nested_cost(
    monkeypatch, quality_enabled, allowed,
):
    tool = EvalTool("query_execute", classification="destructive")
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider([
        tool_call_frame("query", arguments={"sql": "DELETE FROM facts"}),
        text_frame("Ready."),
    ])
    context, _ = await run(provider, registry=registry, consent=AsyncMock(return_value=allowed))
    record = observation(context)
    assert len(tool.runs) == int(allowed)
    assert record["counts"]["tool_calls"] == int(allowed)
    assert budget(record, tool_calls=0) == ("fail" if allowed else "pass")
    if allowed:
        assert "nested_tool_requests_unmeasured" in record["measurement"]["unavailable"][
            "provider_calls"
        ]
        assert budget(record, participants=1) == "unavailable"


async def test_collaboration_does_not_guess_participant_count(quality_enabled):
    context = LoopContext(user_name="alice", agent_id="agent", collaboration_root=True)
    context, _ = await run(ScriptedProvider([text_frame("Ready.")]), context=context)
    assert budget(observation(context), participants=1) == "unavailable"


@pytest.mark.parametrize("duplicate", [False, True])
async def test_prefetch_and_cache_hits_do_not_double_count_dispatches(quality_enabled, duplicate):
    tool = EvalTool("search_knowledge")
    registry = ToolRegistry()
    registry.register(tool)
    first = tool_call_frame("first", name="search_knowledge", sql="SELECT 1")
    second = tool_call_frame("second", name="search_knowledge",
                             sql="SELECT 1" if duplicate else "SELECT 2")
    provider = ScriptedProvider([
        {**first, "tool_calls": [*first["tool_calls"], *second["tool_calls"]]},
        text_frame("Ready."),
    ], turn_plan={"intent": "capability_help", "tools": registry.names(), "required_tools": []})
    context = LoopContext(user_name="alice", agent_id="agent")
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=registry, iterative=True,
    ).run(
        thread=thread(read_only_grant=True), user_content="Find the available guidance.",
        context=context, resolve_consent=AsyncMock(return_value=True),
    )]
    assert TurnResult(frames=frames).finish_reason == "stop"
    assert len(tool.runs) == (1 if duplicate else 2)
    assert observation(context)["counts"]["tool_calls"] == len(tool.runs)
    assert budget(observation(context), tool_calls=1) == ("pass" if duplicate else "fail")


async def test_delayed_translation_keeps_request_and_token_totals_unavailable(
    monkeypatch, quality_enabled,
):
    plan = {"intent": "raw_sql_query", "tools": ["query_execute"],
            "required_tools": ["query_execute"], "ml_task": None}
    provider, requests = http_provider(monkeypatch, [
        (json.dumps(plan), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    ])
    registry = ToolRegistry()
    registry.register(EvalTool("query_execute"))
    translate = Mock()
    monkeypatch.setattr("app.modules.assistant.service._start_translation", translate)
    monkeypatch.setattr("app.modules.assistant.service._turn_language", lambda context: "fr")
    monkeypatch.setattr("app.modules.assistant.messages.known", lambda language: False)
    context, frames = await run(provider, registry=registry, cancelled=lambda: True)
    assert len(requests) == 1
    assert TurnResult(frames=frames).finish_reason == "cancelled"
    translate.assert_called_once()
    record = observation(context)
    assert "background_translation" in record["measurement"]["unavailable"]["provider_calls"]
    assert budget(record, provider_calls=1, total_tokens=15) == "unavailable"


async def test_resumed_attempt_does_not_reset_required_count_budgets(quality_enabled):
    context, _ = await run(ScriptedProvider([text_frame("Ready.")]), resume_state={})
    record = observation(context)
    assert budget(record, tool_calls=0) == "unavailable"
    assert budget(record, provider_calls=1) == "unavailable"


async def test_disabled_quality_preserves_provider_path_and_public_trace(monkeypatch):
    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", False)
    monkeypatch.setattr("app.modules.assistant.decision.decision_session",
                        AsyncMock(return_value=None))
    provider, requests = http_provider(monkeypatch, [
        (json.dumps(PLAN), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        ("Ready.", {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}),
    ])
    context, _ = await run(provider)
    assert len(requests) == 2
    assert context.quality_measurements is None
    assert not any(step["kind"] == "quality_observation" for step in context.steps)
    assert len([step for step in context.steps if step["kind"] == "provider"]) == 1


async def test_a_turn_near_its_token_allowance_answers_instead_of_calling_again():
    tool = EvalTool("search_knowledge")
    registry = ToolRegistry()
    registry.register(tool)
    usage = {"prompt_tokens": 700, "completion_tokens": 50, "total_tokens": 750}

    class Provider(ScriptedProvider):
        """Calls the tool for as long as it is offered one."""

        async def stream(self, *, messages, tools=None, provider=None):
            self.calls += 1
            if tools:
                yield ("message", {**tool_call_frame(
                    f"c{self.calls}", name="search_knowledge", sql=f"SELECT {self.calls}",
                ), "usage": usage})
            else:
                yield ("message", {**text_frame("Here is what I found."), "usage": usage})

    provider = Provider(
        [], turn_plan={"intent": "capability_help", "tools": registry.names(),
                       "required_tools": []},
    )
    context = LoopContext(user_name="alice", agent_id="agent")
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=registry, iterative=True, spend_limit=1000,
    ).run(
        thread=thread(read_only_grant=True), user_content="Find the available guidance.",
        context=context, resolve_consent=AsyncMock(return_value=True),
    )]
    # 750 of 1,000 tokens after the first call: the next model call is the answer.
    assert len(tool.runs) == 1
    assert TurnResult(frames=frames).finish_reason == "stop"
