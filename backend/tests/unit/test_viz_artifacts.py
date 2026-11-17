"""Legacy viz dashboards: tile migration and zone computation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.viz.service import (
    legacy_dashboard_artifact_id,
    viz_service,
    zones_from_legacy_tiles,
)
from app.modules.viz.spec import DashboardSpec

OWNER = {
    "username": "nova_admin",
    "encrypted_password": "ciphertext",
    "session_id": "session-1",
    "roles": ["analyst"],
    "active_role": "analyst",
}

WORKSHEET = {
    "version": 1,
    "title": "Revenue by region",
    "source": {"kind": "sql", "sql": "SELECT region, revenue FROM sales.summary"},
    "columns": [{"field": "region", "field_type": "nominal", "discrete": True}],
    "rows": [
        {
            "field": "revenue",
            "field_type": "quantitative",
            "aggregation": "sum",
            "discrete": False,
        }
    ],
    "marks": {"type": "bar"},
}

DASHBOARD_SPEC = {
    "version": 1,
    "title": "Sales overview",
    "zones": [
        {
            "zone_id": "z1",
            "kind": "sheet",
            "artifact_id": "chart-1",
            "rect": {"x": 0, "y": 0, "w": 12, "h": 2},
        },
        {
            "zone_id": "z2",
            "kind": "sheet",
            "artifact_id": "chart-2",
            "rect": {"x": 12, "y": 0, "w": 12, "h": 2},
        },
    ],
}


def test_legacy_tiles_map_onto_the_24_column_grid() -> None:
    zones = zones_from_legacy_tiles(
        [
            {"artifact_id": "a", "x": 0, "y": 0, "w": 3, "h": 2},
            {"artifact_id": "b", "x": 3, "y": 0, "w": 3, "h": 2},
            {"artifact_id": "c", "x": 0, "y": 2, "w": 6, "h": 1},
            {"x": 0, "y": 3, "w": 6, "h": 1},  # no artifact: dropped
        ]
    )
    assert [zone["rect"] for zone in zones] == [
        {"x": 0, "y": 0, "w": 12, "h": 2},
        {"x": 12, "y": 0, "w": 12, "h": 2},
        {"x": 0, "y": 2, "w": 24, "h": 1},
    ]
    spec = DashboardSpec.model_validate({"title": "Legacy", "zones": zones})
    assert len(spec.sheet_zones()) == 3


def test_legacy_dashboard_artifact_ids_are_deterministic() -> None:
    assert legacy_dashboard_artifact_id("d1") == legacy_dashboard_artifact_id("d1")
    assert legacy_dashboard_artifact_id("d1") != legacy_dashboard_artifact_id("d2")


@pytest.mark.asyncio
async def test_dashboard_zone_over_a_pre_worksheet_chart_still_computes(monkeypatch) -> None:
    legacy = {
        "artifact_id": "chart-1",
        "artifact_type": "chart",
        "title": "Revenue",
        "sql_text": "SELECT region, revenue FROM sales.summary",
        "chart_spec": {
            "mark": "bar",
            "encoding": {"x": {"field": "region"}, "y": {"field": "revenue"}},
        },
        "viz_spec": None,
        "database_name": None,
        "schema_name": None,
    }
    monkeypatch.setattr(
        "app.modules.viz.service.artifact_repository.list", AsyncMock(return_value=[legacy])
    )
    executed: list[str] = []

    async def execute(sql, user, *, max_rows=500):
        executed.append(sql)
        return SimpleNamespace(
            success=True,
            error=None,
            columns=["region", "sum(revenue)"] if "GROUP BY" in sql else ["region", "revenue"],
            rows=[["East", 10.0]],
            row_count=1,
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(viz_service, "_execute", execute)
    spec = DashboardSpec.model_validate(
        {
            "zones": [
                {
                    "zone_id": "z1",
                    "kind": "sheet",
                    "artifact_id": "chart-1",
                    "rect": {"x": 0, "y": 0, "w": 24, "h": 2},
                }
            ]
        }
    )
    result = await viz_service.compute_dashboard(spec, OWNER)
    zone = result["zones"][0]
    assert "error" not in zone
    assert zone["presmodel"]["rows"] == [["East", 10.0]]
    assert executed[0] == legacy["sql_text"]
