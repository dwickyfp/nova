"""Controlled real-loader trajectories, not a live-model decision benchmark."""

import json

import pytest

from app.modules.assistant.registry import build_registry
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.skill_registry import skill_library
from app.modules.assistant.state import AssistantThread
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame


@pytest.mark.parametrize(
    "prompt,answer",
    [
        (
            "Draft CREATE STREAM for analytics.orders; do not execute.",
            "Draft only; execution is unavailable.\n```sql\nCREATE STREAM analytics.orders_stream "
            "ON TABLE analytics.orders APPEND_ONLY = TRUE;\n```",
        ),
        (
            "Explain s versus analytics.s versus analytics.default.s.",
            "With analytics active, all three identify the same Stream. Execution is unavailable.",
        ),
        (
            "Execute CREATE STREAM analytics.s on analytics.orders.",
            "Streams execution is unavailable. "
            "A flag change alone does not make the provider ready.",
        ),
        (
            "Explain DROP STREAM analytics.s; I have not approved deletion.",
            "DROP requires confirmation. Nothing was dropped; Streams execution is unavailable.",
        ),
        (
            "Create a task WHEN NOVA_STREAM_HAS_DATA('analytics.s').",
            "Stream functions in tasks are unavailable. No task was created.",
        ),
    ],
)
async def test_nove_loads_stream_playbook_without_mutation(monkeypatch, prompt, answer):
    from unittest.mock import AsyncMock

    mutation = AsyncMock(side_effect=AssertionError("Unexpected SQL or metadata access"))
    monkeypatch.setattr("app.core.database.db.execute_system", mutation)
    monkeypatch.setattr("app.modules.query.service.query_service.execute", mutation)
    registry = build_registry()
    skill = skill_library.get("nova-streams")

    class CheckingProvider(ScriptedProvider):
        async def stream(self, *, messages, tools=None, provider=None):
            if self.calls:
                assert any(
                    (
                        skill.body.strip() in str(message.get("content", ""))
                        or json.dumps(skill.body.strip())[1:-1] in str(message.get("content", ""))
                    )
                    for message in messages
                ), "Real skill body did not reach the next model iteration"
            async for frame in super().stream(messages=messages, tools=tools, provider=provider):
                yield frame

    drafting = prompt.startswith("Draft")
    script = [
        tool_call_frame("load-streams", name="load_skill", arguments={"name": "nova-streams"})
    ]
    if drafting:
        script.append(
            tool_call_frame(
                "validate-draft",
                name="validate_sql",
                arguments={
                    "sql": "CREATE STREAM analytics.orders_stream "
                    "ON TABLE analytics.orders APPEND_ONLY = TRUE;",
                },
            )
        )
    script.append(text_frame(answer))
    provider = CheckingProvider(
        script=script,
        turn_plan={
            "intent": "sql_authoring" if drafting else "capability_help",
            "tools": ["load_skill"],
            "required_tools": ["load_skill"],
            "skills": [],
            "ml_task": None,
        },
    )

    async def no_consent(*_args):
        raise AssertionError("Read-only skill loading must not ask for execution consent")

    frames = [
        frame
        async for frame in AssistantLoop(
            provider=provider,
            registry=registry,
            system_prompt="Nove\n" + skill_library.catalog_prompt(),
        ).run(
            thread=AssistantThread(thread_id="streams", user_name="alice", title="Streams"),
            user_content=prompt,
            context=LoopContext(user_name="alice"),
            resolve_consent=no_consent,
        )
    ]
    assert provider.calls == (3 if drafting else 2)
    assert any(answer in frame or json.dumps(answer)[1:-1] in frame for frame in frames)
    assert any('"finish_reason":"stop"' in frame.replace(" ", "") for frame in frames)
    mutation.assert_not_awaited()
