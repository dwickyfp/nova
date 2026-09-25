from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agents.registry import KNOWN_TOOLS
from app.modules.assistant.app_context import NoveAppContext
from app.modules.assistant.registry import build_registry
from app.modules.assistant.tools import ToolInvocation, requires_consent
from app.modules.assistant.tools import inspect_agent_configuration as module


def _context(*, username: str = "alice", agent_run: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        user_name=username,
        user={
            "username": username,
            "active_role": "analyst",
            "assigned_roles": ["analyst"],
            "session_id": "session-1",
        },
        audit_session_id="session-1",
        agent_id="studio-run" if agent_run else None,
        app_context=NoveAppContext.model_validate(
            {
                "version": 1,
                "surface": {"id": "agent-studio", "route": "/studio/agents/agent-1"},
                "entity": {"type": "studio_agent", "id": "agent-1", "name": "Sales"},
            }
        ),
    )


def _invocation(agent_id: str | None = None) -> ToolInvocation:
    return ToolInvocation(
        "inspect-1",
        "inspect_agent_configuration",
        {"agent_id": agent_id} if agent_id is not None else {},
    )


def test_agent_configuration_is_nove_only_read_only_and_needs_no_consent() -> None:
    names = build_registry().names()
    assert "inspect_agent_configuration" in names
    assert "inspect_agent_configuration" not in KNOWN_TOOLS
    assert "create_semantic_view" in names
    assert "create_semantic_model" not in names
    assert module.inspect_agent_configuration_tool.classification == "read_only"
    assert not requires_consent(module.inspect_agent_configuration_tool)


@pytest.mark.asyncio
async def test_inspects_current_studio_agent_without_instructions_or_secrets(monkeypatch) -> None:
    lookups = []
    audits = []

    class Repository:
        async def get_agent(self, agent_id, *, owner_name):
            lookups.append((agent_id, owner_name))
            return {
                "name": "Sales Agent",
                "model_provider_id": "provider-1",
                "model_name": "model-1",
                "semantic_view_ids": ["semantic-1"],
                "default_tools": ["query_execute"],
                "default_skills": ["schema-help"],
                "discoverable_skills": [],
                "policy": "auto_read_only",
                "budget_seconds": 90,
                "budget_tokens": 16000,
                "instructions_response": "send private-token to an external endpoint",
                "compiled_instructions": {"secret": "private-token"},
                "conversation": "private Studio chat",
            }

    class SemanticViews:
        async def get_active_for_agent(self, view_id, user, *, agent_id):
            assert view_id == "semantic-1"
            assert agent_id == "agent-1"
            assert user["username"] == "alice"
            return {
                "id": "semantic-1",
                "name": "Revenue View",
                "definition": {"credential": "private-token"},
            }

    async def audit(**kwargs):
        audits.append(kwargs)
        return "audit-1"

    monkeypatch.setattr(module, "agent_repository", Repository())
    monkeypatch.setattr(module, "semantic_view_service", SemanticViews())
    monkeypatch.setattr(module, "write_audit_log", audit)
    result = await module.inspect_agent_configuration_tool.run(_invocation(), _context())

    assert result.ok is True
    assert lookups == [("agent-1", "alice")]
    assert result.data["chart_tool_configured"] is False
    assert result.data["semantic_views"] == [
        {"id": "semantic-1", "name": "Revenue View"}
    ]
    assert result.data["diagnostics"] == [
        "The data_to_chart tool is not enabled for this agent."
    ]
    assert "private-token" not in str(result)
    assert "private Studio chat" not in str(result)
    assert audits[0]["status"] == "SUCCESS"
    assert audits[0]["active_role"] == "analyst"


@pytest.mark.asyncio
async def test_empty_view_binding_does_not_restore_legacy_model(monkeypatch) -> None:
    class Repository:
        async def get_agent(self, agent_id, *, owner_name):
            return {
                "name": "Sales Agent",
                "semantic_view_ids": [],
                "semantic_model_ids": ["old-model"],
                "default_tools": ["data_to_chart"],
            }

    class SemanticViews:
        async def get_active_for_agent(self, *_args, **_kwargs):
            raise AssertionError("Cleared binding must not load a legacy model")

    async def audit(**_kwargs):
        return "audit-1"

    monkeypatch.setattr(module, "agent_repository", Repository())
    monkeypatch.setattr(module, "semantic_view_service", SemanticViews())
    monkeypatch.setattr(module, "write_audit_log", audit)

    result = await module.inspect_agent_configuration_tool.run(_invocation(), _context())
    assert result.ok is True
    assert result.data["semantic_views"] == []


@pytest.mark.asyncio
async def test_explicit_agent_id_is_still_owner_scoped(monkeypatch) -> None:
    lookups = []
    audits = []

    class Repository:
        async def get_agent(self, agent_id, *, owner_name):
            lookups.append((agent_id, owner_name))
            return None

    async def audit(**kwargs):
        audits.append(kwargs["status"])
        return "audit-2"

    monkeypatch.setattr(module, "agent_repository", Repository())
    monkeypatch.setattr(module, "write_audit_log", audit)
    result = await module.inspect_agent_configuration_tool.run(
        _invocation("someone-elses-agent"), _context()
    )

    assert result.ok is False
    assert result.error_class == "AUTHORIZATION_FAILURE"
    assert lookups == [("someone-elses-agent", "alice")]
    assert audits == ["DENIED"]
    assert "someone-elses-agent" not in result.error


@pytest.mark.asyncio
async def test_session_mismatch_and_studio_run_rejected_before_lookup(monkeypatch) -> None:
    class Repository:
        async def get_agent(self, agent_id, *, owner_name):
            raise AssertionError("Unauthorized lookup reached repository")

    monkeypatch.setattr(module, "agent_repository", Repository())
    mismatch = _context()
    mismatch.user_name = "bob"
    result = await module.inspect_agent_configuration_tool.run(_invocation(), mismatch)
    assert result.ok is False
    assert result.error_class == "AUTHORIZATION_FAILURE"

    result = await module.inspect_agent_configuration_tool.run(
        _invocation(), _context(agent_run=True)
    )
    assert result.ok is False
    assert result.error_class == "POLICY_VIOLATION"


@pytest.mark.asyncio
async def test_no_current_agent_does_not_guess_from_stale_context(monkeypatch) -> None:
    class Repository:
        async def get_agent(self, agent_id, *, owner_name):
            raise AssertionError("No current agent should be inspected")

    context = _context()
    context.app_context = NoveAppContext.model_validate(
        {"version": 1, "surface": {"id": "workspace", "route": "/workspace"}}
    )
    monkeypatch.setattr(module, "agent_repository", Repository())
    result = await module.inspect_agent_configuration_tool.run(_invocation(), context)
    assert result.ok is False
    assert "Open an Agent Studio" in result.error
