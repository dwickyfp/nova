"""Unit tests for the Tool Registry catalog, MCP client, and Studio preferences.

These cover the parts that are pure or mockable:

* the builtin tool catalog is well-formed and only exposes agent-bundleable
  names to the builder;
* the MCP client refuses stdio (an RCE surface), rejects a non-http endpoint,
  and normalizes an MCP tool descriptor;
* Studio preferences round-trip through defaults and reject an invalid theme.

No network, no engine.
"""

from __future__ import annotations

import pytest

from app.modules.agents import mcp_client, skill_catalog, tool_catalog
from app.modules.agents.studio_schemas import StudioPreferences
from app.modules.agents.studio_service import _bool, _choice, _str_or_none


def test_builtin_catalog_is_well_formed() -> None:
    rows = tool_catalog.builtin_rows()
    assert rows
    for row in rows:
        assert row["source"] == "builtin"
        assert row["name"]
        assert row["description"]
        assert isinstance(row["input_schema"], dict)


def test_agent_bundleable_tools_exclude_authoring_tools() -> None:
    # An agent must not be offered the tools that create agents/models.
    assert "create_agent" not in tool_catalog.AGENT_BUNDLEABLE_TOOLS
    assert "create_semantic_model" not in tool_catalog.AGENT_BUNDLEABLE_TOOLS
    # ...but they are in the registry catalog (Nove uses them).
    catalog_names = {row["name"] for row in tool_catalog.builtin_rows()}
    assert {"create_agent", "create_semantic_model"} <= catalog_names


def test_builtin_skill_catalog_is_read_only_and_reserves_platform_names() -> None:
    builtins = skill_catalog.builtin_skill_rows()
    native_ml = next(row for row in builtins if row["name"] == "native-ml")
    assert native_ml["skill_id"] == "builtin:native-ml"
    assert native_ml["source"] == "builtin"
    assert native_ml["read_only"] is True
    assert skill_catalog.is_builtin_skill_id(native_ml["skill_id"])

    merged = skill_catalog.merge_skill_rows(
        [
            {"name": "native-ml", "skill_id": "shadow"},
            {"name": "team-procedure", "skill_id": "custom"},
        ]
    )
    assert sum(row["name"] == "native-ml" for row in merged) == 1
    custom = next(row for row in merged if row["name"] == "team-procedure")
    assert custom["source"] == "user"
    assert custom["read_only"] is False


@pytest.mark.asyncio
async def test_skills_endpoint_includes_the_native_ml_builtin(monkeypatch) -> None:
    from app.modules.agents.router import list_skills

    async def list_user_skills(*, owner_name):
        assert owner_name == "alice"
        return []

    monkeypatch.setattr(
        "app.modules.agents.router.agent_repository.list_skills", list_user_skills
    )
    response = await list_skills(user={"username": "alice"})
    native_ml = next(skill for skill in response.skills if skill.name == "native-ml")
    assert native_ml.source == "builtin"
    assert native_ml.read_only is True
    assert response.count == len(response.skills)


@pytest.mark.asyncio
async def test_builtin_skill_cannot_be_deleted() -> None:
    from fastapi import HTTPException

    from app.modules.agents.router import delete_skill

    with pytest.raises(HTTPException) as excinfo:
        await delete_skill("builtin:native-ml", user={"username": "alice"})
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_mcp_refuses_stdio() -> None:
    with pytest.raises(mcp_client.McpError) as exc:
        await mcp_client.list_tools({"transport": "stdio", "command": "rm -rf /"})
    assert "stdio" in str(exc.value)


@pytest.mark.asyncio
async def test_mcp_rejects_unknown_transport() -> None:
    with pytest.raises(mcp_client.McpError):
        await mcp_client.list_tools({"transport": "carrier-pigeon"})


@pytest.mark.asyncio
async def test_mcp_requires_endpoint() -> None:
    with pytest.raises(mcp_client.McpError) as exc:
        await mcp_client.list_tools({"transport": "http", "endpoint": ""})
    assert "endpoint" in str(exc.value).lower()


def test_mcp_tool_normalization() -> None:
    normalized = mcp_client._tool_from_mcp(
        {"name": "get_weather", "description": "d", "inputSchema": {"type": "object"}}
    )
    assert normalized == {
        "name": "get_weather",
        "description": "d",
        "input_schema": {"type": "object"},
    }


def test_mcp_client_never_imports_a_process_runner() -> None:
    # The client must not spawn processes; stdio is refused, not executed.
    # Check imports, not the docstring prose (which names stdio to explain it).
    module = mcp_client
    assert "subprocess" not in dir(module)
    assert "os" not in dir(module)


