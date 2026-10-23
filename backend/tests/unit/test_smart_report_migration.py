from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.modules.smart_reports import migration
from app.modules.smart_reports.service import stable_id
from tests.unit.test_smart_report_lifecycle import MemoryRecords


@pytest.fixture
def inventory(monkeypatch):
    records = MemoryRecords()
    dashboards = [
        [
            f"dashboard-{index:03}",
            "alice",
            f"Report {index}",
            {
                "tiles": [
                    {"artifact_id": "missing", "x": 0, "y": 0, "w": 6, "h": 4},
                ]
            },
            "2026-10-23",
        ]
        for index in range(103)
    ]

    async def execute(sql, params):
        if "CONFIG_STUDIO_DASHBOARDS" in sql:
            assert sql.startswith("SELECT dashboard_id,owner_name,title,layout_json,updated_at")
            return {"rows": [deepcopy(row) for row in dashboards if row[0] > params[0]][:100]}
        assert (
            sql
            == "SELECT title FROM NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS "
            "WHERE artifact_id=%s AND owner_name=%s"
        )
        return {"rows": []}

    @asynccontextmanager
    async def lease(*args, **kwargs):
        yield AsyncMock()

    monkeypatch.setattr(migration.db, "execute_system", execute)
    monkeypatch.setattr(migration, "report_repository", records)
    monkeypatch.setattr(migration, "metadata_lock", lease)
    return records, dashboards


async def test_all_pages_are_migrated_once_and_source_changes_do_not_overwrite_edits(inventory):
    records, dashboards = inventory
    sources = deepcopy(dashboards)
    assert await migration.migrate_dashboards() == {
        "scanned": 103,
        "migrated": 103,
        "source_changed": 0,
    }
    assert len(records.records) == 309
    snapshot = deepcopy(records.records)
    assert await migration.migrate_dashboards() == {
        "scanned": 103,
        "migrated": 0,
        "source_changed": 0,
    }
    assert snapshot == records.records
    report_id = stable_id("legacy-dashboard", dashboards[0][0])
    draft_id = stable_id(report_id, "draft", "alice")
    ledger_id = stable_id("dashboard-migration", dashboards[0][0])
    assert records.records[ledger_id][-1]["payload"]["issues"] == ["tile_0:missing_artifact"]
    widget = records.records[draft_id][0]["payload"]["spec"]["pages"][0]["sections"][0]["widgets"][
        0
    ]
    assert widget["mapping_required"] and widget["query"] is None
    records.records[draft_id][-1]["payload"]["spec"]["title"] = "User's edited draft"
    dashboards[0][2] = "Changed legacy source"
    assert (await migration.migrate_dashboards())["source_changed"] == 1
    assert records.records[ledger_id][-1]["payload"]["status"] == "source_changed"
    assert records.records[draft_id][-1]["payload"]["spec"]["title"] == "User's edited draft"
    dashboards[0] = sources[0]
    await migration.migrate_dashboards()
    assert len(records.records[ledger_id]) == 3
    assert records.records[ledger_id][-1]["payload"]["status"] == "migrated"
    assert len(records.records[draft_id]) == 1
    assert dashboards == sources


async def test_resume_after_lost_ledger_write_recovers_without_duplicate_draft(
    inventory, monkeypatch
):
    records, dashboards = inventory
    del dashboards[1:]
    append = records.append
    failed = False

    async def crash(**kwargs):
        nonlocal failed
        if kwargs["kind"] == "migration" and not failed:
            failed = True
            raise TimeoutError("Ledger write interrupted")
        return await append(**kwargs)

    monkeypatch.setattr(records, "append", crash)
    with pytest.raises(TimeoutError):
        await migration.migrate_dashboards()
    assert len(records.records) == 2
    await migration.migrate_dashboards()
    assert len(records.records) == 3
    assert all(len(history) == 1 for history in records.records.values())
    ledger_id = stable_id("dashboard-migration", dashboards[0][0])
    assert records.records[ledger_id][0]["payload"]["issues"] == ["tile_0:missing_artifact"]


def test_invalid_coordinates_and_oversized_layout_still_produce_bounded_mapping_placeholders():
    spec, issues = migration.replacement_spec(
        "Oversized",
        {
            "tiles": [
                {"x": 0, "y": 10000, "w": 6, "h": 20},
            ]
            * 10001
        },
    )
    assert len(spec.widgets()) == 240
    assert len(spec.pages) == 10
    assert "capacity_exceeded:10001_tiles_source_retained" in issues
    assert all(
        widget.query is None and widget.mapping_required for widget in spec.widgets().values()
    )
    assert "overflow" in spec.widgets()
