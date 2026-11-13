"""Additive persistence contract for agent and semantic intelligence."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import app.modules.intelligence.metadata_repository as metadata
from app.core.metadata_actor import acting_as
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents.repository import AGENTS_DDL, agent_repository


@pytest.fixture(autouse=True)
def caller():
    with acting_as(SecurityContext("alice", "ANALYST", session_id="semantic-login",
                                   security_context_version=7)) as actor:
        yield actor


def test_runtime_schema_contains_backward_compatible_agent_columns():
    assert "discoverable_skills" in AGENTS_DDL
    assert "compiled_instructions" in AGENTS_DDL
    assert "harness_mode" in AGENTS_DDL


def _store(monkeypatch, repo):
    actors = []

    @asynccontextmanager
    async def transaction(actor, *, read_only=False):
        actors.append((actor, read_only))
        yield repo

    monkeypatch.setattr(metadata, "intelligence_metadata", transaction)
    return actors


@pytest.mark.asyncio
async def test_semantic_usage_is_written_and_read_as_the_bound_caller(monkeypatch):
    repo = AsyncMock()
    repo.semantic_usage.return_value = [{
        "owner_name": "alice", "active_role": "ANALYST", "security_context_version": 7,
        "semantic_model_id": "view", "model_fingerprint": "f", "semantic_version": 2,
        "metric_names": ["revenue"], "dimension_names": [], "filter_shape": [],
        "time_grain": None, "succeeded": True, "execution_latency_ms": 4,
        "created_at": datetime.now(UTC), "usage_id": "u-1", "provenance": "runtime",
    }]
    actors = _store(monkeypatch, repo)
    scope = dict(owner_name="alice", active_role="ANALYST", security_context_version=7)

    await agent_repository.record_semantic_usage(
        **scope, semantic_model_id="view", model_fingerprint="f", metrics=["revenue"],
        dimensions=[], filter_shape=[{"field": "region", "operator": "="}], time_grain=None,
        execution_latency_ms=4, succeeded=True, semantic_version=2,
    )
    rows = await agent_repository.list_semantic_usage(
        **scope, semantic_model_id="view", model_fingerprint="f", semantic_version=2,
    )

    written, read = actors
    assert (written[0].principal, written[0].active_role,
            written[0].security_context_version) == ("alice", "ANALYST", 7)
    assert written[0].session_id == "semantic-login"
    assert written[1] is False and read[1] is True and read[0] == written[0]
    usage = repo.record_semantic_usage.await_args.args[0]
    assert usage["filter_shape"] == [{"field": "region", "operator": "="}]
    assert usage["semantic_version"] == 2 and usage["usage_id"]
    assert repo.record_semantic_usage.await_args.kwargs == {"verified_query_id": None}
    repo.semantic_usage.assert_awaited_once_with("view", "f", 2, limit=2000)
    assert rows == [{
        "owner_name": "alice", "active_role": "ANALYST", "security_context_version": 7,
        "semantic_model_id": "view", "model_fingerprint": "f", "semantic_version": 2,
        "metrics": ["revenue"], "dimensions": [], "filter_shape": [], "time_grain": None,
        "succeeded": True, "execution_latency_ms": 4,
    }]


@pytest.mark.asyncio
async def test_semantic_usage_without_role_or_version_fails_closed(monkeypatch):
    repo = AsyncMock()
    actors = _store(monkeypatch, repo)
    with pytest.raises(ValueError):
        await agent_repository.record_semantic_usage(
            owner_name="alice", semantic_model_id="view", model_fingerprint="f", metrics=[],
            dimensions=[], filter_shape=[], time_grain=None, execution_latency_ms=1,
            succeeded=True,
        )
    assert actors == []
    repo.record_semantic_usage.assert_not_awaited()


def test_additive_migration_preserves_legacy_agent_fields():
    root = Path(__file__).resolve().parents[2]
    sql = (root / "migrations" / "20260921_agent_semantic_intelligence.sql").read_text()
    assert "ALTER TABLE NOVA_SYSTEM.CONFIG_AGENTS ADD COLUMN discoverable_skills" in sql
    assert "DROP COLUMN" not in sql.upper()
    assert "CONFIG_SEMANTIC_VERIFIED_QUERIES" in sql


@pytest.mark.parametrize("scope", [
    {"owner_name": "bob", "active_role": "ANALYST", "security_context_version": 7},
    {"owner_name": "alice", "active_role": "VIEWER", "security_context_version": 7},
    {"owner_name": "alice", "active_role": "ANALYST", "security_context_version": 6},
])
async def test_usage_cannot_replace_the_bound_actor(monkeypatch, scope):
    from app.core.metadata_db import MetadataPermissionDenied

    repo = AsyncMock()
    actors = _store(monkeypatch, repo)
    with pytest.raises(MetadataPermissionDenied):
        await agent_repository.list_semantic_usage(
            **scope, semantic_model_id="view", model_fingerprint="f", semantic_version=2)
    assert actors == []
