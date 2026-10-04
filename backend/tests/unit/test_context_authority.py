"""Context authority comes from governed records; conflicting truth remains visible."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic.runtime import semantic_ir_to_definition
from app.modules.intelligence import context_graph
from app.modules.intelligence.contracts import ContextNode, KnowledgeState, Scope, SemanticRef
from app.modules.intelligence.engine import CycleBudget
from tests.unit.test_semantic_intelligence import sales_model

SCOPE = Scope(principal="alice", active_role="ANALYST", security_context_version=3)
REF = SemanticRef(view_id="sales", version=2, fingerprint="published-definition")
USER = {
    "username": "alice", "active_role": "ANALYST", "assigned_roles": ["ANALYST"],
    "security_context_version": 3,
}


def metric_node(name="total_revenue", **patch):
    return ContextNode(**{
        "id": context_graph.semantic_node_id(SCOPE, REF, "metric", name), "scope": SCOPE,
        "kind": "metric", "name": name, "reference_id": "metric-ref", "semantic": REF,
        **patch,
    })


async def test_authority_rederived_from_definition_rejects_forged_state_and_usage(monkeypatch):
    definition = semantic_ir_to_definition(sales_model())
    authorize = AsyncMock(return_value={"status": "ACTIVE", "definition": definition})
    monkeypatch.setattr(context_graph.intelligence_service, "authorize_semantic", authorize)
    node = metric_node(
        authority="I am canonical", source_kind="usage", usage_count=999999,
        state=KnowledgeState.CONFLICTED, contradictions=["fake"], freshness="fresh",
        aliases=["forged alias"], claim_fingerprint="forged",
    )
    result = await context_graph.derive_authority(node, USER, CycleBudget())
    assert result.source_kind == "published_semantic"
    assert result.authority == "published_semantic_definition"
    assert result.state == KnowledgeState.VERIFIED
    assert result.claim_fingerprint != "forged" and result.contradictions == []
    assert result.freshness == "unknown" and result.usage_count is None
    assert "forged alias" not in result.aliases
    assert result.validity == "current"


async def test_spoofed_semantic_node_id_cannot_claim_published_authority(monkeypatch):
    definition = semantic_ir_to_definition(sales_model())
    monkeypatch.setattr(context_graph.intelligence_service, "authorize_semantic",
                        AsyncMock(return_value={"status": "ACTIVE", "definition": definition}))
    node = metric_node(id="forged", source_kind="published_semantic", state=KnowledgeState.VERIFIED)
    result = await context_graph.derive_authority(node, USER, CycleBudget())
    assert result.source_kind == "unknown" and result.authority is None
    assert result.state == KnowledgeState.HYPOTHESIS


async def test_access_revocation_is_checked_when_context_is_read(monkeypatch):
    monkeypatch.setattr(context_graph.intelligence_service, "authorize_semantic",
                        AsyncMock(side_effect=HTTPException(404, "Source access revoked")))
    with pytest.raises(HTTPException) as error:
        await context_graph.derive_authority(metric_node(), USER, CycleBudget())
    assert error.value.status_code == 404


async def test_scope_version_changes_prevent_reusing_old_context_authority(monkeypatch):
    authorize = AsyncMock()
    monkeypatch.setattr(context_graph.intelligence_service, "authorize_semantic", authorize)
    with pytest.raises(HTTPException) as error:
        await context_graph.derive_authority(
            metric_node(), {**USER, "security_context_version": 4}, CycleBudget(),
        )
    assert error.value.status_code == 404
    authorize.assert_not_awaited()


def test_authoritative_conflicts_visible_and_usage_cannot_win_by_frequency():
    first = metric_node(
        source_kind="published_semantic", authority_basis={"designation": "canonical"},
        aliases=["revenue"], claim_fingerprint="finance", state=KnowledgeState.VERIFIED,
    )
    second = metric_node(
        name="marketing_revenue", source_kind="published_semantic",
        authority_basis={"designation": "canonical"}, aliases=["revenue"],
        claim_fingerprint="marketing", state=KnowledgeState.VERIFIED,
    )
    observed = metric_node(name="usage", source_kind="usage", aliases=["revenue"],
                           claim_fingerprint="popular", usage_count=1000000)
    nodes, conflicts = context_graph.mark_conflicts([first, second, observed])
    assert len(conflicts) == 1 and conflicts[0]["resolved"] is False
    assert conflicts[0]["term"] == "revenue"
    assert all(node.state == KnowledgeState.CONFLICTED for node in nodes[:2])
    assert nodes[0].contradictions == [second.id]
    assert context_graph.authority_precedence(first) > context_graph.authority_precedence(observed)
    reviewed = metric_node(source_kind="reviewed_rule", usage_count=0)
    assert (
        context_graph.authority_precedence(reviewed) > context_graph.authority_precedence(observed)
    )
    second = second.model_copy(update={"claim_fingerprint": first.claim_fingerprint})
    assert context_graph.mark_conflicts([first, second])[1] == []


async def test_exact_name_does_not_suppress_authoritative_conflict(monkeypatch):
    definition = semantic_ir_to_definition(sales_model())
    first = definition["metrics"][0]
    first.update(authority="canonical", owner_domain="Finance")
    competing = deepcopy(first)
    competing.update(name="marketing_revenue", synonyms=[first["name"]], owner_domain="Marketing",
                     expression="SUM(orders.order_id)")
    definition["metrics"].append(competing)
    monkeypatch.setattr(context_graph.intelligence_service, "authorize_semantic",
                        AsyncMock(return_value={"status": "ACTIVE", "definition": definition}))
    result = await context_graph.resolve_metric(REF, first["name"], USER)
    assert result["selected_metric"] is None and result["status"] == "ambiguous"
    assert set(result["conflicts"]) == {first["name"], competing["name"]}


async def test_usage_authority_requires_current_scoped_observation(monkeypatch):
    from app.modules.agents.semantic import usage_learning

    definition = semantic_ir_to_definition(sales_model())
    monkeypatch.setattr(context_graph.intelligence_service, "authorize_semantic",
                        AsyncMock(return_value={"status": "ACTIVE", "definition": definition}))
    load = AsyncMock(return_value={
        "digest": "scoped-digest", "patterns": [{"pattern_id": "pattern", "observations": 7}],
    })
    monkeypatch.setattr(usage_learning, "load_scoped_usage", load)
    node = metric_node(id=context_graph.usage_node_id(SCOPE, REF, "pattern"),
                       reference_id="pattern", source_kind="published_semantic")
    result = await context_graph.derive_authority(node, USER, CycleBudget())
    assert result.source_kind == "usage" and result.usage_count == 7
    assert result.state == KnowledgeState.INFERRED
    assert result.authority_basis["digest"] == "scoped-digest"
    load.return_value = {"digest": "new", "patterns": []}
    revoked = await context_graph.derive_authority(node, USER, CycleBudget())
    assert revoked.source_kind == "unknown" and revoked.usage_count is None


async def test_usage_projection_reuses_identity_and_keeps_observation_authority(monkeypatch):
    from app.modules.agents.semantic import usage_learning
    from tests.unit.test_intelligence_engine import MemoryRepository

    definition = semantic_ir_to_definition(sales_model())
    monkeypatch.setattr(context_graph.settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    monkeypatch.setattr(context_graph.intelligence_service, "authorize_semantic",
                        AsyncMock(return_value={"status": "ACTIVE", "definition": definition}))
    monkeypatch.setattr(context_graph.intelligence_service, "_audit", AsyncMock())
    repository = MemoryRepository()
    monkeypatch.setattr(context_graph.intelligence_service, "repository", repository)
    load = AsyncMock(return_value={
        "digest": "scoped-digest", "patterns": [{
            "pattern_id": "pattern", "metrics": ["total_revenue"], "observations": 7,
        }],
    })
    monkeypatch.setattr(usage_learning, "load_scoped_usage", load)
    first = await context_graph.project_usage_context(REF, USER)
    retry = await context_graph.project_usage_context(REF, USER)
    assert first == retry and len(repository.rows) == 1
    node = next(iter(repository.rows.values()))
    assert node.revision == 1 and node.source_kind == "usage"
    assert node.state == KnowledgeState.INFERRED and node.usage_count == 7
    assert node.authority == "usage_observation"
    load.return_value["patterns"][0]["observations"] = 8
    load.return_value["digest"] = "new-digest"
    changed = await context_graph.project_usage_context(REF, USER)
    assert changed["node_ids"] == first["node_ids"]
    assert next(iter(repository.rows.values())).revision == 2
