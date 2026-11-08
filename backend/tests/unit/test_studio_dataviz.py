"""Studio Dataviz: preview references, the capability resolver, the publish saga,
dashboard context and the assistant tools (docs/arch-19-studio-dataviz.md)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.superset import capability, dashboard_context, previews, publication
from app.modules.superset.charts import ChartPlan, to_superset
from app.modules.superset.client import BiError, SupersetClient
from app.modules.superset.openui_nodes import data_nodes
from app.modules.superset.previews import Locator, PreviewError

ALICE = {
    "username": "alice",
    "session_id": "s1",
    "active_role": "finance",
    "roles": ["finance"],
    "security_context_version": 3,
}
STAMP = {
    "principal": "alice",
    "active_role": "finance",
    "security_context_version": 3,
    "session_id": "s1",
}
SQL = "SELECT brand, SUM(qty) AS units FROM sales GROUP BY brand"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(previews.settings, "SECRET_KEY", "test-signing-key")


@pytest.fixture(autouse=True)
def _superset_up(monkeypatch):
    async def available():
        return True

    monkeypatch.setattr(capability, "provider_available", available)


# ── OpenUI analysis ─────────────────────────────────────────────────────────


def test_openui_nodes_find_charts_their_evidence_and_transforms():
    program = """$sel = -1
rows = @Filter(@Evidence("e2"), "region", "==", $r)
route = GeoMap(@Evidence("e3"), "lon", "lat", null, "name", "Routes", $sel,
  {layer: "route", sequence: "stop_seq"})