def test_studio_preference_helpers() -> None:
    assert _str_or_none("  ") is None
    assert _str_or_none("x") == "x"
    assert _bool("true", False) is True
    assert _bool(None, True) is True
    assert _bool("no", True) is False
    assert _choice("dark", ("light", "dark", "system"), "system") == "dark"
    assert _choice("chartreuse", ("light", "dark", "system"), "system") == "system"


def test_studio_preferences_defaults() -> None:
    prefs = StudioPreferences()
    assert prefs.theme == "system"
    assert prefs.language == "en"
    assert prefs.extended_thinking is True
    assert prefs.role is None


def test_studio_router_literal_paths_are_registered_before_agent_dynamic() -> None:
    """The registries must not be shadowed by /{agent_id}.

    FastAPI matches in declaration order. studio_router has literal paths
    ("/tools", "/mcp-servers") that the agent router's dynamic "/{agent_id}"
    would capture if the agent router were included first. This asserts the
    studio router's paths exist and that the agent router declares its dynamic
    route, so the ordering in main.py is the only thing keeping them apart.
    """
    from app.modules.agents.router import router as agent_router
    from app.modules.agents.studio_router import router as studio_router

    studio_paths = {r.path for r in studio_router.routes}
    assert "/tools" in studio_paths
    assert "/mcp-servers" in studio_paths
    assert "/studio/settings" in studio_paths
    assert "/studio/artifacts" in studio_paths
    assert "/studio/artifacts/{artifact_id}/refresh" in studio_paths

    agent_paths = {r.path for r in agent_router.routes}
    assert "/{agent_id}" in agent_paths


# ── per-agent context token budget (NOVA-124) ────────────────────────────────


def test_token_budget_unset_or_invalid_uses_default() -> None:
    from app.modules.agents.service import _clamp_token_budget

    assert _clamp_token_budget(None) is None
    assert _clamp_token_budget(0) is None
    assert _clamp_token_budget(-5) is None
    assert _clamp_token_budget("48000") is None
    assert _clamp_token_budget(True) is None


def test_token_budget_is_clamped_to_a_sane_range() -> None:
    from app.modules.agents.service import (
        MAX_CONTEXT_TOKEN_BUDGET,
        MIN_CONTEXT_TOKEN_BUDGET,
        _clamp_token_budget,
    )

    assert _clamp_token_budget(10) == MIN_CONTEXT_TOKEN_BUDGET
    assert _clamp_token_budget(10_000_000) == MAX_CONTEXT_TOKEN_BUDGET
    assert _clamp_token_budget(48_000) == 48_000


@pytest.mark.asyncio
async def test_build_loop_inputs_returns_a_token_budget() -> None:
    from app.modules.agents.service import AgentService

    agent = {
        "agent_id": "a1",
        "database_name": "db",
        "schema_name": "s",
        "budget_seconds": None,
        "budget_tokens": 48_000,
        "default_tools": ["query_execute"],
        "default_skills": [],
    }
    registry, _prompt, seconds, tokens = await AgentService().build_loop_inputs(agent)
    assert registry.get("query_execute") is not None
    assert seconds == 60
    assert tokens == 48_000


def test_thread_title_is_the_question_bounded_to_one_line() -> None:
    from app.modules.agents.router import _thread_title

    assert _thread_title("Show revenue by product category") == (
        "Show revenue by product category"
    )
    # Whitespace collapses: a title is a single line in a sidebar.
    assert _thread_title("  Show\n revenue   by category ") == (
        "Show revenue by category"
    )


def test_thread_title_is_truncated_with_an_ellipsis() -> None:
    from app.modules.agents.router import _TITLE_MAX, _thread_title

    title = _thread_title("x" * 500)
    assert len(title) == _TITLE_MAX
    assert title.endswith("\u2026")


def test_clean_title_strips_quotes_and_punctuation() -> None:
    from app.modules.agents.router import _clean_title

    assert _clean_title('"Revenue by category"', "q") == "Revenue by category"
    assert _clean_title("Monthly active users.", "q") == "Monthly active users"


def test_clean_title_falls_back_to_the_question_when_empty() -> None:
    from app.modules.agents.router import _clean_title

    assert _clean_title("   ", "  Show\n revenue ") == "Show revenue"
    assert _clean_title("", "Show revenue") == "Show revenue"


def test_clean_title_is_bounded_to_the_sidebar_width() -> None:
    from app.modules.agents.router import _TITLE_MAX, _clean_title

    title = _clean_title("y" * 500, "q")
    assert len(title) == _TITLE_MAX
    assert title.endswith("\u2026")


