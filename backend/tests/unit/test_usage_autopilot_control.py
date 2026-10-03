"""Observed usage supports review but never grants publication authority."""

from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic import usage_learning
from app.modules.intelligence import autopilot
from app.modules.intelligence.autopilot import (
    AutopilotProposal,
    AutopilotReview,
    SemanticChange,
)
from app.modules.intelligence.contracts import Scope, SemanticRef
from tests.benchmark.business_intelligence.model import starter_definition

USER = {
    "username": "alice", "active_role": "ANALYST", "assigned_roles": ["ANALYST"],
    "security_context_version": 3,
}
REF = SemanticRef(view_id="sales", version=2, fingerprint="active-definition")
DIGEST = "a" * 64


@asynccontextmanager
async def unlocked(*args):
    yield


def setup(monkeypatch):
    from app.modules.agents import router

    monkeypatch.setattr(autopilot.settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True, raising=False)
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value={
        "semantic_view_ids": ["sales"],
    }))
    monkeypatch.setattr(autopilot, "metadata_lock", unlocked)
    monkeypatch.setattr(autopilot.semantic_view_service, "_owned", AsyncMock(return_value={
        "active_version": 2,
    }))
    monkeypatch.setattr(
        autopilot.semantic_view_service, "_readable_version", AsyncMock(return_value=(
            {"active_version": 2},
            {"version": 2, "fingerprint": REF.fingerprint, "definition": starter_definition()},
        )),
    )
    monkeypatch.setattr(
        autopilot.semantic_view_service, "_source_access", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(autopilot.semantic_view_service, "_audit", AsyncMock())
    published = AsyncMock()
    monkeypatch.setattr(autopilot.semantic_view_service, "publish", published)
    load = AsyncMock(return_value={
        "digest": DIGEST, "patterns": [{"pattern_id": "observed"}],
        "scope": Scope.from_user(USER).model_dump(exclude={"session_id"}),
        "authority": "usage_observation", "review_required": True,
    })
    monkeypatch.setattr(usage_learning, "load_scoped_usage", load)
    saved = {}

    async def get(ident, **kwargs):
        return deepcopy(saved.get(ident))

    async def create(fields):
        saved[fields["proposal_id"]] = {
            **deepcopy(fields), "status": "pending", "previewed_at": None,
        }
        return deepcopy(saved[fields["proposal_id"]])

    monkeypatch.setattr(autopilot.rule_proposal_repository, "get", AsyncMock(side_effect=get))
    monkeypatch.setattr(autopilot.rule_proposal_repository, "create", AsyncMock(side_effect=create))
    body = AutopilotProposal(
        operation_id="usage-proposal-1", agent_id="agent", base=REF, usage_digest=DIGEST,
        changes=[SemanticChange(kind="synonyms", name="order_count", synonyms=["recorded orders"])],
    )
    return body, load, published, saved


async def test_summary_uses_live_scope_and_review_gate(monkeypatch):
    _, load, published, _ = setup(monkeypatch)
    result = await autopilot.usage_observations("sales", "agent", USER)
    assert result["review_required"] and result["authority"] == "usage_observation"
    assert load.await_args.args[0] == Scope.from_user(USER)
    assert load.await_args.args[1] == REF
    published.assert_not_awaited()


async def test_same_change_deduplicates_across_operations_and_never_autopublishes(monkeypatch):
    body, _, published, _ = setup(monkeypatch)
    first = await autopilot.propose("sales", body, USER)
    retry = await autopilot.propose(
        "sales", body.model_copy(update={"operation_id": "usage-proposal-2"}), USER,
    )
    assert first["proposal_id"] == retry["proposal_id"]
    assert first["status"] == "pending" and first["previewed_at"] is None
    assert first["details"]["usage_evidence"]["digest"] == DIGEST
    autopilot.rule_proposal_repository.create.assert_awaited_once()
    published.assert_not_awaited()


async def test_new_observations_do_not_create_duplicate_semantic_change(monkeypatch):
    body, load, _, _ = setup(monkeypatch)
    first = await autopilot.propose("sales", body, USER)
    load.return_value = {**load.return_value, "digest": "b" * 64}
    later = await autopilot.propose("sales", body.model_copy(update={
        "operation_id": "usage-proposal-2", "usage_digest": "b" * 64,
    }), USER)
    assert later["proposal_id"] == first["proposal_id"]
    assert later["details"]["usage_evidence"]["digest"] == DIGEST
    autopilot.rule_proposal_repository.create.assert_awaited_once()


async def test_changed_scope_and_unavailable_feature_refuse_learning(monkeypatch):
    _, load, _, _ = setup(monkeypatch)
    monkeypatch.setattr(autopilot.settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False)
    with pytest.raises(HTTPException) as error:
        await autopilot.usage_observations("sales", "agent", USER)
    assert error.value.status_code == 404
    load.assert_not_awaited()


@pytest.mark.parametrize("summary, status", [
    ({"digest": "b" * 64, "patterns": [{"pattern_id": "other"}]}, 409),
    ({"digest": DIGEST, "patterns": []}, 422),
])
async def test_missing_or_changed_usage_cannot_support_candidate(monkeypatch, summary, status):
    body, load, published, _ = setup(monkeypatch)
    load.return_value = summary
    with pytest.raises(HTTPException) as error:
        await autopilot.propose("sales", body, USER)
    assert error.value.status_code == status
    autopilot.rule_proposal_repository.create.assert_not_awaited()
    published.assert_not_awaited()


async def test_approval_requires_exact_preview_and_live_security_scope(monkeypatch):
    body, _, published, saved = setup(monkeypatch)
    created = await autopilot.propose("sales", body, USER)
    monkeypatch.setattr(autopilot.semantic_view_service, "_get", AsyncMock(return_value={
        "active_version": 2,
    }))
    monkeypatch.setattr(autopilot.semantic_view_service, "_version", AsyncMock(return_value={
        "fingerprint": REF.fingerprint,
    }))
    review = AutopilotReview(proposed_fingerprint=created["proposed_fingerprint"])
    with pytest.raises(HTTPException) as error:
        await autopilot.approve("sales", created["proposal_id"], review, USER)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        await autopilot._proposal("sales", created["proposal_id"], {
            **USER, "security_context_version": 4,
        })
    assert error.value.status_code == 404
    saved[created["proposal_id"]]["previewed_at"] = "reviewed"
    wrong = AutopilotReview(proposed_fingerprint="other-definition")
    with pytest.raises(HTTPException) as error:
        await autopilot.approve("sales", created["proposal_id"], wrong, USER)
    assert error.value.status_code == 409
    published.assert_not_awaited()


async def test_reviewed_candidate_calls_existing_regression_publication_gate(monkeypatch):
    body, _, published, saved = setup(monkeypatch)
    created = await autopilot.propose("sales", body, USER)
    proposal_id = created["proposal_id"]
    saved[proposal_id]["previewed_at"] = "reviewed"
    monkeypatch.setattr(autopilot.semantic_view_service, "_get", AsyncMock(return_value={
        "active_version": 2,
    }))
    monkeypatch.setattr(autopilot.semantic_view_service, "_version", AsyncMock(return_value={
        "fingerprint": REF.fingerprint,
    }))
    monkeypatch.setattr(autopilot.semantic_view_service, "describe", AsyncMock(return_value={
        "versions": [{"version": 3, "fingerprint": created["proposed_fingerprint"]}],
    }))
    set_status = AsyncMock()
    monkeypatch.setattr(autopilot.rule_proposal_repository, "set_status", set_status)
    published.side_effect = HTTPException(409, "Regression review required")
    review = AutopilotReview(proposed_fingerprint=created["proposed_fingerprint"])
    with pytest.raises(HTTPException) as error:
        await autopilot.approve("sales", proposal_id, review, USER)
    assert error.value.status_code == 409
    set_status.assert_not_awaited()
    published.side_effect = None
    reviewed = review.model_copy(update={"acknowledge_regressions": True})
    await autopilot.approve("sales", proposal_id, reviewed, USER)
    assert published.await_args.kwargs["acknowledge_regressions"] is True
    assert published.await_args.args == ("sales", 3, USER)
    set_status.assert_awaited_once()


async def test_rejected_or_drifted_draft_never_publishes(monkeypatch):
    body, _, published, saved = setup(monkeypatch)
    created = await autopilot.propose("sales", body, USER)
    ident = created["proposal_id"]
    saved[ident].update(previewed_at="reviewed", status="rejected")
    monkeypatch.setattr(autopilot.semantic_view_service, "_get", AsyncMock(return_value={
        "active_version": 2,
    }))
    monkeypatch.setattr(autopilot.semantic_view_service, "_version", AsyncMock(return_value={
        "fingerprint": REF.fingerprint,
    }))
    review = AutopilotReview(proposed_fingerprint=created["proposed_fingerprint"])
    with pytest.raises(HTTPException):
        await autopilot.approve("sales", ident, review, USER)
    saved[ident]["status"] = "pending"
    monkeypatch.setattr(autopilot.semantic_view_service, "describe", AsyncMock(return_value={
        "versions": [{"version": 3, "fingerprint": "modified-draft"}],
    }))
    with pytest.raises(HTTPException) as error:
        await autopilot.approve("sales", ident, review, USER)
    assert error.value.status_code == 409
    published.assert_not_awaited()
