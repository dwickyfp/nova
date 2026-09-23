from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.agents import router as agent_router
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.access import load_authorized_models
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlanner
from app.modules.assistant.service import LoopContext
from app.modules.query.service import query_service


@pytest.mark.asyncio
async def test_shared_agent_lookup_uses_the_authenticated_active_role(monkeypatch) -> None:
    async def no_owned_agent(_agent_id, *, owner_name):
        assert owner_name == "reader"
        return None

    async def shared_agent(_agent_id, *, role_name):
        if role_name == "city_reader":
            return {"agent_id": "agent-1", "owner_name": "owner"}
        return None

    monkeypatch.setattr(agent_repository, "get_agent", no_owned_agent)
    monkeypatch.setattr(agent_repository, "get_shared_agent", shared_agent)
    async def verified(_agent, *, role_name, user):
        return role_name == "city_reader" and user["username"] == "reader"

    monkeypatch.setattr(agent_router, "has_verified_access", verified)
    user = {
        "username": "reader", "roles": ["city_reader"], "active_role": "city_reader",
    }
    assert (await agent_router._require_agent("agent-1", user))["agent_id"] == "agent-1"
    user["active_role"] = "ACCOUNTADMIN"
    with pytest.raises(HTTPException) as invalid_session:
        await agent_router._require_agent("agent-1", user)
    assert invalid_session.value.status_code == 403
    user["roles"] = ["ACCOUNTADMIN"]
    with pytest.raises(HTTPException) as error:
        await agent_router._require_agent("agent-1", user)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_shared_semantic_model_uses_owner_metadata_but_caller_sql(monkeypatch) -> None:
    definition = parse_ossie(
        (Path(__file__).resolve().parents[3] / "workspace/rbac_city_demo/city_sales.ossie.yaml")
        .read_text()
    ).as_dict()
    calls = []

    async def get_model(model_id, *, owner_name):
        calls.append(("model", model_id, owner_name))
        return {"semantic_model_id": model_id, "definition": definition}

    async def execute(**kwargs):
        calls.append(("query", kwargs["username"], kwargs["role"]))
        return SimpleNamespace(error=None)

    monkeypatch.setattr(agent_repository, "get_semantic_model", get_model)
    monkeypatch.setattr(query_service, "execute", execute)
    context = LoopContext(
        user_name="rbac_jakarta", role="city_reader", database="rbac_city_demo",
        agent_owner_name="nova_admin", semantic_model_id="model-1",
        user={"username": "rbac_jakarta", "encrypted_password": "opaque"},
    )
    models = await load_authorized_models(context)
    assert len(models) == 1
    assert calls == [
        ("model", "model-1", "nova_admin"),
        ("query", "rbac_jakarta", "city_reader"),
    ]


def test_city_question_preserves_explicit_forbidden_city_filter() -> None:
    definition = parse_ossie(
        (Path(__file__).resolve().parents[3] / "workspace/rbac_city_demo/city_sales.ossie.yaml")
        .read_text()
    ).as_dict()
    model = SemanticModelIR.from_ossie(definition)
    planned = SemanticPlanner().plan(
        model, "Berapa total_amount per city hanya untuk Bandung?"
    )
    assert planned.confidence.unresolved == ()
    assert planned.plan is not None
    assert planned.plan.metrics == ("total_amount",)
    assert planned.plan.dimensions == ("city",)
    assert [(item.field, item.operator, item.value) for item in planned.plan.filters] == [
        ("city", "=", "Bandung")
    ]
