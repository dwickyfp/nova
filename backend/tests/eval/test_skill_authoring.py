from app.modules.agents.personal_skills import SKILL_AUTHORING_PROMPT
from app.modules.agents.tools.load_personal_skill import PersonalSkillLoader
from app.modules.assistant.intelligence import SkillDefinition
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame


async def test_skill_authoring_uses_bounded_engine_without_executing_tools():
    from app.modules.agents.service import agent_service
    from app.modules.agents.skill_author import skill_author_config

    document = (
        "```skill\n---\nname: weekly-review\ndescription: Review weekly reports\n---\n"
        "Ask for a report. Summarize changes.\n```"
    )
    provider = ScriptedProvider(script=[text_frame(document)])
    registry, prompt, budget, tokens = await agent_service.build_loop_inputs(
        skill_author_config("alice")
    )
    assert budget == 60 and tokens == 16000
    context = LoopContext(user_name="alice", instructions=SKILL_AUTHORING_PROMPT)

    async def consent(invocation, classification):
        raise AssertionError("Authoring cannot invoke tools")

    frames = [
        frame
        async for frame in AssistantLoop(
            provider=provider,
            registry=registry,
            system_prompt=prompt,
        ).run(
            thread=AssistantThread(thread_id="draft", user_name="alice", title="Draft"),
            user_content="/create-skill-with-chat Help me write a weekly report summary skill.",
            context=context,
            resolve_consent=consent,
        )
    ]
    assert registry.names() == []
    assert any("weekly-review" in frame for frame in frames)
    assert any('"finish_reason":"stop"' in frame.replace(" ", "") for frame in frames)
    assert not any(frame.startswith("event: tool_call") for frame in frames)


async def test_private_skill_load_trajectory_preserves_owner_and_consent_boundaries():
    definitions = {
        "weekly-review": SkillDefinition(
            name="weekly-review",
            summary="Review reports",
            triggers=("weekly",),
            body="Ask for the report before summarizing.",
            trust_level="user_skill",
        )
    }
    loader = PersonalSkillLoader("alice", definitions)
    registry = ToolRegistry()
    registry.register(loader)
    registry.skill_definitions = definitions
    registry.discoverable_skills = ("weekly-review",)
    provider = ScriptedProvider(
        script=[
            tool_call_frame("load1", name="load_skill", arguments={"name": "weekly-review"}),
            text_frame("Please provide your weekly report."),
        ]
    )

    async def consent(invocation, classification):
        raise AssertionError("Reading an owned skill is a pure read")

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="use-skill", user_name="alice", title="Review"),
            user_content="Use weekly-review to help me review my report.",
            context=LoopContext(user_name="alice"),
            resolve_consent=consent,
        )
    ]
    assert any("load_skill" in frame for frame in frames)
    assert any('"finish_reason":"stop"' in frame.replace(" ", "") for frame in frames)
    invocation = ToolInvocation(
        tool_call_id="cross-user", tool_name="load_skill", arguments={"name": "weekly-review"}
    )
    denied = await loader.run(invocation, LoopContext(user_name="bob"))
    assert not denied.ok
    assert "Ask for the report" not in str(denied)


async def test_personal_skill_with_credentials_is_never_loaded():
    loader = PersonalSkillLoader(
        "alice",
        {
            "old-skill": SkillDefinition(
                name="old-skill",
                summary="Legacy skill",
                triggers=(),
                body="password='private-password-value'",
                trust_level="user_skill",
            )
        },
    )
    outcome = await loader.run(
        ToolInvocation(
            tool_call_id="secret", tool_name="load_skill", arguments={"name": "old-skill"}
        ),
        LoopContext(user_name="alice"),
    )
    assert not outcome.ok
    assert "private-password-value" not in str(outcome)
