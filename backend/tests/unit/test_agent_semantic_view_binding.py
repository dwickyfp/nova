"""Published Semantic Views are the only runtime catalog for Agent Studio."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import repository
from app.modules.agents.router import _normalize_view_binding
from app.modules.agents.semantic.access import bound_view_ids, load_authorized_models
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.assistant.service import LoopContext, _has_semantic_binding
from app.modules.intelligence.semantic_views import semantic_view_service
from tests.unit.agent_metadata_fakes import install_agent_store


@pytest.fixture
def definition() -> dict:
    text = Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    return parse_ossie(text).as_dict()


def test_canonical_empty_binding_overrides_legacy_ids() -> None:
    assert bound_view_ids({"semantic_view_ids": [], "semantic_model_ids": ["old"]}) == []
    assert bound_view_ids({"semantic_model_ids": ["old"]}) == ["old"]
    assert repository._view_ids("[]", ["old"]) == []
    assert repository._view_ids(None, ["old"]) == ["old"]
    assert not _has_semantic_binding(LoopContext(
        user_name="owner", semantic_view_ids=[], semantic_model_ids=["old"]
    ))
    assert _has_semantic_binding(LoopContext(
        user_name="owner", semantic_model_ids=["old"]
    ))


@pytest.mark.parametrize("canonical,legacy,expected", [
    (None, ["old-view", "draft-view"], ["old-view", "draft-view"]),
    ([], ["old-view"], []),
    (["modern-view"], ["old-view"], ["modern-view"]),
])
async def test_imported_binding_is_projected_without_rewriting_history(
    monkeypatch, canonical, legacy, expected,
):
    store = install_agent_store(monkeypatch, AsyncMock(), principal="owner")
    original = {"agent_id": "old-agent", "owner_name": "owner", "name": "Legacy",
                "semantic_model_ids": legacy, "semantic_view_ids": canonical}
    store.get_agent.return_value = dict(original)
    first = await repository.agent_repository.get_agent("old-agent", owner_name="owner")
    second = await repository.agent_repository.get_agent("old-agent", owner_name="owner")
    assert first["semantic_view_ids"] == second["semantic_view_ids"] == expected
    assert store.get_agent.return_value == original
    store.update_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_agent_writes_only_canonical_view_binding(monkeypatch) -> None:
    from app.modules.agents.versions import agent_versions

    monkeypatch.setattr(agent_versions, "store", AsyncMock())
    store = install_agent_store(monkeypatch, AsyncMock(), principal="owner")
    row = {"agent_id": "created", "owner_name": "owner", "name": "Analyst"}
    store.create_agent.return_value = row
    store.get_agent.return_value = row
    store.update_agent.return_value = row
    repo = repository.AgentRepository()
    await repo.create_agent(owner_name="owner", fields={
        "name": "Analyst", "semantic_view_ids": ["v1", "v2", "v1"],
        "semantic_model_id": "legacy", "semantic_model_ids": ["legacy"],
    })
    fields = store.create_agent.await_args.args[0]
    assert fields["semantic_model_id"] is None and fields["semantic_model_ids"] is None
    assert fields["semantic_view_ids"] == ["v1", "v2"]
    await repo.update_agent("created", owner_name="owner", fields={"semantic_view_ids": []})
    assert store.update_agent.await_args.args == ("created", {"semantic_view_ids": []})


@pytest.mark.asyncio
async def test_bound_agent_loads_only_published_authorized_view(monkeypatch, definition) -> None:
    active = {
        "id": "view-1", "name": "nova_sales", "version": 3,
        "definition": definition, "fingerprint": "current", "status": "ACTIVE",
    }
    get_active = AsyncMock(
        side_effect=lambda view_id, _user, **_: active if view_id == "view-1" else None
    )
    list_active = AsyncMock()
    monkeypatch.setattr(semantic_view_service, "get_active_for_agent", get_active)
    monkeypatch.setattr(semantic_view_service, "list_active_for_agent", list_active)
    context = SimpleNamespace(
        agent_id="agent-1", agent_owner_name="owner", semantic_view_ids=["view-1", "draft-1"],
        user={"username": "reader", "encrypted_password": "sealed"}, role="analyst",
        audit_session_id="session-1",
    )

    models = await load_authorized_models(context)

    assert [model["semantic_model_id"] for model in models] == ["view-1"]
    assert models[0]["version"] == 3
    assert get_active.await_count == 2
    assert all(call.args[1]["active_role"] == "analyst" for call in get_active.await_args_list)
    assert all(call.kwargs["agent_id"] == "agent-1" for call in get_active.await_args_list)
    list_active.assert_not_awaited()
    assert "total_revenue" in context.semantic_routing_terms


@pytest.mark.asyncio
async def test_unbound_agent_cannot_use_global_view_catalog(monkeypatch) -> None:
    list_active = AsyncMock()
    monkeypatch.setattr(semantic_view_service, "list_active_for_agent", list_active)
    context = SimpleNamespace(
        agent_id="agent-1", semantic_view_ids=[],
        user={"username": "reader", "encrypted_password": "sealed"},
    )
    assert await load_authorized_models(context) == []
    list_active.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_write_maps_to_published_view_and_draft_fails(monkeypatch) -> None:
    get_active = AsyncMock(
        side_effect=lambda view_id, _user: {"id": view_id} if view_id == "active" else None
    )
    monkeypatch.setattr(semantic_view_service, "get_active_for_agent", get_active)
    user = {"username": "owner", "encrypted_password": "sealed"}
    fields = {"semantic_model_ids": ["active"], "semantic_view_ids": []}
    await _normalize_view_binding(fields, provided={"semantic_model_ids"}, user=user)
    assert fields == {"semantic_view_ids": ["active"]}

    fields = {"semantic_view_ids": ["draft"]}
    with pytest.raises(HTTPException) as error:
        await _normalize_view_binding(fields, provided={"semantic_view_ids"}, user=user)
    assert error.value.status_code == 422
