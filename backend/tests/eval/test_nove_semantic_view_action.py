"""A Semantic View creation request must reach a real write tool and approval."""

from __future__ import annotations

import pytest

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult

_CREATE_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "tables": {"type": "array", "items": {"type": "string"}},
        "publish": {"type": "boolean"},
    },
    "required": ["name", "tables"],
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "Buatkan Semantic View sales dari tabel sales.orders.",
        "Create and publish a Semantic View from sales.orders.",
        "Crea una vista semántica para sales.orders.",
        "Crée une vue sémantique depuis sales.orders.",
        "sales.orders からセマンティックビューを作成して",
    ],
)
async def test_creation_executes_write_tool_after_approval(prompt):
    registry = ToolRegistry()
    create = EvalTool(
        "create_semantic_view", classification="destructive", parameters=_CREATE_PARAMETERS
    )
    registry.register(create)
    provider = ScriptedProvider(
        [
            tool_call_frame(
                "create",
                name="create_semantic_view",
                arguments={
                    "name": "sales_view",
                    "tables": ["sales.orders"],
                    "publish": True,
                },
            ),
            text_frame("The Semantic View was published."),
        ],
        turn_plan={
            "intent": "ui_operation",
            "tools": ["create_semantic_view"],
            "required_tools": ["create_semantic_view"],
            "skills": [],
            "ml_task": None,
        },
    )
    approvals = []

    async def consent(invocation, classification):
        approvals.append((invocation.tool_name, classification))
        return True

    result = TurnResult(frames=[
        frame async for frame in AssistantLoop(
            provider=provider, registry=registry, system_prompt="test"
        ).run(
            thread=thread(),
            user_content=prompt,
            context=LoopContext("bench"),
            resolve_consent=consent,
        )
    ])
    assert result.finish_reason == "stop"
    assert len(create.runs) == 1
    assert create.runs[0].arguments["publish"] is True
    assert approvals == [("create_semantic_view", "destructive")]


@pytest.mark.asyncio
async def test_denied_creation_does_not_run():
    registry = ToolRegistry()
    create = EvalTool(
        "create_semantic_view", classification="destructive", parameters=_CREATE_PARAMETERS
    )
    registry.register(create)
    provider = ScriptedProvider(
        [tool_call_frame("create", name="create_semantic_view", arguments={
            "name": "sales_view", "tables": ["sales.orders"],
        })],
        turn_plan={
            "intent": "ui_operation",
            "tools": ["create_semantic_view"],
            "required_tools": ["create_semantic_view"],
            "skills": [],
            "ml_task": None,
        },
    )

    async def deny(_invocation, _classification):
        return False

    result = TurnResult(frames=[
        frame async for frame in AssistantLoop(
            provider=provider, registry=registry, system_prompt="test"
        ).run(
            thread=thread(),
            user_content="Create a Semantic View",
            context=LoopContext("bench"),
            resolve_consent=deny,
        )
    ])
    assert create.runs == []
    assert result.finish_reason == "denied"


@pytest.mark.asyncio
async def test_create_request_cannot_finish_with_draft_only():
    registry = ToolRegistry()
    registry.register(
        EvalTool(
            "create_semantic_view",
            classification="destructive",
            parameters=_CREATE_PARAMETERS,
        )
    )
    provider = ScriptedProvider(
        [text_frame("Here is a YAML draft you can create yourself.")],
        turn_plan={
            "intent": "ui_operation",
            "tools": ["create_semantic_view"],
            "required_tools": ["create_semantic_view"],
            "skills": [],
            "ml_task": None,
        },
    )

    async def unexpected_consent(_invocation, _classification):
        raise AssertionError("No tool call should reach consent")

    result = TurnResult(frames=[
        frame async for frame in AssistantLoop(
            provider=provider, registry=registry, system_prompt="test"
        ).run(
            thread=thread(),
            user_content="Create a Semantic View from sales.orders",
            context=LoopContext("bench"),
            resolve_consent=unexpected_consent,
        )
    ])
    assert result.finish_reason == "required_capability_incomplete"
    assert all("YAML draft" not in frame for frame in result.frames)
