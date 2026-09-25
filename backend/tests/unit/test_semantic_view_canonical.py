"""Canonical Semantic View reads stay published, role-scoped, and owner-private."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.semantic_views import (
    SemanticViewService,
    SemanticViewVerifiedQueryCreate,
    SemanticViewVersionCreate,
)
from app.modules.intelligence.semantic_views import (
    router as semantic_view_router,
)


def _definition() -> dict:
    return parse_ossie(
        Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    ).as_dict()


def _view(*, status: str = "ACTIVE", visibility: str = "PUBLIC") -> dict:
    return {
        "id": "view-1",
        "name": "nova_sales",
        "owner_name": "owner",
        "database_name": "NOVA_DEMO",
        "active_version": 1,
        "status": status,
        "visibility": visibility,
    }


def _version(*, status: str = "ACTIVE", fingerprint: str | None = None) -> dict:
    definition = _definition()
    return {
        "view_id": "view-1",
        "version": 1,
        "status": status,
        "definition": definition,
        "fingerprint": fingerprint or SemanticModelIR.from_ossie(definition).fingerprint,
    }


def _user(name: str = "reader") -> dict:
    return {
        "username": name,
        "encrypted_password": "test-encrypted-value",
        "active_role": "ANALYST",
        "session_id": "session-1",
    }


def test_rule_proposal_routes_alias_existing_agent_handlers():
    from app.modules.agents import router as agent_router_module

    routes = {
        (next(iter(route.methods)), route.path): route.endpoint
        for route in semantic_view_router.routes
        if "rule-proposals" in route.path
    }
    assert routes == {
        ("POST", "/{model_id}/rule-proposals"):
            agent_router_module.create_rule_proposal,
        ("GET", "/{model_id}/rule-proposals"):
            agent_router_module.list_rule_proposals,
        ("POST", "/{model_id}/rule-proposals/{proposal_id}/preview"):
            agent_router_module.preview_rule_proposal,
        ("POST", "/{model_id}/rule-proposals/{proposal_id}/approve"):
            agent_router_module.approve_rule_proposal,
        ("POST", "/{model_id}/rule-proposals/{proposal_id}/reject"):
            agent_router_module.reject_rule_proposal,
    }


@pytest.mark.asyncio
async def test_source_probe_uses_caller_role_and_fails_closed(monkeypatch):
    import app.modules.intelligence.semantic_views as module

    probe = AsyncMock(return_value=SimpleNamespace(error=None))
    monkeypatch.setattr(module.query_service, "execute", probe)
    definition = {"datasets": [{"source": "NOVA_DEMO.orders"}]}
    assert await SemanticViewService._source_access(definition, _user())
    assert probe.await_args.kwargs["role"] == "ANALYST"
    assert probe.await_args.kwargs["username"] == "reader"
    probe.return_value.error = "denied"
    assert not await SemanticViewService._source_access(definition, _user())
    assert not await SemanticViewService._source_access(definition, {"username": "reader"})


@pytest.mark.asyncio
async def test_agent_loader_returns_only_active_authorized_definition(monkeypatch):
    service = SemanticViewService()
    monkeypatch.setattr(service, "_get", AsyncMock(return_value=_view()))
    monkeypatch.setattr(service, "_version", AsyncMock(return_value=_version()))
    source = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_source_access", source)
    monkeypatch.setattr(service, "_entity_access", AsyncMock(return_value=True))

    record = await service.get_active_for_agent("view-1", _user())
    assert record is not None
    assert record["semantic_model_id"] == "view-1"
    assert record["version"] == 1
    assert record["fingerprint"] == _version()["fingerprint"]
    source.assert_awaited_once()
    assert source.await_args.args[1]["active_role"] == "ANALYST"

    service._get.return_value = _view(status="DRAFT")
    assert await service.get_active_for_agent("view-1", _user()) is None
    service._get.return_value = _view()
    service._version.return_value = _version(status="DRAFT")
    assert await service.get_active_for_agent("view-1", _user()) is None
    service._version.return_value = _version(fingerprint="tampered")
    assert await service.get_active_for_agent("view-1", _user()) is None
    service._version.return_value = _version()
    source.return_value = False
    assert await service.get_active_for_agent("view-1", _user()) is None


@pytest.mark.asyncio
async def test_private_import_requires_owner_or_verified_shared_agent(monkeypatch):
    import app.modules.agents.repository as repository_module

    service = SemanticViewService()
    monkeypatch.setattr(service, "_get", AsyncMock(return_value=_view(visibility="PRIVATE")))
    monkeypatch.setattr(service, "_version", AsyncMock(return_value=_version()))
    monkeypatch.setattr(service, "_source_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_entity_access", AsyncMock(return_value=True))
    grant = AsyncMock(return_value=None)
    monkeypatch.setattr(repository_module.agent_repository, "get_shared_agent", grant)

    assert await service.get_active_for_agent("view-1", _user()) is None
    assert await service.get_active_for_agent("view-1", _user(), agent_id="agent-1") is None
    grant.return_value = {
        "owner_name": "owner",
        "semantic_view_ids": ["view-1"],
    }
    assert await service.get_active_for_agent("view-1", _user(), agent_id="agent-1")
    grant.assert_awaited_with("agent-1", role_name="ANALYST")
    grant.return_value = {
        "owner_name": "owner",
        "semantic_view_ids": [],
        "semantic_model_ids": ["view-1"],
    }
    assert await service.get_active_for_agent("view-1", _user(), agent_id="agent-1") is None
    assert await service.get_active_for_agent("view-1", _user("owner"))

    with pytest.raises(HTTPException) as exc:
        await service._visible("view-1", _user())
    assert exc.value.status_code == 404
    admin = _user()
    admin["active_role"] = "ACCOUNTADMIN"
    with pytest.raises(HTTPException) as exc:
        await service._owned("view-1", admin)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_draft_preview_and_quality_are_owner_only(monkeypatch):
    service = SemanticViewService()
    monkeypatch.setattr(service, "_get", AsyncMock(return_value=_view(status="DRAFT")))
    monkeypatch.setattr(service, "_version", AsyncMock(return_value=_version(status="DRAFT")))
    monkeypatch.setattr(service, "_source_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_entity_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_audit", AsyncMock())

    with pytest.raises(HTTPException) as exc:
        await service.quality("view-1", 1, _user())
    assert exc.value.status_code == 404
    quality = await service.quality("view-1", 1, _user("owner"))
    assert quality["view_id"] == "view-1"
    assert quality["quality"]["verified_query_count"] == 0
    preview = await service.preview("view-1", 1, "total revenue", _user("owner"))
    assert "SELECT" in preview["generated_sql"]
    assert preview["version"] == 1
    assert service._audit.await_count == 2


@pytest.mark.asyncio
async def test_unsupported_private_draft_can_be_inspected_and_replaced(monkeypatch):
    import app.modules.intelligence.semantic_views as module

    service = SemanticViewService()
    view = _view(status="DRAFT", visibility="PRIVATE")
    view.update(name="legacy_legacymodelid", active_version=None)
    raw = {"datasets": [], "metrics": []}
    version = {
        "view_id": "view-1",
        "version": 1,
        "status": "DRAFT",
        "definition": {**raw, "verified_queries": []},
        "fingerprint": "legacy-fingerprint",
        "validation": {
            "valid": False,
            "errors": ["Unsupported legacy Ossie version 1.0"],
            "migration": {
                "source": "CONFIG_SEMANTIC_MODELS",
                "legacy_verified_queries": [],
                "raw_definition": raw,
            },
        },
    }
    monkeypatch.setattr(service, "_get", AsyncMock(return_value=view))
    monkeypatch.setattr(service, "_version", AsyncMock(return_value=version))
    source = AsyncMock(side_effect=AssertionError("review must not probe sources"))
    monkeypatch.setattr(service, "_source_access", source)
    monkeypatch.setattr(service, "_entity_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_audit", AsyncMock())

    async def execute(sql: str, params: list):
        if "MAX(version)" in sql or "SELECT version" in sql:
            return {"rows": [[1]]}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    detail = await service.describe("view-1", _user("owner"))
    assert detail["versions"][0]["validation"]["migration"]["raw_definition"] == raw
    assert (await service.quality("view-1", 1, _user("owner")))["valid"] is False
    assert await service.get_active_for_agent("view-1", _user("owner")) is None
    source.assert_not_awaited()
    with pytest.raises(HTTPException) as exc:
        await service.describe("view-1", _user())
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await service.preview("view-1", 1, "revenue", _user("owner"))
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await service.validate("view-1", 1, _user("owner"))
    assert exc.value.status_code == 422

    source.side_effect = None
    source.return_value = True
    monkeypatch.setattr(service, "_insert_version", AsyncMock())
    replacement = Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    replacement = replacement.replace("name: nova_sales", "name: legacy_legacymodelid", 1)
    await service.add_version(
        "view-1", SemanticViewVersionCreate(definition=replacement), _user("owner")
    )
    assert service._insert_version.await_args.args[1] == 2


@pytest.mark.asyncio
async def test_verified_query_save_creates_new_draft_without_mutating_active(monkeypatch):
    import app.modules.intelligence.semantic_views as module

    service = SemanticViewService()
    base = _version()
    view = _view()
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=view))
    monkeypatch.setattr(service, "_readable_version", AsyncMock(return_value=(view, base)))
    monkeypatch.setattr(service, "_insert_version", AsyncMock())
    monkeypatch.setattr(
        service, "_version", AsyncMock(return_value={"version": 2, "status": "DRAFT"})
    )
    monkeypatch.setattr(service, "_audit", AsyncMock())
    monkeypatch.setattr(module.db, "execute_system", AsyncMock(return_value={"rows": [[1]]}))
    plan = SemanticPlan(metrics=("total_revenue",))
    sql = SemanticCompiler().compile(SemanticModelIR.from_ossie(base["definition"]), plan).sql
    body = SemanticViewVerifiedQueryCreate(
        question="How much revenue?", semantic_plan=plan.as_dict(), verified_sql=sql
    )

    result = await service.add_verified_query("view-1", 1, body, _user("owner"))
    assert result["status"] == "DRAFT"
    inserted = service._insert_version.await_args.args
    assert inserted[1] == 2
    assert inserted[2]["verified_queries"][0]["question"] == "How much revenue?"
    assert base["definition"]["verified_queries"] == []

    service._insert_version.reset_mock()
    with pytest.raises(HTTPException) as exc:
        await service.add_verified_query(
            "view-1", 1,
            SemanticViewVerifiedQueryCreate(
                question="Remove data", semantic_plan=plan.as_dict(),
                verified_sql="DELETE FROM NOVA_DEMO.orders",
            ),
            _user("owner"),
        )
    assert exc.value.status_code == 422
    service._insert_version.assert_not_awaited()