root = Col([Kpi(@Evidence("e1"), "total", "Total"), Table(rows, ["a"], "T"), route,
  Chart(@Sort(@Evidence("e1"), "units", "desc"), "brand", "units", "bar", "By brand")])"""
    nodes = {node.component: node for node in data_nodes(program)}
    assert nodes["Kpi"].evidence_id == "e1" and nodes["Kpi"].transforms == []
    assert [t.name for t in nodes["Table"].transforms] == ["Filter"]
    assert nodes["GeoMap"].prop("options") == {"layer": "route", "sequence": "stop_seq"}
    chart = nodes["Chart"]
    assert (chart.evidence_id, chart.prop("x"), chart.prop("y"), chart.prop("type")) == (
        "e1",
        "brand",
        "units",
        "bar",
    )
    assert [t.name for t in chart.transforms] == ["Sort"]


def test_openui_expressions_it_does_not_model_are_opaque_not_guessed():
    program = 'root = Col([Chart(@Evidence("e1") + 1, "x", "y", "bar")])'
    (node,) = data_nodes(program)
    assert node.opaque and node.evidence_id is None


# ── Signed references ───────────────────────────────────────────────────────


def test_a_reference_is_bound_to_its_principal_and_cannot_be_edited():
    locator = Locator("t1", "m1", "content-1")
    ref = previews.sign(locator, ALICE)
    assert previews.verify(ref, ALICE) == locator
    with pytest.raises(PreviewError):
        previews.verify(ref, {**ALICE, "username": "bob"})
    prefix, payload, mac = ref.split(".")
    forged = previews._b64(json.dumps({"t": "t1", "m": "m2", "c": "content-1"}).encode())
    with pytest.raises(PreviewError):
        previews.verify(f"{prefix}.{forged}.{mac}", ALICE)


# ── Resolving from Nova's own record ────────────────────────────────────────


def _message(steps, *, stamp=STAMP):
    async def execute_system(sql, params=None):
        assert "user_name" in sql and params[-1] == "alice"
        return {"rows": [("assistant", json.dumps(stamp), json.dumps(steps), "agent-1")]}

    return execute_system


def _semantic_step(tool_call_id="q1", evidence_id="evidence_1", sql=SQL):
    return {
        "kind": "tool",
        "name": "semantic_query",
        "tool_call_id": tool_call_id,
        "evidence_id": evidence_id,
        "status": "done",
        "preview": sql,
        "semantic_evidence": {
            "view_id": "v1",
            "metrics": ["units"],
            "business_context": {
                "ontology_release_id": "rel-1",
                "manifest_hash": "h",
                "concepts": [
                    {
                        "id": "c1",
                        "qualified_name": "Sales.Units",
                        "label": "Units",
                        "definition": "Units ordered",
                        "metric": "units",
                    }
                ],
            },
        },
    }


def _ui_block(program, rows):
    return {
        "kind": "ui",
        "content_id": "content-2",
        "program": program,
        "evidence": {"evidence_1": rows},
        "provenance": {"evidence_ids": ["evidence_1"], "tool_calls": {"evidence_1": "q1"}},
    }


ROWS = [{"brand": "A", "units": 3}, {"brand": "B", "units": 5}]
BAR = (
    'root = Col([Chart(@Sort(@Evidence("evidence_1"), "units", "desc"), '
    '"brand", "units", "bar", "Units")])'
)
BAR_NODE = {
    "component": "Chart",
    "evidence_id": "evidence_1",
    "x": "brand",
    "y": "units",
    "type": "bar",
    "title": "Units",
}


async def test_an_interactive_chart_resolves_to_its_exact_producing_query(monkeypatch):
    monkeypatch.setattr(
        previews.db, "execute_system", _message([_semantic_step(), _ui_block(BAR, ROWS)])
    )
    preview = await previews.resolve(Locator("t1", "m1", "content-2", BAR_NODE), ALICE)
    assert preview.sql == SQL and preview.provenance == "tool_step"
    assert preview.render_kind == "chart" and preview.columns == ["brand", "units"]
    assert [t["name"] for t in preview.transforms] == ["Sort"]
    assert preview.business_context["concepts"][0]["qualified_name"] == "Sales.Units"


async def test_a_table_without_its_producer_has_no_provenance_and_no_guess(monkeypatch):
    # An earlier query exists in the message, but the table names another call.
    steps = [
        _semantic_step(tool_call_id="other"),
        {
            "kind": "table",
            "content_id": "content-1",
            "tool_call_id": "missing",
            "columns": ["brand"],
            "rows": [["A"]],
        },
    ]
    monkeypatch.setattr(previews.db, "execute_system", _message(steps))
    preview = await previews.resolve(Locator("t1", "m1", "content-1"), ALICE)
    assert preview.sql is None and preview.provenance == "none"
    result = await capability.check(preview)
    assert result.status == "UNKNOWN" and result.reason_code == "NO_PROVENANCE"


async def test_a_chart_whose_recorded_source_differs_from_its_producer_is_refused(monkeypatch):
    steps = [
        _semantic_step(),
        {
            "kind": "tool",
            "name": "data_to_chart",
            "tool_call_id": "c1",
            "status": "done",
            "arguments": {"evidence_id": "evidence_1"},
        },
        {
            "kind": "chart",
            "content_id": "content-3",
            "tool_call_id": "c1",
            "viz_spec": {"source": {"sql": "SELECT 1"}, "rows": [], "columns": []},
            "chart_spec": json.dumps({"mark": "bar"}),
        },
    ]
    monkeypatch.setattr(previews.db, "execute_system", _message(steps))
    preview = await previews.resolve(Locator("t1", "m1", "content-3"), ALICE)
    assert preview.sql is None and "differs" in (preview.provenance_issue or "")


async def test_a_result_from_another_role_is_not_resolved(monkeypatch):
    monkeypatch.setattr(
        previews.db,
        "execute_system",
        _message([_semantic_step(), _ui_block(BAR, ROWS)], stamp={**STAMP, "active_role": "hr"}),
    )
    with pytest.raises(PreviewError):
        await previews.resolve(Locator("t1", "m1", "content-2", BAR_NODE), ALICE)


async def test_a_node_the_program_does_not_contain_is_refused(monkeypatch):
    monkeypatch.setattr(
        previews.db, "execute_system", _message([_semantic_step(), _ui_block(BAR, ROWS)])
    )
    with pytest.raises(PreviewError):
        await previews.resolve(
            Locator("t1", "m1", "content-2", {**BAR_NODE, "y": "revenue"}), ALICE
        )


# ── Capability ──────────────────────────────────────────────────────────────


def _preview(**overrides):
    base = dict(
        locator=Locator("t1", "m1", "content-2", BAR_NODE),
        render_kind="chart",
        title="Units",
        sql=SQL,
        provenance="tool_step",
        producer_tool="semantic_query",
        columns=["brand", "units"],
        visualization={"component": "Chart", "x": "brand", "y": "units", "type": "bar"},
        transforms=[{"name": "Sort", "args": ["units", "desc"], "exact": True}],
        rows=ROWS,
    )
    base.update(overrides)
    return previews.ResolvedPreview(**base)


async def test_a_sorted_bar_chart_is_supported_with_its_order():
    result = await capability.check(_preview())
    assert result.status == "SUPPORTED" and result.direct_publish_allowed
    assert result.plan.kind == "bar" and result.plan.order == "value_desc"


async def test_a_locally_filtered_chart_is_not_published_over_the_unfiltered_query():
    result = await capability.check(
        _preview(transforms=[{"name": "Filter", "args": ["region"], "exact": True}])
    )
    assert result.status == "UNSUPPORTED"
    assert result.reason_code == "DERIVED_RESULT_NOT_REPRODUCIBLE"
    assert result.plan is None and result.alternatives[0].kind == "requery"


async def test_several_rows_per_category_would_be_summed_so_it_is_unsupported():
    rows = [{"brand": "A", "units": 1}, {"brand": "A", "units": 2}]
    result = await capability.check(_preview(rows=rows))
    assert result.status == "UNSUPPORTED" and result.reason_code == "NON_UNIQUE_CATEGORY"


async def test_a_route_map_offers_no_point_map_and_needs_consent():
    preview = _preview(
        render_kind="route_map",
        visualization={
            "component": "GeoMap",
            "longitude": "lon",
            "latitude": "lat",
            "label": "stop",
            "sequence": "stop_seq",
        },
        columns=["stop", "lon", "lat", "stop_seq", "km"],
        transforms=[],
    )
    result = await capability.check(preview)
    assert result.status == "CONVERTIBLE" and not result.direct_publish_allowed
    strategies = [alt.strategy_id for alt in result.alternatives]
    assert "table.route_stops" in strategies
    assert not any("point" in name for name in strategies)
    table = next(a for a in result.alternatives if a.strategy_id == "table.route_stops")
    assert table.requires_consent and "the map" in table.lost and table.meaning == "partial"


async def test_a_computed_route_is_unsupported_and_explained():
    preview = _preview(
        render_kind="route_map",
        sql=None,
        provenance="none",
        producer_tool="route_optimization",
        transforms=[],
        visualization={"component": "GeoMap", "sequence": "stop_seq"},
    )
    result = await capability.check(preview)
    assert result.status == "UNSUPPORTED"
    assert result.reason_code == "COMPUTED_RESULT_NOT_QUERYABLE"
    assert result.plans == {} and "route_optimization" in result.message


async def test_superset_down_is_unknown_never_supported(monkeypatch):
    async def down():
        return False

    monkeypatch.setattr(capability, "provider_available", down)
    result = await capability.check(_preview())
    assert result.status == "UNKNOWN" and result.reason_code == "PROVIDER_UNAVAILABLE"


async def test_an_unknown_vega_mark_never_becomes_a_table():
    preview = _preview(
        locator=Locator("t1", "m1", "content-3"),
        transforms=[],
        chart_spec={"mark": "rule", "encoding": {}},
        viz_spec=None,
    )
    result = await capability.check(preview)
    assert result.status == "UNSUPPORTED" and result.plan is None


async def test_a_kpi_over_one_row_becomes_a_big_number():
    preview = _preview(
        render_kind="kpi",
        rows=[{"total": 9}],
        columns=["total"],
        transforms=[],
        visualization={"component": "Kpi", "column": "total"},
    )
    result = await capability.check(preview)
    assert result.status == "SUPPORTED" and result.plan.kind == "big_number"


# ── Layout merge ────────────────────────────────────────────────────────────


LAYOUT = {
    "DASHBOARD_VERSION_KEY": "v2",
    "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
    "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": ["ROW-1"], "parents": ["ROOT_ID"]},
    "ROW-1": {
        "type": "ROW",
        "id": "ROW-1",
        "children": ["CHART-1"],
        "parents": ["ROOT_ID", "GRID_ID"],
    },
    "CHART-1": {
        "type": "CHART",
        "id": "CHART-1",
        "children": [],
        "parents": ["ROOT_ID", "GRID_ID", "ROW-1"],
        "meta": {"chartId": 5},
    },
}


def test_adding_a_chart_keeps_every_node_and_adds_one_row():
    layout, where = publication.append_chart(LAYOUT, 9, "New", "Sales")
    assert {k: v for k, v in layout.items() if k in LAYOUT} == {
        **LAYOUT,
        "GRID_ID": {**LAYOUT["GRID_ID"], "children": layout["GRID_ID"]["children"]},
    }
    assert publication._chart_ids(layout) == {5, 9}
    assert layout["GRID_ID"]["children"][0] == "ROW-1" and "bottom" in where


def test_a_chart_already_placed_is_not_added_twice():
    layout, where = publication.append_chart(LAYOUT, 5, "Old", "Sales")
    assert layout == LAYOUT and where == "already on the dashboard"


def test_a_tabbed_dashboard_gets_the_chart_in_its_first_tab():
    tabs = {
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["TABS-1"]},
        "TABS-1": {"type": "TABS", "id": "TABS-1", "children": ["TAB-a"], "parents": ["ROOT_ID"]},
        "TAB-a": {
            "type": "TAB",
            "id": "TAB-a",
            "children": [],
            "parents": ["ROOT_ID", "TABS-1"],
            "meta": {"text": "Overview"},
        },
    }
    layout, where = publication.append_chart(tabs, 9, "New", "Sales")
    assert len(layout["TAB-a"]["children"]) == 1 and "Overview" in where


# ── The publish saga ────────────────────────────────────────────────────────


class FakeSuperset:
    """Superset 6.1.0 for the calls the saga makes, with failure switches."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.dashboards = {
            7: {
                "id": 7,
                "dashboard_title": "Sales",
                "owners": [{"id": 1}],
                "position_json": json.dumps(LAYOUT),
                "json_metadata": "{}",
                "changed_on_utc": "t0",
            }
        }
        self.charts: dict[int, dict] = {5: {"id": 5, "dashboards": [{"id": 7}, {"id": 8}]}}
        self.datasets: dict[int, dict] = {}
        self.next_id = 100
        self.fail_chart_data = False
        self.fail_dashboard_put = False
        self.change_during_attach = False

    async def nova_database_id(self):
        return 1

    async def request(self, method, path, *, body=None, headers=None):
        self.calls.append((method, path))
        if path == "/api/v1/me/":
            return {"result": {"id": 1}}
        if method == "GET" and path.startswith("/api/v1/dataset/?q="):
            return {"result": [{"id": i, "owners": [{"id": 1}]} for i in self.datasets]}
        if method == "POST" and path == "/api/v1/dataset/":
            self.next_id += 1
            self.datasets[self.next_id] = {"sql": body["sql"]}
            return {"id": self.next_id}
        if (
            method == "GET"
            and path.startswith("/api/v1/dataset/")
            and path.endswith("related_objects")
        ):
            return {"charts": {"count": 0}}
        if method == "GET" and path.startswith("/api/v1/dataset/"):
            dataset_id = int(path.rsplit("/", 1)[1])
            return {
                "result": {
                    **self.datasets.get(dataset_id, {}),
                    "columns": [
                        {"column_name": "brand"},
                        {"column_name": "units"},
                        {"column_name": "order_date", "is_dttm": True},
                    ],
                }
            }
        if method == "DELETE":
            kind, object_id = path.split("/")[3], int(path.rsplit("/", 1)[1])
            (self.charts if kind == "chart" else self.datasets).pop(object_id, None)
            return {}
        if path.startswith("/api/v1/chart/data"):
            if self.fail_chart_data:
                raise BiError("query failed", status=500)
            return {"result": [{"data": [{"count": 1}]}]}
        if method == "POST" and path == "/api/v1/chart/":
            self.next_id += 1
            self.charts[self.next_id] = {"id": self.next_id, "dashboards": []}
            return {"id": self.next_id}
        if method == "GET" and path.startswith("/api/v1/chart/"):
            return {"result": self.charts[int(path.rsplit("/", 1)[1])]}
        if method == "PUT" and path.startswith("/api/v1/chart/"):
            self.charts[int(path.rsplit("/", 1)[1])]["dashboards"] = [
                {"id": i} for i in body["dashboards"]
            ]
            return {}
        if method == "POST" and path == "/api/v1/dashboard/":
            self.next_id += 1
            self.dashboards[self.next_id] = {
                "id": self.next_id,
                "owners": [{"id": 1}],
                "position_json": body["position_json"],
                "json_metadata": "{}",
                "changed_on_utc": "n",
            }
            return {"id": self.next_id}
        if method == "GET" and path.endswith("/charts"):
            dashboard_id = int(path.split("/")[4])
            return {
                "result": [
                    {"id": c["id"]}
                    for c in self.charts.values()
                    if {"id": dashboard_id} in c["dashboards"]
                ]
            }
        if method == "GET" and path.startswith("/api/v1/dashboard/"):
            dashboard = self.dashboards[int(path.rsplit("/", 1)[1])]
            if self.change_during_attach and self.calls.count(("GET", path)) > 1:
                dashboard = {**dashboard, "changed_on_utc": "someone-saved"}
            return {"result": dashboard}
        if method == "PUT" and path.startswith("/api/v1/dashboard/"):
            if self.fail_dashboard_put:
                raise BiError("dashboard update failed", status=500)
            dashboard = self.dashboards[int(path.rsplit("/", 1)[1])]
            dashboard.update(position_json=body["position_json"], changed_on_utc="t1")
            return {}
        raise AssertionError(f"unexpected {method} {path}")