def test_generate_thread_title_uses_the_prompt_and_question(monkeypatch) -> None:
    import asyncio

    from app.modules.agents import router as agents_router

    captured: dict = {}

    class _Provider:
        async def resolve(self, *, provider_id=None, model=None):
            captured["resolved"] = (provider_id, model)
            return object()

        async def complete(self, *, messages, provider=None):
            captured["messages"] = messages
            return {"content": '  "Revenue by category"  '}

    monkeypatch.setattr(agents_router, "assistant_provider", _Provider())

    title = asyncio.run(
        agents_router._generate_thread_title(
            "Show revenue by category", provider_id="p1", model="m1"
        )
    )

    assert title == "Revenue by category"
    assert captured["resolved"] == ("p1", "m1")
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert agents_router.CREATE_THREAD_TITLE_PROMPT in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "Show revenue by category"}


def test_generate_thread_title_falls_back_on_provider_error(monkeypatch) -> None:
    import asyncio

    from app.modules.agents import router as agents_router

    class _Provider:
        async def resolve(self, *, provider_id=None, model=None):
            raise RuntimeError("no provider")

    monkeypatch.setattr(agents_router, "assistant_provider", _Provider())

    title = asyncio.run(
        agents_router._generate_thread_title("Show revenue", provider_id=None, model=None)
    )
    assert title == "Show revenue"


# ── thread rename endpoint ───────────────────────────────────────────────────


def _rename_client(monkeypatch):
    from datetime import UTC, datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core import deps as deps_module
    from app.modules.agents import router as agents_router
    from app.modules.agents.router import router as agent_routes

    now = datetime.now(UTC).replace(tzinfo=None)
    agents = {"a1": {"agent_id": "a1", "owner_name": "alice", "name": "A"}}
    threads = {
        "alice-thread": {
            "thread_id": "alice-thread",
            "user_name": "alice",
            "title": "Old",
            "workspace_file_id": None,
            "agent_id": "a1",
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        },
        "bob-thread": {
            "thread_id": "bob-thread",
            "user_name": "bob",
            "title": "Bob's",
            "workspace_file_id": None,
            "agent_id": "a1",
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        },
    }

    class _Agents:
        async def get_agent(self, agent_id, *, owner_name):
            row = agents.get(agent_id)
            return row if row and row["owner_name"] == owner_name else None

    class _Threads:
        async def get_thread(self, thread_id, *, user_name):
            row = threads.get(thread_id)
            return row if row and row["user_name"] == user_name else None

        async def rename_thread(self, thread_id, title, *, user_name):
            row = await self.get_thread(thread_id, user_name=user_name)
            if row is None:
                return None
            row["title"] = title
            return row

    class _Store:
        def register(self, **kwargs):
            return None

        def remove(self, *args, **kwargs):
            return None

    monkeypatch.setattr(agents_router, "agent_repository", _Agents(), raising=True)
    async def verified_agent(_agent, *, role_name, user):
        return role_name == "analyst" and user["username"] == "alice"

    monkeypatch.setattr(agents_router, "has_verified_access", verified_agent, raising=True)
    monkeypatch.setattr(agents_router, "assistant_repository", _Threads(), raising=True)
    monkeypatch.setattr(agents_router, "thread_store", _Store(), raising=True)

    app = FastAPI()
    app.include_router(agent_routes, prefix="/api/v1/agents")

    current = {"username": "alice"}

    async def fake_current_user():
        return {
            "username": current["username"],
            "session_id": "sess-1",
            "roles": ["analyst"],
            "active_role": "analyst",
            "encrypted_password": "",
        }

    app.dependency_overrides[deps_module.get_current_user] = fake_current_user
    return TestClient(app, raise_server_exceptions=False), current, threads


def test_rename_thread_updates_the_owners_conversation(monkeypatch) -> None:
    client, _current, threads = _rename_client(monkeypatch)

    response = client.put("/api/v1/agents/a1/threads/alice-thread", json={"title": "Revenue check"})

    assert response.status_code == 200
    assert response.json()["title"] == "Revenue check"
    assert threads["alice-thread"]["title"] == "Revenue check"


def test_rename_thread_answers_404_for_another_users_thread(monkeypatch) -> None:
    client, _current, threads = _rename_client(monkeypatch)

    response = client.put("/api/v1/agents/a1/threads/bob-thread", json={"title": "Hijacked"})

    assert response.status_code == 404
    assert threads["bob-thread"]["title"] == "Bob's"


def test_rename_thread_rejects_a_blank_title(monkeypatch) -> None:
    client, _current, _threads = _rename_client(monkeypatch)

    response = client.put("/api/v1/agents/a1/threads/alice-thread", json={"title": ""})

    assert response.status_code == 422
