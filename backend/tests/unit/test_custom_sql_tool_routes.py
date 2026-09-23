from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.modules.agents import studio_router
from app.modules.agents.schemas import CustomToolCreateRequest, CustomToolUpdateRequest


def _row(name: str, tool_id: str) -> dict:
    now = datetime.now(UTC)
    return {
        "tool_id": tool_id,
        "owner_name": "alice",
        "name": name,
        "description": "Read one row",
        "kind": "procedure",
        "database_name": "scratch",
        "function_name": None,
        "definition": {"parameters": [], "statements": ["SELECT 1"]},
        "created_at": now,
        "updated_at": now,
    }


async def test_create_rejects_duplicate_custom_tool_name(monkeypatch) -> None:
    class Repository:
        async def list_custom_tools(self, *, owner_name):
            assert owner_name == "alice"
            return [_row("lookup", "existing")]

        async def create_custom_tool(self, **_kwargs):
            raise AssertionError("duplicate tool must not be created")

    async def no_audit(**_kwargs):
        raise AssertionError("rejected create must not be audited as a success")

    monkeypatch.setattr(studio_router, "agent_repository", Repository())
    monkeypatch.setattr(studio_router, "write_audit_log", no_audit)
    body = CustomToolCreateRequest(
        name="lookup",
        description="Read one row",
        kind="procedure",
        database_name="scratch",
        definition={"parameters": [], "statements": ["SELECT 1"]},
    )

    with pytest.raises(HTTPException) as exc:
        await studio_router.create_custom_tool(body, user={"username": "alice"})
    assert exc.value.status_code == 409


async def test_rename_updates_every_selected_agent_reference(monkeypatch) -> None:
    existing = _row("lookup", "tool-1")
    agent_updates = []
    audit_calls = []

    class Repository:
        async def get_custom_tool(self, tool_id, *, owner_name):
            assert (tool_id, owner_name) == ("tool-1", "alice")
            return existing

        async def list_custom_tools(self, *, owner_name):
            assert owner_name == "alice"
            return [existing, _row("unrelated", "tool-2")]

        async def update_custom_tool(self, tool_id, *, owner_name, fields):
            assert (tool_id, owner_name, fields) == ("tool-1", "alice", {"name": "new_lookup"})
            return {**existing, "name": "new_lookup"}

        async def list_agents(self, *, owner_name):
            assert owner_name == "alice"
            return [
                {
                    "agent_id": "agent-one",
                    "default_tools": ["custom:lookup", "query_execute"],
                },
                {
                    "agent_id": "agent-two",
                    "default_tools": ["custom:lookup", "custom:unrelated"],
                },
                {"agent_id": "agent-three", "default_tools": ["custom:unrelated"]},
            ]

        async def update_agent(self, agent_id, *, owner_name, fields):
            agent_updates.append((agent_id, owner_name, fields))

    async def record_audit(**kwargs):
        audit_calls.append(kwargs)

    monkeypatch.setattr(studio_router, "agent_repository", Repository())
    monkeypatch.setattr(studio_router, "write_audit_log", record_audit)
    updated = await studio_router.update_custom_tool(
        "tool-1",
        CustomToolUpdateRequest(name="new_lookup"),
        user={"username": "alice", "session_id": "session-1"},
    )

    assert updated.name == "new_lookup"
    assert agent_updates == [
        (
            "agent-one",
            "alice",
            {"default_tools": ["custom:new_lookup", "query_execute"]},
        ),
        (
            "agent-two",
            "alice",
            {"default_tools": ["custom:new_lookup", "custom:unrelated"]},
        ),
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "update"
