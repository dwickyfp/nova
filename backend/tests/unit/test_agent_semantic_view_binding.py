"""Published Semantic Views are the only runtime catalog for Agent Studio."""

from __future__ import annotations

import json
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


@pytest.mark.asyncio
async def test_startup_backfill_persists_legacy_bindings_once(monkeypatch) -> None:
    agents = {
        "list": ["old-scalar", '["active-view","draft-view","active-view"]', None],
        "scalar": ["scalar-view", "[]", None],
        "unbound": [None, "[]", None],
        "revoked": ["old-view", '["old-view"]', "[]"],
        "modern": ["old-view", '["old-view"]', '["modern-view"]'],
    }
    writes = []

    class FakeDB:
        async def execute_system(self, sql, params=None):
            if sql.startswith("SELECT"):
                return {
                    "rows": [
                        [agent_id, *values[:2]]
                        for agent_id, values in agents.items()
                        if values[2] is None
                    ]
                }
            assert sql == (
                "UPDATE NOVA_SYSTEM.CONFIG_AGENTS SET semantic_view_ids = %s "
                "WHERE agent_id = %s AND semantic_view_ids IS NULL"
            )
            writes.append((sql, params))
            agent_id = params[1]
            if agents[agent_id][2] is not None:
                return {"affected": 0}
            agents[agent_id][2] = params[0]
            return {"affected": 1}

    monkeypatch.setattr(repository, "db", FakeDB())
    repo = repository.AgentRepository()
    assert await repo.backfill_semantic_view_ids() == 3
    assert json.loads(agents["list"][2]) == ["active-view", "draft-view"]
    assert json.loads(agents["scalar"][2]) == ["scalar-view"]
    assert json.loads(agents["unbound"][2]) == []
    assert agents["revoked"][2] == "[]"
    assert json.loads(agents["modern"][2]) == ["modern-view"]
    assert len(writes) == 3

    assert await repo.backfill_semantic_view_ids() == 0
    assert len(writes) == 3
    assert agents["list"][:2] == [
        "old-scalar", '["active-view","draft-view","active-view"]'
    ]


@pytest.mark.asyncio
async def test_backfill_keeps_concurrent_explicit_unbind(monkeypatch) -> None:
    class FakeDB:
        async def execute_system(self, sql, params=None):
            if sql.startswith("SELECT"):
                return {"rows": [["agent-1", "legacy-view", None]]}
            assert "AND semantic_view_ids IS NULL" in sql
            assert params == ['["legacy-view"]', "agent-1"]
            return {"affected": 0}

    monkeypatch.setattr(repository, "db", FakeDB())
    assert await repository.AgentRepository().backfill_semantic_view_ids() == 0


@pytest.mark.asyncio
async def test_new_agent_writes_only_canonical_view_binding(monkeypatch) -> None:
    writes = []

    class FakeDB:
        async def execute_system(self, sql, params=None):
            writes.append((sql, params))
            return {"rows": []}

    monkeypatch.setattr(repository, "db", FakeDB())
    monkeypatch.setattr(
        repository.AgentRepository, "get_agent", AsyncMock(return_value={"agent_id": "created"})
    )
    repo = repository.AgentRepository()
    await repo.create_agent(
        owner_name="owner", fields={"name": "Analyst", "semantic_view_ids": ["v1", "v2"]}
    )
    sql, values = writes[0]
    columns = [item.strip() for item in sql.split("(", 1)[1].split(") VALUES", 1)[0].split(",")]
    inserted = dict(zip(columns, values, strict=True))
    assert inserted["semantic_model_id"] is None
    assert inserted["semantic_model_ids"] is None
    assert json.loads(inserted["semantic_view_ids"]) == ["v1", "v2"]

    await repo.update_agent(
        "created", owner_name="owner", fields={"semantic_view_ids": []}
    )
    update_sql, update_values = writes[1]
    assert "semantic_view_ids = %s" in update_sql
    assert "semantic_model_ids = %s" not in update_sql
    assert update_values[0] == "[]"


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