class Journal:
    def __init__(self):
        self.rows: dict[str, publication.Operation] = {}

    async def save(self, op):
        self.rows[op.operation_id] = publication.Operation(
            **{**op.__dict__, "created": list(op.created)}
        )


@pytest.fixture
def saga(monkeypatch):
    fake = FakeSuperset()
    journal = Journal()
    artifacts: list[dict] = []
    audits: list[dict] = []

    @asynccontextmanager
    async def for_user(user, *, author=True):
        yield fake

    async def by_scope(scope, owner):
        found = [op for op in journal.rows.values() if op.idempotency_scope == scope]
        return found[-1] if found else None

    async def reusable(owner, query_fp, viz_fp):
        found = [
            op
            for op in journal.rows.values()
            if op.status == "SUCCEEDED"
            and op.query_fingerprint == query_fp
            and op.visualization_fingerprint == viz_fp
        ]
        return found[-1] if found else None

    async def create_bi(**kwargs):
        artifacts.append(kwargs)
        return {"artifact_id": f"art{len(artifacts)}"}

    async def audit(**kwargs):
        audits.append(kwargs)

    class Lease:
        def __init__(self):
            self.values = {}

        async def set(self, key, value, nx=False, px=None):
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

        async def get(self, key):
            return self.values.get(key)

        async def delete(self, key):
            self.values.pop(key, None)

    lease = Lease()

    async def resolve(locator, user):
        return _preview(locator=locator)

    async def author(user):
        return user.get("active_role") != "viewer"

    async def superset_role(role):
        return "NovaAuthor"

    monkeypatch.setattr(SupersetClient, "for_user", for_user)
    monkeypatch.setattr(publication, "_save", journal.save)
    monkeypatch.setattr(publication, "_by_scope", by_scope)
    monkeypatch.setattr(publication, "_reusable_chart", reusable)
    monkeypatch.setattr(publication, "_redis", lambda: lease)
    monkeypatch.setattr(publication, "resolve", resolve)
    monkeypatch.setattr(publication, "write_audit_log", audit)
    monkeypatch.setattr(publication.artifact_repository, "create_bi", create_bi)
    monkeypatch.setattr(capability, "may_publish", author)
    import app.modules.superset.access as access

    monkeypatch.setattr(access, "superset_role_for", superset_role)
    return SimpleNamespace(fake=fake, journal=journal, artifacts=artifacts, audits=audits)


def _ref():
    return previews.sign(Locator("t1", "m1", "content-2", BAR_NODE), ALICE)


async def test_adding_to_an_existing_dashboard_is_additive_and_verified(saga):
    op = await publication.publish(
        ALICE,
        ref=_ref(),
        idempotency_key="key-00000001",
        target=publication.Target("existing", dashboard_id=7),
    )
    assert op.status == "SUCCEEDED" and op.dashboard_id == 7
    layout = json.loads(saga.fake.dashboards[7]["position_json"])
    assert publication._chart_ids(layout) == {5, op.chart_id}
    assert saga.fake.charts[op.chart_id]["dashboards"] == [{"id": 7}]
    assert saga.fake.charts[5]["dashboards"] == [{"id": 7}, {"id": 8}]  # untouched
    assert op.business_context is None and saga.artifacts and saga.audits[-1]["status"] == "SUCCESS"


