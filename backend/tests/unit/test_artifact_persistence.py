"""Artifact ports keep revision conflicts, owner identity, and historical specs."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_actor import acting_as
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied, MetadataUnavailable
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


async def test_source_update_preserves_timestamp_api_and_fences_by_revision(store):
    repository = module.ArtifactRepository()
    current = store.get_artifact.return_value
    store.update_artifact.return_value = {**current, "sql_text": "SELECT 2"}
    assert (
        await repository.update_bi_source(
            "a1",
            owner_name="alice",
            expected_updated_at=current["updated_at"],
            sql_text="SELECT 2",
        )
    )["sql_text"] == "SELECT 2"
    store.update_artifact.assert_awaited_once_with(
        "a1", {"sql_text": "SELECT 2"}, expected_revision=7
    )
    store.update_artifact.reset_mock()
    assert (
        await repository.update_bi_source(
            "a1",
            owner_name="alice",
            expected_updated_at=current["updated_at"] - timedelta(seconds=1),
            sql_text="SELECT 3",
        )
        is None
    )
    store.update_artifact.assert_not_awaited()
    store.update_artifact.side_effect = MetadataConflict()
    assert (
        await repository.update_bi_source(
            "a1",
            owner_name="alice",
            expected_updated_at=current["updated_at"],
            sql_text="SELECT 3",
        )
        is None
    )


async def test_conversion_keeps_id_and_hides_retained_historical_specs(store):
    repository = module.ArtifactRepository()
    store.update_artifact.return_value = {
        **store.get_artifact.return_value,
        "bi_kind": "chart",
        "bi_object_id": 31,
        "bi_dataset_id": 12,
    }
    converted = await repository.attach_bi(
        "a1",
        owner_name="alice",
        bi_kind="chart",
        bi_object_id=31,
        bi_dataset_id=12,
    )
    assert converted["artifact_id"] == "a1" and converted["legacy"] is False
    assert converted["chart_spec"] is None and converted["viz_spec"] is None
    assert converted["bi_view_path"].startswith("/bi/explore/")
    store.update_artifact.assert_awaited_once_with(
        "a1",
        {"bi_kind": "chart", "bi_object_id": 31, "bi_dataset_id": 12},
        expected_revision=7,
    )
    store.get_artifact.return_value = store.update_artifact.return_value
    assert (
        await repository.attach_bi(
            "a1",
            owner_name="alice",
            bi_kind="chart",
            bi_object_id=32,
            bi_dataset_id=12,
        )
        is None
    )
    assert store.update_artifact.await_count == 1


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


async def test_invalid_bi_kind_or_id_never_writes(store):
    repository = module.ArtifactRepository()
    with pytest.raises(ValueError):
        await repository.attach_bi(
            "a1",
            owner_name="alice",
            bi_kind="dashboard",
            bi_object_id=1,
            bi_dataset_id=None,
        )
    with pytest.raises(ValueError):
        await repository.create_bi(
            owner_name="alice",
            title="Chart",
            artifact_type="chart",
            sql_text="SELECT 1",
            database_name=None,
            schema_name=None,
            bi_kind="chart",
            bi_object_id=True,
            bi_dataset_id=None,
        )
    store.create_artifact.assert_not_awaited()
    store.update_artifact.assert_not_awaited()


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
