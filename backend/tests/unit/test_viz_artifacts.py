"""The unified artifact model: chart|dashboard artifacts backed by viz specs."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.artifact_repository import ArtifactRepository
from app.modules.agents.studio_router import create_artifact, refresh_artifact
from app.modules.agents.studio_schemas import ArtifactCreateRequest
from app.modules.viz.service import (
    legacy_dashboard_artifact_id,
    viz_service,
    zones_from_legacy_tiles,
)
from app.modules.viz.spec import DashboardSpec, WorksheetSpec

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


@pytest.mark.asyncio
async def test_chart_artifact_stores_its_worksheet(monkeypatch) -> None:
    repository = ArtifactRepository()
    insert = AsyncMock(return_value={"affected": 1})
    monkeypatch.setattr("app.modules.agents.artifact_repository.db.execute_system", insert)
    monkeypatch.setattr(
        repository,
        "get",
        AsyncMock(
            return_value={"artifact_id": "a1", "artifact_type": "chart", "viz_spec": WORKSHEET}
        ),
    )

    await repository.create(
        owner_name="nova_admin",
        title="Revenue by region",
        artifact_type="chart",
        sql_text=WORKSHEET["source"]["sql"],
        database_name=None,
        schema_name=None,
        viz_spec=WORKSHEET,
    )

    stored = insert.call_args.args[1]
    assert stored[5] == "chart"
    assert json.loads(stored[10])["marks"]["type"] == "bar"


@pytest.mark.asyncio
async def test_dashboard_artifact_requires_a_valid_zone_layout(monkeypatch) -> None:
    repository = ArtifactRepository()
    monkeypatch.setattr("app.modules.agents.artifact_repository.db.execute_system", AsyncMock())
    with pytest.raises(ValueError, match="zone layout"):
        await repository.create(
            owner_name="nova_admin",
            title="Dashboard",
            artifact_type="dashboard",
            sql_text="",
            database_name=None,
            schema_name=None,
            viz_spec=None,
        )
    with pytest.raises(ValueError, match="(?i)worksheet"):
        await repository.create(
            owner_name="nova_admin",
            title="Chart",
            artifact_type="chart",
            sql_text="SELECT 1",
            database_name=None,
            schema_name=None,
            viz_spec={"version": 1, "source": None},
        )


@pytest.mark.asyncio
async def test_dashboard_artifact_zones_must_reference_owned_charts(monkeypatch) -> None:
    owned = [
        {"artifact_id": "chart-1", "artifact_type": "chart"},
        {"artifact_id": "other", "artifact_type": "dashboard"},
    ]
    monkeypatch.setattr(
        "app.modules.viz.service.artifact_repository.list",
        AsyncMock(return_value=owned),
    )
    with pytest.raises(ValueError, match="unavailable chart"):
        await viz_service.save_dashboard(
            user=OWNER, title="Sales overview", spec=DashboardSpec.model_validate(DASHBOARD_SPEC)
        )


@pytest.mark.asyncio
async def test_dashboard_route_creates_an_artifact(monkeypatch) -> None:
    """A dashboard save places the owner's BI charts on a BI dashboard."""
    from app.modules.superset import artifacts as bi_artifacts

    saved = {
        "artifact_id": "d1",
        "owner_name": "nova_admin",
        "agent_id": None,
        "thread_id": None,
        "title": "Sales overview",
        "artifact_type": "dashboard",
        "sql_text": "",
        "database_name": None,
        "schema_name": None,
        "chart_spec": None,
        "viz_spec": None,
        "bi_kind": "dashboard",
        "bi_object_id": 7,
        "bi_view_path": "/bi/superset/dashboard/7/?standalone=2",
        "created_at": "2026-10-24 08:00:00",
        "updated_at": "2026-10-24 08:00:00",
    }
    owned = AsyncMock(return_value=[{"artifact_id": "c1", "bi_object_id": 3, "title": "C"}])
    publish = AsyncMock(return_value=saved)
    monkeypatch.setattr(bi_artifacts, "owned_bi_charts", owned)
    monkeypatch.setattr(bi_artifacts, "publish_dashboard", publish)

    created = await create_artifact(
        ArtifactCreateRequest(
            title="Sales overview", artifact_type="dashboard", viz_spec=DASHBOARD_SPEC
        ),
        user=OWNER,
    )

    assert created.artifact_id == "d1"
    assert created.artifact_type == "dashboard"
    assert created.bi_view_path == "/bi/superset/dashboard/7/?standalone=2"
    zone_ids = [zone["artifact_id"] for zone in DASHBOARD_SPEC["zones"] if zone["kind"] == "sheet"]
    assert owned.await_args.args[1] == zone_ids
    assert publish.await_args.kwargs["chart_artifacts"] == owned.return_value