async def test_a_new_dashboard_is_created_with_the_chart(saga):
    op = await publication.publish(
        ALICE,
        ref=_ref(),
        idempotency_key="key-00000002",
        target=publication.Target("new", title="Overview"),
    )
    assert op.status == "SUCCEEDED" and op.dashboard_id in saga.fake.dashboards
    assert {"dashboard", "chart", "dataset"} <= set(op.created)
    kinds = [(item["artifact_type"], item["bi_object_id"]) for item in saga.artifacts]
    assert kinds == [("chart", op.chart_id), ("dashboard", op.dashboard_id)]


async def test_a_retry_returns_the_first_outcome_and_creates_nothing(saga):
    target = publication.Target("existing", dashboard_id=7)
    first = await publication.publish(
        ALICE, ref=_ref(), idempotency_key="key-00000003", target=target
    )
    calls = len(saga.fake.calls)
    again = await publication.publish(
        ALICE, ref=_ref(), idempotency_key="key-00000003", target=target
    )
    assert again.operation_id == first.operation_id and len(saga.fake.calls) == calls


async def test_a_reused_key_with_another_change_is_refused(saga):
    await publication.publish(
        ALICE,
        ref=_ref(),
        idempotency_key="key-00000004",
        target=publication.Target("existing", dashboard_id=7),
    )
    with pytest.raises(publication.PublishError) as caught:
        await publication.publish(
            ALICE,
            ref=_ref(),
            idempotency_key="key-00000004",
            target=publication.Target("new", title="Other"),
        )
    assert caught.value.status == 409 and caught.value.code == "IDEMPOTENCY_CONFLICT"


async def test_the_same_chart_on_a_second_dashboard_is_reused_not_copied(saga):
    first = await publication.publish(
        ALICE,
        ref=_ref(),
        idempotency_key="key-00000005",
        target=publication.Target("existing", dashboard_id=7),
    )
    second = await publication.publish(
        ALICE,
        ref=_ref(),
        idempotency_key="key-00000006",
        target=publication.Target("new", title="Copy"),
    )
    assert second.chart_id == first.chart_id and "chart" not in second.created
    assert {d["id"] for d in saga.fake.charts[first.chart_id]["dashboards"]} == {
        7,
        second.dashboard_id,
    }


async def test_a_dashboard_saved_meanwhile_is_a_conflict_and_nothing_is_left(saga):
    saga.fake.change_during_attach = True
    with pytest.raises(publication.PublishError) as caught:
        await publication.publish(
            ALICE,
            ref=_ref(),
            idempotency_key="key-00000007",
            target=publication.Target("existing", dashboard_id=7),
        )
    assert caught.value.status == 409 and caught.value.code == "REVISION_CONFLICT"
    assert set(saga.fake.charts) == {5} and saga.fake.datasets == {}
    assert json.loads(saga.fake.dashboards[7]["position_json"]) == LAYOUT


async def test_a_stale_preview_revision_is_refused_before_anything_is_created(saga):
    with pytest.raises(publication.PublishError) as caught:
        await publication.publish(
            ALICE,
            ref=_ref(),
            idempotency_key="key-00000008",
            target=publication.Target("existing", dashboard_id=7, expected_revision="sha256:old"),
        )
    assert caught.value.code == "REVISION_CONFLICT" and saga.fake.datasets == {}


async def test_unsaved_edits_in_the_open_editor_block_the_write(saga):
    with pytest.raises(publication.PublishError) as caught:
        await publication.publish(
            ALICE,
            ref=_ref(),
            idempotency_key="key-00000009",
            target=publication.Target("existing", dashboard_id=7),
            editor_state="dirty",
            open_dashboard_id=7,
        )
    assert caught.value.code == "EDITOR_HAS_UNSAVED_CHANGES" and saga.fake.calls == []


async def test_a_viewer_role_is_refused_and_audited_without_calling_superset(saga):
    viewer = {**ALICE, "active_role": "viewer"}
    with pytest.raises(publication.PublishError) as caught:
        await publication.publish(
            viewer,
            ref=previews.sign(Locator("t1", "m1", "c"), viewer),
            idempotency_key="key-00000010",
            target=publication.Target("existing", dashboard_id=7),
        )
    assert caught.value.status == 403 and saga.fake.calls == []
    assert saga.audits[-1]["status"] == "DENIED"


async def test_a_dashboard_the_caller_does_not_own_is_refused(saga):
    saga.fake.dashboards[7]["owners"] = [{"id": 2}]
    with pytest.raises(publication.PublishError) as caught:
        await publication.publish(
            ALICE,
            ref=_ref(),
            idempotency_key="key-00000011",
            target=publication.Target("existing", dashboard_id=7),
        )
    assert caught.value.code == "NOT_DASHBOARD_EDITOR" and saga.fake.datasets == {}


async def test_a_failing_query_removes_the_dataset_it_created(saga):
    saga.fake.fail_chart_data = True
    with pytest.raises(publication.PublishError) as caught:
        await publication.publish(
            ALICE,
            ref=_ref(),
            idempotency_key="key-00000012",
            target=publication.Target("existing", dashboard_id=7),
        )
    assert caught.value.code == "PROVIDER_ERROR" and caught.value.status == 503
    assert saga.fake.datasets == {}
    (op,) = saga.journal.rows.values()
    assert op.status == "FAILED_RETRYABLE" and op.created == []


async def test_an_unconfirmed_attach_is_left_for_reconciliation(saga):
    saga.fake.fail_dashboard_put = True
    with pytest.raises(publication.PublishError) as caught:
        await publication.publish(
            ALICE,
            ref=_ref(),
            idempotency_key="key-00000013",
            target=publication.Target("existing", dashboard_id=7),
        )
    assert caught.value.code == "NEEDS_RECONCILIATION"
    (op,) = saga.journal.rows.values()
    assert op.status == "NEEDS_RECONCILIATION" and op.chart_id in saga.fake.charts
    assert json.loads(saga.fake.dashboards[7]["position_json"]) == LAYOUT


async def test_the_chart_carries_its_ontology_meaning(saga, monkeypatch):
    business = {
        "ontology_release_id": "rel-1",
        "concepts": [
            {"id": "c1", "qualified_name": "Sales.Units", "label": "Units", "definition": "Ordered"}
        ],
    }

    async def resolve(locator, user):
        return _preview(locator=locator, semantic={"business_context": business})

    monkeypatch.setattr(publication, "resolve", resolve)
    bodies: list[dict] = []
    original = saga.fake.request

    async def spy(method, path, *, body=None, headers=None):
        if method == "POST" and path == "/api/v1/chart/":
            bodies.append(body)
        return await original(method, path, body=body)

    saga.fake.request = spy
    op = await publication.publish(
        ALICE,
        ref=_ref(),
        idempotency_key="key-00000014",
        target=publication.Target("existing", dashboard_id=7),
    )
    assert "Sales.Units" in bodies[0]["description"] and op.business_context == business
    assert "Sales.Units" in saga.audits[-1]["object_name"]


