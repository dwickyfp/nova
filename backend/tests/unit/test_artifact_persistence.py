"""Artifact ports keep owner identity and historical specs."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_actor import acting_as
from app.core.metadata_db import MetadataPermissionDenied, MetadataUnavailable
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents import artifact_repository as module
from app.modules.viz import migration


@pytest.fixture
def store(monkeypatch):
    row = {
        "artifact_id": "a1",
        "owner_name": "alice",
        "title": "Revenue",
        "artifact_type": "chart",
        "sql_text": "SELECT 1",
        "bi_object_id": None,
        "updated_at": datetime(2026, 11, 12, tzinfo=UTC),
        "metadata_revision": 7,
        "chart_spec": None,
        "viz_spec": {"historic": True},
    }
    repo = SimpleNamespace(
        get_artifact=AsyncMock(return_value=row),
        update_artifact=AsyncMock(),
        create_artifact=AsyncMock(),
        list_artifacts=AsyncMock(return_value=[]),
        legacy_dashboards=AsyncMock(
            return_value=[{"dashboard_id": "tile-a", "title": "A", "layout_json": {"tiles": []}}]
        ),
        forget_legacy_dashboard=AsyncMock(return_value=True),
        shared_dashboard=AsyncMock(),
    )

    @asynccontextmanager
    async def scope(*args, **kwargs):
        yield repo

    monkeypatch.setattr(module, "agent_metadata", scope)
    monkeypatch.setattr(migration, "agent_metadata", scope)
    engine = AsyncMock(side_effect=AssertionError("Artifact metadata reached StarRocks"))
    monkeypatch.setattr("app.core.database.db.execute_system", engine)
    with acting_as(SecurityContext("alice", "ANALYST", session_id="login")):
        yield repo
    engine.assert_not_awaited()


async def test_artifacts_read_their_historical_specs_unless_they_pointed_at_bi(store):
    repository = module.ArtifactRepository()
    found = await repository.get("a1", owner_name="alice")
    assert found["viz_spec"] == {"historic": True} and found["retired"] is False
    assert not {"bi_kind", "bi_object_id", "bi_view_path", "legacy"} & set(found)
    store.get_artifact.return_value = {**store.get_artifact.return_value, "bi_object_id": 31}
    retired = await repository.get("a1", owner_name="alice")
    assert retired["retired"] is True and retired["viz_spec"] is None
    assert not hasattr(repository, "create_bi") and not hasattr(repository, "attach_bi")


async def test_artifact_owner_and_dependency_refusals_never_use_another_store(store):
    repository = module.ArtifactRepository()
    with pytest.raises(MetadataPermissionDenied):
        await repository.get("a1", owner_name="bob")
    store.get_artifact.assert_not_awaited()
    store.list_artifacts.side_effect = MetadataUnavailable()
    with pytest.raises(MetadataUnavailable):
        await repository.list(owner_name="alice")
    with pytest.raises(MetadataUnavailable):
        await migration.legacy_tile_dashboards("alice")


async def test_legacy_tiles_use_the_same_owner_and_are_removed_after_conversion(store):
    from app.modules.viz.service import legacy_dashboard_artifact_id

    identifier = legacy_dashboard_artifact_id("tile-a")
    assert (await migration.legacy_tile_dashboards("alice"))[0]["artifact_id"] == identifier
    store.list_artifacts.return_value = [{"artifact_id": identifier}]
    assert await migration.legacy_tile_dashboards("alice") == []
    assert await migration.forget_tile_dashboard("alice", "unrelated") == 0
    store.forget_legacy_dashboard.assert_not_awaited()
    assert await migration.forget_tile_dashboard("alice", identifier) == 1
    store.forget_legacy_dashboard.assert_awaited_once_with("tile-a")
