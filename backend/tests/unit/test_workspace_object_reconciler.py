"""NDV-STORE-009: orphaned Workspace objects are removed only after the retention,
only when no live metadata references them, and only under ``.objects`` or
``.viz-assets``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.exceptions import StorageError
from app.modules.workspaces import object_reconciler
from app.modules.workspaces.object_reconciler import StoredObject, candidates, reconcile

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=2)
FRESH = NOW - timedelta(hours=1)


class Store:
    def __init__(self, objects, *, failing=()):
        self.objects = {item.key: item for item in objects}
        self.failing = set(failing)
        self.deleted: list[str] = []

    def list_objects(self):
        return list(self.objects.values())

    async def delete(self, key):
        if key in self.failing:
            raise StorageError("unavailable", status_code=503)
        self.deleted.append(key)
        del self.objects[key]


def referenced(keys_in_use):
    asked: list[list[str]] = []

    async def lookup(keys):
        asked.append(list(keys))
        return {key for key in keys if key in keys_in_use}

    return lookup, asked


def obj(key, when=OLD):
    return StoredObject(key, when)


async def test_an_old_unreferenced_object_is_removed_and_a_referenced_one_kept():
    orphan = "workspaces/~ws/w1/.objects/e1/aa"
    live = "workspaces/~ws/w1/.objects/e2/bb"
    store = Store([obj(orphan), obj(live)])
    lookup, asked = referenced({live})
    result = await reconcile(store, lookup, now=NOW)
    assert store.deleted == [orphan]
    assert result.summary() == {"examined": 2, "referenced": 1, "removed": 1,
                                "failed": 0, "deferred": 0}
    assert sorted(asked[0]) == sorted([orphan, live])


async def test_a_recent_object_is_never_considered():
    """A write that may still commit is younger than the retention."""
    store = Store([obj("workspaces/alice/.objects/e1/aa", FRESH)])
    lookup, asked = referenced(set())
    result = await reconcile(store, lookup, now=NOW)
    assert store.deleted == [] and asked == [] and result.examined == 0


def test_only_objects_and_dashboard_image_keys_are_candidates():
    """Imported files, version snapshots and notebook outputs are never touched;
    saved objects and dashboard images (0064) are."""
    image = "workspaces/~ws/w1/.viz-assets/d1/a1"
    keys = ["workspaces/alice/report.sql", "workspaces/alice/.versions/e1/1",
            "workspaces/alice/.notebook-outputs/n1/x.json", "workspaces/alice/.objects/e1/aa",
            "workspaces/alice/viz-assets/d1/a1", image]
    assert candidates([obj(key) for key in keys], now=NOW) == [
        "workspaces/alice/.objects/e1/aa", image]


async def test_a_failed_removal_is_counted_and_retried_on_a_later_pass():
    key = "workspaces/alice/.objects/e1/aa"
    store = Store([obj(key)], failing={key})
    lookup, _ = referenced(set())
    assert (await reconcile(store, lookup, now=NOW)).failed == 1
    store.failing.clear()
    assert (await reconcile(store, lookup, now=NOW)).removed == 1
    assert store.objects == {}


async def test_removals_per_pass_are_bounded():
    store = Store([obj(f"workspaces/alice/.objects/e{i}/aa") for i in range(7)])
    lookup, _ = referenced(set())
    result = await reconcile(store, lookup, now=NOW, limit=3)
    assert (result.removed, result.deferred, len(store.objects)) == (3, 4, 4)


async def test_metadata_failure_removes_nothing():
    """Without an answer about references, nothing is assumed unreferenced."""
    store = Store([obj("workspaces/alice/.objects/e1/aa")])

    async def unavailable(keys):
        raise RuntimeError("metadata unavailable")

    with pytest.raises(RuntimeError):
        await reconcile(store, unavailable, now=NOW)
    assert store.deleted == []


async def test_lookups_are_batched(monkeypatch):
    monkeypatch.setattr(object_reconciler, "BATCH", 2)
    store = Store([obj(f"workspaces/alice/.objects/e{i}/aa") for i in range(5)])
    lookup, asked = referenced(set())
    await reconcile(store, lookup, now=NOW)
    assert [len(batch) for batch in asked] == [2, 2, 1]


def test_the_maintenance_worker_runs_the_reconciler():
    import inspect

    from app.worker import maintenance

    assert "object_reconciler.run(stop)" in inspect.getsource(maintenance.MaintenanceWorker)