@pytest.mark.asyncio
async def test_viz_chart_refresh_compiles_the_worksheet(monkeypatch) -> None:
    artifact = {
        "artifact_id": "a1",
        "owner_name": "nova_admin",
        "agent_id": None,
        "thread_id": None,
        "title": "Revenue by region",
        "artifact_type": "chart",
        "sql_text": WORKSHEET["source"]["sql"],
        "database_name": None,
        "schema_name": None,
        "chart_spec": None,
        "viz_spec": WORKSHEET,
        "created_at": "2026-10-24 08:00:00",
        "updated_at": "2026-10-24 08:00:00",
    }
    monkeypatch.setattr(
        "app.modules.agents.studio_router.artifact_repository.get",
        AsyncMock(return_value=artifact),
    )
    compute = AsyncMock(
        return_value=SimpleNamespace(
            presmodel={"row_count": 2, "rows": [["East", 391.0]]},
            sql=(
                "SELECT `region`, SUM(`revenue`) AS `sum(revenue)` FROM "
                "(SELECT region, revenue FROM sales.summary) AS nova_source "
                "GROUP BY `region`"
            ),
            columns=["region", "sum(revenue)"],
            rows=[["East", 391.0]],
            row_count=1,
            elapsed_ms=5.0,
        )
    )
    monkeypatch.setattr(viz_service, "compute", compute)

    response = await refresh_artifact("a1", user=OWNER)

    assert response.presmodel["row_count"] == 2
    assert "GROUP BY" in response.sql
    spec = compute.call_args.args[0]
    assert isinstance(spec, WorksheetSpec)
    assert spec.marks.type == "bar"


@pytest.mark.asyncio
async def test_dashboard_refresh_is_redirected_to_the_dashboard_view(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.agents.studio_router.artifact_repository.get",
        AsyncMock(
            return_value={
                "artifact_id": "d1",
                "owner_name": "nova_admin",
                "agent_id": None,
                "thread_id": None,
                "title": "D",
                "artifact_type": "dashboard",
                "sql_text": "",
                "database_name": None,
                "schema_name": None,
                "chart_spec": None,
                "viz_spec": DASHBOARD_SPEC,
            }
        ),
    )
    with pytest.raises(HTTPException, match="dashboard view"):
        await refresh_artifact("d1", user=OWNER)


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
async def test_dashboard_build_creates_charts_and_a_zone_layout(monkeypatch) -> None:
    """Each requested view becomes a BI chart, placed in order on a BI dashboard."""
    from app.modules.agents.tools.dashboard_build import dashboard_build_tool
    from app.modules.assistant.tools import ToolInvocation
    from app.modules.superset import artifacts as bi_artifacts

    saved_charts = [{"artifact_id": f"c{i}", "title": f"Chart {i}"} for i in range(3)]
    publish_chart = AsyncMock(side_effect=saved_charts)
    publish_dashboard = AsyncMock(return_value={"artifact_id": "d1", "title": "Overview"})
    monkeypatch.setattr(bi_artifacts, "publish_chart", publish_chart)
    monkeypatch.setattr(bi_artifacts, "publish_dashboard", publish_dashboard)

    context = SimpleNamespace(
        user=OWNER,
        agent_id=None,
        thread_id="t1",
        database="sales",
        last_result={
            "title": "Sales",
            "columns": ["region", "month", "revenue"],
            "rows": [["West", "2026-01-01", 100]],
            "sql": "SELECT region, month, revenue FROM sales.summary",
        },
    )
    outcome = await dashboard_build_tool.run(
        ToolInvocation(
            "call-1",
            "dashboard_build",
            {
                "title": "Overview",
                "charts": [
                    {"title": "Revenue", "chart_type": "bar", "x": "region", "y": "revenue"},
                    {"title": "Trend", "chart_type": "line", "x": "month", "y": "revenue"},
                    {"title": "Share", "chart_type": "pie"},
                ],
            },
        ),
        context,
    )

    assert outcome.ok
    block = outcome.metadata["dashboard_artifact"]
    assert block["artifact_id"] == "d1"
    assert len(block["charts"]) == 3
    first = publish_chart.await_args_list[0].kwargs
    assert first["sql_text"] == "SELECT region, month, revenue FROM sales.summary"
    assert first["database_name"] == "sales"
    assert (first["plan"].kind, first["plan"].x) == ("bar", "region")
    placed = publish_dashboard.await_args.kwargs["chart_artifacts"]
    assert [chart["artifact_id"] for chart in placed] == ["c0", "c1", "c2"]