# ── Dashboard context ───────────────────────────────────────────────────────


class ContextBi:
    def __init__(self, filters, state):
        self.filters, self.state = filters, state

    async def request(self, method, path, *, body=None, headers=None):
        if path == "/api/v1/me/":
            return {"result": {"id": 1}}
        if path == "/api/v1/dashboard/7":
            return {
                "result": {
                    "id": 7,
                    "dashboard_title": "Sales",
                    "owners": [{"id": 1}],
                    "position_json": json.dumps(LAYOUT),
                    "changed_on_utc": "t",
                    "json_metadata": json.dumps({"native_filter_configuration": self.filters}),
                }
            }
        if path.endswith("/charts"):
            return {
                "result": [
                    {
                        "id": 5,
                        "slice_name": "Units",
                        "form_data": {
                            "viz_type": "pie",
                            "datasource": "9__table",
                            "metrics": ["units"],
                            "groupby": ["brand"],
                        },
                    }
                ]
            }
        if path.endswith("/datasets"):
            return {"result": [{"id": 9, "table_name": "sales_ds", "sql": "SELECT 1"}]}
        if "/filter_state/" in path:
            if self.state is None:
                raise BiError("gone", status=404)
            return {"value": json.dumps(self.state)}
        raise AssertionError(path)


REGION = [
    {
        "id": "NATIVE_FILTER-1",
        "name": "Region",
        "filterType": "filter_select",
        "targets": [{"column": {"name": "region"}}],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
    }
]


@pytest.fixture
def context_env(monkeypatch):
    def install(bi, role="NovaAuthor"):
        @asynccontextmanager
        async def for_user(user, *, author=True):
            yield bi

        async def superset_role(_):
            return role

        async def semantic(ids):
            return {9: {"view_id": "v1", "version": 2, "status": "SYNCED"}}

        async def published(ids):
            return {}

        class Ontology(dashboard_context._Ontology):
            async def load(self):
                self.snapshot = None

            def bound(self, view_id, metric):
                return [{"qualified_name": "Sales.Units", "link": "binding", "metric": metric}]

        monkeypatch.setattr(SupersetClient, "for_user", for_user)
        import app.modules.superset.access as access

        monkeypatch.setattr(access, "superset_role_for", superset_role)
        monkeypatch.setattr(dashboard_context, "_semantic_views", semantic)
        monkeypatch.setattr(dashboard_context, "_published_contexts", published)
        monkeypatch.setattr(dashboard_context, "_Ontology", Ontology)

    return install


async def test_filters_are_verified_only_through_superset_filter_state(context_env):
    context_env(ContextBi(REGION, {"NATIVE_FILTER-1": {"filterState": {"value": ["Jawa"]}}}))
    described = await dashboard_context.dashboard_context(ALICE, 7, native_filters_key="k1")
    assert described["filter_state_status"] == "verified"
    assert described["filters"][0]["value"] == ["Jawa"]
    chart = described["charts"][0]
    assert chart["source"]["kind"] == "semantic_view"
    assert chart["concepts"][0]["qualified_name"] == "Sales.Units"


async def test_without_a_filter_key_the_state_is_unavailable_and_said_so(context_env):
    context_env(ContextBi(REGION, None))
    described = await dashboard_context.dashboard_context(ALICE, 7)
    assert described["filter_state_status"] == "unavailable"
    assert any("filter selection could not be read" in gap for gap in described["gaps"])


async def test_an_expired_filter_key_is_stale(context_env):
    context_env(ContextBi(REGION, None))
    described = await dashboard_context.dashboard_context(ALICE, 7, native_filters_key="gone")
    assert described["filter_state_status"] == "stale"


async def test_a_viewer_never_receives_dataset_sql(context_env):
    context_env(ContextBi([], None), role="NovaViewer")
    described = await dashboard_context.dashboard_context(ALICE, 7)
    assert "sql" not in described["charts"][0]["source"]
    assert described["filter_state_status"] == "verified" and described["filters_configured"] == 0


# ── Assistant tools and the harness ─────────────────────────────────────────


def test_only_declared_tools_can_settle_declared_capabilities():
    from app.modules.agents.tools.dataviz import ApplyTool, CheckResultTool
    from app.modules.assistant.service import _settled_capabilities
    from app.modules.assistant.tools import ToolOutcome

    asked = ToolOutcome(
        ok=True, summary="", metadata={"settles_capabilities": ["dataviz_apply", "semantic_query"]}
    )
    assert _settled_capabilities(CheckResultTool(), asked) == {"dataviz_apply"}
    assert _settled_capabilities(ApplyTool(), asked) == set()
    failed = ToolOutcome(ok=False, summary="", metadata={"settles_capabilities": ["dataviz_apply"]})
    assert _settled_capabilities(CheckResultTool(), failed) == set()


def test_the_apply_tool_needs_consent_and_only_mutation_is_session_change():
    from app.modules.agents.tools import dataviz

    by_name = {tool.name: tool for tool in dataviz.TOOLS}
    assert by_name["dataviz_apply"].requires_consent
    assert by_name["dataviz_apply"].classification == "session_change"
    for name, tool in by_name.items():
        if name != "dataviz_apply":
            assert tool.classification == "read_only" and not tool.requires_consent


async def test_results_get_stable_ids_from_stored_messages(monkeypatch):
    from app.modules.agents.tools import dataviz
    from app.modules.assistant.repository import assistant_repository

    async def messages(thread_id, *, user_name):
        return [
            {"role": "user", "message_id": "u1", "steps": []},
            {
                "role": "assistant",
                "message_id": "m1",
                "steps": [
                    {"kind": "table", "content_id": "content-1", "title": "Brands"},
                    _ui_block(BAR, ROWS),
                ],
            },
        ]

    monkeypatch.setattr(assistant_repository, "list_messages", messages)
    found = await dataviz._results(SimpleNamespace(thread_id="t1"), ALICE)
    assert [(item["result_id"], item["kind"]) for item in found] == [
        ("R1", "table"),
        ("R2", "chart"),
    ]
    assert found[1]["node"]["x"] == "brand"


async def test_dashboard_build_never_borrows_another_results_query():
    from app.modules.agents.tools.dashboard_build import dashboard_build_tool
    from app.modules.assistant.tools import ToolInvocation

    context = SimpleNamespace(
        user=ALICE,
        evidence_tables={
            "evidence_1": {"columns": ["a"], "rows": [], "sql": "SELECT 1"},
            "evidence_2": {"columns": ["b"], "rows": [], "sql": "SELECT 2"},
        },
        evidence_sql={},
        last_result=None,
    )
    outcome = await dashboard_build_tool.run(
        ToolInvocation(
            "c1",
            "dashboard_build",
            {"title": "D", "charts": [{"title": "X", "chart_type": "bar", "x": "a", "y": "a"}]},
        ),
        context,
    )
    assert not outcome.ok and "evidence_1" in outcome.error and "evidence_2" in outcome.error


