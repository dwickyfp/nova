"""Studio artifacts on Superset: chart translation, publishing and the API client."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from app.modules.superset import artifacts as bi
from app.modules.superset import charts
from app.modules.superset.charts import ChartPlan, Measure
from app.modules.superset.client import BiError, SupersetClient

OWNER = {
    "username": "alice",
    "session_id": "s1",
    "active_role": "finance",
    "roles": ["finance"],
    "security_context_version": 2,
}


def _worksheet(**overrides):
    sheet = {
        "version": 1,
        "source": {"kind": "sql", "sql": "SELECT 1"},
        "columns": [{"field": "region", "field_type": "nominal"}],
        "rows": [{"field": "revenue", "field_type": "quantitative", "aggregation": "sum"}],
        "marks": {"type": "bar"},
    }
    sheet.update(overrides)
    return sheet


# ── Chart plans ──────────────────────────────────────────────────────────


def test_worksheet_bar_becomes_an_axis_chart():
    plan = charts.plan_from_worksheet(_worksheet())
    assert (plan.kind, plan.x, plan.series) == ("bar", "region", None)
    assert plan.measures == [Measure(column="revenue", aggregate="SUM")]


def test_worksheet_date_part_sets_the_time_grain_and_color_stacks_bars():
    plan = charts.plan_from_worksheet(
        _worksheet(
            columns=[{"field": "day", "field_type": "temporal", "date_part": "month"}],
            marks={"type": "bar", "color": {"field": "region"}},
        )
    )
    assert (plan.kind, plan.x, plan.time_grain, plan.series) == (
        "bar_stacked",
        "day",
        "P1M",
        "region",
    )


@pytest.mark.parametrize(
    ("overrides", "kind"),
    [
        ({"marks": {"type": "pie"}}, "pie"),
        ({"marks": {"type": "line"}}, "line"),
        ({"marks": {"type": "circle"}}, "scatter"),
        ({"marks": {"type": "text"}}, "table"),
        ({"columns": []}, "big_number"),
        ({"rows": []}, "table"),
    ],
)
def test_worksheet_marks_map_to_superset_kinds(overrides, kind):
    assert charts.plan_from_worksheet(_worksheet(**overrides)).kind == kind


def test_aggregations_superset_cannot_express_draw_the_computed_value():
    sheet = _worksheet(
        rows=[{"field": "revenue", "field_type": "quantitative", "aggregation": "median"}]
    )
    assert charts.plan_from_worksheet(sheet).measures[0].aggregate == "MAX"


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (
            {"mark": "bar", "encoding": {"x": {"field": "r"}, "y": {"field": "v"}}},
            ("bar", "r", ["v"]),
        ),
        (
            {"mark": {"type": "line"}, "encoding": {"x": {"field": "d"}, "y": {"field": "v"}}},
            ("line", "d", ["v"]),
        ),
        (
            {"mark": "arc", "encoding": {"color": {"field": "r"}, "theta": {"field": "v"}}},
            ("pie", "r", ["v"]),
        ),
        ({"mark": "geoshape", "encoding": {}}, ("table", None, [])),
    ],
)
def test_vega_lite_marks_map_to_plans(spec, expected):
    plan = charts.plan_from_vega(spec)
    assert (plan.kind, plan.x, [m.column for m in plan.measures]) == expected


def test_vega_fields_outside_known_columns_fall_back_to_a_table():
    spec = {"mark": "bar", "encoding": {"x": {"field": "r"}, "y": {"field": "secret"}}}
    plan = charts.plan_from_vega(spec, ["r", "v"])
    assert (plan.kind, plan.columns) == ("table", ["r", "v"])


# ── Superset payloads ────────────────────────────────────────────────────


def test_axis_chart_payload_uses_a_time_axis_for_temporal_columns():
    plan = ChartPlan(kind="line", x="day", time_grain="P1D", measures=[Measure(column="v")])
    viz, form, context = charts.to_superset(
        plan, dataset_id=9, columns=["day", "v"], temporal={"day"}
    )
    assert viz == "echarts_timeseries_line"
    assert form["datasource"] == "9__table" and form["time_grain_sqla"] == "P1D"
    axis = context["queries"][0]["columns"][0]
    assert axis["columnType"] == "BASE_AXIS" and axis["timeGrain"] == "P1D"
    assert context["datasource"] == {"id": 9, "type": "table"}


def test_categorical_axis_and_stacking():
    plan = ChartPlan(kind="bar_stacked", x="r", series="s", measures=[Measure(column="v")])
    viz, form, context = charts.to_superset(
        plan, dataset_id=1, columns=["r", "s", "v"], temporal=set()
    )
    assert viz == "echarts_timeseries_bar" and form["stack"] == "Stack"
    assert context["queries"][0]["columns"] == ["r", "s"]


@pytest.mark.parametrize(
    ("plan", "viz", "key"),
    [
        (ChartPlan(kind="table", columns=["a", "b"]), "table", "all_columns"),
        (
            ChartPlan(kind="table", columns=["a"], measures=[Measure(column="b")]),
            "table",
            "groupby",
        ),
        (ChartPlan(kind="pie", x="a", measures=[Measure(column="b")]), "pie", "metric"),
        (
            ChartPlan(kind="big_number", measures=[Measure(column="b")]),
            "big_number_total",
            "metric",
        ),
        (
            ChartPlan(kind="heatmap", x="a", series="c", measures=[Measure(column="b")]),
            "heatmap_v2",
            "x_axis",
        ),
    ],
)
def test_every_kind_has_a_payload(plan, viz, key):
    result_viz, form, _ = charts.to_superset(
        plan, dataset_id=1, columns=["a", "b", "c"], temporal=set()
    )
    assert result_viz == viz and key in form


def test_a_missing_column_is_refused_before_superset_sees_it():
    plan = ChartPlan(kind="bar", x="region", measures=[Measure(column="gone")])
    with pytest.raises(ValueError, match="gone"):
        charts.to_superset(plan, dataset_id=1, columns=["region"], temporal=set())


# ── Publishing ───────────────────────────────────────────────────────────


class FakeBi:
    """Records Superset calls and answers them like Superset 6.1.0."""

    def __init__(self, *, columns=("region", "total"), fail_chart=False):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.columns = columns
        self.fail_chart = fail_chart
        self.stored_query_context = "{}"

    async def nova_database_id(self) -> int:
        return 1

    async def request(self, method, path, *, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path == "/api/v1/dataset/":
            return {"id": 11}
        if method == "GET" and path.startswith("/api/v1/dataset/"):
            return {
                "result": {
                    "columns": [
                        {"column_name": name, "is_dttm": name == "day"} for name in self.columns
                    ]
                }
            }
        if method == "POST" and path == "/api/v1/chart/":
            if self.fail_chart:
                raise BiError("chart refused", status=422)
            return {"id": 21}
        if method == "POST" and path == "/api/v1/dashboard/":
            return {"id": 31}
        if method == "GET" and path.startswith("/api/v1/chart/"):
            return {"result": {"query_context": self.stored_query_context}}
        if method == "POST" and path == "/api/v1/chart/data":
            if body == {}:
                raise BiError("no query", status=400)
            return {"result": []}
        return {}

    def paths(self, method):
        return [path for verb, path, _ in self.calls if verb == method]


@pytest.fixture
def fake_bi(monkeypatch):
    fake = FakeBi()

    @asynccontextmanager
    async def for_user(user, *, author=True):
        yield fake

    monkeypatch.setattr(SupersetClient, "for_user", for_user)
    monkeypatch.setattr(bi, "write_audit_log", AsyncMock())
    return fake


async def test_publish_chart_creates_dataset_validated_chart_and_pointer(fake_bi, monkeypatch):
    create_bi = AsyncMock(return_value={"artifact_id": "a1"})
    monkeypatch.setattr(bi.artifact_repository, "create_bi", create_bi)

    plan = ChartPlan(kind="bar", x="region", measures=[Measure(column="total")])
    await bi.publish_chart(
        OWNER,
        title="Revenue by region",
        sql_text="SELECT region, SUM(x) AS total FROM t GROUP BY region",
        plan=plan,
        database_name="sales",
        schema_name=None,
    )

    dataset = next(body for verb, path, body in fake_bi.calls if path == "/api/v1/dataset/")
    assert dataset["schema"] == "sales" and dataset["table_name"].startswith(
        "nova_revenue_by_region_"
    )
    assert "/api/v1/chart/data" in fake_bi.paths("POST")
    chart = next(body for verb, path, body in fake_bi.calls if path == "/api/v1/chart/")
    assert chart["viz_type"] == "echarts_timeseries_bar"
    assert json.loads(chart["params"])["x_axis"] == "region"
    stored = create_bi.await_args.kwargs
    assert (stored["bi_kind"], stored["bi_object_id"], stored["bi_dataset_id"]) == (
        "chart",
        21,
        11,
    )
    bi.write_audit_log.assert_awaited()


async def test_a_plan_over_missing_columns_becomes_a_table(fake_bi, monkeypatch):
    monkeypatch.setattr(bi.artifact_repository, "create_bi", AsyncMock(return_value={}))
    await bi.publish_chart(
        OWNER,
        title="t",
        sql_text="SELECT region, total FROM t",
        plan=ChartPlan(kind="bar", x="gone", measures=[Measure(column="total")]),
        database_name=None,
        schema_name=None,
    )
    chart = next(body for verb, path, body in fake_bi.calls if path == "/api/v1/chart/")
    assert chart["viz_type"] == "table"


async def test_a_refused_chart_removes_its_dataset(fake_bi, monkeypatch):
    fake_bi.fail_chart = True
    create_bi = AsyncMock()
    monkeypatch.setattr(bi.artifact_repository, "create_bi", create_bi)
    with pytest.raises(BiError):
        await bi.publish_chart(
            OWNER,
            title="t",
            sql_text="SELECT 1 AS region",
            plan=None,
            database_name=None,
            schema_name=None,
        )
    assert "/api/v1/dataset/11" in fake_bi.paths("DELETE")
    create_bi.assert_not_called()


async def test_write_sql_is_refused_before_superset(fake_bi):
    with pytest.raises(ValueError):
        await bi.publish_chart(
            OWNER,
            title="t",
            sql_text="DROP TABLE t",
            plan=None,
            database_name=None,
            schema_name=None,
        )
    assert fake_bi.calls == []


async def test_publish_dashboard_lays_out_and_links_the_charts(fake_bi, monkeypatch):
    create_bi = AsyncMock(return_value={"artifact_id": "d1"})
    monkeypatch.setattr(bi.artifact_repository, "create_bi", create_bi)
    charts_ = [
        {"bi_object_id": 21, "title": "A"},
        {"bi_object_id": 22, "title": "B"},
        {"bi_object_id": 23, "title": "C"},
    ]

    await bi.publish_dashboard(OWNER, title="Overview", chart_artifacts=charts_)

    body = next(body for verb, path, body in fake_bi.calls if path == "/api/v1/dashboard/")
    layout = json.loads(body["position_json"])
    assert layout["GRID_ID"]["children"] == ["ROW-0", "ROW-1"]
    assert layout["ROW-0"]["children"] == ["CHART-21", "CHART-22"]
    assert layout["CHART-23"]["meta"]["chartId"] == 23
    links = [(path, body) for verb, path, body in fake_bi.calls if verb == "PUT"]
    assert links == [(f"/api/v1/chart/{i}", {"dashboards": [31]}) for i in (21, 22, 23)]
    assert create_bi.await_args.kwargs["bi_kind"] == "dashboard"


async def test_dashboards_place_only_owned_bi_charts(monkeypatch):
    rows = {
        "c1": {"artifact_type": "chart", "bi_object_id": 3},
        "old": {"artifact_type": "chart", "bi_object_id": None},
    }
    monkeypatch.setattr(
        bi.artifact_repository,
        "get",
        AsyncMock(side_effect=lambda artifact_id, owner_name: rows.get(artifact_id)),
    )
    assert len(await bi.owned_bi_charts(OWNER, ["c1"])) == 1
    for refused in (["old"], ["someone-elses"]):
        with pytest.raises(ValueError):
            await bi.owned_bi_charts(OWNER, refused)


async def test_an_edit_keeps_a_chart_that_still_draws(fake_bi, monkeypatch):
    fake_bi.stored_query_context = json.dumps({"queries": [{}]})
    update = AsyncMock(return_value={"artifact_id": "a1"})
    monkeypatch.setattr(bi.artifact_repository, "update_bi_source", update)
    artifact = {"artifact_id": "a1", "bi_dataset_id": 11, "bi_object_id": 21}

    await bi.update_chart_source(
        OWNER,
        artifact,
        sql_text="SELECT region, total FROM t",
        plan=None,
        expected_updated_at="2026-10-30 00:00:00",
    )

    assert ("PUT", "/api/v1/dataset/11", {"sql": "SELECT region, total FROM t"}) in fake_bi.calls
    assert "/api/v1/dataset/11/refresh" in fake_bi.paths("PUT")
    assert "/api/v1/chart/21" not in fake_bi.paths("PUT")
    update.assert_awaited()


async def test_an_edit_that_breaks_the_chart_redraws_it_as_a_table(fake_bi, monkeypatch):
    monkeypatch.setattr(
        bi.artifact_repository, "update_bi_source", AsyncMock(return_value={"artifact_id": "a1"})
    )
    artifact = {"artifact_id": "a1", "bi_dataset_id": 11, "bi_object_id": 21}
    await bi.update_chart_source(
        OWNER, artifact, sql_text="SELECT region FROM t", plan=None, expected_updated_at="x"
    )
    redraw = next(
        body for verb, path, body in fake_bi.calls if path == "/api/v1/chart/21" and verb == "PUT"
    )
    assert redraw["viz_type"] == "table"


async def test_deleting_removes_superset_objects_best_effort(fake_bi):
    await bi.delete_bi_objects(
        OWNER, {"artifact_id": "a1", "bi_kind": "chart", "bi_object_id": 21, "bi_dataset_id": 11}
    )
    assert fake_bi.paths("DELETE") == ["/api/v1/chart/21", "/api/v1/dataset/11"]
    fake_bi.calls.clear()
    await bi.delete_bi_objects(OWNER, {"artifact_id": "legacy", "bi_kind": None})
    assert fake_bi.calls == []


# ── Client ───────────────────────────────────────────────────────────────


async def test_a_viewer_role_is_refused_before_superset_is_called(monkeypatch):
    from app.modules.superset import access, tokens

    monkeypatch.setattr(access, "author_roles", AsyncMock(return_value=frozenset()))
    issue = AsyncMock()
    monkeypatch.setattr(tokens, "issue_handle", issue)
    with pytest.raises(BiError) as refused:
        async with SupersetClient.for_user(OWNER):
            pass
    assert refused.value.status == 403
    issue.assert_not_called()


async def test_client_sends_csrf_and_maps_refusals(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/security/csrf_token/"):
            return httpx.Response(200, json={"result": "csrf-1"})
        if request.url.path.endswith("/database/"):
            return httpx.Response(200, json={"result": [{"database_name": "Nova", "id": 4}]})
        if request.url.path.endswith("/chart/"):
            return httpx.Response(403, json={"message": "Forbidden"})
        return httpx.Response(422, json={"message": {"sql": ["bad"]}})

    client = SupersetClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://bi/bi")
    )
    assert await client.nova_database_id() == 4
    with pytest.raises(BiError) as forbidden:
        await client.request("POST", "/api/v1/chart/", body={})
    assert forbidden.value.status == 403 and "BI author" in forbidden.value.message
    assert seen[-1].headers["X-CSRFToken"] == "csrf-1"
    with pytest.raises(BiError) as invalid:
        await client.request("PUT", "/api/v1/dataset/1", body={})
    assert invalid.value.status == 422 and "bad" in invalid.value.message


# ── Studio route ─────────────────────────────────────────────────────────


async def test_saving_a_chart_publishes_it_with_the_described_plan(monkeypatch):
    from app.modules.agents.studio_router import create_artifact
    from app.modules.agents.studio_schemas import ArtifactCreateRequest

    published = {
        "artifact_id": "a1",
        "owner_name": "alice",
        "agent_id": None,
        "thread_id": None,
        "title": "T",
        "artifact_type": "chart",
        "sql_text": "SELECT 1",
        "database_name": "db",
        "schema_name": None,
        "chart_spec": None,
        "viz_spec": None,
        "bi_kind": "chart",
        "bi_object_id": 21,
        "bi_view_path": "/bi/explore/?slice_id=21&standalone=1",
        "created_at": "2026-10-30 00:00:00",
        "updated_at": "2026-10-30 00:00:00",
    }
    publish = AsyncMock(return_value=published)
    monkeypatch.setattr(bi, "publish_chart", publish)
    view = await create_artifact(
        ArtifactCreateRequest(
            title="T",
            artifact_type="chart",
            sql_text="SELECT r, v FROM t",
            database_name="db",
            chart_spec={"mark": "line", "encoding": {"x": {"field": "r"}, "y": {"field": "v"}}},
        ),
        user=OWNER,
    )
    assert view.bi_view_path.endswith("slice_id=21&standalone=1")
    assert publish.await_args.kwargs["plan"].kind == "line"

    publish.side_effect = BiError("Your active role cannot", status=403)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        await create_artifact(
            ArtifactCreateRequest(title="T", artifact_type="chart", sql_text="SELECT 1"),
            user=OWNER,
        )
    assert refused.value.status_code == 403
