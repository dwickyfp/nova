"""Exact evidence selection and canonical graph sources retain current access checks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic.runtime import semantic_ir_to_definition
from app.modules.intelligence import context_graph, context_sources
from app.modules.intelligence.context_sources import ContextSourceRef, read_context_source
from app.modules.intelligence.contracts import ContextNode, KnowledgeState, Scope, SemanticRef
from app.modules.intelligence.engine import CycleBudget
from tests.unit.test_intelligence_engine import MemoryRepository
from tests.unit.test_semantic_intelligence import sales_model

USER = {
    "username": "alice",
    "active_role": "ANALYST",
    "assigned_roles": ["ANALYST"],
    "security_context_version": 3,
    "session_id": "current",
}
REF = SemanticRef(view_id="sales", version=2, fingerprint="published-definition")


def setup(monkeypatch, status="ACTIVE"):
    definition = semantic_ir_to_definition(sales_model())
    service = context_graph.intelligence_service
    repository = MemoryRepository()
    authorize = AsyncMock(return_value={"status": status, "definition": definition})
    monkeypatch.setattr(service, "authorize_semantic", authorize)
    monkeypatch.setattr(service, "repository", repository)
    monkeypatch.setattr(service, "_audit", AsyncMock())
    monkeypatch.setattr(context_graph.settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    return service, repository, authorize


async def test_exact_metric_returns_same_authorized_historical_version_and_neighborhood(
    monkeypatch,
):
    _, _, authorize = setup(monkeypatch, "DEPRECATED")
    result = await context_graph.resolve_metric(
        REF,
        "total_revenue",
        USER,
        exact=True,
        include_context=True,
    )
    assert result["status"] == "resolved"
    assert result["selected_node"].kind == "metric"
    assert result["selected_node"].semantic == REF
    assert result["selected_node"].validity == "historical"
    assert result["node_id"] == result["selected_node"].id
    assert len(result["graph"]["nodes"]) <= 50
    assert all(not call.kwargs.get("active") for call in authorize.await_args_list)


async def test_exact_selection_does_not_resolve_an_alias_or_project_any_nodes(monkeypatch):
    _, repository, _ = setup(monkeypatch)
    result = await context_graph.resolve_metric(
        REF,
        "revenue",
        USER,
        exact=True,
        include_context=True,
    )
    assert result["status"] == "unknown" and result["node_id"] is None
    assert result["graph"] is None and repository.rows == {}


async def test_readable_draft_cannot_be_presented_as_published_context(monkeypatch):
    setup(monkeypatch, "DRAFT")
    with pytest.raises(HTTPException) as error:
        await context_graph.resolve_metric(REF, "total_revenue", USER, include_context=True)
    assert error.value.status_code == 404


async def test_revocation_stops_exact_selection_before_projection(monkeypatch):
    _, repository, authorize = setup(monkeypatch)
    authorize.side_effect = HTTPException(404, "Source unavailable")
    with pytest.raises(HTTPException):
        await context_graph.resolve_metric(REF, "total_revenue", USER, include_context=True)
    assert repository.rows == {}


async def test_mission_projection_pins_refs_and_uses_mission_authorized_historical_reads(
    monkeypatch,
):
    from app.modules.agents import mission
    from app.modules.agents.mission_schema import ObjectRef

    _, repository, _ = setup(monkeypatch)
    ref = ObjectRef(kind="investigation", id="investigation", revision=2)
    source = SimpleNamespace(
        revision=3,
        object_refs=[ref],
        model_dump=lambda **kwargs: {"object_refs": [ref.model_dump()]},
    )
    monkeypatch.setattr(mission.mission_service, "get", AsyncMock(return_value=source))
    read = AsyncMock(
        return_value={
            "id": "investigation",
            "revision": 2,
            "scope": Scope.from_user(USER).model_dump(),
            "semantic": REF.model_dump(),
            "news_id": "news",
            "status": "complete",
            "hypotheses": [],
            "decompositions": [],
            "timeline": [],
            "residual": 0,
            "evidence": [],
            "method": "test",
        }
    )
    # Projection only needs the canonical JSON; type validation is exercised by the owner.
    monkeypatch.setattr(mission.mission_service, "canonical_read", read)
    monkeypatch.setattr(mission.mission_service, "deliverables", AsyncMock(return_value=[]))
    request = ContextSourceRef(kind="mission", id="mission", revision=3)
    result = await context_graph.project_canonical_context(request, USER)
    assert result["nodes"] == 3
    assert read.await_args.args[0] == "mission" and read.await_args.args[1] == ref
    node = repository.rows[("nodes", result["root_id"])]
    assert node.kind == "mission" and node.state == KnowledgeState.INFERRED
    source.revision = 4
    with pytest.raises(HTTPException) as error:
        await context_graph.derive_authority(node, USER, CycleBudget())
    assert error.value.status_code == 409


async def test_mutable_dashboard_reference_has_no_reviewed_authority_and_fails_on_change(
    monkeypatch,
):
    from app.modules.agents.dashboard_repository import dashboard_repository
    from app.modules.agents.studio_schemas import DashboardLayout
    from app.modules.intelligence.contracts import fingerprint

    source = {"updated_at": "2026-10-04", "layout": DashboardLayout(tiles=[])}
    get = AsyncMock(return_value=source)
    monkeypatch.setattr(dashboard_repository, "get", get)
    pinned = fingerprint({"updated_at": "2026-10-04", "layout": {"tiles": []}})
    request = ContextSourceRef(kind="dashboard", id="dashboard", fingerprint=pinned)
    value = await read_context_source(request, USER, CycleBudget())
    assert value["authority"] == "owner_scoped_reference"
    assert value["source_kind"] == "studio_reference"
    assert get.await_args.kwargs["owner_name"] == "alice"
    source["updated_at"] = "2026-10-05"
    with pytest.raises(HTTPException) as error:
        await read_context_source(request, USER, CycleBudget())
    assert error.value.status_code == 409


async def test_document_projection_requires_current_grant_and_excludes_body(monkeypatch):
    from app.modules.agents.harness_repository import harness_repository
    from app.modules.agents.resource_delegation import ResourceMetadata, resource_delegation

    source = ResourceMetadata(
        resource_id="document",
        message_id="message",
        attachment_index=0,
        name="Private title",
        media_type="text/plain",
        size_bytes=100,
        digest="d" * 64,
    )
    run = {"run_id": "run"}
    monkeypatch.setattr(harness_repository, "get", AsyncMock(return_value=run))
    available = AsyncMock(return_value=[source])
    monkeypatch.setattr(resource_delegation, "available", available)
    monkeypatch.setattr(
        resource_delegation,
        "load",
        AsyncMock(
            return_value=(
                [
                    {"content": "PRIVATE-DOCUMENT-BODY"},
                ],
                ["document"],
            )
        ),
    )
    request = ContextSourceRef(kind="document", id="document", run_id="run", fingerprint="d" * 64)
    result = await read_context_source(request, USER, CycleBudget())
    assert "PRIVATE-DOCUMENT-BODY" not in str(result)
    assert result["name"] == "Granted document reference"
    available.return_value = []
    with pytest.raises(HTTPException) as error:
        await read_context_source(request, USER, CycleBudget())
    assert error.value.status_code == 404


async def test_forged_canonical_reference_id_cannot_claim_lifecycle_authority(monkeypatch):
    read = AsyncMock()
    monkeypatch.setattr(context_sources, "read_context_source", read)
    node = ContextNode(
        id="forged",
        scope=Scope.from_user(USER),
        name="Forged",
        kind="mission",
        reference_id="mission",
        reference_revision=1,
    )
    with pytest.raises(HTTPException):
        await context_sources.authorize_context_reference(node, USER, CycleBudget())
    read.assert_not_awaited()


async def test_release_projection_links_immutable_dependencies_and_rechecks_resource_access(
    monkeypatch,
):
    from app.modules.agents import releases, resources, router

    _, repository, _ = setup(monkeypatch)
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value={"owner_name": "alice"}))
    manifest = {
        "id": "release",
        "fingerprint": "r" * 64,
        "dependencies": {
            "semantic_views": [REF.model_dump()],
            "configuration": {"resource_bindings": {}},
            "skills": [{"name": "investigate", "description": "Reviewed skill snapshot"}],
            "tools": [{"name": "semantic_query", "classification": "read_only"}],
            "policy_fingerprint": "p" * 64,
        },
    }
    from app.modules.intelligence.contracts import fingerprint

    manifest["fingerprint"] = fingerprint(manifest["dependencies"])
    monkeypatch.setattr(releases, "get_manifest", AsyncMock(return_value=manifest))
    validate = AsyncMock()
    monkeypatch.setattr(resources, "validate_resources", validate)
    result = await context_graph.project_canonical_context(
        ContextSourceRef(
            kind="agent_release",
            id="release",
            agent_id="agent",
        ),
        USER,
    )
    kinds = {node.kind for (kind, _), node in repository.rows.items() if kind == "nodes"}
    assert {"agent", "agent_release", "skill", "tool", "policy", "semantic_view"} <= kinds
    root = repository.rows[("nodes", result["root_id"])]
    assert root.state == KnowledgeState.INFERRED and root.source_kind == "agent_release"
    validate.side_effect = HTTPException(422, "Resource revoked")
    with pytest.raises(HTTPException):
        await context_graph.derive_authority(root, USER, CycleBudget())