async def test_dashboard_build_stops_when_the_role_cannot_author(monkeypatch) -> None:
    from app.modules.agents.tools.dashboard_build import dashboard_build_tool
    from app.modules.assistant.tools import ToolInvocation
    from app.modules.superset import artifacts as bi_artifacts
    from app.modules.superset.client import BiError

    publish_chart = AsyncMock(side_effect=BiError("Your active role cannot", status=403))
    monkeypatch.setattr(bi_artifacts, "publish_chart", publish_chart)
    context = SimpleNamespace(
        user=OWNER,
        agent_id=None,
        thread_id="t1",
        last_result={"columns": ["region", "revenue"], "rows": [], "sql": "SELECT 1"},
    )
    outcome = await dashboard_build_tool.run(
        ToolInvocation(
            "call-1",
            "dashboard_build",
            {"title": "D", "charts": [{"title": "A"}, {"title": "B"}]},
        ),
        context,
    )
    assert not outcome.ok and "cannot" in outcome.error
    assert publish_chart.await_count == 1


@pytest.mark.asyncio
async def test_dashboard_build_requires_provenance_sql(monkeypatch) -> None:
    from app.modules.agents.tools.dashboard_build import dashboard_build_tool
    from app.modules.assistant.tools import ToolInvocation

    context = SimpleNamespace(
        user=OWNER,
        agent_id=None,
        thread_id="t1",
        last_result={"title": "Sales", "columns": ["region"], "rows": [["W"]], "sql": ""},
    )
    outcome = await dashboard_build_tool.run(
        ToolInvocation(
            "call-1",
            "dashboard_build",
            {"title": "Overview", "charts": [{"title": "Revenue"}]},
        ),
        context,
    )
    assert not outcome.ok
    assert "cannot be saved" in outcome.error


@pytest.mark.asyncio
async def test_pre_worksheet_chart_refresh_derives_an_editable_worksheet(monkeypatch) -> None:
    artifact = {
        "artifact_id": "legacy-1",
        "owner_name": "nova_admin",
        "agent_id": None,
        "thread_id": None,
        "title": "Revenue by channel",
        "artifact_type": "chart",
        "sql_text": "SELECT channel, revenue FROM sales.summary",
        "database_name": None,
        "schema_name": None,
        "chart_spec": {
            "mark": "line",
            "encoding": {
                "x": {"field": "channel", "type": "nominal"},
                "y": {"field": "revenue", "type": "quantitative"},
            },
        },
        "viz_spec": None,
        "created_at": "2026-09-23 08:00:00",
        "updated_at": "2026-09-23 08:00:00",
    }
    monkeypatch.setattr(
        "app.modules.agents.studio_router.artifact_repository.get",
        AsyncMock(return_value=artifact),
    )
    result = SimpleNamespace(
        success=True,
        error=None,
        columns=["channel", "revenue"],
        rows=[["Online", 1_500_000_000], ["Retail", 900_000_000]],
        row_count=2,
        elapsed_ms=4.0,
    )
    monkeypatch.setattr(
        "app.modules.agents.studio_router.query_service.execute_statements",
        AsyncMock(return_value=[result]),
    )

    response = await refresh_artifact("legacy-1", user=OWNER)

    derived = WorksheetSpec.model_validate(response.derived_viz_spec)
    assert derived.source.sql == artifact["sql_text"]
    assert derived.marks.type == "line"
    assert [pill.field for pill in derived.columns] == ["channel"]
    assert [pill.label for pill in derived.rows] == ["sum(revenue)"]
    assert response.presmodel is not None
    assert response.presmodel["row_count"] == 2


@pytest.mark.asyncio
async def test_an_empty_query_result_stays_a_plain_result(monkeypatch) -> None:
    artifact = {
        "artifact_id": "table-1",
        "owner_name": "nova_admin",
        "agent_id": None,
        "thread_id": None,
        "title": "Orders",
        "artifact_type": "chart",
        "sql_text": "SELECT region, amount FROM sales.orders",
        "database_name": None,
        "schema_name": None,
        "chart_spec": None,
        "viz_spec": None,
        "created_at": "2026-10-24 08:00:00",
        "updated_at": "2026-10-24 08:00:00",
    }
    monkeypatch.setattr(
        "app.modules.agents.studio_router.artifact_repository.get",
        AsyncMock(return_value=artifact),
    )
    result = SimpleNamespace(
        success=True, error=None, columns=[], rows=[], row_count=0, elapsed_ms=1.0
    )
    monkeypatch.setattr(
        "app.modules.agents.studio_router.query_service.execute_statements",
        AsyncMock(return_value=[result]),
    )
    response = await refresh_artifact("table-1", user=OWNER)
    # No columns: nothing to place, so the artifact stays a plain result.
    assert response.derived_viz_spec is None
    assert response.presmodel is None


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