def test_chart_plans_for_dataviz_support_donuts_strictly():
    from app.modules.superset.charts import Measure, to_superset

    _viz, form, _query = to_superset(
        ChartPlan(kind="donut", x="brand", measures=[Measure(column="units")]),
        dataset_id=1,
        columns=["brand", "units"],
        temporal=set(),
    )
    assert form["viz_type"] == "pie" and form["donut"] is True
    with pytest.raises(ValueError):
        to_superset(
            ChartPlan(kind="bar", x="nope", measures=[Measure(column="units")]),
            dataset_id=1,
            columns=["brand", "units"],
            temporal=set(),
        )


# ── The agent-free Dataviz assistant ────────────────────────────────────────


async def test_the_assistant_is_admitted_only_on_conversation_routes():
    from fastapi import HTTPException

    from app.modules.agents import router
    from app.modules.agents.dataviz_assistant import DATAVIZ_ASSISTANT_ID

    agent = await router._require_agent(DATAVIZ_ASSISTANT_ID, ALICE, platform=True)
    assert agent["agent_id"] == DATAVIZ_ASSISTANT_ID and agent["policy"] == "auto_read_only"
    for user, platform in ((ALICE, False), ("alice", True)):
        with pytest.raises(HTTPException) as caught:
            await router._require_agent(DATAVIZ_ASSISTANT_ID, user, platform=platform)
        assert caught.value.status_code == 404


def test_the_assistant_asks_for_five_w_one_h_answers_and_has_no_write_tools():
    from app.modules.agents.dataviz_assistant import TOOLS, dataviz_assistant_config

    config = dataviz_assistant_config("alice", semantic_view_ids=["v1"])
    text = " ".join(config["instructions_response"].split())
    for word in ("What:", "Who:", "When:", "Where:", "Why:", "How:"):
        assert word in text
    assert "not prove its cause" in text or "does not prove" in text
    assert config["semantic_view_ids"] == ["v1"]
    assert not {"ml_execute", "dashboard_build", "query_execute"} & set(TOOLS)


async def _none():
    return []


async def test_scope_puts_the_dashboards_views_first_and_drops_unreadable(monkeypatch):
    from app.modules.agents import dataviz_assistant as assistant
    from app.modules.intelligence.semantic_views import semantic_view_service

    async def dashboard(user, dashboard_id):
        return ["dash-view", "hidden"]

    async def question(user, text, scope=None):
        return ["onto-view"]

    async def readable(view_id, user, agent_id=None):
        return None if view_id == "hidden" else {"id": view_id}

    async def everything(user, agent_id=None):
        return [{"id": "other"}, {"id": "dash-view"}]

    monkeypatch.setattr(assistant, "_views_for_dashboard", dashboard)
    monkeypatch.setattr(assistant, "_views_for_question", question)
    monkeypatch.setattr(semantic_view_service, "get_active_for_agent", readable)
    monkeypatch.setattr(semantic_view_service, "list_active_for_agent", everything)
    views = await assistant.scope_views(ALICE, dashboard_id=7, question="kenapa omzet turun")
    # The dashboard is this turn's business: no other business's views join it.
    assert views == ["dash-view", "onto-view"]
    # Without a dashboard, the role's other readable views fill the scope.
    monkeypatch.setattr(assistant, "_views_for_dashboard", lambda user, dashboard_id: _none())
    assert await assistant.scope_views(ALICE, question="kenapa omzet turun") == [
        "onto-view", "other", "dash-view",
    ]


async def test_the_assistants_threads_need_no_agent_grant(monkeypatch):
    from app.modules.agents import mission
    from app.modules.agents.dataviz_assistant import DATAVIZ_ASSISTANT_ID

    async def thread(thread_id, user_name):
        return {"thread_id": thread_id, "agent_id": DATAVIZ_ASSISTANT_ID}

    monkeypatch.setattr(mission.assistant_repository, "get_thread", thread)
    found = await mission.require_thread("t1", ALICE)
    assert found["agent_id"] == DATAVIZ_ASSISTANT_ID


# ── Answers from the dashboard's own dataset ────────────────────────────────


class QueryBi:
    def __init__(self):
        self.queries: list[dict] = []

    async def request(self, method, path, *, body=None, headers=None):
        if path == "/api/v1/dataset/9":
            return {
                "result": {
                    "table_name": "sales_ds",
                    "main_dttm_col": "order_date",
                    "columns": [
                        {"column_name": "region"},
                        {"column_name": "brand"},
                        {"column_name": "order_date", "is_dttm": True},
                        {"column_name": "units"},
                    ],
                    "metrics": [{"metric_name": "net_revenue"}],
                }
            }
        if path == "/api/v1/chart/data":
            query = body["queries"][0]
            self.queries.append(query)
            window = next((f["val"] for f in query["filters"] if f["op"] == "TEMPORAL_RANGE"), None)
            data = (
                ({"brand": "A", "SUM(units)": 10}, {"brand": "B", "SUM(units)": 20})
                if window and window.startswith("2026-10")
                else ({"brand": "A", "SUM(units)": 30}, {"brand": "B", "SUM(units)": 22})
            )
            if not query["columns"]:
                data = ({"SUM(units)": sum(row["SUM(units)"] for row in data)},)
            return {"result": [{"data": list(data)}]}
        raise AssertionError(path)


@pytest.fixture
def dashboard_query(monkeypatch):
    from app.modules.superset import dashboard_query as module

    bi = QueryBi()

    @asynccontextmanager
    async def for_user(user, *, author=True):
        yield bi

    async def context(user, dashboard_id, native_filters_key=None, edit_state="unknown"):
        return {
            "available": True,
            "filter_state_status": "verified",
            "filters": [
                {"name": "Region", "column": "region", "value": ["Jawa"]},
                {"name": "Channel", "column": "channel", "value": ["GT"]},
            ],
            "charts": [
                {
                    "source": {"dataset_id": 9, "kind": "semantic_view"},
                    "concepts": [{"qualified_name": "Sales.Units"}],
                }
            ],
        }

    monkeypatch.setattr(SupersetClient, "for_user", for_user)
    monkeypatch.setattr(dashboard_context, "dashboard_context", context)
    return module, bi


async def test_dashboard_answers_apply_verified_filters_and_report_the_rest(dashboard_query):
    module, bi = dashboard_query
    answer = await module.query_dashboard_dataset(
        ALICE,
        dashboard_id=7,
        dataset_id=9,
        metrics=["SUM(units)"],
        dimensions=["brand"],
        period="2026-10-01 : 2026-11-01",
    )
    filters = bi.queries[0]["filters"]
    assert {"col": "region", "op": "IN", "val": ["Jawa"]} in filters
    assert {
        "col": "order_date",
        "op": "TEMPORAL_RANGE",
        "val": "2026-10-01 : 2026-11-01",
    } in filters
    assert answer.facts["filters_applied"][0]["column"] == "region"
    assert answer.facts["filters_not_applicable"][0]["filter"] == "Channel"
    assert answer.facts["concepts"][0]["qualified_name"] == "Sales.Units"
    assert answer.columns == ["brand", "SUM(units)"]
    assert answer.rows[0] == ["Total", 30]


