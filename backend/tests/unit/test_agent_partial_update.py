"""A partial agent update changes only the fields it names."""

from contextlib import suppress
from unittest.mock import AsyncMock

from app.modules.agents import router as module
from app.modules.agents.schemas import AgentUpdateRequest

USER = {"username": "alice"}


async def test_updating_a_description_keeps_the_agent_tools(monkeypatch):
    existing = {"agent_id": "routes", "description": "old", "default_tools": ["semantic_query"],
                "config_revision": 3}
    monkeypatch.setattr(module, "_require_agent", AsyncMock(return_value=existing))
    monkeypatch.setattr(module, "_normalize_view_binding", AsyncMock())
    monkeypatch.setattr("app.modules.agents.resources.validate_resources", AsyncMock())
    update = AsyncMock(return_value=None)
    monkeypatch.setattr(module.agent_repository, "update_agent", update)
    # The mocked repository returns no row (404); only the written fields matter.
    with suppress(module.HTTPException):
        await module.update_agent("routes", AgentUpdateRequest(description="new"), USER)
    fields = update.await_args.kwargs["fields"]
    # Omitting default_tools must not reset them to an empty list.
    assert "default_tools" not in fields
    assert fields["description"] == "new"

    update.reset_mock()
    with suppress(module.HTTPException):
        await module.update_agent(
            "routes", AgentUpdateRequest(default_tools=["semantic_query", "ml_execute"]), USER,
        )
    assert update.await_args.kwargs["fields"]["default_tools"] == ["semantic_query", "ml_execute"]
