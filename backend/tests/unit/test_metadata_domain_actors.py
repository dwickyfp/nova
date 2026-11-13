"""Domain adapters preserve the caller and task-local transaction ownership."""

import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest

from app.core.metadata_actor import acting_as
from app.core.metadata_db import MetadataPermissionDenied
from app.core.metadata_scope import metadata_transaction
from app.modules.access_control.security_context import SecurityContext
from app.modules.intelligence.metadata_repository import intelligence_metadata
from app.modules.intelligence.semantic_views import _reader
from app.modules.ml_engine.registry.repository import ModelRegistryRepository, security_actor
from app.modules.ml_engine.spec import MLSecurityContext
from app.modules.query_autopilot.repository import AutopilotRepository, acting
from app.modules.stages.access import StageAccessDenied
from app.modules.stages.service import caller_actor, stage_metadata

ALICE = SecurityContext("alice", "ANALYST", session_id="login-1", security_context_version=3)


def test_stage_legacy_caller_preserves_the_complete_bound_identity():
    with acting_as(ALICE):
        assert caller_actor("alice", "ANALYST") == ALICE
        for changes in [{"session_id": "other-login"}, {"version": 2}]:
            with pytest.raises(StageAccessDenied):
                caller_actor("alice", "ANALYST", **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": "other-login"},
        {"security_context_version": 2},
        {"principal_type": "SERVICE"},
        {"active_role": "PUBLIC"},
    ],
)
async def test_stage_does_not_join_or_open_independently_for_another_actor(
    fake_metadata_db, changes
):
    async with metadata_transaction(ALICE):
        with pytest.raises(ValueError, match="another actor"):
            async with stage_metadata(replace(ALICE, **changes)):
                pytest.fail("different actor admitted")
    assert len(fake_metadata_db.transactions) == 1


async def test_stage_write_cannot_escape_a_read_only_transaction(fake_metadata_db):
    async with metadata_transaction(ALICE, read_only=True):
        with pytest.raises(ValueError, match="read-only"):
            async with stage_metadata(ALICE, read_only=False):
                pytest.fail("write admitted")


@pytest.mark.parametrize("reader", [_reader, ModelRegistryRepository()._reader])
def test_metadata_read_does_not_substitute_a_service_identity(reader):
    with pytest.raises(MetadataPermissionDenied):
        reader(None)
    with acting_as(ALICE):
        assert reader(None) == ALICE


def test_invalid_semantic_session_cannot_fallback_to_a_bound_identity():
    from fastapi import HTTPException

    with acting_as(ALICE), pytest.raises((HTTPException, ValueError)):
        _reader({"username": "alice", "active_role": "ADMIN", "assigned_roles": ["ANALYST"]})


def test_ml_owner_preserves_the_callers_login():
    spec = MLSecurityContext("alice", "", role="ANALYST", security_context_version=3)
    with acting_as(ALICE):
        assert security_actor(spec) == ALICE
    explicit = MLSecurityContext(
        "alice",
        "",
        role="ANALYST",
        security_context_version=3,
        session_id="login-2",
    )
    with acting_as(ALICE):
        assert security_actor(explicit).session_id == "login-2"


async def test_intelligence_joins_the_owning_unit(fake_metadata_db):
    async with metadata_transaction(ALICE) as outer, intelligence_metadata(ALICE) as repository:
        assert repository.connection is outer.connection
        assert repository.actor == ALICE
    assert len(fake_metadata_db.transactions) == 1


async def test_autopilot_child_does_not_inherit_connection_or_cas_reads(
    fake_metadata_db, monkeypatch
):
    from app.modules.query_autopilot import repository as module

    monkeypatch.setattr(
        module,
        "AutopilotMetadataRepository",
        lambda connection, actor: Mock(
            connection=connection,
            actor=actor,
        ),
    )
    repo = AutopilotRepository()

    async def child():
        with pytest.raises(ValueError, match="not_read"):
            repo._read("policies", "default")
        async with repo._store() as own:
            return own.connection

    with acting(ALICE):
        async with repo.transaction():
            repo._seen("policies", "default", {"metadata_revision": 7})
            async with repo._store() as parent:
                child_connection = await asyncio.create_task(child())
                assert child_connection is not parent.connection
            assert repo._read("policies", "default") == {"metadata_revision": 7}
    assert len(fake_metadata_db.transactions) == 2
    assert all(
        tx.actor == ALICE and tx.outcome == "committed" for tx in fake_metadata_db.transactions
    )


async def test_autopilot_cannot_escalate_a_read_only_unit(fake_metadata_db):
    with acting(ALICE):
        async with metadata_transaction(ALICE, read_only=True):
            with pytest.raises(ValueError, match="read-only"):
                async with AutopilotRepository().transaction():
                    pytest.fail("write admitted")
