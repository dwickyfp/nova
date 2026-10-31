"""Converting Studio artifacts saved before BI into Superset objects (Phase 6)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.artifact_repository import ArtifactRepository
from app.modules.agents.studio_router import convert_artifacts, get_artifact
from app.modules.agents.studio_schemas import ArtifactConvertRequest
from app.modules.superset import conversion
from app.modules.superset.charts import to_superset, worksheet_conversion
from app.modules.superset.client import BiError

USER = {"username": "analyst", "session_id": "session-1", "active_role": "analyst"}

WORKSHEET = {
    "version": 1,
    "source": {"kind": "sql", "sql": "SELECT region, revenue FROM sales.summary"},
    "columns": [{"field": "region"}],
    "rows": [{"field": "revenue", "field_type": "quantitative", "aggregation": "sum"}],
    "marks": {"type": "bar"},
    "filters": [
        {"kind": "categorical", "field": "region", "values": ["West"], "exclude": True},
        {"kind": "quantitative", "field": "revenue", "min": 10},
    ],
}

LOSSY = {
    **WORKSHEET,
    "rows": [{"field": "revenue", "field_type": "quantitative", "aggregation": "median"}],
    "calculations": [{"name": "Margin", "formula": "SUM([profit]) / SUM([revenue])"}],
    "table_calcs": [{"kind": "running_total", "field": "revenue"}],
    "filters": [
        {"kind": "relative_date", "field": "ordered_at", "range": "last_n_days", "n": 7},
        {"kind": "quantitative", "field": "revenue", "aggregation": "sum", "min": 5},
    ],
    "sort": [{"field": "revenue", "direction": "desc"}],
    "marks": {"type": "bar", "size": {"field": "orders"}},
}


def legacy(artifact_id: str, artifact_type: str = "chart", **fields) -> dict:
    return {
        "artifact_id": artifact_id,
        "owner_name": "analyst",
        "title": artifact_id.title(),
        "artifact_type": artifact_type,
        "sql_text": "SELECT region, revenue FROM sales.summary",
        "schema_name": "sales",
        "database_name": None,
        "chart_spec": None,
        "viz_spec": None,
        "bi_object_id": None,
        "legacy": True,
        **fields,
    }


def test_a_plain_worksheet_converts_with_its_row_filters() -> None:
    plan, blockers, notes = worksheet_conversion(WORKSHEET)

    assert blockers == []
    assert notes == []
    assert plan.kind == "bar"
    assert [(f.column, f.operator, f.values) for f in plan.filters] == [
        ("region", "NOT IN", ["West"]),
        ("revenue", ">=", [10.0]),
    ]
    _viz, form, query = to_superset(
        plan, dataset_id=1, columns=["region", "revenue"], temporal=set()
    )
    assert form["adhoc_filters"][0]["operator"] == "NOT IN"
    assert query["queries"][0]["filters"][0]["op"] == "NOT IN"


def test_features_bi_computes_differently_block_conversion() -> None:
    _plan, blockers, notes = worksheet_conversion(LOSSY)

    text = " | ".join(blockers)
    assert "calculated fields" in text and "Margin" in text
    assert "table calculations (running_total)" in text
    assert "median" in text
    assert "relative date filter on ordered_at" in text
    assert "aggregated value of revenue" in text
    assert "sort order" in notes
    assert "the size mark encoding" in notes


def test_chart_conversion_reads_vega_and_sql_only_artifacts() -> None:
    vega = {
        "mark": "line",
        "encoding": {
            "x": {"field": "region", "type": "nominal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
    }
    plan, blockers, _notes = conversion.chart_conversion(legacy("v", chart_spec=vega))
    assert plan is not None and plan.kind == "line" and blockers == []

    assert conversion.chart_conversion(legacy("s")) == (None, [], [])

    _plan, blockers, _notes = conversion.chart_conversion(
        legacy("bad", viz_spec={"version": 1, "source": None})
    )
    assert blockers and "unreadable worksheet" in blockers[0]


def test_dashboard_conversion_places_converted_charts_and_reports_the_rest() -> None:
    zones = [
        {"kind": "sheet", "artifact_id": "c2", "rect": {"x": 12, "y": 0, "w": 12, "h": 2}},
        {"kind": "sheet", "artifact_id": "c1", "rect": {"x": 0, "y": 0, "w": 8, "h": 2}},
        {"kind": "sheet", "artifact_id": "c3", "rect": {"x": 0, "y": 2, "w": 24, "h": 2}},
        {"kind": "text", "text": {"content": "Notes"}, "rect": {"x": 0, "y": 4, "w": 24, "h": 1}},
        {"kind": "filter", "filter": {"field": "region"}, "rect": {"x": 0, "y": 5, "w": 6, "h": 1}},
    ]
    charts = {
        "c1": {"title": "One", "bi_object_id": 1},
        "c2": {"title": "Two", "bi_object_id": 2},
        "c3": {"title": "Three", "bi_object_id": None},
    }

    placed, texts, blockers, notes = conversion.dashboard_conversion(
        zones, charts, [{"kind": "filter"}]
    )

    assert placed == ["c1", "c2"]
    assert texts == ["Notes"]
    assert blockers == ["charts that are not converted (Three)"]
    assert any("filter on region" in note for note in notes)
    assert any("two charts per row" in note for note in notes)
    assert any("dashboard actions" in note for note in notes)


@pytest.fixture
def bi(monkeypatch):
    """Superset objects and the repository, recorded rather than called."""
    state = {"charts": {}, "dashboards": [], "attached": [], "created": [], "audits": []}

    @asynccontextmanager
    async def for_user(user):
        assert user["username"] == "analyst"
        yield object()

    async def create_chart(_bi, *, title, sql, plan, schema):
        if title == "Broken":
            raise BiError("The dataset could not be created", status=422)
        if title == "Refused":
            raise BiError("Not a BI author", status=403)
        chart_id = 100 + len(state["charts"])
        state["charts"][title] = plan
        return chart_id + 1000, chart_id

    async def create_dashboard(_bi, *, title, chart_artifacts, texts=None):
        state["dashboards"].append((title, [c["artifact_id"] for c in chart_artifacts], texts))
        return 500 + len(state["dashboards"])

    rows: dict[str, dict] = {}

    async def attach(artifact_id, *, owner_name, bi_kind, bi_object_id, bi_dataset_id):
        state["attached"].append((artifact_id, bi_kind, bi_object_id, bi_dataset_id))
        rows[artifact_id] = {**rows[artifact_id], "bi_object_id": bi_object_id, "legacy": False}
        return {**rows[artifact_id], "bi_view_path": f"/bi/{bi_kind}/{bi_object_id}"}

    async def create_bi(**kwargs):
        state["created"].append(kwargs)
        return {"bi_view_path": f"/bi/dashboard/{kwargs['bi_object_id']}"}

    async def list_rows(*, owner_name):
        return list(rows.values())

    async def list_legacy(*, owner_name):
        return [row for row in rows.values() if row["legacy"]]

    monkeypatch.setattr(conversion.SupersetClient, "for_user", for_user)
    monkeypatch.setattr(conversion, "create_chart_objects", create_chart)
    monkeypatch.setattr(conversion, "create_dashboard_object", create_dashboard)
    repo = conversion.artifact_repository
    monkeypatch.setattr(repo, "attach_bi", attach)
    monkeypatch.setattr(repo, "create_bi", create_bi)
    monkeypatch.setattr(repo, "list", list_rows)
    monkeypatch.setattr(repo, "list_legacy", list_legacy)
    monkeypatch.setattr(repo, "ensure_bi_columns", AsyncMock(return_value=[]))
    retire = AsyncMock(return_value=False)
    monkeypatch.setattr(repo, "retire_legacy_columns", retire)
    monkeypatch.setattr(
        conversion,
        "write_audit_log",
        AsyncMock(side_effect=lambda **kw: state["audits"].append(kw)),
    )
    tiles = AsyncMock(return_value=[])
    monkeypatch.setattr("app.modules.viz.migration.legacy_tile_dashboards", tiles)
    state.update(rows=rows, retire=retire, tiles=tiles)
    return state


@pytest.mark.asyncio
async def test_conversion_keeps_ids_and_holds_back_lossy_charts(bi) -> None:
    bi["rows"].update(
        {
            "plain": legacy("plain", viz_spec=WORKSHEET),
            "lossy": legacy("lossy", viz_spec=LOSSY),
            "grid": legacy("grid"),
            "board": legacy(
                "board",
                "dashboard",
                viz_spec={
                    "zones": [
                        {
                            "kind": "sheet",
                            "artifact_id": "plain",
                            "rect": {"x": 0, "y": 0, "w": 12, "h": 2},
                        },
                        {
                            "kind": "sheet",
                            "artifact_id": "lossy",
                            "rect": {"x": 12, "y": 0, "w": 12, "h": 2},
                        },
                    ]
                },
            ),
        }
    )

    outcomes = {o.artifact_id: o for o in await conversion.convert_owner(USER)}

    assert outcomes["plain"].status == "converted"
    assert outcomes["plain"].bi_view_path == "/bi/chart/100"
    assert outcomes["grid"].status == "converted"
    assert bi["charts"]["Grid"] is None  # drawn as a result table
    assert outcomes["lossy"].status == "kept"
    assert outcomes["lossy"].blockers
    assert outcomes["board"].status == "kept"
    assert "Lossy" in outcomes["board"].blockers[0]
    assert {a[0] for a in bi["attached"]} == {"plain", "grid"}
    assert len(bi["audits"]) == 4
    assert {a["status"] for a in bi["audits"]} == {"SUCCESS", "SKIPPED"}
    assert all(a["action"] == "CONVERT_ARTIFACT" for a in bi["audits"])
    bi["retire"].assert_awaited_once()

    forced = {o.artifact_id: o for o in await conversion.convert_owner(USER, force=True)}

    assert forced["lossy"].status == "converted"
    assert forced["board"].status == "converted"
    assert bi["dashboards"] == [("Board", ["plain", "lossy"], [])]
    assert ("board", "dashboard", 501, None) in bi["attached"]


@pytest.mark.asyncio
async def test_converting_a_dashboard_brings_its_legacy_charts(bi) -> None:
    bi["rows"].update(
        {
            "plain": legacy("plain", viz_spec=WORKSHEET),
            "other": legacy("other", viz_spec=WORKSHEET),
            "board": legacy(
                "board",
                "dashboard",
                viz_spec={
                    "zones": [
                        {
                            "kind": "sheet",
                            "artifact_id": "plain",
                            "rect": {"x": 0, "y": 0, "w": 12, "h": 2},
                        }
                    ]
                },
            ),
        }
    )

    outcomes = await conversion.convert_owner(USER, artifact_ids=["board"])

    assert [(o.artifact_id, o.status) for o in outcomes] == [
        ("plain", "converted"),
        ("board", "converted"),
    ]
    assert bi["rows"]["other"]["legacy"] is True


@pytest.mark.asyncio
async def test_tile_dashboards_become_artifacts_under_their_deterministic_id(bi) -> None:
    bi["rows"]["plain"] = {**legacy("plain"), "bi_object_id": 9, "legacy": False}
    bi["tiles"].return_value = [
        {
            "artifact_id": "legacy-dashboard-1",
            "title": "Tiles",
            "zones": [
                {"kind": "sheet", "artifact_id": "plain", "rect": {"x": 0, "y": 0, "w": 24, "h": 2}}
            ],
        }
    ]

    outcomes = await conversion.convert_owner(USER)

    assert [(o.artifact_id, o.status) for o in outcomes] == [("legacy-dashboard-1", "converted")]
    created = bi["created"][0]
    assert created["artifact_id"] == "legacy-dashboard-1"
    assert created["bi_kind"] == "dashboard"


@pytest.mark.asyncio
async def test_failures_are_reported_per_artifact_and_refusals_stop(bi) -> None:
    bi["rows"].update(
        {
            "broken": legacy("broken", viz_spec=WORKSHEET),
            "plain": legacy("plain", viz_spec=WORKSHEET),
        }
    )

    outcomes = {o.artifact_id: o for o in await conversion.convert_owner(USER)}

    assert outcomes["broken"].status == "failed"
    assert outcomes["broken"].error == "The dataset could not be created"
    assert outcomes["plain"].status == "converted"
    assert any(a["status"] == "FAILED" for a in bi["audits"])

    bi["rows"]["refused"] = legacy("refused", viz_spec=WORKSHEET)
    with pytest.raises(BiError):
        await conversion.convert_owner(USER, artifact_ids=["refused"])


@pytest.mark.asyncio
async def test_a_dashboard_view_reports_its_charts_blockers(bi) -> None:
    bi["rows"].update(
        {
            "lossy": legacy("lossy", viz_spec=LOSSY),
            "board": legacy(
                "board",
                "dashboard",
                viz_spec={
                    "zones": [
                        {
                            "kind": "sheet",
                            "artifact_id": "lossy",
                            "rect": {"x": 0, "y": 0, "w": 12, "h": 2},
                        },
                        {
                            "kind": "sheet",
                            "artifact_id": "gone",
                            "rect": {"x": 12, "y": 0, "w": 12, "h": 2},
                        },
                    ]
                },
            ),
        }
    )

    blockers, _notes = await conversion.legacy_report(bi["rows"]["board"])

    assert any(item.startswith("Lossy: ") for item in blockers)
    assert any("no longer exists (gone)" in item for item in blockers)
    assert await conversion.legacy_report({**bi["rows"]["lossy"], "legacy": False}) == ([], [])


@pytest.mark.asyncio
async def test_repository_attaches_bi_in_place_and_retires_columns_when_done(monkeypatch) -> None:
    repository = ArtifactRepository()
    calls: list[tuple[str, list | None]] = []
    remaining = {"count": 1}

    async def execute(sql, params=None):
        calls.append((sql, params))
        if sql.startswith("SELECT COUNT(*)"):
            return {"rows": [[remaining["count"]]]}
        if sql.startswith("DESCRIBE"):
            return {"rows": [["artifact_id"], ["chart_spec"], ["viz_spec"]]}
        return {"affected": 1}

    monkeypatch.setattr("app.modules.agents.artifact_repository.db.execute_system", execute)
    monkeypatch.setattr(repository, "get", AsyncMock(return_value={"artifact_id": "a1"}))

    await repository.attach_bi(
        "a1", owner_name="analyst", bi_kind="chart", bi_object_id=3, bi_dataset_id=4
    )
    update, params = calls[-1]
    assert "chart_spec = NULL, viz_spec = NULL" in update
    assert "bi_object_id IS NULL" in update
    assert params[-2:] == ["a1", "analyst"]

    assert await repository.retire_legacy_columns() is False
    remaining["count"] = 0
    assert await repository.retire_legacy_columns() is True
    assert [sql for sql, _ in calls if "DROP COLUMN" in sql] == [
        "ALTER TABLE NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS DROP COLUMN chart_spec",
        "ALTER TABLE NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS DROP COLUMN viz_spec",
    ]
    assert await repository.retire_legacy_columns() is False
    assert "chart_spec" not in repository._columns()


@pytest.mark.asyncio
async def test_repository_rereads_the_schema_after_another_process_drops_columns(
    monkeypatch,
) -> None:
    repository = ArtifactRepository()
    selects: list[str] = []

    async def execute(sql, params=None):
        if sql.startswith("DESCRIBE"):
            return {"rows": [["artifact_id"], ["bi_kind"], ["bi_object_id"], ["bi_dataset_id"]]}
        if sql.startswith("SELECT"):
            selects.append(sql)
            if "viz_spec" in sql:
                raise RuntimeError("Column 'chart_spec' cannot be resolved.")
            return {"rows": []}
        return {"affected": 0}

    monkeypatch.setattr("app.modules.agents.artifact_repository.db.execute_system", execute)

    assert await repository.list(owner_name="analyst") == []
    assert len(selects) == 2 and "viz_spec" not in selects[1]


@pytest.mark.asyncio
async def test_bi_artifacts_are_inserted_without_the_legacy_columns(monkeypatch) -> None:
    repository = ArtifactRepository()
    execute = AsyncMock(return_value={"affected": 1})
    monkeypatch.setattr("app.modules.agents.artifact_repository.db.execute_system", execute)
    monkeypatch.setattr(repository, "get", AsyncMock(return_value={"artifact_id": "d1"}))

    await repository.create_bi(
        owner_name="analyst",
        title="Board",
        artifact_type="dashboard",
        sql_text="",
        database_name=None,
        schema_name=None,
        bi_kind="dashboard",
        bi_object_id=5,
        bi_dataset_id=None,
        artifact_id="d1",
    )

    statement, params = execute.await_args.args
    assert "viz_spec" not in statement and "chart_spec" not in statement
    assert params[0] == "d1"
    assert statement.count("%s") == len(params)


@pytest.mark.asyncio
async def test_convert_route_returns_outcomes_and_maps_refusals(monkeypatch) -> None:
    convert = AsyncMock(
        return_value=[
            conversion.Outcome("a1", "Revenue", "chart", "kept", ["table calculations"], [])
        ]
    )
    monkeypatch.setattr(conversion, "convert_owner", convert)

    response = await convert_artifacts(
        ArtifactConvertRequest(artifact_ids=["a1"], force=False), user=USER
    )

    assert response.outcomes[0].status == "kept"
    assert response.outcomes[0].blockers == ["table calculations"]
    assert convert.await_args.kwargs == {"artifact_ids": ["a1"], "force": False}

    convert.side_effect = BiError("Your active role cannot author BI content", status=403)
    with pytest.raises(HTTPException) as refused:
        await convert_artifacts(ArtifactConvertRequest(), user=USER)
    assert refused.value.status_code == 403


@pytest.mark.asyncio
async def test_a_legacy_artifact_view_carries_its_conversion_report(monkeypatch) -> None:
    artifact = {
        **legacy("lossy", viz_spec=LOSSY),
        "agent_id": None,
        "thread_id": None,
        "bi_kind": None,
        "bi_dataset_id": None,
        "bi_view_path": None,
        "bi_edit_path": None,
        "created_at": "2026-09-21 08:00:00",
        "updated_at": "2026-09-21 08:00:00",
    }
    monkeypatch.setattr(
        "app.modules.agents.studio_router.artifact_repository.get",
        AsyncMock(return_value=artifact),
    )

    view = await get_artifact("lossy", user=USER)

    assert view.legacy is True
    assert any("table calculations" in item for item in view.legacy_blockers)
    assert "sort order" in view.legacy_notes


@pytest.mark.asyncio
async def test_deleting_the_last_legacy_artifact_forgets_its_source_and_retires_columns(
    monkeypatch,
) -> None:
    from app.modules.agents.studio_router import delete_artifact
    from app.modules.viz.service import legacy_dashboard_artifact_id

    artifact_id = legacy_dashboard_artifact_id("tiles-1")
    statements: list[tuple[str, list | None]] = []

    async def execute(sql, params=None):
        statements.append((sql, params))
        if sql.startswith("SELECT dashboard_id"):
            return {"rows": [["tiles-1"], ["tiles-2"]]}
        return {"affected": 1}

    monkeypatch.setattr("app.modules.viz.migration.db.execute_system", execute)
    repo = conversion.artifact_repository
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=legacy(artifact_id, "dashboard")))
    monkeypatch.setattr(repo, "delete", AsyncMock(return_value=True))
    retire = AsyncMock(return_value=True)
    monkeypatch.setattr(repo, "retire_legacy_columns", retire)
    monkeypatch.setattr("app.modules.agents.studio_router.write_audit_log", AsyncMock())

    await delete_artifact(artifact_id, user=USER)

    deletes = [params for sql, params in statements if sql.startswith("DELETE")]
    assert deletes == [["tiles-1", "analyst"]]
    retire.assert_awaited_once()
