"""Report creation traverses shared consent, tool execution and ordered SSE."""

import json
from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from app.modules.smart_reports.authoring import smart_report_tool
from app.modules.smart_reports.service import report_service
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.unit.test_smart_reports import USER, report_spec


@pytest.mark.parametrize("approved", [True, False])
async def test_report_creation_requires_consent_and_emits_a_safe_replayable_block(
    monkeypatch, approved,
):
    create = AsyncMock(return_value={
        "report_id": "report-a", "draft_id": "draft-a", "draft_revision": 1,
    })
    monkeypatch.setattr(report_service, "create", create)
    registry = ToolRegistry()
    registry.register(smart_report_tool)
    provider = ScriptedProvider(script=[
        tool_call_frame("report-operation", name="smart_report", arguments={
            "action": "create", "spec": report_spec().model_dump(mode="json"),
        }),
        text_frame("Your report draft is available." if approved else "No report was created."),
    ])
    context = LoopContext(
        user_name="alice", user=USER.copy(), agent_id="agent-a", thread_id="thread-a",
    )
    requests = []

    async def consent(invocation, classification):
        requests.append((invocation.tool_name, classification))
        return approved

    frames = [frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
        thread=AssistantThread(thread_id="thread-a", user_name="alice", title="Revenue"),
        user_content="Create a city revenue report filtered to Jakarta",
        context=context, resolve_consent=consent,
    )]
    assert requests == [("smart_report", "session_change")]
    emitted = [json.loads(frame.split("data: ", 1)[1].split("\n", 1)[0])
               for frame in frames if frame.startswith("event: smart_report\n")]
    persisted = [step for step in context.steps or [] if step["kind"] == "smart_report"]
    if not approved:
        create.assert_not_awaited()
        assert emitted == persisted == []
        return
    create.assert_awaited_once()
    request, user = create.call_args.args
    assert request.operation_id == "report-operation"
    assert request.origin.agent_id == "agent-a" and request.origin.thread_id == "thread-a"
    assert request.spec.filters[0].default.values == ["Jakarta"]
    assert user == USER
    assert len(emitted) == len(persisted) == 1
    block = emitted[0]["payload"]
    assert block["report_id"] == "report-a" and block["draft_revision"] == 1
    assert not {"rows", "spec", "sources", "filters", "owner", "grants"} & block.keys()
    assert {key: persisted[0][key] for key in block} == block
    assert emitted[0]["content_id"] == persisted[0]["content_id"]
    assert emitted[0]["content_index"] == persisted[0]["content_index"]
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")
