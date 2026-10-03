from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.modules.agents.quality_scoring import (
    Assertion,
    PromotionGates,
    score_assertion,
)
from app.modules.assistant import decision as decision_module
from app.modules.assistant import measurements as module
from app.modules.assistant import provider as provider_module
from app.modules.assistant.measurements import (
    AttemptMeasurements,
    current_measurements,
    measurement_scope,
    metadata_borrows_observed,
    metadata_execution_scope,
    metadata_reads_observed,
    record_metadata_borrow,
    record_metadata_read,
    record_metadata_read_failure,
)
from app.modules.assistant.provider import AssistantProviderClient, ProviderConfig
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import EvalTool
from tests.unit.test_assistant_quality_measurements import (
    budget,
    http_provider,
    observation,
    run,
)
from tests.unit.test_decision_runtime import QUESTIONS, answer
from tests.unit.test_decision_runtime import settings as decision_settings

CONFIG = ProviderConfig("owner", "fixture", "https://fixture.invalid/v1", "private-key")
USAGE = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}


@pytest.fixture
def quality_enabled(monkeypatch):
    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", True)
    monkeypatch.setattr(decision_module, "decision_session", AsyncMock(return_value=None))


def native_provider(monkeypatch, handler, *, max_attempts=1):
    @asynccontextmanager
    async def client(**kwargs):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        ) as value:
            yield value

    monkeypatch.setattr(provider_module, "guarded_async_client", client)
    monkeypatch.setattr(provider_module, "resolve_and_validate_url", lambda endpoint: None)
    return AssistantProviderClient(max_attempts=max_attempts, retry_base_seconds=0)


async def test_concurrent_attempts_count_native_retries_without_cross_request_leak(monkeypatch):
    seen = {"one": 0, "two": 0}
    both_started = asyncio.Event()

    async def handler(request):
        label = json.loads(request.content)["messages"][0]["content"]
        seen[label] += 1
        if all(seen.values()):
            both_started.set()
        await both_started.wait()
        if label == "two" and seen[label] < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "private-answer"}}],
                "usage": USAGE,
            },
        )

    provider = native_provider(monkeypatch, handler, max_attempts=3)
    scopes = [AttemptMeasurements(), AttemptMeasurements()]

    async def task(label, measured):
        with measurement_scope(measured):
            await provider.complete(messages=[{"role": "user", "content": label}], provider=CONFIG)
        assert current_measurements() is None

    await asyncio.gather(
        *(task(label, measured) for label, measured in zip(seen, scopes, strict=True))
    )
    assert [measured.provider_dispatches for measured in scopes] == [1, 3]
    assert [measured.provider_invocations for measured in scopes] == [1, 1]
    assert scopes[0].observation(None)[0]["total_tokens"] == 12
    assert "total_tokens" not in scopes[1].observation(None)[0]
    assert scopes[1].observation(None)[0]["provider_calls"] == 3
    assert current_measurements() is None
    assert "private" not in json.dumps([measured.observation(None) for measured in scopes])


