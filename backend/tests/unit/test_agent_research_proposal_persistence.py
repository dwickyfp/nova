"""Facades refuse identity changes and lost writes before reporting success."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents import deep_research, rule_proposals


@pytest.fixture
def store(monkeypatch):
    store = Mock(actor=SecurityContext("alice", "analyst", session_id="login-1"))
    store.get_research_run = AsyncMock(return_value=None)
    store.update_research_run = AsyncMock()
    store.preview_rule_proposal = AsyncMock(return_value=False)
    store.set_rule_proposal_status = AsyncMock(return_value=False)

    @asynccontextmanager
    async def scope(*args, **kwargs):
        yield store

    monkeypatch.setattr(deep_research, "agent_metadata", scope)
    monkeypatch.setattr(rule_proposals, "agent_metadata", scope)
    return store


async def test_research_update_refuses_missing_or_foreign_run(store):
    with pytest.raises(MetadataConflict):
        await deep_research.deep_research_repository.update("r1", status="running")
    store.update_research_run.assert_not_awaited()


async def test_research_uses_the_locked_revision_and_preserves_dependency_failure(store):
    store.get_research_run.return_value = {"metadata_revision": 7}
    store.update_research_run.side_effect = MetadataConflict()
    with pytest.raises(MetadataConflict):
        await deep_research.deep_research_repository.update("r1", progress=[{"status": "done"}])
    store.get_research_run.assert_awaited_once_with("r1", for_update=True)
    store.update_research_run.assert_awaited_once_with(
        "r1", {"progress_json": [{"status": "done"}]}, expected_revision=7,
    )


async def test_research_stale_run_reports_interruption_without_mutating_journal(store):
    store.get_research_run.return_value = {
        "run_id": "r1", "owner_name": "alice", "status": "running", "progress_json": "[]",
        "created_at": datetime.now(UTC) - timedelta(hours=2),
        "updated_at": datetime.now(UTC) - timedelta(hours=1),
    }
    record = await deep_research.deep_research_repository.get("r1", owner_name="alice")
    assert record["status"] == "interrupted" and record["progress"] == []
    store.update_research_run.assert_not_awaited()


@pytest.mark.parametrize("method", ["mark_previewed", "set_status"])
async def test_proposal_lost_decision_is_a_conflict(store, method):
    kwargs = {"owner_name": "alice", "role_name": "analyst"}
    if method == "set_status":
        kwargs["status"] = "approved"
    with pytest.raises(MetadataConflict):
        await getattr(rule_proposals.rule_proposal_repository, method)("p1", **kwargs)


@pytest.mark.parametrize("owner,role", [("other", "analyst"), ("alice", "admin")])
async def test_proposal_owner_and_role_cannot_substitute_the_current_actor(store, owner, role):
    with pytest.raises(MetadataPermissionDenied):
        await rule_proposals.rule_proposal_repository.set_status(
            "p1", owner_name=owner, role_name=role, status="approved",
        )
    store.set_rule_proposal_status.assert_not_awaited()