async def test_a_comparison_ranks_where_the_change_happened(dashboard_query):
    module, _bi = dashboard_query
    answer = await module.query_dashboard_dataset(
        ALICE,
        dashboard_id=7,
        dataset_id=9,
        metrics=["SUM(units)"],
        dimensions=["brand"],
        period="2026-10-01 : 2026-11-01",
        compare_period="2026-09-01 : 2026-10-01",
    )
    assert answer.facts["change"] == -22 and answer.facts["total_previous"] == 52
    assert answer.facts["change_pct"] == round(-22 / 52 * 100, 2)
    # The total comes from its own query and leads the table.
    assert answer.rows[0] == ["Total", 30.0, 52.0, -22.0, round(-22 / 52 * 100, 2), 100.0]
    assert answer.rows[1][:5] == ["A", 10.0, 30.0, -20.0, round(-20 / 30 * 100, 2)]
    assert answer.rows[1][5] == round(-20 / -22 * 100, 2)
    assert answer.columns[1:3] == ["SUM(units) 2026-10", "SUM(units) 2026-09"]
    first = answer.facts["top_contributors"][0]
    assert first == {
        "member": "A",
        "change": -20.0,
        "change_pct": round(-20 / 30 * 100, 2),
        "share_of_change_pct": round(-20 / -22 * 100, 2),
    }
    # The biggest mover leads the rows; the biggest members are named apart.
    assert answer.facts["row_order"].startswith("by absolute change")
    largest = [item["SUM(units)"] for item in answer.facts["largest_in_period"]]
    assert largest == sorted(largest, reverse=True) and largest[0] >= 10.0


async def test_a_time_dimension_in_a_comparison_is_dropped_and_reported(dashboard_query):
    module, bi = dashboard_query
    answer = await module.query_dashboard_dataset(
        ALICE,
        dashboard_id=7,
        dataset_id=9,
        metrics=["SUM(units)"],
        dimensions=["order_date", "brand"],
        period="2026-10-01 : 2026-11-01",
        compare_period="2026-09-01 : 2026-10-01",
    )
    assert all(query["columns"] in ([], ["brand"]) for query in bi.queries)
    assert "order_date" in answer.facts["adjusted"][0]
    assert answer.facts["change"] == -22


async def test_comparing_a_period_with_itself_is_a_plain_query(dashboard_query):
    module, _bi = dashboard_query
    answer = await module.query_dashboard_dataset(
        ALICE,
        dashboard_id=7,
        dataset_id=9,
        metrics=["SUM(units)"],
        dimensions=["brand"],
        period="2026-10-01 : 2026-11-01",
        compare_period="2026-10-01 : 2026-11-01",
    )
    assert answer.facts["compare_period"] is None
    assert answer.columns == ["brand", "SUM(units)"]
    assert answer.facts["adjusted"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dataset_id": 8, "metrics": ["SUM(units)"]},
        {"dataset_id": 9, "metrics": ["DROP(units)"]},
        {"dataset_id": 9, "metrics": ["SUM(units)"], "dimensions": ["secret"]},
        {"dataset_id": 9, "metrics": ["SUM(units)"], "period": "last month"},
        {"dataset_id": 9, "metrics": ["SUM(units)"], "compare_period": "2026-09-01 : 2026-10-01"},
    ],
)
async def test_only_the_dashboards_dataset_and_its_fields_can_be_queried(dashboard_query, kwargs):
    module, bi = dashboard_query
    with pytest.raises(module.QueryRefused):
        await module.query_dashboard_dataset(ALICE, dashboard_id=7, **kwargs)
    assert bi.queries == []


def test_the_prompt_brief_names_fields_and_filters_but_never_sql():
    context = {
        "available": True,
        "dashboard_id": 7,
        "title": "Sales",
        "can_edit": False,
        "role": "finance",
        "filter_state_status": "verified",
        "filters": [{"name": "Region", "value": ["Jawa"]}],
        "charts": [
            {
                "chart_id": 5,
                "title": "Units",
                "viz_type": "pie",
                "metrics": ["units"],
                "dimensions": ["brand"],
                "source": {"dataset_id": 9, "sql": "SELECT secret"},
                "concepts": [{"qualified_name": "Sales.Units", "definition": "Units ordered"}],
            }
        ],
        "datasets": [
            {
                "dataset_id": 9,
                "name": "sales_ds",
                "metrics": ["units"],
                "columns": ["brand", "region"],
                "time_columns": ["order_date"],
                "semantic_view": {"name": "retail_sales", "version": 18},
            }
        ],
        "ontology": {"named_in_titles": []},
    }
    text = dashboard_context.brief(context)
    assert "SELECT" not in text and "view only" in text
    assert "Region = ['Jawa']" in text and "Semantic View “retail_sales” v18" in text
    assert "Sales.Units: Units ordered" in text
    unknown = dashboard_context.brief({**context, "filter_state_status": "unavailable"})
    assert "selection is unavailable, so say it is unknown" in unknown


def test_a_grounded_semantic_query_settles_ontology_resolution_only():
    from app.modules.agents.tools.semantic_query import semantic_query_tool
    from app.modules.assistant.service import _settled_capabilities
    from app.modules.assistant.tools import ToolOutcome

    grounded = ToolOutcome(
        ok=True,
        summary="",
        metadata={"settles_capabilities": ["ontology_resolve", "diagnose_change"]},
    )
    assert _settled_capabilities(semantic_query_tool, grounded) == {"ontology_resolve"}


@pytest.mark.parametrize(
    ("word", "column"),
    [
        ("salesman", "employee_name"),
        ("employee_name", "employee_name"),
        ("region", "region"),
        ("kota", "city"),
        ("unknown thing", None),
    ],
)
def test_business_words_resolve_to_one_readable_column(word, column):
    from app.modules.superset.dashboard_query import resolve_dimension

    described = {
        "employee_name": "Nama salesman. Pakai ini saat menyebut salesman.",
        "salesman_id": "Kode salesman yang mencatat order.",
        "employee_id": "Kode pegawai (salesman).",
        "region": "Wilayah penjualan.",
        "city": "Kota outlet.",
    }
    assert resolve_dimension(word, described) == column


def test_month_labels_come_from_the_time_grain():
    from app.modules.superset.dashboard_query import _period_label

    october = datetime(2026, 10, 1, tzinfo=UTC).timestamp() * 1000
    assert _period_label(october, "P1M") == "2026-10"
    assert _period_label(october, "P3M") == "2026-Q4"
    assert _period_label("Jawa", "P1M") == "Jawa"


async def test_a_change_is_broken_down_by_one_dimension(dashboard_query):
    module, bi = dashboard_query
    answer = await module.query_dashboard_dataset(
        ALICE,
        dashboard_id=7,
        dataset_id=9,
        metrics=["SUM(units)"],
        dimensions=["brand", "region"],
        period="2026-10-01 : 2026-11-01",
        compare_period="2026-09-01 : 2026-10-01",
    )
    assert all(query["columns"] in ([], ["brand"]) for query in bi.queries)
    assert "one dimension" in answer.facts["adjusted"][0]


