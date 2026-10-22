"""Intelligent UI uses final composition without a second tool or inference loop."""

import json

import pytest

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame
from tests.unit.test_intelligent_ui import CALCULATOR


@pytest.mark.parametrize("root", [False, True])
@pytest.mark.parametrize("table_conflict", [False, True])
async def test_simulation_prose_and_ui_share_checked_values(root, table_conflict):
    program = ('root=Col([Chart([{name:"Venue",amount:20},'
               '{name:"Catering",amount:16}],"name","amount","bar")])')
    draft = ("| name | amount |\n| --- | --- |\n| Catering | 999 |" if table_conflict
             else "Planning assumptions. Catering increased by 999.")
    provider = ScriptedProvider(
        script=[text_frame("Draft."), text_frame(
            draft + "\n```openui\n" + program + "\n```"
        )],
        turn_plan={"intent": "direct_answer", "tools": [], "required_tools": [],
                   "skills": [], "ml_task": None, "presentation": "interactive"},
    )
    context = LoopContext(user_name="alice", intelligent_ui=True, collaboration_root=root)
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=ToolRegistry(),
    ).run(thread=AssistantThread(thread_id="simulation", user_name="alice", title="Simulation"),
          user_content="Build an allocation simulator", context=context, resolve_consent=allow)]
    text = "".join(json.loads(frame.split("data: ", 1)[1])["text"] for frame in frames
                   if frame.startswith("event: text_delta\n"))
    assert text == ("Calculated from the simulation assumptions:" if table_conflict
                    else "Planning assumptions.")
    block = next(json.loads(frame.split("data: ", 1)[1]) for frame in frames
                 if frame.startswith("event: ui\n"))["payload"]
    assert "999" not in block["fallback"]
    if table_conflict:
        assert "| Catering | 16 |" in block["fallback"]
        assert "| Catering |" not in text
    else:
        assert block["fallback"] == text
    assert block["provenance"]["scope"] == "user_simulation"
    assert block["evidence"] == {}
    assert any(step.get("ui_simulation_prose_adjusted") for step in context.steps)
    assert provider.calls == 2


@pytest.mark.parametrize("presentation", ["interactive", "text", None])
async def test_interactive_direct_and_smart_root_use_verified_final_composer(presentation):
    for root in (False, True):
        provider = ScriptedProvider(
            script=([text_frame("Draft.")] if presentation == "interactive" else []) + [
                text_frame("Adjust the assumptions.\n```openui\n" + CALCULATOR + "\n```")
            ],
            turn_plan={"intent": "direct_answer", "tools": [], "required_tools": [],
                       "skills": [], "ml_task": None,
                       **({"presentation": presentation} if presentation else {})},
        )
        context = LoopContext(user_name="alice", intelligent_ui=True, collaboration_root=root)
        frames = [frame async for frame in AssistantLoop(
            provider=provider, registry=ToolRegistry(),
        ).run(thread=AssistantThread(thread_id="ui-eval", user_name="alice", title="UI"),
              user_content="Build a calculator", context=context, resolve_consent=allow)]
        assert any(frame.startswith("event: ui\n") for frame in frames)
        assert "```openui" not in "".join(frame for frame in frames
                                        if frame.startswith("event: text_delta\n"))
        assert provider.calls == (2 if presentation == "interactive" else 1)
        assert context.quality_facts["ui_validity"] == {"valid": True}
        assert not any(frame.startswith("event: tool_call\n") for frame in frames)


async def test_specialist_does_not_receive_ui_catalog():
    provider = ScriptedProvider(script=[text_frame("Hello.")])
    context = LoopContext(user_name="alice", intelligent_ui=False)
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=ToolRegistry(),
    ).run(thread=AssistantThread(thread_id="plain", user_name="alice", title="Plain"),
          user_content="Hello", context=context, resolve_consent=allow)]
    assert provider.calls == 1 and not context.ui_prompt
    assert not any(frame.startswith("event: ui\n") for frame in frames)


@pytest.mark.parametrize("root", [False, True])
async def test_default_ui_keeps_plain_answers_in_one_provider_call(root):
    from tests.unit.test_assistant_stage_b import FakeProvider

    provider = FakeProvider([text_frame("Hello.")])
    context = LoopContext(user_name="alice", intelligent_ui=True, collaboration_root=root)
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=ToolRegistry(),
    ).run(thread=AssistantThread(thread_id="greeting", user_name="alice", title="Hello"),
          user_content="Hello", context=context, resolve_consent=allow)]
    assert len(provider.calls) == 1
    assert any("OPTIONAL_UI_CATALOG" in message.get("content", "")
               for message in provider.calls[0]["messages"])
    assert not any(frame.startswith("event: ui\n") for frame in frames)
    assert not any(step.get("ui_repair") or step.get("ui_fallback") for step in context.steps)
    assert "ui_validity" not in context.quality_facts
    text = "".join(json.loads(frame.split("data: ", 1)[1])["text"] for frame in frames
                   if frame.startswith("event: text_delta\n"))
    assert text == "Hello."


@pytest.mark.parametrize("program", ['root=Col([Unknown("x")])', 'root=Col([Text("unfinished")])'])
async def test_optional_ui_still_repairs_invalid_or_unclosed_programs(program):
    suffix = "\n```" if "Unknown" in program else ""
    provider = ScriptedProvider(script=[
        text_frame("Useful answer.\n```openui\n" + program + suffix),
        text_frame("Adjust the assumptions.\n```openui\n" + CALCULATOR + "\n```"),
    ])
    context = LoopContext(user_name="alice", intelligent_ui=True)
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=ToolRegistry(),
    ).run(thread=AssistantThread(thread_id="optional-repair", user_name="alice", title="UI"),
          user_content="Help me explore these assumptions", context=context, resolve_consent=allow)]
    assert provider.calls == 2
    assert any(frame.startswith("event: ui\n") for frame in frames)
    assert sum(bool(step.get("ui_repair")) for step in context.steps) == 1


@pytest.mark.parametrize("draft", [
    "Your calculator is below.",
    'Assumptions.\n```openui\n$a=5\n$total=@Sum([$a,2])\nroot=Col([Text($total)])\n```',
])
async def test_missing_requested_controls_can_repair_within_the_same_loop(draft):
    provider = ScriptedProvider(
        script=[text_frame("Draft."), text_frame(draft),
                text_frame("Adjust the assumptions.\n```openui\n" + CALCULATOR + "\n```")],
        turn_plan={"intent": "direct_answer", "tools": [], "required_tools": [],
                   "skills": [], "ml_task": None, "presentation": "interactive"},
    )
    context = LoopContext(user_name="alice", intelligent_ui=True)
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=ToolRegistry(),
    ).run(thread=AssistantThread(thread_id="missing", user_name="alice", title="UI"),
          user_content="Build a calculator", context=context, resolve_consent=allow)]
    assert provider.calls == 3
    assert any(frame.startswith("event: ui\n") for frame in frames)
    assert sum(bool(step.get("ui_repair")) for step in context.steps) == 1


@pytest.mark.parametrize("answer", [
    "Useful text fallback, but the requested controls were omitted.",
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
    assert "Only text is available" in "".join(frames)
