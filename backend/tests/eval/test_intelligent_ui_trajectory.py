"""Intelligent UI uses final composition without a second tool or inference loop."""

import pytest

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame
from tests.unit.test_intelligent_ui import CALCULATOR


async def test_interactive_direct_and_smart_root_use_verified_final_composer():
    for root in (False, True):
        provider = ScriptedProvider(
            script=[text_frame("Draft."), text_frame("Adjust the assumptions.\n```openui\n" +
                                                   CALCULATOR + "\n```")],
            turn_plan={"intent": "direct_answer", "tools": [], "required_tools": [],
                       "skills": [], "ml_task": None, "presentation": "interactive"},
        )
        context = LoopContext(user_name="alice", intelligent_ui=True, collaboration_root=root)
        frames = [frame async for frame in AssistantLoop(
            provider=provider, registry=ToolRegistry(),
        ).run(thread=AssistantThread(thread_id="ui-eval", user_name="alice", title="UI"),
              user_content="Build a calculator", context=context, resolve_consent=allow)]
        assert any(frame.startswith("event: ui\n") for frame in frames)
        assert "```openui" not in "".join(frame for frame in frames
                                        if frame.startswith("event: text_delta\n"))
        assert provider.calls == 2
        assert not any(frame.startswith("event: tool_call\n") for frame in frames)


async def test_specialist_and_simple_text_do_not_receive_ui_catalog():
    provider = ScriptedProvider(script=[text_frame("Hello.")])
    context = LoopContext(user_name="alice", intelligent_ui=False)
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=ToolRegistry(),
    ).run(thread=AssistantThread(thread_id="plain", user_name="alice", title="Plain"),
          user_content="Hello", context=context, resolve_consent=allow)]
    assert provider.calls == 1 and not context.ui_prompt
    assert not any(frame.startswith("event: ui\n") for frame in frames)


@pytest.mark.parametrize("answer", [
    'Useful text fallback.\n```openui\nroot = Col([Unknown("x")])\n```',
    '```openui\n' + CALCULATOR + '\n```',
])
async def test_invalid_ui_repairs_once_then_publishes_nonempty_text(answer):
    provider = ScriptedProvider(
        script=[text_frame("Draft."), text_frame(answer), text_frame(answer)],
        turn_plan={"intent": "direct_answer", "tools": [], "required_tools": [],
                   "skills": [], "ml_task": None, "presentation": "interactive"},
    )
    context = LoopContext(user_name="alice", intelligent_ui=True)
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=ToolRegistry(),
    ).run(thread=AssistantThread(thread_id="repair", user_name="alice", title="Repair"),
          user_content="Build a calculator", context=context, resolve_consent=allow)]
    assert provider.calls == 3
    assert not any(frame.startswith("event: ui\n") for frame in frames)
    assert any(frame.startswith("event: text_delta\n") for frame in frames)
    assert any(frame.startswith("event: done\n") for frame in frames)
    assert any(step.get("ui_fallback") == "validation_failed" for step in context.steps)