@pytest.mark.parametrize(("bi_kind", "status"), [("dashboard", 422), (None, 201)])
def test_dataviz_dashboards_follow_roles_and_are_not_shared(monkeypatch, bi_kind, status):
    """D8: a Dataviz dashboard belongs to its Nova role; a legacy one can be shared."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_user
    from app.modules.agents import sharing, studio_router

    async def owned(object_type, object_id, owner):
        return {"artifact_type": "dashboard", "bi_kind": bi_kind, "bi_object_id": 7}

    async def create(*, owner_name, body):
        return {"share_id": "s1", "object_id": body.object_id}

    async def audit(**kwargs):
        return None

    monkeypatch.setattr(studio_router, "_owned_share_object", owned)
    monkeypatch.setattr(sharing.share_repository, "create", create)
    monkeypatch.setattr(studio_router, "write_audit_log", audit)
    app = FastAPI()
    app.include_router(studio_router.router)
    app.dependency_overrides[get_current_user] = lambda: ALICE
    response = TestClient(app).post(
        "/studio/shares",
        json={
            "object_type": "dashboard",
            "object_id": "d1",
            "target_type": "user",
            "target_name": "bob",
        },
    )
    assert response.status_code == status, response.text


@pytest.mark.parametrize(
    ("window", "label"),
    [
        ("2026-10-01 : 2026-11-01", "2026-10"),
        ("2026-12-01 : 2027-01-01", "2026-12"),
        ("2026-10-05 : 2026-10-12", "2026-10-05..2026-10-11"),
    ],
)
def test_comparison_columns_name_their_periods(window, label):
    from app.modules.superset.dashboard_query import period_label

    assert period_label(window) == label


@pytest.mark.parametrize(
    ("written", "period"),
    [
        ("2026-10-01 : 2026-11-01", "2026-10-01 : 2026-11-01"),
        ("2026-10-01:2026-11-01", "2026-10-01 : 2026-11-01"),
        ("2026-10-01 to 2026-11-01", "2026-10-01 : 2026-11-01"),
        ("2026-10", "2026-10-01 : 2026-11-01"),
        ("2026-12", "2026-12-01 : 2027-01-01"),
        (".", None),
        ("", None),
        ("last month", None),
        ("2026-10-01 until later", "2026-10-01 until later"),
    ],
)
def test_periods_are_read_in_the_forms_a_model_writes(written, period):
    from app.modules.superset.dashboard_query import normalize_period

    assert normalize_period(written) == period


async def test_a_comparison_period_without_dates_is_dropped_and_reported(dashboard_query):
    module, _bi = dashboard_query
    answer = await module.query_dashboard_dataset(
        ALICE,
        dashboard_id=7,
        dataset_id=9,
        metrics=["SUM(units)"],
        dimensions=["brand"],
        period="2026-10",
        compare_period=".",
    )
    assert answer.facts["compare_period"] is None
    assert "names no dates" in answer.facts["adjusted"][0]


DATASET_SOURCE = {
    "dashboard_id": 7,
    "dataset_id": 41,
    "dimensions": ["brand"],
    "measures": [{"saved": "net_revenue"}, {"column": "units", "aggregate": "SUM"}],
    "period": "2026-10-01 : 2026-11-01",
    "compare_period": None,
    "time_column": "order_date",
    "time_grain": None,
    "concepts": [{"qualified_name": "Penjualan.NetRevenue", "definition": "after returns"}],
}


def _dataset_preview(**source):
    return _preview(
        render_kind="table",
        sql=None,
        producer_tool="dataviz_query",
        columns=["brand", "net_revenue", "SUM(units)"],
        visualization={"kind": "table"},
        transforms=[],
        locator=Locator("t1", "m1", "content-9"),
        dataset_source={**DATASET_SOURCE, **source},
    )


async def test_a_dashboard_dataset_answer_is_drawn_on_that_dataset():
    result = await capability.check(_dataset_preview())
    assert result.status == "SUPPORTED" and result.direct_publish_allowed
    viz_type, form, query = to_superset(
        result.plan,
        dataset_id=41,
        columns=["brand", "units", "order_date"],
        temporal={"order_date"},
    )
    # Saved metrics by name; the question's period is fixed; dashboard filters are not.
    assert form["metrics"][0] == "net_revenue" and form["groupby"] == ["brand"]
    assert form["adhoc_filters"] == [
        {
            "expressionType": "SIMPLE",
            "clause": "WHERE",
            "subject": "order_date",
            "operator": "TEMPORAL_RANGE",
            "comparator": "2026-10-01 : 2026-11-01",
        }
    ]
    assert query["queries"][0]["filters"] == [
        {"col": "order_date", "op": "TEMPORAL_RANGE", "val": "2026-10-01 : 2026-11-01"}
    ]


async def test_a_two_period_comparison_is_not_offered():
    result = await capability.check(_dataset_preview(compare_period="2026-09-01 : 2026-10-01"))
    assert result.status == "UNSUPPORTED" and result.reason_code == "COMPUTED_COMPARISON"
    assert not result.direct_publish_allowed and result.plan is None


async def test_publishing_a_dashboard_answer_creates_no_dataset(saga, monkeypatch):
    async def resolve(locator, user):
        return _dataset_preview(locator=locator)

    monkeypatch.setattr(publication, "resolve", resolve)
    saga.fake.datasets[41] = {"sql": None}
    op = await publication.publish(
        ALICE,
        ref=previews.sign(Locator("t1", "m1", "content-9"), ALICE),
        idempotency_key="key-00000041",
        target=publication.Target("existing", dashboard_id=7),
    )
    assert op.status == "SUCCEEDED" and op.dataset_id == 41
    assert "dataset" not in op.created and list(saga.fake.datasets) == [41]
    # No SQL-backed Studio record for a chart on the dashboard's own dataset.
    assert op.artifact_id is None and not saga.artifacts
    assert saga.fake.charts[op.chart_id]["dashboards"] == [{"id": 7}]


async def test_a_change_prepared_earlier_in_the_conversation_can_be_applied_later(monkeypatch):
    """A "yes" in the next turn finds the change this conversation prepared, for
    its owner only, and not once the change itself has expired."""
    from app.modules.superset import dataviz as service

    class Store:
        def __init__(self):
            self.values = {}

        async def set(self, key, value, ex=None):
            self.values[key] = value

        async def get(self, key):
            return self.values.get(key)

    store = Store()
    monkeypatch.setattr(service, "_redis", lambda: store)
    store.values["nova:dataviz:action:tok1"] = json.dumps(
        {"owner": "alice", "changes": ["Add 'Net revenue' to dashboard 7"]}
    )
    await service.remember_pending(ALICE, "t1", "tok1")

    pending = await service.pending_change(ALICE, "t1")
    assert pending["action_token"] == "tok1" and pending["changes"]
    assert await service.pending_change({**ALICE, "username": "bob"}, "t1") is None
    assert await service.pending_change(ALICE, "t2") is None
    del store.values["nova:dataviz:action:tok1"]
    assert await service.pending_change(ALICE, "t1") is None