@pytest.mark.parametrize("stream", [False, True])
async def test_failed_transport_dispatches_and_retries_have_exact_count(monkeypatch, stream):
    calls = 0

    async def failed(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("private-endpoint-and-key", request=request)

    provider = native_provider(monkeypatch, failed, max_attempts=2)
    measured = AttemptMeasurements()
    with measurement_scope(measured), pytest.raises(provider_module.AssistantProviderError):
        if stream:
            async for _frame in provider.stream(messages=[], provider=CONFIG):
                pass
        else:
            await provider.complete(messages=[], provider=CONFIG)
    counts, detail = measured.observation(None)
    assert counts["provider_calls"] == calls == 2
    assert "total_tokens" not in counts
    assert detail["unavailable"]["total_tokens"] == ["provider_usage_missing"]


async def test_native_nested_tool_calls_are_observed_without_claiming_unknown_tool_coverage(
    monkeypatch,
    quality_enabled,
):
    nested, requests = http_provider(monkeypatch, [("inner", USAGE), ("inner", USAGE)])
    registry = ToolRegistry()
    tool = EvalTool("load_skill")

    async def execute(invocation, context):
        for _ in range(2):
            await nested.complete(messages=[], provider=CONFIG)
        return ToolOutcome(ok=True, summary="Ready.")

    tool.run = execute
    registry.register(tool)
    provider = ScriptedProvider(
        [
            tool_call_frame("nested", name="load_skill", arguments={}),
            text_frame("Ready."),
        ],
        turn_plan={"intent": "capability_help", "tools": ["load_skill"], "required_tools": []},
    )
    context, _ = await run(provider, registry=registry)
    record = observation(context)
    assert len(requests) == 2
    assert record["counts"]["tool_calls"] == 1
    assert record["measurement"]["observed_counts"]["provider_calls"] == 2
    assert record["measurement"]["reported_total_tokens"] == 24
    assert budget(record, provider_calls=2) == "unavailable"
    assert (
        "nested_tool_requests_unmeasured" in record["measurement"]["unavailable"]["provider_calls"]
    )


async def test_sse_consumers_do_not_inherit_scope_and_closed_children_cannot_add_reads(
    quality_enabled,
):
    context = LoopContext(user_name="alice", agent_id="agent")
    iterator = AssistantLoop(
        provider=ScriptedProvider([text_frame("Ready.")]),
        registry=ToolRegistry(),
    ).run(
        thread=thread(),
        user_content="Explain.",
        context=context,
        resolve_consent=AsyncMock(return_value=True),
    )
    await anext(iterator)
    measured = context.quality_measurements
    assert measured is not None
    assert current_measurements() is None
    record_metadata_read()
    assert measured.metadata_reads == 0

    released = asyncio.Event()

    async def late_child():
        await released.wait()
        assert current_measurements() is None
        record_metadata_read()

    with measurement_scope(measured):
        child = asyncio.create_task(late_child())
    await iterator.aclose()
    assert not measured.active
    released.set()
    await child
    assert measured.metadata_reads == 0


async def test_cancellation_restores_scope_and_closes_attempt(quality_enabled):
    context = LoopContext(user_name="alice", agent_id="agent")
    entered = asyncio.Event()

    class Waiting(ScriptedProvider):
        async def stream(self, **kwargs):
            entered.set()
            await asyncio.Event().wait()
            yield ("message", {})

    iterator = AssistantLoop(
        provider=Waiting([text_frame("Ready.")]),
        registry=ToolRegistry(),
    ).run(
        thread=thread(),
        user_content="Explain.",
        context=context,
        resolve_consent=AsyncMock(return_value=True),
    )

    async def consumer():
        try:
            async for _frame in iterator:
                assert current_measurements() is None
        finally:
            assert current_measurements() is None

    task = asyncio.create_task(consumer())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context.quality_measurements.active is False
    assert current_measurements() is None


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": 15, "output_tokens": 3},
        {"prompt_tokens": 15, "completion_tokens": 3, "total_tokens": 18},
        {"prompt_tokens": 15, "total_tokens": 18},
    ],
)
async def test_native_decision_dispatch_and_usage_are_counted_at_owner(monkeypatch, usage):
    monkeypatch.setattr(
        decision_module,
        "registered_model",
        AsyncMock(
            return_value=(
                {"name": "decision"},
                {"id": "owner", "endpoint": "https://fixture.invalid/decision"},
            )
        ),
    )
    monkeypatch.setattr(
        decision_module.ai_service, "get_provider_api_key", AsyncMock(return_value="private-key")
    )
    sent = []

    async def respond(request):
        sent.append(1)
        return httpx.Response(
            200,
            json={
                "answers": {"selection": answer()},
                "usage": usage,
            },
        )

    @asynccontextmanager
    async def client(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as value:
            yield value

    monkeypatch.setattr(decision_module, "guarded_async_client", client)
    decision = decision_module.DecisionSession(decision_settings())
    measured = AttemptMeasurements()
    with measurement_scope(measured):
        assert await decision.ask("selection", {"private-request": "secret"}, QUESTIONS)
    counts, detail = measured.observation(decision)
    assert counts["provider_calls"] == len(sent) == 1
    assert counts["total_tokens"] == 18
    assert counts["context_tokens"] == 15
    assert detail["estimated_peak_context_tokens"] > 0
    assert "private" not in json.dumps(detail)


async def test_failed_decision_configuration_proves_zero_dispatch(monkeypatch):
    monkeypatch.setattr(
        decision_module, "registered_model", AsyncMock(side_effect=ValueError("private-record"))
    )
    decision = decision_module.DecisionSession(decision_settings())
    measured = AttemptMeasurements()
    with measurement_scope(measured):
        assert await decision.ask("selection", {}, QUESTIONS) == {}
    counts, _ = measured.observation(decision)
    assert counts["provider_calls"] == 0
    assert counts["total_tokens"] == 0


async def test_unknown_http_client_does_not_fabricate_transport_count():
    measured = AttemptMeasurements()
    with measurement_scope(measured):
        async with module.observe_http_dispatches(object()):
            pass
    counts, detail = measured.observation(None)
    assert "provider_calls" not in counts
    assert detail["unavailable"]["provider_calls"] == ["custom_transport_unmeasured"]


async def test_shared_client_concurrent_scopes_and_nested_observation_do_not_double_count():
    both_observing = asyncio.Event()
    entered = 0

    async def respond(request):
        return httpx.Response(200, json={})

    scopes = [AttemptMeasurements(), AttemptMeasurements()]
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:

        async def task(measured, calls):
            nonlocal entered
            with measurement_scope(measured):
                async with module.observe_http_dispatches(client):
                    entered += 1
                    if entered == 2:
                        both_observing.set()
                    await both_observing.wait()
                    async with module.observe_http_dispatches(client):
                        for _ in range(calls):
                            await client.post("https://fixture.invalid/")

        await asyncio.gather(task(scopes[0], 1), task(scopes[1], 3))
        assert [scope.provider_dispatches for scope in scopes] == [1, 3]
        assert client.event_hooks["request"] == []
        await client.post("https://fixture.invalid/")
        assert [scope.provider_dispatches for scope in scopes] == [1, 3]


def test_metadata_hook_declares_coverage_and_count_budgets_fail_closed(monkeypatch):
    from app.core.database import db

    @metadata_reads_observed
    async def owner():
        pass

    @metadata_borrows_observed
    async def connection_owner():
        pass

    monkeypatch.setattr(db, "execute_system", owner)
    monkeypatch.setattr(db, "system_conn", connection_owner)
    measured = AttemptMeasurements()
    with measurement_scope(measured):
        record_metadata_read()
        record_metadata_read()
    counts, detail = measured.observation(None)
    assert counts["metadata_reads"] == 2
    assert detail["metadata_read_basis"] == "executed_result_set_reads"
    assert budget({"counts": counts}, metadata_reads=1) == "fail"
    assert budget({"counts": counts}, metadata_reads=2) == "pass"
    with measurement_scope(measured):
        record_metadata_read_failure()
    counts, detail = measured.observation(None)
    assert "metadata_reads" not in counts
    assert detail["observed_counts"]["metadata_reads"] == 2
    assert budget({"counts": counts}, metadata_reads=2) == "unavailable"


def test_metadata_without_owner_hook_and_resumes_cannot_pass_zero_read_limits(monkeypatch):
    monkeypatch.setattr(module, "metadata_read_coverage", lambda: False)
    measured = AttemptMeasurements()
    counts, detail = measured.observation(None)
    assert budget({"counts": counts}, metadata_reads=0) == "unavailable"
    assert detail["unavailable"]["metadata_reads"] == ["metadata_owner_uninstrumented"]
    monkeypatch.setattr(module, "metadata_read_coverage", lambda: True)
    resumed = AttemptMeasurements(resumed=True)
    counts, detail = resumed.observation(None)
    assert detail["observed_counts"]["participants"] == 1
    assert budget({"counts": counts}, provider_calls=0, metadata_reads=0) == "unavailable"


async def test_direct_metadata_borrow_and_inherited_child_scope_are_unavailable(monkeypatch):
    monkeypatch.setattr(module, "metadata_read_coverage", lambda: True)
    measured = AttemptMeasurements()
    with measurement_scope(measured), metadata_execution_scope():
        record_metadata_borrow()
        record_metadata_read()
        assert not measured.metadata_direct_unobserved

        async def direct_child():
            record_metadata_borrow()

        await asyncio.create_task(direct_child())
    counts, detail = measured.observation(None)
    assert detail["observed_counts"]["metadata_reads"] == 1
    assert detail["unavailable"]["metadata_reads"] == ["direct_metadata_borrow_unmeasured"]
    assert budget({"counts": counts}, metadata_reads=100) == "unavailable"


async def test_metadata_execution_scope_restores_on_failure_and_does_not_cover_later_borrows(
    monkeypatch,
):
    monkeypatch.setattr(module, "metadata_read_coverage", lambda: True)
    measured = AttemptMeasurements()
    with measurement_scope(measured):
        with pytest.raises(RuntimeError), metadata_execution_scope():
            record_metadata_borrow()
            raise RuntimeError("failed owner")
        record_metadata_borrow()
    counts, detail = measured.observation(None)
    assert detail["unavailable"]["metadata_reads"] == ["direct_metadata_borrow_unmeasured"]
    assert budget({"counts": counts}, metadata_reads=0) == "unavailable"


def test_bounded_counters_and_reason_labels_never_turn_saturation_into_pass():
    measured = AttemptMeasurements(tool_dispatches=module.MAX_COUNT)
    measured.increment("tool_dispatches")
    counts, detail = measured.observation(None)
    assert "tool_calls" not in counts
    assert detail["observed_counts"]["tool_calls"] == module.MAX_COUNT
    assert budget({"counts": counts}, tool_calls=module.MAX_COUNT) == "unavailable"
    with pytest.raises(ValueError, match="coverage reason"):
        measured.unavailable_provider("private-url-or-secret")
    with pytest.raises(ValueError, match="counter"):
        measured.increment("private-url-or-secret")


def test_metadata_budget_and_independent_scoring_duration_use_existing_safe_metrics(monkeypatch):
    from app.observability.metrics import STUDIO_WORKFLOW_OPERATIONS

    gates = PromotionGates(
        count_budgets={
            "provider_calls": 1,
            "tool_calls": 1,
            "participants": 1,
            "total_tokens": 100,
            "context_tokens": 100,
            "metadata_reads": 2,
        }
    )
    assertion = gates.performance_assertions()[0]
    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", True)
    completed = STUDIO_WORKFLOW_OPERATIONS.labels("quality", "score", "completed")
    before = completed._value.get()
    trace = {"duration_ms": 999, "counts": {"metadata_reads": 3}}
    score = score_assertion(Assertion(scorer="efficiency", expected={"metadata_reads": 2}), trace)
    assert score.status == "fail"
    assert score.scoring_duration_ms is not None and score.scoring_duration_ms >= 0
    assert completed._value.get() == before + 1
    assert trace["duration_ms"] == 999
    assert assertion.expected["metadata_reads"] == 2


def test_explicit_offline_scoring_measures_overhead_with_production_feature_disabled(monkeypatch):
    from app.observability.metrics import STUDIO_WORKFLOW_OPERATIONS

    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", False)
    completed = STUDIO_WORKFLOW_OPERATIONS.labels("quality", "score", "completed")
    before = completed._value.get()
    score = score_assertion(Assertion(scorer="efficiency", expected={"metadata_reads": 0}), {})
    assert score.status == "unavailable"
    assert score.scoring_duration_ms is not None and score.scoring_duration_ms >= 0
    assert completed._value.get() == before + 1


async def test_explicit_offline_evaluation_measures_with_production_feature_disabled(monkeypatch):
    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", False)
    monkeypatch.setattr(decision_module, "decision_session", AsyncMock(return_value=None))
    plan = {"intent": "direct_answer", "tools": [], "required_tools": [], "ml_task": None}
    provider, requests = http_provider(monkeypatch, [(json.dumps(plan), USAGE), ("Ready.", USAGE)])
    context = LoopContext(user_name="alice", agent_id="agent", quality_evaluation=True)
    context, _ = await run(provider, context=context)
    record = observation(context)
    assert settings.STUDIO_QUALITY_ENABLED is False
    assert len(requests) == 2
    assert record["counts"]["provider_calls"] == 2
    assert budget(record, provider_calls=1) == "fail"
    assert budget(record, provider_calls=2, total_tokens=24) == "pass"
    assert record["facts"]["execution"]["finish_reason"] == "stop"
