"""A provider's reused wire ID cannot identify a later Nova approval."""

import asyncio
import json
from uuid import UUID

import pytest

from app.modules.assistant.consent import ConsentBroker
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import text_frame, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult
from tests.eval.test_harness_guards_trajectory import RecordingProvider
from tests.unit.test_assistant_consent_ownership import _signed_in


async def _turn(thread_id, resolver, *, script=None, granted=False, iterative=False):
    tool = EvalTool("query_execute")
    registry = ToolRegistry()
    registry.register(tool)
    provider = RecordingProvider(script or [
        tool_call_frame("provider-reused-id", sql="SELECT 42"), text_frame("42"),
    ])
    thread = AssistantThread(thread_id, "alice", "Synthetic consent identity")
    thread.consent.always_allow_read_only = granted
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=registry, iterative=iterative,
    ).run(thread=thread, user_content="Run the requested constant queries",
          context=LoopContext("alice", thread_id=thread_id), resolve_consent=resolver)]
    return tool, provider, TurnResult(frames=frames)


def _public_ids(result):
    return [json.loads(frame.split("data: ", 1)[1])["tool_call_id"]
            for frame in result.frames if frame.startswith("event: tool_call\n")]


async def test_a_previous_decision_cannot_resolve_a_later_provider_id_reuse():
    broker = ConsentBroker()
    identities = []

    async def resolve(invocation, classification):
        future = broker.open(invocation.tool_call_id, thread_id="one-thread",
                             user=_signed_in("alice"), classification=classification)
        try:
            if identities:
                assert not broker.resolve(identities[-1], True, user=_signed_in("alice"))
                assert not future.done()
            identities.append(invocation.tool_call_id)
            assert broker.resolve(invocation.tool_call_id, True, user=_signed_in("alice"))
            return await future
        finally:
            broker.clear()

    results = [await _turn("one-thread", resolve) for _ in range(2)]
    assert len(set(identities)) == 2
    assert all(UUID(identity).version == 4 for identity in identities)
    for tool, provider, result in results:
        assert result.finish_reason == "stop" and len(tool.runs) == 1
        assert _public_ids(result) == [tool.runs[0].tool_call_id]
        messages = provider.requests[-1]
        assistant_call = next(m for m in messages if m.get("tool_calls"))
        response = next(m for m in messages if m.get("role") == "tool")
        assert assistant_call["tool_calls"][0]["id"] == "provider-reused-id"
        assert response["tool_call_id"] == "provider-reused-id"
        assert tool.runs[0].tool_call_id != response["tool_call_id"]


async def test_parallel_turns_with_the_same_wire_id_keep_independent_approvals():
    broker = ConsentBroker()
    opened = []
    both = asyncio.Event()

    async def resolve(invocation, classification):
        future = broker.open(invocation.tool_call_id, thread_id="thread-" + str(len(opened)),
                             user=_signed_in("alice"), classification=classification)
        opened.append(invocation.tool_call_id)
        if len(opened) == 2:
            both.set()
        await both.wait()
        assert broker.resolve(invocation.tool_call_id, True, user=_signed_in("alice"))
        return await future

    try:
        async with asyncio.timeout(5):
            results = await asyncio.gather(_turn("first", resolve), _turn("second", resolve))
        assert len(set(opened)) == 2
        assert all(len(tool.runs) == 1 and result.finish_reason == "stop"
                   for tool, _, result in results)
    finally:
        broker.cancel_all()


async def test_prefetched_calls_with_reused_wire_ids_run_once_and_keep_public_identity():
    message = tool_call_frame("reused-in-batch", sql="SELECT 1")
    message["tool_calls"] += tool_call_frame("reused-in-batch", sql="SELECT 2")["tool_calls"]

    async def unexpected_consent(*_args):
        pytest.fail("The existing read-only grant covers this controlled turn")

    tool, provider, result = await _turn(
        "prefetch", unexpected_consent, granted=True, iterative=True,
        script=[message, text_frame("Both constant queries finished")],
    )
    assert result.finish_reason == "stop"
    assert sorted(run.arguments["sql"] for run in tool.runs) == ["SELECT 1", "SELECT 2"]
    ids = _public_ids(result)
    assert len(ids) == len(set(ids)) == 2
    assert set(ids) == {run.tool_call_id for run in tool.runs}
    assert all(UUID(identity).version == 4 for identity in ids)
    replies = [m for m in provider.requests[-1] if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in replies] == ["reused-in-batch", "reused-in-batch"]


async def test_reused_response_object_gets_a_new_identity_without_repeating_its_effect():
    message = tool_call_frame("same-object", sql="SELECT 42")
    approved = []

    async def resolve(invocation, _classification):
        approved.append(invocation.tool_call_id)
        return True

    tool, provider, result = await _turn(
        "reused-object", resolve,
        script=[message, message, text_frame("The recorded result remains 42")],
    )
    assert result.finish_reason == "stop"
    assert len(tool.runs) == len(approved) == 1
    statuses = [json.loads(frame.split("data: ", 1)[1]) for frame in result.frames
                if frame.startswith("event: tool_status\n")]
    assert [status["status"] for status in statuses] == ["running", "done", "done"]
    assert statuses[0]["tool_call_id"] == statuses[1]["tool_call_id"] == approved[0]
    assert statuses[2]["tool_call_id"] != approved[0]
    replies = [m for m in provider.requests[-1] if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in replies] == ["same-object", "same-object"]
    assert json.loads(replies[-1]["content"])["metadata"]["deduplicated"] is True
