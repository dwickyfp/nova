"""Runtime consumers preserve deployment calendars and persisted object overrides."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.agents import automations, deep_research, quality, router, turns
from app.modules.agents.quality_scoring import PromotionGates
from app.modules.agents.service import agent_service
from app.modules.assistant import service as assistant_service
from app.modules.assistant.tools import ToolRegistry
from tests.unit.test_agent_automations import automation
from tests.unit.test_deep_research import Repository
from tests.unit.test_quality_lab_boundaries import case
from tests.unit.test_quality_lab_boundaries import metadata as metadata

ZONES = ("Asia/Jakarta", "UTC", "Europe/Berlin", "America/New_York")
AGENT = {"agent_id": "finance", "owner_name": "alice"}
USER = {
    "username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"],
    "session_id": "session", "security_context_version": 1, "encrypted_password": "encrypted",
}


@pytest.fixture
def observed_loops(monkeypatch):
    contexts = []

    class Loop:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *, context, **_kwargs):
            contexts.append(context)
            context.usage = {"total_tokens": 10}
            context.quality_facts = {"task_completeness": {"completed": True}}
            yield 'event: text_delta\ndata: {"text": "Calendar checked."}\n\n'
            yield 'event: done\ndata: {"message_id": "reply", "finish_reason": "stop"}\n\n'

    monkeypatch.setattr(assistant_service, "AssistantLoop", Loop)
    monkeypatch.setattr(agent_service, "build_loop_inputs", AsyncMock(
        return_value=(ToolRegistry(), "Use governed data.", 60, 4096),
    ))
    return contexts


@pytest.mark.parametrize("zone", ZONES)
async def test_unattended_turn_reads_normalized_configuration_at_call_time(
    monkeypatch, observed_loops, zone,
):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", f" {zone} ")
    output = await turns.run_agent_turn(AGENT, user=USER, question="Revenue today",
                                       thread_id="timezone-turn")
    assert output.text == "Calendar checked." and output.finish_reason == "stop"
    assert observed_loops[-1].execution_timezone == zone
    assert observed_loops[-1].user == USER
    assert await turns.read_only_consent(None, "read_only")
    assert not await turns.read_only_consent(None, "destructive")
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC" if zone != "UTC" else "Europe/Berlin")
    await turns.run_agent_turn(AGENT, user=USER, question="Revenue today", thread_id="next-turn")
    assert observed_loops[-1].execution_timezone == settings.NOVA_TIMEZONE


@pytest.mark.parametrize("zone", ZONES)
async def test_automation_turn_keeps_persisted_timezone_when_deployment_changes(
    monkeypatch, observed_loops, zone,
):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC" if zone != "UTC" else "Asia/Jakarta")
    stored = automation(timezone=zone)
    before = dict(stored)
    text, _, _ = await automations._run_turn(AGENT, stored, "automation-thread")
    context = observed_loops[-1]
    assert text == "Calendar checked." and context.execution_timezone == zone
    assert context.user["username"] == stored["owner_name"]
    assert context.user["active_role"] == stored["role_name"]
    assert stored == before


@pytest.mark.parametrize("override", ("", "Europe/Berlin"))
async def test_explicit_turn_override_is_not_replaced(monkeypatch, observed_loops, override):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC")
    await turns.run_agent_turn(AGENT, user=USER, question="Revenue today", thread_id="override",
                               execution_timezone=override)
    assert observed_loops[-1].execution_timezone == override


@pytest.mark.parametrize("zone", ZONES)
async def test_readiness_context_uses_deployment_timezone(monkeypatch, zone):
    from app.modules.agents.semantic import access

    monkeypatch.setattr(settings, "NOVA_TIMEZONE", f" {zone} ")
    monkeypatch.setattr(router, "_require_owned_agent", AsyncMock(return_value=AGENT))
    load = AsyncMock(return_value=[])
    monkeypatch.setattr(access, "load_authorized_models", load)
    await router.agent_readiness("finance", USER)
    context = load.await_args.args[0]
    assert context.execution_timezone == zone and context.user == USER


@pytest.mark.parametrize("zone", ZONES)
async def test_research_planning_and_investigations_share_configured_timezone(
    monkeypatch, observed_loops, zone,
):
    from app.modules.agents.semantic import access

    monkeypatch.setattr(settings, "NOVA_TIMEZONE", zone)
    load = AsyncMock(return_value=[])
    monkeypatch.setattr(access, "load_authorized_models", load)
    monkeypatch.setattr(deep_research, "plan_investigations", AsyncMock(
        return_value=["Revenue today"],
    ))
    monkeypatch.setattr(deep_research, "_complete", AsyncMock(return_value="Calendar checked."))
    monkeypatch.setattr(deep_research, "_publish", AsyncMock())
    monkeypatch.setattr(deep_research, "write_audit_log", AsyncMock())
    repository = Repository()
    await deep_research.run("research", AGENT, USER, "Revenue today", "research-thread",
                            repository=repository)
    assert repository.updates[-1]["status"] == "done"
    assert load.await_args.args[0].execution_timezone == zone
    assert len(observed_loops) == 1 and observed_loops[0].execution_timezone == zone


@pytest.mark.parametrize("zone", ZONES)
async def test_quality_evaluation_uses_configured_timezone(
    monkeypatch, observed_loops, metadata, zone,
):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", zone)
    manifest = {
        "id": "manifest", "version_id": "version", "fingerprint": "digest",
        "dependencies": {"configuration": {}},
    }
    monkeypatch.setattr(quality, "load_runtime_manifest", AsyncMock(return_value=manifest))
    run = await quality.evaluate(AGENT, manifest, [case()], USER,
                                 PromotionGates(max_latency_ms=100000))
    assert run["status"] == "passed"
    assert len(observed_loops) == 1 and observed_loops[0].execution_timezone == zone
    assert observed_loops[0].quality_evaluation


@pytest.mark.parametrize("zone", ZONES)
async def test_nove_stream_uses_configured_timezone(monkeypatch, observed_loops, zone):
    from app.modules.assistant import router as nove
    from app.modules.assistant.schemas import MessageRequest
    from app.modules.assistant.state import thread_store

    monkeypatch.setattr(settings, "NOVA_TIMEZONE", zone)
    monkeypatch.setattr(nove, "_require_thread", AsyncMock(return_value={
        "title": "Calendar", "thread_id": "nove-timezone",
    }))
    monkeypatch.setattr(nove.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(nove.assistant_repository, "append_message", AsyncMock())
    monkeypatch.setattr(nove, "_loop", assistant_service.AssistantLoop())
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    try:
        response = await nove.send_message("nove-timezone", MessageRequest(content="Revenue today"),
                                           request, USER)
        frames = [frame async for frame in response.body_iterator]
        assert any("Calendar checked." in frame for frame in frames)
        assert observed_loops[-1].execution_timezone == zone
        assert observed_loops[-1].role == USER["active_role"]
    finally:
        thread_store.remove("nove-timezone", user_name=USER["username"])


@pytest.mark.parametrize("zone", ZONES)
async def test_direct_studio_stream_uses_configured_timezone(monkeypatch, observed_loops, zone):
    from app.modules.agents import personal_skills
    from app.modules.agents.memory import memory_repository
    from app.modules.assistant.schemas import AgentMessageRequest
    from app.modules.assistant.state import thread_store

    monkeypatch.setattr(settings, "NOVA_TIMEZONE", zone)
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=AGENT))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={
        "title": "Calendar", "thread_id": "studio-timezone",
    }))
    monkeypatch.setattr(router, "_resolve_database", AsyncMock(return_value=None))
    monkeypatch.setattr(personal_skills, "is_skill_authoring", lambda *_args: False)
    monkeypatch.setattr(memory_repository, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.assistant_repository, "learning_enabled", AsyncMock(
        return_value=False,
    ))
    monkeypatch.setattr(router.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.assistant_repository, "append_message", AsyncMock())
    monkeypatch.setattr(router, "_generate_thread_title", AsyncMock(return_value="Calendar"))
    monkeypatch.setattr(router.assistant_repository, "rename_thread", AsyncMock())
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    for method in ("start", "heartbeat", "append_batch", "finish"):
        monkeypatch.setattr(router.run_journal, method, AsyncMock())
    monkeypatch.setattr(router, "AssistantLoop", assistant_service.AssistantLoop)
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    try:
        response = await router.send_agent_message(
            "finance", "studio-timezone", AgentMessageRequest(content="Revenue today"),
            request, USER,
        )
        frames = [frame async for frame in response.body_iterator]
        assert any("Calendar checked." in frame for frame in frames)
        assert observed_loops[-1].execution_timezone == zone
        assert observed_loops[-1].role == USER["active_role"]
    finally:
        thread_store.remove("studio-timezone", user_name=USER["username"])
