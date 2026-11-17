"""Add to Dataviz on native dashboards: result references, capability, listing
and publishing, with the native document services faked at their boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.exceptions import NovaException
from app.modules.agents.tools import native_dataviz as native
from app.modules.viz import studio_publish, studio_results
from app.modules.viz.contracts.authoring import SqlInspection
from app.modules.viz.contracts.charts import (
    CartesianEncoding,
    CategoryEncoding,
    SingleMeasureEncoding,
    TableEncoding,
)
from app.modules.viz.contracts.dashboards import DashboardDefinition, DatasetReference
from app.modules.viz.contracts.datasets import DatasetDefinition, DatasetField, SqlSource
from app.modules.viz.openui_nodes import data_nodes
from app.modules.viz.operations import apply_operation
from app.modules.viz.studio_publish import PublishError, Target
from app.modules.viz.studio_results import Locator, PreviewError

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
SQL = "SELECT brand, SUM(qty) AS units FROM sales.orders GROUP BY brand"
EXISTING = UUID("11111111-1111-4111-8111-111111111111")
PAGE = UUID("22222222-2222-4222-8222-222222222222")
KEY = "5f0c7a52-8a4e-4c55-9d0e-6a1f8f0b2c11"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(studio_results.settings, "SECRET_KEY", "test-signing-key")


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
        "e1", "brand", "units", "bar",
    )
    assert [t.name for t in chart.transforms] == ["Sort"]


def test_openui_expressions_it_does_not_model_are_opaque_not_guessed():
    (node,) = data_nodes('root = Col([Chart(@Evidence("e1") + 1, "x", "y", "bar")])')
    assert node.opaque and node.evidence_id is None


# ── Signed references ───────────────────────────────────────────────────────


def test_a_reference_is_bound_to_its_principal_and_cannot_be_edited():
    locator = Locator("t1", "m1", "content-1")
    ref = studio_results.sign(locator, ALICE)
    assert studio_results.verify(ref, ALICE) == locator
    assert "SELECT" not in ref
    with pytest.raises(PreviewError):
        studio_results.verify(ref, {**ALICE, "username": "bob"})
    prefix, _payload, mac = ref.split(".")
    forged = studio_results._b64(json.dumps({"t": "t1", "m": "m2", "c": "content-1"}).encode())
    with pytest.raises(PreviewError):
        studio_results.verify(f"{prefix}.{forged}.{mac}", ALICE)


# ── Resolving from Nova's own record ────────────────────────────────────────


def _message(steps, *, stamp=STAMP, owner="alice"):
    async def message(locator, user):
        # The metadata transaction runs as the caller: another user's message
        # is simply not found.
        if user["username"] != owner:
            raise PreviewError("That result is not in your conversations.")
        assert locator.thread_id == "t1" and locator.message_id == "m1"
        return steps, stamp, "agent-1"

    return message


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
            "business_context": {"concepts": [{
                "qualified_name": "Sales.Units", "label": "Units",
                "definition": "Units ordered", "metric": "units",
            }]},
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
BAR_NODE = {"component": "Chart", "evidence_id": "evidence_1", "x": "brand", "y": "units",
            "type": "bar", "title": "Units"}
TABLE_STEPS = [
    _semantic_step(),
    {"kind": "table", "content_id": "content-1", "tool_call_id": "q1",
     "columns": ["brand", "units"], "title": "Units by brand"},
]


async def test_an_interactive_chart_resolves_to_its_exact_producing_query(monkeypatch):
    monkeypatch.setattr(studio_results, "_message",
                        _message([_semantic_step(), _ui_block(BAR, ROWS)]))
    preview = await studio_results.resolve(Locator("t1", "m1", "content-2", BAR_NODE), ALICE)
    assert preview.sql == SQL and preview.provenance == "tool_step"
    assert preview.render_kind == "chart" and preview.columns == ["brand", "units"]
    assert [t["name"] for t in preview.transforms] == ["Sort"]


async def test_a_result_from_another_role_is_not_resolved(monkeypatch):
    monkeypatch.setattr(studio_results, "_message", _message(
        [_semantic_step(), _ui_block(BAR, ROWS)], stamp={**STAMP, "active_role": "hr"}))
    with pytest.raises(PreviewError):
        await studio_results.resolve(Locator("t1", "m1", "content-2", BAR_NODE), ALICE)


async def test_a_node_the_program_does_not_contain_is_refused(monkeypatch):
    monkeypatch.setattr(studio_results, "_message",
                        _message([_semantic_step(), _ui_block(BAR, ROWS)]))
    with pytest.raises(PreviewError):
        await studio_results.resolve(
            Locator("t1", "m1", "content-2", {**BAR_NODE, "y": "revenue"}), ALICE)


# ── Capability ──────────────────────────────────────────────────────────────


def _preview(**overrides):
    base = dict(
        locator=Locator("t1", "m1", "content-2", BAR_NODE),
        render_kind="chart", title="Units", sql=SQL, provenance="tool_step",
        producer_tool="semantic_query", columns=["brand", "units"],
        visualization={"component": "Chart", "x": "brand", "y": "units", "type": "bar"},
        transforms=[{"name": "Sort", "args": ["units", "desc"], "exact": True}],
        rows=ROWS,
    )
    base.update(overrides)
    return studio_results.ResolvedPreview(**base)


def test_a_sorted_bar_chart_is_supported_with_its_order():
    result = studio_publish.capability(_preview())
    assert result.status == "SUPPORTED"
    assert result.chart == {"visual": "bar", "dimensions": [{"field": "brand"}],
                            "measures": [{"field": "units", "aggregation": "sum"}],
                            "sort": {"field": "units", "direction": "desc"}}


@pytest.mark.parametrize(("overrides", "status", "code"), [
    ({"transforms": [{"name": "Filter", "args": [], "exact": True}]},
     "UNSUPPORTED", "DERIVED_RESULT_NOT_REPRODUCIBLE"),
    ({"rows": [{"brand": "A", "units": 1}, {"brand": "A", "units": 2}]},
     "UNSUPPORTED", "NON_UNIQUE_CATEGORY"),
    ({"render_kind": "map"}, "UNSUPPORTED", "MAP_NOT_SUPPORTED"),
    ({"sql": None, "provenance": "none"}, "UNKNOWN", "NO_PROVENANCE"),
    ({"visualization": {"component": "Chart", "x": "brand", "y": "revenue", "type": "bar"}},
     "UNKNOWN", "FIELDS_NOT_IN_RESULT"),
    ({"visualization": {"component": "Chart", "x": "brand", "y": "units", "type": "radar"}},
     "UNSUPPORTED", "CHART_KIND_NOT_REPRESENTABLE"),
])
def test_results_a_native_chart_would_change_are_not_offered(overrides, status, code):
    result = studio_publish.capability(_preview(**overrides))
    assert (result.status, result.reason_code, result.chart) == (status, code, None)
    assert "SELECT" not in result.message


def test_tables_kpis_and_vega_charts_map_to_native_visuals():
    table = studio_publish.capability(_preview(
        locator=Locator("t1", "m1", "content-1"), render_kind="table", transforms=[],
        visualization={"kind": "table", "columns": ["brand", "units"]}))
    assert table.chart == {"visual": "table", "measures": [],
                           "dimensions": [{"field": "brand"}, {"field": "units"}]}
    kpi = studio_publish.capability(_preview(
        render_kind="kpi", transforms=[], rows=[{"units": 8}], columns=["units"],
        visualization={"component": "Kpi", "column": "units"}))
    assert kpi.chart == {"visual": "kpi", "measures": [{"field": "units", "aggregation": "sum"}]}
    pie = studio_publish.capability(_preview(
        locator=Locator("t1", "m1", "content-3"), viz_spec=None,
        chart_spec={"mark": "arc", "encoding": {"color": {"field": "brand"},
                                                "theta": {"field": "units"}}}))
    assert pie.chart and pie.chart["visual"] == "pie"
    weird = studio_publish.capability(_preview(
        locator=Locator("t1", "m1", "content-3"), chart_spec={"mark": "rule"}))
    assert weird.status == "UNSUPPORTED"


async def test_check_signs_supported_results_and_explains_the_rest(monkeypatch):
    monkeypatch.setattr(studio_results, "_message", _message(TABLE_STEPS))
    (ok,) = await studio_publish.check([Locator("t1", "m1", "content-1")], ALICE)
    assert ok["status"] == "SUPPORTED" and ok["can_add"] and ok["title"] == "Units by brand"
    assert studio_results.verify(ok["preview_ref"], ALICE) == Locator("t1", "m1", "content-1")
    assert ok["concepts"][0]["qualified_name"] == "Sales.Units"
    (gone,) = await studio_publish.check([Locator("t1", "m1", "content-9")], ALICE)
    assert gone == {"preview_ref": None, "status": "UNKNOWN", "can_add": False,
                    "reason_code": "PREVIEW_NOT_FOUND", "message": gone["message"]}
    # Bob cannot check, sign or reuse Alice's result.
    (bob,) = await studio_publish.check([Locator("t1", "m1", "content-1")],
                                        {**ALICE, "username": "bob"})
    assert bob["preview_ref"] is None and not bob["can_add"]
    refused = await studio_publish.check_ref(ok["preview_ref"], {**ALICE, "username": "bob"})
    assert refused["reason_code"] == "PREVIEW_NOT_FOUND"


# ── Fakes at the native service boundary ────────────────────────────────────


def _receipt_for(operation_id: UUID, identity: UUID, base: int, new: int) -> dict[str, Any]:
    return {"operation_id": operation_id, "resource_id": identity, "base_revision": base,
            "new_revision": new, "event_sequence": 1,
            "committed_at": datetime(2026, 11, 16, tzinfo=UTC)}


def _empty_dashboard(identity: UUID = EXISTING) -> DashboardDefinition:
    return DashboardDefinition.model_validate({
        "dashboard_id": str(identity), "title": "Sales",
        "pages": [{"page_id": str(PAGE), "title": "Overview", "widgets": [{
            "kind": "chart", "widget_id": "55555555-5555-4555-8555-555555555555",
            "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
            "chart_ref": {"chart_id": "44444444-4444-4444-8444-444444444444", "revision": 1},
        }]}],
    })


class FakeDocuments:
    def __init__(self) -> None:
        self.docs: dict[UUID, list[Any]] = {EXISTING: [_empty_dashboard()]}
        self.workspaces: dict[UUID, str] = {EXISTING: "abc"}
        self.receipts: dict[UUID, dict[str, Any]] = {}
        self.editable = True
        self.conflicts = 0
        self.calls: list[str] = []

    async def read(self, user, identity, *, draft=False, revision=None):
        if identity not in self.docs:
            raise NovaException("Document not found", status_code=404)
        if draft and not self.editable:
            raise NovaException("Metadata operation is not authorized", status_code=403)
        history = self.docs[identity]
        number = revision or len(history)
        return {"revision": number, "publication_id": None, "definition": history[number - 1]}

    async def workspace_of(self, user, identity):
        return self.workspaces[identity]

    async def create_visual(self, user, workspace_id, document, *, operation_id):
        self.calls.append("create")
        identity = getattr(document, "chart_id", None) or document.dashboard_id
        self.docs[identity] = [document]
        self.workspaces[identity] = "personal-ws" if workspace_id == "personal" else workspace_id
        self.receipts[operation_id] = _receipt_for(operation_id, identity, 0, 1)
        return self.receipts[operation_id]

    async def apply(self, user, identity, operation):
        self.calls.append("apply")
        if self.conflicts:
            self.conflicts -= 1
            self.docs[identity].append(self.docs[identity][-1])
            raise NovaException("Revision conflict", status_code=409)
        if len(self.docs[identity]) != operation.expected_revision:
            raise NovaException("Revision conflict", status_code=409)
        self.docs[identity].append(apply_operation(self.docs[identity][-1], operation))
        new = len(self.docs[identity])
        self.receipts[operation.operation_id] = _receipt_for(
            operation.operation_id, identity, new - 1, new)
        return self.receipts[operation.operation_id]

    async def receipt(self, user, identity, operation_id):
        if operation_id not in self.receipts:
            raise NovaException("Operation receipt not found", status_code=404)
        return self.receipts[operation_id]

    async def browse(self, user, workspace_id, *, kind, draft, limit):
        assert draft and kind == "dashboard"
        # The real store rejects pages above 100 rows.
        assert 1 <= limit <= 100
        rows = {
            "personal-ws": [
                {"resource_id": EXISTING, "title": "Sales", "owner_name": "alice",
                 "current_publication_id": None,
                 "updated_at": datetime(2026, 11, 15, tzinfo=UTC)},
            ],
            "shared-ws": [
                {"resource_id": UUID(int=7), "title": "Team sales", "owner_name": "bob",
                 "current_publication_id": UUID(int=9),
                 "updated_at": datetime(2026, 11, 16, tzinfo=UTC)},
                {"resource_id": UUID(int=8), "title": "Stock", "owner_name": "bob",
                 "current_publication_id": None, "updated_at": None},
            ],
        }
        return rows.get(workspace_id, [])


def _fields() -> tuple[DatasetField, ...]:
    return (
        DatasetField(field_id="brand", name="brand", source_column="brand",
                     logical_type="string", physical_type="VARCHAR", semantic_role="dimension",
                     nullable=True),
        DatasetField(field_id="units", name="units", source_column="units",
                     logical_type="integer", physical_type="BIGINT", semantic_role="measure",
                     nullable=True),
    )


class FakeDatasets:
    def __init__(self, docs: FakeDocuments) -> None:
        self.docs = docs
        self.inspected: list[Any] = []

    async def inspect(self, user, request):
        self.inspected.append(request)
        source = SqlSource(sql=request.source.sql, ast_digest="b" * 64,
                           catalog="default_catalog", database=request.source.database,
                           dependencies=({"kind": "table", "catalog": "default_catalog",
                                          "database": "sales", "object_name": "orders",
                                          "physical_id": "t1"},))
        return SqlInspection(dataset_id=request.dataset_id, source=source, fields=_fields(),
                             schema_fingerprint="c" * 64)

    async def create_in_dashboard(self, user, identity, request, *, operation_id,
                                  expected_revision):
        self.docs.calls.append("dataset")
        if len(self.docs.docs[identity]) != expected_revision:
            raise NovaException("Revision conflict", status_code=409)
        dataset = DatasetDefinition(
            dataset_id=request.dataset.dataset_id, title=request.dataset.title,
            source=(await self.inspect(user, request.dataset)).source, fields=_fields(),
            schema_fingerprint="c" * 64)
        self.docs.docs[dataset.dataset_id] = [dataset]
        dashboard = self.docs.docs[identity][-1]
        self.docs.docs[identity].append(dashboard.model_copy(update={"datasets": (
            *dashboard.datasets, DatasetReference(dataset_id=dataset.dataset_id, revision=1))}))
        new = len(self.docs.docs[identity])
        self.docs.receipts[operation_id] = _receipt_for(operation_id, identity, new - 1, new)
        return self.docs.receipts[operation_id]


class Space:
    def __init__(self, identity: str, can_edit: bool) -> None:
        self.id, self.name, self.can_edit = identity, identity, can_edit


@pytest.fixture
def docs(monkeypatch) -> FakeDocuments:
    from app.modules.workspaces import spaces

    fake = FakeDocuments()
    datasets = FakeDatasets(fake)
    monkeypatch.setattr(native, "_documents", lambda: fake)
    monkeypatch.setattr(studio_publish, "_documents", lambda: fake)
    monkeypatch.setattr(studio_publish, "_datasets", lambda: datasets)

    async def visible(user):
        return [Space("personal-ws", True), Space("shared-ws", True), Space("read-ws", False)]

    monkeypatch.setattr(spaces.space_service, "visible", visible)
    monkeypatch.setattr(studio_results, "_message", _message(
        [*TABLE_STEPS, _ui_block(BAR, ROWS)]))
    fake.datasets = datasets  # type: ignore[attr-defined]
    return fake


def _widgets(dashboard: DashboardDefinition) -> list[Any]:
    return [widget for page in dashboard.pages for widget in page.widgets]


# ── Listing ─────────────────────────────────────────────────────────────────


async def test_listing_shows_editable_dashboards_newest_first(docs):
    listed = await studio_publish.editable_dashboards(ALICE)
    assert listed["page"] == 0 and listed["has_more"] is False
    assert [item["title"] for item in listed["dashboards"]] == ["Team sales", "Sales", "Stock"]
    first = listed["dashboards"][0]
    assert first == {"id": str(UUID(int=7)), "title": "Team sales", "published": True,
                     "own": False, "changed": "2026-11-16T00:00:00+00:00",
                     "workspace_id": "shared-ws"}
    assert listed["dashboards"][1]["own"] is True
    searched = await studio_publish.editable_dashboards(ALICE, search="sales", page=0)
    assert {item["title"] for item in searched["dashboards"]} == {"Sales", "Team sales"}
    assert (await studio_publish.editable_dashboards(ALICE, page=1))["dashboards"] == []


# ── Publishing ──────────────────────────────────────────────────────────────


async def _ref(locator: Locator) -> str:
    return studio_results.sign(locator, ALICE)


async def test_adding_a_table_to_an_existing_dashboard_places_one_widget(docs):
    ref = await _ref(Locator("t1", "m1", "content-1"))
    out = await studio_publish.publish(ALICE, ref=ref, idempotency_key=KEY,
                                       target=Target("existing", EXISTING))
    assert out["status"] == "SUCCEEDED" and out["dashboard_id"] == str(EXISTING)
    assert out["workspace_id"] == "abc" and out["reused_chart"] is False
    assert out["concepts"][0]["qualified_name"] == "Sales.Units"
    request = docs.datasets.inspected[0]
    assert request.source.database == "sales" and request.source.sql == SQL
    dashboard = docs.docs[EXISTING][-1]
    assert [item.dataset_id for item in dashboard.datasets] == [
        docs.docs[UUID(out["chart_id"])][0].dataset_id]
    widgets = _widgets(dashboard)
    assert len(widgets) == 2
    added = widgets[1]
    assert str(added.chart_ref.chart_id) == out["chart_id"]
    assert (added.layout.x, added.layout.y) == (6, 0)
    chart = docs.docs[UUID(out["chart_id"])][0]
    assert chart.visual == "table" and isinstance(chart.encoding, TableEncoding)
    assert docs.workspaces[UUID(out["chart_id"])] == "abc"


async def test_a_replayed_request_returns_its_first_outcome_and_changes_nothing(docs):
    ref = await _ref(Locator("t1", "m1", "content-2", BAR_NODE))
    first = await studio_publish.publish(ALICE, ref=ref, idempotency_key=KEY,
                                         target=Target("existing", EXISTING))
    revisions, calls = len(docs.docs[EXISTING]), len(docs.calls)
    again = await studio_publish.publish(ALICE, ref=ref, idempotency_key=KEY,
                                         target=Target("existing", EXISTING))
    assert again == first
    assert len(docs.docs[EXISTING]) == revisions and len(docs.calls) == calls
    chart = docs.docs[UUID(first["chart_id"])][0]
    assert isinstance(chart.encoding, CartesianEncoding)
    assert chart.sort[0].field_id == "units" and chart.sort[0].direction == "desc"


async def test_a_new_dashboard_is_created_in_the_personal_workspace(docs):
    ref = await _ref(Locator("t1", "m1", "content-1"))
    out = await studio_publish.publish(ALICE, ref=ref, idempotency_key=KEY,
                                       target=Target("new", None, "Brand units"))
    dashboard_id = UUID(out["dashboard_id"])
    assert dashboard_id != EXISTING and out["workspace_id"] == "personal-ws"
    dashboard = docs.docs[dashboard_id][-1]
    assert dashboard.title == "Brand units" and len(_widgets(dashboard)) == 1


async def test_a_conflict_is_read_again_and_retried_once(docs):
    docs.conflicts = 1
    ref = await _ref(Locator("t1", "m1", "content-1"))
    out = await studio_publish.publish(ALICE, ref=ref, idempotency_key=KEY,
                                       target=Target("existing", EXISTING))
    assert out["status"] == "SUCCEEDED" and len(_widgets(docs.docs[EXISTING][-1])) == 2
    docs.conflicts = 2
    with pytest.raises(PublishError) as caught:
        await studio_publish.publish(ALICE, ref=ref, idempotency_key=KEY.replace("5f", "6f"),
                                     target=Target("existing", EXISTING), title="Units again")
    assert caught.value.status == 409


async def test_refusals_are_plain_and_change_nothing(docs, monkeypatch):
    revisions = len(docs.docs[EXISTING])
    # A tampered reference.
    ref = await _ref(Locator("t1", "m1", "content-1"))
    prefix, payload, mac = ref.split(".")
    with pytest.raises(PublishError) as tampered:
        await studio_publish.publish(ALICE, ref=f"{prefix}.{payload}.{mac[::-1]}",
                                     idempotency_key=KEY, target=Target("existing", EXISTING))
    assert tampered.value.status == 404
    # Another user's reference.
    with pytest.raises(PublishError) as foreign:
        await studio_publish.publish({**ALICE, "username": "bob"}, ref=ref,
                                     idempotency_key=KEY, target=Target("existing", EXISTING))
    assert foreign.value.code == "PREVIEW_NOT_FOUND"
    # A result Dataviz cannot draw the same way.
    monkeypatch.setattr(studio_results, "_message", _message([
        _semantic_step(), _ui_block(BAR, [{"brand": "A", "units": 1},
                                          {"brand": "A", "units": 2}])]))
    bar = await _ref(Locator("t1", "m1", "content-2", BAR_NODE))
    with pytest.raises(PublishError) as unsupported:
        await studio_publish.publish(ALICE, ref=bar, idempotency_key=KEY,
                                     target=Target("existing", EXISTING))
    assert unsupported.value.code == "NON_UNIQUE_CATEGORY"
    # A role that cannot edit the dashboard.
    monkeypatch.setattr(studio_results, "_message", _message(TABLE_STEPS))
    docs.editable = False
    with pytest.raises(PublishError) as viewer:
        await studio_publish.publish(ALICE, ref=ref, idempotency_key=KEY,
                                     target=Target("existing", EXISTING))
    assert viewer.value.status == 403
    assert len(docs.docs[EXISTING]) == revisions
    for refusal in (tampered, foreign, unsupported, viewer):
        text = refusal.value.message
        assert "SELECT" not in text and "/api" not in text and "nova_meta" not in text


async def test_a_query_without_a_named_database_is_refused(docs, monkeypatch):
    monkeypatch.setattr(studio_results, "_message", _message([
        _semantic_step(sql="SELECT brand FROM orders"),
        {"kind": "table", "content_id": "content-1", "tool_call_id": "q1",
         "columns": ["brand"]},
    ]))
    ref = await _ref(Locator("t1", "m1", "content-1"))
    with pytest.raises(PublishError) as caught:
        await studio_publish.publish(ALICE, ref=ref, idempotency_key=KEY,
                                     target=Target("existing", EXISTING))
    assert caught.value.code == "SOURCE_UNSUPPORTED"


def test_charts_are_built_from_the_dataset_fields():
    dataset = DatasetDefinition(
        dataset_id=UUID(int=3), title="t",
        source=SqlSource(sql=SQL, ast_digest="b" * 64, catalog="default_catalog",
                         database="sales", dependencies=({
                             "kind": "table", "catalog": "default_catalog",
                             "database": "sales", "object_name": "orders",
                             "physical_id": "t1"},)),
        fields=_fields(), schema_fingerprint="c" * 64)
    common = {"title": "T", "chart_id": UUID(int=4), "dataset_id": UUID(int=3),
              "revision": 1, "dataset": dataset}
    pie = studio_publish.build_native_chart(
        {"visual": "pie", "dimensions": [{"field": "brand"}],
         "measures": [{"field": "units", "aggregation": "sum"}], "donut": True}, **common)
    assert isinstance(pie.encoding, CategoryEncoding) and pie.encoding.donut
    kpi = studio_publish.build_native_chart(
        {"visual": "kpi", "measures": [{"field": "units", "aggregation": "sum"}]}, **common)
    assert isinstance(kpi.encoding, SingleMeasureEncoding)
    with pytest.raises(native.Refusal):
        studio_publish.build_native_chart(
            {"visual": "kpi", "measures": [{"field": "brand", "aggregation": "sum"}]}, **common)


# ── HTTP contract ───────────────────────────────────────────────────────────


@pytest.fixture
def client(docs):
    from fastapi import FastAPI

    from app.core.deps import get_current_user
    from app.modules.viz.studio_publish_router import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/agents/studio/dataviz")
    app.dependency_overrides[get_current_user] = lambda: ALICE
    return TestClient(app)


def test_the_routes_keep_their_paths_and_shapes(client):
    base = "/api/v1/agents/studio/dataviz"
    checked = client.post(f"{base}/capabilities/check", json={"items": [
        {"thread_id": "t1", "message_id": "m1", "content_id": "content-1"}]})
    assert checked.status_code == 200
    (result,) = checked.json()["results"]
    assert result["status"] == "SUPPORTED" and result["can_add"]
    listed = client.get(f"{base}/dashboards", params={"search": "", "page": 0}).json()
    assert {"dashboards", "page", "has_more"} <= set(listed)
    published = client.post(f"{base}/publish", json={
        "preview_ref": result["preview_ref"], "idempotency_key": KEY,
        "target": {"mode": "existing", "dashboard_id": str(EXISTING)},
        "editor_state": "clean"})
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["status"] == "SUCCEEDED" and UUID(body["chart_id"]) and UUID(body["operation_id"])
    bad = client.post(f"{base}/publish", json={
        "preview_ref": "pv1.x.y", "idempotency_key": "not-a-uuid",
        "target": {"mode": "existing"}})
    assert bad.status_code == 422
    missing = client.post(f"{base}/publish", json={
        "preview_ref": "pv1.aaaa.bbbb", "idempotency_key": KEY,
        "target": {"mode": "new", "title": "X"}})
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "PREVIEW_NOT_FOUND"


def test_http_errors_carry_only_a_code_and_a_message():
    from app.modules.viz.studio_publish_router import _error

    error = _error(PublishError("Nothing was changed.", status=403, code="NOT_PERMITTED"))
    assert isinstance(error, HTTPException) and error.status_code == 403
    assert error.detail == {"code": "NOT_PERMITTED", "message": "Nothing was changed."}


async def test_adding_the_same_answer_again_reuses_its_chart_and_dataset(docs):
    # 2026-11-16 walkthrough: a second click added a duplicate chart and dataset.
    ref = await _ref(Locator("t1", "m1", "content-2", BAR_NODE))
    first = await studio_publish.publish(ALICE, ref=ref, idempotency_key=KEY,
                                         target=Target("existing", EXISTING))
    revisions, inspected = len(docs.docs[EXISTING]), len(docs.datasets.inspected)
    again = await studio_publish.publish(ALICE, ref=ref, idempotency_key=uuid4().hex,
                                         target=Target("existing", EXISTING))
    assert again["reused_chart"] is True and again["chart_id"] == first["chart_id"]
    assert len(docs.docs[EXISTING]) == revisions
    assert len(docs.datasets.inspected) == inspected
    assert len(docs.docs[EXISTING][-1].datasets) == 1


async def test_a_different_chart_of_the_same_query_shares_its_dataset(docs):
    ref = await _ref(Locator("t1", "m1", "content-2", BAR_NODE))
    first = await studio_publish.publish(ALICE, ref=ref, idempotency_key=KEY,
                                         target=Target("existing", EXISTING))
    other = await studio_publish.publish(ALICE, ref=ref, idempotency_key=uuid4().hex,
                                         target=Target("existing", EXISTING),
                                         title="Units, ranked")
    assert other["reused_chart"] is False and other["chart_id"] != first["chart_id"]
    dashboard = docs.docs[EXISTING][-1]
    assert len(dashboard.datasets) == 1
    assert (docs.docs[UUID(other["chart_id"])][0].dataset_id
            == docs.docs[UUID(first["chart_id"])][0].dataset_id)


@pytest.mark.parametrize(("asked", "answer", "chart", "title"), [
    (None, "Berapa total sell-out unit per bulan dari Agustus sampai Oktober 2026?",
     {"dimensions": [{"field": "outlet_stock_daily.stock_date"}],
      "measures": [{"field": "sell_out_units"}]}, "Sell out units by stock date"),
    (None, "Revenue by region", {"measures": [{"field": "revenue"}]}, "Revenue by region"),
    ("My chart", "Anything?", {}, "My chart"),
    (None, "How do prices relate to units?", {"x": "price", "y": "units"}, "Units by price"),
    (None, "x" * 80, {}, "x" * 59 + "…"),
    (None, None, {}, "Chart"),
])
def test_chart_titles_are_short_labels_not_questions(asked, answer, chart, title):
    assert studio_publish.short_title(asked, answer, chart) == title
