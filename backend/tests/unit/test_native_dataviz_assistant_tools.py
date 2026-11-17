"""Native Dataviz assistant tools, with the document services faked at their boundary."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4, uuid5

import pytest
from pydantic import ValidationError

from app.core.exceptions import NovaException
from app.core.metadata_db import MetadataConflict
from app.modules.agents.tools import native_dataviz as native
from app.modules.assistant import events
from app.modules.assistant.plain_language import find_technical_leaks, make_plain
from app.modules.assistant.schemas import AgentMessageRequest, DatavizTurnInput
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from app.modules.viz.contracts.charts import ChartDefinition
from app.modules.viz.contracts.common import DecimalValue, IntegerValue, StringValue
from app.modules.viz.contracts.dashboards import DashboardDefinition, Layout
from app.modules.viz.contracts.datasets import DatasetDefinition
from app.modules.viz.contracts.queries import DatasetQueryResult, QueryColumn
from app.modules.viz.operations import apply_operation

USER = {"username": "alice", "active_role": "ANALYST", "session_id": "s1",
        "security_context_version": 2}
DASHBOARD = UUID("11111111-1111-4111-8111-111111111111")
PAGE = UUID("22222222-2222-4222-8222-222222222222")
DATASET = UUID("33333333-3333-4333-8333-333333333333")
CHART = UUID("44444444-4444-4444-8444-444444444444")
WIDGET = UUID("55555555-5555-4555-8555-555555555555")
FINGERPRINT = "a" * 64


def _field(field_id: str, name: str, kind: str, role: str, **extra: Any) -> dict[str, Any]:
    return {"field_id": field_id, "name": name, "source_column": field_id,
            "logical_type": kind, "physical_type": kind.upper(), "semantic_role": role,
            "nullable": field_id != "order_id", **extra}


DATASET_DEF = DatasetDefinition.model_validate({
    "dataset_id": str(DATASET), "title": "Orders",
    "source": {"kind": "table", "catalog": "default_catalog", "database": "sales",
               "object_name": "orders", "physical_id": "t1"},
    "fields": [
        _field("order_id", "Order", "integer", "dimension"),
        _field("city", "City", "string", "dimension"),
        _field("order_date", "Order date", "date", "dimension"),
        _field("revenue", "Revenue", "decimal", "measure", currency="IDR"),
        _field("quantity", "Quantity", "integer", "measure"),
    ],
    "schema_fingerprint": FINGERPRINT,
})


def _chart(chart_id: UUID = CHART, visual: str = "bar") -> ChartDefinition:
    return ChartDefinition.model_validate({
        "chart_id": str(chart_id), "title": "Revenue by city", "dataset_id": str(DATASET),
        "dataset_revision": 1, "visual": visual,
        "encoding": {"kind": "cartesian",
                     "dimensions": [{"field_id": "city"}],
                     "measures": [{"field_id": "revenue", "aggregation": "sum"}]},
    })


def _dashboard() -> DashboardDefinition:
    return DashboardDefinition.model_validate({
        "dashboard_id": str(DASHBOARD), "title": "Sales",
        "pages": [{"page_id": str(PAGE), "title": "Overview", "widgets": [{
            "kind": "chart", "widget_id": str(WIDGET),
            "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
            "chart_ref": {"chart_id": str(CHART), "revision": 1},
        }]}],
        "datasets": [{"dataset_id": str(DATASET), "revision": 1}],
    })


class FakeDocuments:
    """The native document service boundary: revisions, CAS and receipts."""

    def __init__(self) -> None:
        self.docs: dict[UUID, list[Any]] = {
            DASHBOARD: [_dashboard()], DATASET: [DATASET_DEF], CHART: [_chart()],
        }
        self.receipts: dict[UUID, dict[str, Any]] = {}
        self.workspaces: dict[UUID, str] = {DASHBOARD: "abc", DATASET: "abc", CHART: "abc"}
        self.conflicts = 0
        #: What the other person saved when a conflict happens.
        self.someone_else = lambda document: document
        self.crash: Exception | None = None
        self.crash_commits = True
        self.applied: list[Any] = []
        self.published: list[Any] = []
        self.created: list[tuple[str, Any]] = []
        self.editable = True

    def _receipt(self, operation_id: UUID, identity: UUID, base: int) -> dict[str, Any]:
        receipt = {"operation_id": operation_id, "resource_id": identity,
                   "base_revision": base, "new_revision": len(self.docs[identity]),
                   "event_sequence": len(self.receipts) + 1,
                   "committed_at": datetime(2026, 11, 16, tzinfo=UTC)}
        self.receipts[operation_id] = receipt
        return receipt

    async def read(self, user, identity, *, draft=False, revision=None):
        if identity not in self.docs:
            raise NovaException("Document not found", status_code=404)
        if draft and not self.editable:
            raise NovaException("Metadata operation is not authorized", status_code=403)
        history = self.docs[identity]
        number = revision or len(history)
        return {"resource_id": identity, "revision": number, "metadata_revision": 1,
                "grants_revision": 1, "publication_id": None,
                "definition": history[number - 1]}

    async def workspace_of(self, user, identity):
        return self.workspaces[identity]

    async def create_visual(self, user, workspace_id, document, *, operation_id):
        if operation_id in self.receipts:
            return self.receipts[operation_id]
        identity = getattr(document, "chart_id", None) or document.dashboard_id
        self.created.append((workspace_id, document))
        self.docs[identity] = [document]
        self.workspaces[identity] = workspace_id
        return self._receipt(operation_id, identity, 0)

    async def replace_visual(self, user, document, *, operation_id, expected_revision):
        if operation_id in self.receipts:
            return self.receipts[operation_id]
        identity = document.chart_id
        if len(self.docs[identity]) != expected_revision:
            raise MetadataConflict()
        self.docs[identity].append(document)
        return self._receipt(operation_id, identity, expected_revision)

    async def apply(self, user, identity, operation):
        if operation.operation_id in self.receipts:
            return self.receipts[operation.operation_id]
        if self.conflicts:
            self.conflicts -= 1
            self.docs[identity].append(self.someone_else(self.docs[identity][-1]))
            raise MetadataConflict()
        if len(self.docs[identity]) != operation.expected_revision:
            raise MetadataConflict()
        self.applied.append(operation)
        if self.crash is not None:
            if self.crash_commits:
                self.docs[identity].append(apply_operation(self.docs[identity][-1], operation))
                self._receipt(operation.operation_id, identity, operation.expected_revision)
            raise self.crash
        self.docs[identity].append(apply_operation(self.docs[identity][-1], operation))
        return self._receipt(operation.operation_id, identity, operation.expected_revision)

    async def publish(self, user, identity, request):
        if len(self.docs[identity]) != request.expected_revision:
            raise MetadataConflict()
        self.published.append(request)
        return self._receipt(request.operation_id, identity, request.expected_revision)

    async def receipt(self, user, identity, operation_id):
        if operation_id not in self.receipts:
            raise NovaException("Operation receipt not found", status_code=404)
        return self.receipts[operation_id]

    async def browse(self, user, workspace_id, *, kind, draft, limit):
        return [{"resource_id": DASHBOARD, "title": "Sales", "revision": 3,
                 "current_publication_id": None if draft else uuid4()}]


class FakeDatasets:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def query(self, user, identity, request):
        self.requests.append(request)
        return DatasetQueryResult(
            dataset_id=identity, revision=request.revision,
            columns=(QueryColumn(key="d0", name="City", logical_type="string", role="dimension"),
                     QueryColumn(key="m0", name="Revenue", logical_type="decimal",
                                 role="measure")),
            rows=((StringValue(value="Surabaya"), DecimalValue(value="1573810277.50")),
                  (StringValue(value="Malang"), IntegerValue(value="12"))),
            truncated=False,
        )


@pytest.fixture
def docs(monkeypatch) -> FakeDocuments:
    fake = FakeDocuments()
    monkeypatch.setattr(native, "_documents", lambda: fake)
    return fake


@pytest.fixture
def data(monkeypatch) -> FakeDatasets:
    fake = FakeDatasets()
    monkeypatch.setattr(native, "_datasets", lambda: fake)
    return fake


def _context(**native_context: Any) -> SimpleNamespace:
    opened = {"dashboard_id": str(DASHBOARD), "revision": 1, **native_context}
    return SimpleNamespace(user=USER, dataviz={"native_dashboard": opened}, thread_id="t1")


def _call(name: str, arguments: dict[str, Any], call_id: str = "call-1") -> ToolInvocation:
    return ToolInvocation(tool_call_id=call_id, tool_name=name, arguments=arguments)


def _tool(name: str) -> Any:
    registry = ToolRegistry()
    native.register(registry)
    return registry.get(name)


async def _run(name: str, arguments: dict[str, Any], context: Any = None,
               call_id: str = "call-1"):
    return await _tool(name).run(_call(name, arguments, call_id), context or _context())


def _leaks(*texts: str) -> list[Any]:
    return [leak for text in texts for leak in find_technical_leaks(text)]


# ── Arguments ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"charts": [{"title": "x", "visual": "bar", "dimensions": ["city"], "sql": "DROP"}]},
    {"charts": []},
    {"charts": [{"title": "x", "visual": "sunburst"}]},
    {"charts": [{"title": "x" * 300, "visual": "kpi"}]},
    {"charts": [{"title": "t", "visual": "kpi", "measures": [{"field": "revenue"}]}] * 7},
    {"charts": [{"title": "t", "visual": "kpi"}], "dashboard_id": "not-a-uuid"},
])
async def test_invalid_arguments_are_refused_without_echoing_them(docs, arguments):
    outcome = await _run("native_dashboard_add_charts", arguments)
    assert not outcome.ok
    assert "DROP" not in outcome.error and "not-a-uuid" not in outcome.error
    assert not docs.applied and not docs.created
    assert not _leaks(outcome.error)


# ── Encoding per visual ──────────────────────────────────────────────


@pytest.mark.parametrize(("spec", "kind"), [
    ({"visual": "bar", "dimensions": ["City"], "measures": [{"field": "Revenue"}]},
     "cartesian"),
    ({"visual": "line", "dimensions": [{"field": "order_date", "date_grain": "week"}],
      "measures": [{"field": "quantity", "aggregation": "avg"}]}, "cartesian"),
    ({"visual": "area", "dimensions": ["order date"], "measures": [{"field": "revenue"}],
      "stack": "stacked", "color": "city"}, "cartesian"),
    ({"visual": "pie", "dimensions": ["city"], "measures": [{"field": "revenue"}],
      "donut": True}, "category"),
    ({"visual": "kpi", "measures": [{"field": "revenue"}]}, "single_measure"),
    ({"visual": "table", "dimensions": ["city"], "measures": [{"field": "city",
                                                               "aggregation": "count"}]},
     "table"),
    ({"visual": "scatter", "x": "quantity", "y": "revenue", "color": "city"}, "scatter"),
])
def test_each_visual_maps_to_its_encoding(spec, kind):
    chart = native.build_chart(
        native.ChartSpec.model_validate({"title": "T", **spec}), chart_id=uuid4(),
        dataset_id=DATASET, revision=1, dataset=DATASET_DEF,
    )
    assert chart.visual == spec["visual"] and chart.encoding.kind == kind
    if spec["visual"] == "line":
        assert chart.encoding.dimensions[0].date_grain == "week"
        assert chart.encoding.measures[0].series == "line"
    if spec["visual"] == "area":
        # A date without a grain is grouped by month, never by raw timestamp.
        assert chart.encoding.dimensions[0].date_grain == "month"
        assert chart.encoding.color.field_id == "city"
    if spec["visual"] in {"bar", "pie"}:
        assert chart.sort[0].field_id == "revenue" and chart.sort[0].direction == "desc"
        assert chart.sort[0].aggregation == "sum"
    if spec["visual"] == "pie":
        # NDV-CHART-009: as in the builder, a capped pie of a sum shows the rest as Other.
        assert chart.encoding.overflow == "other"


def test_a_pie_of_an_average_keeps_only_its_top_slices():
    """An average of the rest is not the rest of the average: no Other slice."""
    chart = native.build_chart(
        native.ChartSpec.model_validate({"title": "T", "visual": "pie", "dimensions": ["city"],
                                         "measures": [{"field": "revenue", "aggregation": "avg"}]}),
        chart_id=uuid4(), dataset_id=DATASET, revision=1, dataset=DATASET_DEF,
    )
    assert chart.encoding.overflow == "error"


@pytest.mark.parametrize(("spec", "reason"), [
    ({"visual": "kpi", "dimensions": ["city"], "measures": [{"field": "revenue"}]},
     "exactly one measure"),
    ({"visual": "bar", "dimensions": ["city"], "measures": [{"field": "city"}]},
     "not a number"),
    ({"visual": "bar", "dimensions": ["province"], "measures": [{"field": "revenue"}]},
     "no field named 'province'"),
    ({"visual": "scatter", "x": "city", "y": "revenue"}, "not a number"),
    ({"visual": "pie", "dimensions": ["city", "order_date"],
      "measures": [{"field": "revenue"}]}, "exactly one dimension"),
])
def test_charts_that_do_not_fit_their_data_are_refused_in_plain_words(spec, reason):
    with pytest.raises(native.Refusal) as refused:
        native.build_chart(native.ChartSpec.model_validate({"title": "T", **spec}),
                           chart_id=uuid4(), dataset_id=DATASET, revision=1,
                           dataset=DATASET_DEF)
    assert reason in str(refused.value)
    assert not _leaks(str(refused.value))


def _build(**spec):
    return native.build_chart(native.ChartSpec.model_validate({"title": "T", **spec}),
                              chart_id=uuid4(), dataset_id=DATASET, revision=1,
                              dataset=DATASET_DEF)


def test_neva_builds_a_shaded_pivot_with_totals_and_reads_it_back():
    chart = _build(visual="pivot", dimensions=["City"],
                   columns=[{"field": "Order date", "date_grain": "month"}],
                   measures=[{"field": "Revenue"}], totals=True, shade=["Revenue"])
    assert chart.visual == "pivot"
    assert [item.field_id for item in chart.encoding.pivot_columns] == ["order_date"]
    assert chart.encoding.pivot_columns[0].date_grain == "month" and chart.encoding.grand_total
    formats = chart.style.conditional_formats
    assert [(item.field_id, item.kind) for item in formats] == [("revenue", "scale")]
    spec = native.spec_from_chart(chart)
    assert spec["columns"] == [{"field": "order_date", "date_grain": "month"}]
    assert spec["shade"] == ["revenue"] and spec["totals"] is True


@pytest.mark.parametrize(("spec", "reason"), [
    ({"visual": "pivot", "dimensions": ["City"], "measures": [{"field": "Revenue"}]},
     "column fields"),
    ({"visual": "bar", "dimensions": ["City"], "measures": [{"field": "Revenue"}],
      "shade": ["Revenue"]}, "tables and pivot tables"),
    ({"visual": "table", "dimensions": ["City"], "measures": [{"field": "Revenue"}],
      "shade": ["Quantity"]}, "measures the table shows"),
])
def test_pivots_and_shading_that_do_not_fit_are_refused_plainly(spec, reason):
    with pytest.raises(native.Refusal) as refused:
        _build(**spec)
    assert reason in str(refused.value) and not _leaks(str(refused.value))


def test_a_date_level_on_a_text_field_is_ignored_in_a_chart():
    chart = native.build_chart(native.ChartSpec.model_validate({
        "title": "T", "visual": "bar", "dimensions": [{"field": "city", "date_grain": "month"}],
        "measures": [{"field": "revenue"}]}), chart_id=uuid4(), dataset_id=DATASET, revision=1,
        dataset=DATASET_DEF)
    assert chart.encoding.dimensions[0].date_grain is None


def test_placement_takes_the_first_free_slot():
    taken = [Layout(x=0, y=0, w=6, h=4)]
    assert native.first_free(taken, 6, 4).model_dump() == {"x": 6, "y": 0, "w": 6, "h": 4}
    taken.append(Layout(x=6, y=0, w=6, h=4))
    assert native.first_free(taken, 3, 2).model_dump() == {"x": 0, "y": 4, "w": 3, "h": 2}
    assert native.first_free([], 12, 4).model_dump() == {"x": 0, "y": 0, "w": 12, "h": 4}


# ── Adding charts ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adding_a_chart_commits_a_revision_in_the_dashboards_workspace(docs):
    outcome = await _run("native_dashboard_add_charts", {"charts": [
        {"title": "Revenue by city", "visual": "bar", "dimensions": ["city"],
         "measures": [{"field": "revenue"}]},
        {"title": "Total revenue", "visual": "kpi", "measures": [{"field": "revenue"}]},
    ]})
    assert outcome.ok, outcome.error
    assert outcome.data["revision"] == 2 and outcome.data["dashboard_id"] == str(DASHBOARD)
    assert outcome.metadata["native_dashboard_change"] == {
        "dashboard_id": str(DASHBOARD), "revision": 2,
    }
    assert [workspace for workspace, _ in docs.created] == ["abc", "abc"]
    page = docs.docs[DASHBOARD][-1].pages[0]
    assert [widget.layout.model_dump() for widget in page.widgets[1:]] == [
        {"x": 6, "y": 0, "w": 6, "h": 4}, {"x": 0, "y": 4, "w": 3, "h": 2},
    ]
    assert {widget.chart_ref.chart_id for widget in page.widgets[1:]} == {
        chart.chart_id for _, chart in docs.created
    }
    assert docs.applied[0].expected_revision == 1
    assert not _leaks(outcome.summary, json.dumps(outcome.data, default=str))


@pytest.mark.asyncio
async def test_a_conflict_is_reread_and_retried_once_then_reported(docs):
    docs.conflicts = 1
    spec = {"charts": [{"title": "K", "visual": "kpi", "measures": [{"field": "revenue"}]}]}
    outcome = await _run("native_dashboard_add_charts", spec)
    assert outcome.ok and outcome.data["revision"] == 3
    assert [op.expected_revision for op in docs.applied] == [2]

    docs.conflicts = 2
    outcome = await _run("native_dashboard_add_charts", spec, call_id="call-2")
    assert not outcome.ok
    assert "changed while this was being saved" in outcome.error
    assert not _leaks(outcome.error)


@pytest.mark.asyncio
async def test_a_concurrent_edit_of_the_same_widget_is_reported_not_overwritten(docs):
    from app.modules.viz.contracts.commands import LayoutChange, Operation
    from app.modules.viz.operations import apply_operation

    def moved_by_a_person(document):
        page = document.pages[0]
        operation = Operation(operation_id=uuid4(), expected_revision=1, changes=(LayoutChange(
            page_id=page.page_id, placements=({"widget_id": WIDGET,
                                              "layout": {"x": 0, "y": 6, "w": 6, "h": 4}},)),))
        return apply_operation(document, operation)

    docs.someone_else = moved_by_a_person
    docs.conflicts = 1
    outcome = await _run("native_dashboard_arrange",
                         {"move": [{"widget_id": str(WIDGET), "x": 6, "y": 0}]})
    assert not outcome.ok
    assert "Someone else changed that part of the dashboard" in outcome.error
    assert docs.applied == []
    assert docs.docs[DASHBOARD][-1].pages[0].widgets[0].layout.y == 6


@pytest.mark.asyncio
async def test_a_person_editing_another_widget_and_neva_moving_one_both_survive(docs):
    """NDV-AGENT-003: the person moves widget A while Neva moves widget B."""
    from app.modules.viz.contracts.commands import LayoutChange, Operation
    from app.modules.viz.operations import apply_operation

    other = UUID("66666666-6666-4666-8666-666666666666")
    start = docs.docs[DASHBOARD][0]
    page = start.pages[0]
    from app.modules.viz.contracts.dashboards import Layout, TextWidget

    note = TextWidget(widget_id=other, title="Notes", text="Q3 plan",
                      layout=Layout(x=6, y=0, w=6, h=2))
    docs.docs[DASHBOARD][0] = start.model_copy(update={"pages": (
        page.model_copy(update={"widgets": (*page.widgets, note)}),)})

    def edited_by_a_person(document):
        return apply_operation(document, Operation(
            operation_id=uuid4(), expected_revision=1, changes=(LayoutChange(
                page_id=document.pages[0].page_id,
                placements=({"widget_id": WIDGET, "layout": {"x": 0, "y": 0, "w": 6, "h": 4}},
                            {"widget_id": other,
                             "layout": {"x": 6, "y": 4, "w": 6, "h": 2}})),)))

    docs.someone_else = edited_by_a_person
    docs.conflicts = 1
    outcome = await _run("native_dashboard_arrange",
                         {"move": [{"widget_id": str(WIDGET), "x": 0, "y": 8}]})
    assert outcome.ok, outcome.error
    final = {w.widget_id: w.layout for w in docs.docs[DASHBOARD][-1].pages[0].widgets}
    assert (final[WIDGET].x, final[WIDGET].y) == (0, 8)
    assert (final[other].x, final[other].y) == (6, 4)


@pytest.mark.asyncio
async def test_an_unknown_outcome_is_resolved_by_its_receipt_not_replayed(docs):
    spec = {"charts": [{"title": "K", "visual": "kpi", "measures": [{"field": "revenue"}]}]}
    docs.crash = ConnectionResetError("connection lost")
    outcome = await _run("native_dashboard_add_charts", spec)
    assert outcome.ok and outcome.data["revision"] == 2
    assert len(docs.applied) == 1

    docs.crash_commits = False
    outcome = await _run("native_dashboard_add_charts", spec, call_id="call-2")
    assert not outcome.ok and "could not be confirmed" in outcome.error
    assert len(docs.applied) == 2  # asked once, never sent again


@pytest.mark.asyncio
async def test_a_retried_call_returns_its_receipt_without_a_second_change(docs):
    spec = {"charts": [{"title": "K", "visual": "kpi", "measures": [{"field": "revenue"}]}]}
    first = await _run("native_dashboard_add_charts", spec)
    again = await _run("native_dashboard_add_charts", spec)
    assert first.data["revision"] == again.data["revision"] == 2
    assert len(docs.applied) == 1 and len(docs.docs[DASHBOARD]) == 2


@pytest.mark.asyncio
async def test_without_an_open_dashboard_a_change_asks_which_one(docs):
    context = SimpleNamespace(user=USER, dataviz=None, thread_id="t1")
    outcome = await _run("native_dashboard_add_charts",
                         {"charts": [{"title": "K", "visual": "kpi",
                                      "measures": [{"field": "revenue"}]}]}, context)
    assert not outcome.ok and "No dashboard is open" in outcome.error


@pytest.mark.asyncio
async def test_a_role_that_cannot_edit_is_told_plainly(docs):
    docs.editable = False
    outcome = await _run("native_dashboard_add_charts",
                         {"charts": [{"title": "K", "visual": "kpi",
                                      "measures": [{"field": "revenue"}]}]})
    assert not outcome.ok and "active role" in outcome.error
    assert not _leaks(outcome.error) and "403" not in outcome.error


# ── Updating, arranging, filters, publishing ─────────────────────────


@pytest.mark.asyncio
async def test_updating_a_chart_pins_its_new_revision_on_the_widget(docs):
    outcome = await _run("native_chart_update", {
        "widget_id": str(WIDGET), "visual": "line", "title": "Revenue trend",
        "dimensions": [{"field": "order_date", "date_grain": "month"}],
    })
    assert outcome.ok, outcome.error
    chart = docs.docs[CHART][-1]
    assert chart.visual == "line" and chart.title == "Revenue trend"
    assert chart.encoding.measures[0].field_id == "revenue"
    widget = docs.docs[DASHBOARD][-1].pages[0].widgets[0]
    assert widget.chart_ref.revision == 2 and outcome.data["revision"] == 2


@pytest.mark.asyncio
async def test_a_title_only_update_keeps_the_encoding(docs):
    outcome = await _run("native_chart_update", {"widget_id": str(WIDGET), "title": "New"})
    assert outcome.ok
    assert docs.docs[CHART][-1].encoding == _chart().encoding


@pytest.mark.asyncio
async def test_arranging_includes_every_widget_and_refuses_overlap(docs):
    added = await _run("native_dashboard_add_charts", {"charts": [
        {"title": "K", "visual": "kpi", "measures": [{"field": "revenue"}]}]})
    kpi = UUID(added.data["added"][0]["widget_id"])
    overlap = await _run("native_dashboard_arrange",
                         {"move": [{"widget_id": str(kpi), "x": 2, "y": 0}]}, call_id="c2")
    assert not overlap.ok and "overlap" in overlap.error
    moved = await _run("native_dashboard_arrange",
                       {"move": [{"widget_id": str(kpi), "x": 0, "y": 4, "w": 12}]},
                       call_id="c3")
    assert moved.ok
    layout = docs.applied[-1].changes[-1]
    assert {item.widget_id for item in layout.placements} == {WIDGET, kpi}
    removed = await _run("native_dashboard_arrange", {"remove": [str(kpi)]}, call_id="c4")
    assert removed.ok
    assert [w.widget_id for w in docs.docs[DASHBOARD][-1].pages[0].widgets] == [WIDGET]


@pytest.mark.asyncio
async def test_a_city_filter_binds_the_pinned_dataset_revision(docs):
    outcome = await _run("native_dashboard_filters", {"filters": [
        {"title": "City", "field": "city", "defaults": ["Surabaya"]}]})
    assert outcome.ok, outcome.error
    saved = docs.docs[DASHBOARD][-1].filters[0]
    assert saved.kind == "multi_value" and saved.scope.kind == "global"
    assert (saved.field.dataset_id, saved.field.dataset_revision, saved.field.field_id) == (
        DATASET, 1, "city")
    assert saved.defaults[0].value == "Surabaya"
    refused = await _run("native_dashboard_filters", {"filters": [
        {"title": "When", "field": "city", "kind": "date_range"}]}, call_id="c2")
    assert not refused.ok and not _leaks(refused.error)



NOTES = UUID("66666666-6666-4666-8666-666666666666")
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")


def _with_widgets(docs: FakeDocuments, *widgets: Any) -> None:
    """Put more widgets on the first page of the dashboard's first revision."""
    start = docs.docs[DASHBOARD][0]
    page = start.pages[0]
    docs.docs[DASHBOARD][0] = start.model_copy(update={"pages": (
        page.model_copy(update={"widgets": (*page.widgets, *widgets)}),)})


def _note(widget_id: UUID, title: str, x: int, y: int) -> Any:
    from app.modules.viz.contracts.dashboards import TextWidget

    return TextWidget(widget_id=widget_id, title=title, text="Q3 plan",
                      layout=Layout(x=x, y=y, w=6, h=2))


async def _approval(name: str, arguments: dict[str, Any], context: Any = None) -> str:
    """The text the person approves: the loop shows the tool's consent preview."""
    tool = _tool(name)
    return await tool.consent_preview(_call(name, arguments), context or _context())


@pytest.mark.asyncio
async def test_the_filter_approval_and_result_name_the_widgets_it_filters(docs):
    """NDV-AGENT-007: field, scope and the affected widgets, by their titles."""
    _with_widgets(docs, _note(NOTES, "Notes", 6, 0))
    arguments = {"filters": [{"title": "City", "field": "city", "defaults": ["Surabaya"]}]}
    approval = await _approval("native_dashboard_filters", arguments)
    assert "'City'" in approval and "Revenue by city" in approval
    assert "Notes" not in approval  # a text box is not filtered
    assert not UUID_TEXT.search(approval) and not _leaks(approval)
    assert not docs.applied  # showing the approval changes nothing

    outcome = await _run("native_dashboard_filters", arguments)
    assert outcome.ok, outcome.error
    assert outcome.data["filters"] == [{"title": "City", "applies_to": ["Revenue by city"]}]
    assert "Revenue by city" in outcome.summary
    assert not _leaks(outcome.summary, json.dumps(outcome.data["filters"]))


@pytest.mark.asyncio
async def test_a_filter_no_widget_uses_says_so_and_a_bad_field_is_shown_before_approval(docs):
    other = UUID("77777777-7777-4777-8777-777777777777")
    start = docs.docs[DASHBOARD][0]
    from app.modules.viz.contracts.dashboards import Page

    docs.docs[DASHBOARD][0] = start.model_copy(update={"pages": (
        *start.pages, Page(page_id=other, title="Notes page", widgets=(
            _note(NOTES, "Notes", 0, 0),)))})
    page_only = {"filters": [{"title": "City", "field": "city", "scope": "page",
                              "page_id": str(other)}]}
    approval = await _approval("native_dashboard_filters", page_only)
    assert "no widget" in approval and "Revenue by city" not in approval
    outcome = await _run("native_dashboard_filters", page_only)
    assert outcome.ok and outcome.data["filters"] == [{"title": "City", "applies_to": []}]

    wrong = {"filters": [{"title": "Province", "field": "province"}]}
    approval = await _approval("native_dashboard_filters", wrong)
    assert "cannot be saved" in approval and "province" in approval
    assert not _leaks(approval)


@pytest.mark.asyncio
async def test_the_arrange_approval_names_each_widget_and_what_happens_to_it(docs):
    """NDV-AGENT-009: every widget change is visible, and checked, before approval."""
    _with_widgets(docs, _note(NOTES, "Notes", 6, 0))
    arguments = {"move": [{"widget_id": str(WIDGET), "y": 4, "w": 12}],
                 "remove": [str(NOTES)]}
    approval = await _approval("native_dashboard_arrange", arguments)
    assert "'Revenue by city'" in approval and "'Notes'" in approval
    assert "moved" in approval and "resized" in approval and "removed" in approval
    assert approval.index("Revenue by city") < approval.index("moved")
    assert not UUID_TEXT.search(approval) and not _leaks(approval)
    assert not docs.applied

    resize_only = await _approval("native_dashboard_arrange",
                                  {"move": [{"widget_id": str(WIDGET), "h": 6}]})
    assert "'Revenue by city' resized" in resize_only and "moved" not in resize_only

    # The same local check the change runs: an overlap is shown before approval.
    overlap = await _approval("native_dashboard_arrange",
                              {"move": [{"widget_id": str(WIDGET), "x": 4}]})
    assert "cannot be saved" in overlap and "overlap" in overlap
    assert not docs.applied

    outcome = await _run("native_dashboard_arrange", arguments)
    assert outcome.ok, outcome.error
    assert {w.widget_id for w in docs.docs[DASHBOARD][-1].pages[0].widgets} == {WIDGET}


@pytest.mark.asyncio
async def test_a_large_layout_change_is_listed_within_bounds(docs):
    notes = [UUID(f"88888888-8888-4888-8888-{index:012d}") for index in range(15)]
    _with_widgets(docs, *(_note(item, f"Note {index}", 0, 4 + 2 * index)
                          for index, item in enumerate(notes)))
    approval = await _approval("native_dashboard_arrange", {"remove": [str(item)
                                                                       for item in notes]})
    assert "'Note 0' removed" in approval and "'Note 9' removed" in approval
    assert "Note 10" not in approval and "and 5 more widgets" in approval
    assert len(approval) < 800


@pytest.mark.asyncio
async def test_without_a_dashboard_the_approval_falls_back_to_the_plain_preview(docs):
    context = SimpleNamespace(user=USER, dataviz=None, thread_id="t1")
    arguments = {"remove": [str(WIDGET)]}
    tool = _tool("native_dashboard_arrange")
    approval = await _approval("native_dashboard_arrange", arguments, context)
    assert approval.startswith(tool.preview(_call(tool.name, arguments)))
    assert not _leaks(approval)


@pytest.mark.parametrize(("answer", "claims"), [
    ("I added the Total revenue KPI to the dashboard (version 2).", {"change"}),
    ("I've moved the revenue chart below the KPI.", {"change"}),
    ("Published.", {"publish"}),
    ("Done. The dashboard has been published.", {"publish"}),
    ("Added the City filter; it applies to Revenue by city.", {"change"}),
    ("I added the KPI and published the dashboard.", {"change", "publish"}),
    ("The chart has been removed from the page.", {"change"}),
    ("Saya sudah menambahkan grafik pendapatan ke dasbor.", {"change"}),
    ("Dasbor sudah diterbitkan.", {"publish"}),
    # Descriptions, refusals, offers and data answers claim nothing.
    ("This dashboard has a bar chart, Revenue by city, on the Overview page.", set()),
    ("It was published last week by the sales team.", set()),
    ("Created by Alice, the dashboard tracks revenue by city.", set()),
    ("I have not changed the dashboard. Shall I add the KPI?", set()),
    ("I couldn't add the chart because your role can only view this dashboard.", set()),
    ("If you approve, I will add a Total revenue KPI to the dashboard.", set()),
    ("Surabaya leads with revenue 1573810277.5; Malang follows with 12.", set()),
    ("I filtered the query to Surabaya; revenue there is 1573810277.5.", set()),
    ("The unpublished draft has two charts.", set()),
])
def test_change_and_publication_claims_are_recognized(answer, claims):
    from app.modules.assistant.intelligence import dashboard_change_claims

    assert dashboard_change_claims(answer) == frozenset(claims)


@pytest.mark.asyncio
async def test_publishing_needs_consent_and_publishes_the_draft_it_read(docs):
    tool = _tool("native_dashboard_publish")
    assert tool.requires_consent and tool.classification == "session_change"
    outcome = await _run("native_dashboard_publish", {})
    assert outcome.ok and docs.published[0].expected_revision == 1
    assert outcome.metadata["native_dashboard_change"]["published"] is True


def test_every_change_needs_consent_and_reads_do_not():
    from app.modules.assistant.tools import requires_consent

    for tool in native.TOOLS:
        reading = tool.name in {"native_dashboard_list", *native.READ_TOOLS}
        assert requires_consent(tool) is not reading
        assert tool.classification == ("read_only" if reading else "session_change")


@pytest.mark.asyncio
async def test_creating_a_dashboard_attaches_an_existing_dataset(docs):
    outcome = await _run("native_dashboard_create",
                         {"title": "Field sales", "dataset_id": str(DATASET)},
                         SimpleNamespace(user=USER, dataviz=None, thread_id="t1"))
    assert outcome.ok, outcome.error
    workspace, created = docs.created[0]
    assert workspace == "personal" and created.title == "Field sales"
    assert created.datasets[0].dataset_id == DATASET
    assert outcome.metadata["native_dashboard_change"]["created"] is True


# ── Reading and querying ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reading_summarizes_widgets_datasets_and_selection(docs):
    outcome = await _run("native_dashboard_read", {}, _context(selected_widget_id=str(WIDGET)))
    assert outcome.ok
    widget = outcome.data["pages"][0]["widgets"][0]
    assert widget["chart_title"] == "Revenue by city" and widget["visual"] == "bar"
    assert widget["selected"] is True and widget["dimensions"] == ["City"]
    names = [field["name"] for field in outcome.data["datasets"][0]["fields"]]
    assert "Revenue" in names and outcome.data["revision"] == 1
    assert not _leaks(json.dumps(outcome.data))


@pytest.mark.asyncio
async def test_listing_merges_drafts_and_publications(docs, monkeypatch):
    async def spaces(user):
        return [SimpleNamespace(id="personal", name="Personal Workspace")]

    monkeypatch.setattr(native, "_spaces", spaces)
    outcome = await _run("native_dashboard_list", {"search": "sal"})
    assert outcome.data["dashboards"] == [{
        "dashboard_id": str(DASHBOARD), "title": "Sales", "workspace": "Personal Workspace",
        "workspace_id": "personal", "can_edit": True, "draft_revision": 3, "published": True,
    }]


@pytest.mark.asyncio
async def test_querying_uses_the_pinned_revision_and_typed_filters(docs, data):
    outcome = await _run("native_dataset_query", {
        "dataset_id": str(DATASET), "dimensions": ["city"],
        "measures": [{"field": "revenue", "aggregation": "sum"}],
        "filters": [{"field": "quantity", "op": "gte", "values": [3]},
                    {"field": "city", "op": "in", "values": ["Surabaya", "Malang"]}],
    })
    assert outcome.ok, outcome.error
    request = data.requests[0]
    assert request.revision == 1 and request.draft is False
    assert request.sort[0].column == "m0" and request.sort[0].direction == "desc"
    children = request.filters.expression.children
    assert children[0].values[0] == IntegerValue(value="3")
    assert outcome.data["rows"] == [["Surabaya", 1573810277.5], ["Malang", 12]]
    assert outcome.metadata["settles_capabilities"] == ["semantic_query"]
    assert outcome.table["columns"] == ["City", "Revenue"]


@pytest.mark.asyncio
async def test_a_query_refusal_is_plain(docs, data, monkeypatch):
    async def refuse(user, identity, request):
        raise NovaException("Dataset query is unavailable or unsupported", status_code=422)

    monkeypatch.setattr(data, "query", refuse)
    outcome = await _run("native_dataset_query", {
        "dataset_id": str(DATASET), "measures": [{"field": "revenue"}]})
    assert not outcome.ok and "cannot be answered" in outcome.error
    assert not _leaks(outcome.error)


@pytest.mark.asyncio
async def test_a_wrong_argument_is_handed_back_for_neva_to_correct(docs, data):
    # Live run 2026-11-16: a date level on a text field ended the turn.
    outcome = await _run("native_dataset_query", {
        "dataset_id": str(DATASET),
        "dimensions": [{"field": "city", "date_grain": "year"}],
        "measures": [{"field": "revenue", "aggregation": "sum"}],
    })
    # The date level on a text field is ignored, so the question is answered.
    assert outcome.ok, outcome.error
    assert data.requests[0].dimensions[0].date_grain is None
    # A measure named the way SQL writes it still sorts by that measure.
    sorted_sql = await _run("native_dataset_query", {
        "dataset_id": str(DATASET), "dimensions": ["city"],
        "measures": [{"field": "revenue", "aggregation": "sum"}],
        "sort": {"field": "sum(revenue)", "direction": "desc"}})
    assert sorted_sql.ok, sorted_sql.error
    assert data.requests[-1].sort[0].column == "m0"
    # A refusal the model can act on stays in the turn.
    missing = await _run("native_dataset_query", {
        "dataset_id": str(DATASET), "measures": [{"field": "no_such_field"}]})
    assert not missing.ok and "no field named" in missing.error
    assert missing.recoverable and missing.error_class == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_an_access_refusal_is_not_retried(docs):
    docs.editable = False
    outcome = await _run("native_dashboard_add_charts",
                         {"charts": [{"title": "K", "visual": "kpi",
                                      "measures": [{"field": "revenue"}]}]})
    assert not outcome.ok and not outcome.recoverable


# ── Leak-free wording and the turn context ───────────────────────────


def test_previews_and_descriptions_are_plain():
    calls = {
        "native_dashboard_add_charts": {"charts": [{"title": "Revenue", "visual": "bar"}]},
        "native_chart_update": {"widget_id": str(WIDGET), "visual": "pie"},
        "native_dashboard_arrange": {"remove": [str(WIDGET)]},
        "native_dashboard_filters": {"filters": [{"title": "City", "field": "city"}]},
        "native_dashboard_create": {"title": "Sales"},
        "native_dashboard_attach_dataset": {"table": {"database": "s", "table": "orders"}},
        "native_dashboard_publish": {},
    }
    for tool in native.TOOLS:
        preview = tool.preview(_call(tool.name, calls.get(tool.name, {})))
        assert not _leaks(preview), (tool.name, preview)
        # Descriptions go to the model; they may name sibling tools, nothing else.
        assert all(leak.kind == "tool_name" for leak in _leaks(tool.description))
    for status in (401, 403, 404, 409, 422, 500, 503, 504):
        assert not _leaks(native._explain(NovaException("x", status_code=status)))


def test_the_new_tool_names_are_kept_out_of_answers():
    text = "I used native_dashboard_add_charts and native_dataset_query."
    assert {leak.text for leak in find_technical_leaks(text)} == {
        "native_dashboard_add_charts", "native_dataset_query"}
    assert not find_technical_leaks(make_plain(text))


def test_the_turn_context_carries_the_native_dashboard_strictly():
    request = AgentMessageRequest.model_validate({"content": "add a chart", "dataviz": {
        "native_dashboard": {"dashboard_id": str(DASHBOARD), "revision": 4,
                             "page_id": str(PAGE), "selected_widget_id": None}}})
    dumped = request.dataviz.model_dump(mode="json")
    assert dumped["native_dashboard"] == {"dashboard_id": str(DASHBOARD), "revision": 4,
                                          "page_id": str(PAGE), "selected_widget_id": None,
                                          "view_filters": []}
    assert dumped["dashboard_id"] is None
    assert DatavizTurnInput.model_validate({"dashboard_id": 7}).native_dashboard is None
    for bad in ({"dashboard_id": "x", "revision": 1}, {"dashboard_id": str(DASHBOARD)},
                {"dashboard_id": str(DASHBOARD), "revision": 0},
                {"dashboard_id": str(DASHBOARD), "revision": "2"},
                {"dashboard_id": str(DASHBOARD), "revision": 1, "token": "t"}):
        with pytest.raises(ValidationError):
            DatavizTurnInput.model_validate({"native_dashboard": bad})


@pytest.mark.asyncio
async def test_the_brief_reads_the_open_dashboard_and_notes_a_newer_revision(docs):
    text, read = await native.brief(USER, {"dashboard_id": str(DASHBOARD), "revision": 9})
    assert read and "Sales" in text and "now at revision 1" in text
    docs.docs.pop(DASHBOARD)
    text, read = await native.brief(USER, {"dashboard_id": str(DASHBOARD), "revision": 1})
    assert not read and "could not be read" in text and not _leaks(text)


def test_the_change_event_carries_only_the_dashboard_and_revision():
    frame = events.dashboard_changed("call-1", {"dashboard_id": DASHBOARD, "revision": 5,
                                                "published": True, "secret": "x"})
    assert frame.startswith("event: dashboard_changed\n")
    payload = json.loads(frame.split("data: ", 1)[1])
    assert payload == {"tool_call_id": "call-1", "dashboard_id": str(DASHBOARD),
                       "revision": 5, "published": True}


# ── NDV-AGENT-001: Neva saves what the chart builder saves ───────────

PARITY = json.loads((Path(__file__).parents[3] / "frontend/src/features/native-dataviz/charts"
                     / "__fixtures__/neva-parity.json").read_text())


@pytest.mark.parametrize("name", ["bar", "line", "kpi"])
def test_neva_builds_the_chart_the_chart_builder_saves(name):
    # The frontend test pins PARITY["manual"] to builder-model.toChart.
    manual = ChartDefinition.model_validate(PARITY["manual"][name])
    neva = native.build_chart(native.ChartSpec.model_validate(PARITY["neva_specs"][name]),
                              chart_id=manual.chart_id, dataset_id=DATASET, revision=1,
                              dataset=DATASET_DEF)
    assert neva.model_dump(mode="json") == manual.model_dump(mode="json")


@pytest.mark.asyncio
async def test_added_chart_is_stored_as_built_under_the_tool_derived_operation(docs):
    outcome = await _run("native_dashboard_add_charts",
                         {"charts": [PARITY["neva_specs"]["bar"]]}, call_id="call-parity")
    assert outcome.ok, outcome.error
    [(_, stored)] = docs.created
    expected = ChartDefinition.model_validate(PARITY["manual"]["bar"]).model_dump(mode="json")
    saved = stored.model_dump(mode="json")
    assert {**saved, "chart_id": None} == {**expected, "chart_id": None}
    base = uuid5(native.NAMESPACE, "call-parity")
    assert stored.chart_id == uuid5(base, "chart:0")
    # The create carries the id derived from the tool call, so a retry replays it.
    assert uuid5(base, "chart-create:0") in docs.receipts


# ── NDV-REVIS-011: the page on screen was removed meanwhile ──────────


@pytest.mark.asyncio
async def test_a_page_removed_since_the_screen_loaded_is_refused_not_replaced(docs):
    gone = uuid4()
    outcome = await _run("native_dashboard_add_charts", {"charts": [
        {"title": "Revenue", "visual": "kpi", "measures": [{"field": "revenue"}]}]},
        _context(page_id=str(gone)))
    assert not outcome.ok and outcome.recoverable
    assert "no longer on this dashboard" in outcome.error and "Overview" in outcome.error
    assert not docs.applied
    # Naming a page that exists still works, and with no page on screen the
    # first page is the default.
    named = await _run("native_dashboard_add_charts", {"page_id": str(PAGE), "charts": [
        {"title": "Revenue", "visual": "kpi", "measures": [{"field": "revenue"}]}]},
        _context(page_id=str(gone)))
    assert named.ok and len(docs.applied) == 1
    default = await _run("native_dashboard_add_charts", {"charts": [
        {"title": "Quantity", "visual": "kpi", "measures": [{"field": "quantity"}]}]},
        _context(revision=2), call_id="call-2")
    assert default.ok and len(docs.applied) == 2


@pytest.mark.asyncio
async def test_the_brief_says_the_page_on_screen_is_gone(docs):
    text, read = await native.brief(USER, {"dashboard_id": str(DASHBOARD), "revision": 1,
                                           "page_id": str(uuid4())})
    assert read and "no longer on the dashboard" in text
    text, _ = await native.brief(USER, {"dashboard_id": str(DASHBOARD), "revision": 1,
                                        "page_id": str(PAGE)})
    assert "no longer on the dashboard" not in text


# ── NDV-AGENT-005: answers use the filters on screen ─────────────────


def test_view_filters_are_bounded_and_strict():
    one = {"source": "filter", "label": "City", "text": "Surabaya",
           "dataset_id": str(DATASET), "field_id": "city", "values": ["Surabaya"]}
    parsed = DatavizTurnInput.model_validate({"native_dashboard": {
        "dashboard_id": str(DASHBOARD), "revision": 1, "view_filters": [one]}})
    assert parsed.native_dashboard.view_filters[0].values == ["Surabaya"]
    for bad in ([one] * 33, [{**one, "values": ["x"] * 21}], [{**one, "label": "x" * 129}],
                [{**one, "sql": "DROP"}], [{**one, "source": "url"}], [{**one, "label": ""}]):
        with pytest.raises(ValidationError):
            DatavizTurnInput.model_validate({"native_dashboard": {
                "dashboard_id": str(DASHBOARD), "revision": 1, "view_filters": bad}})


@pytest.mark.asyncio
async def test_the_brief_lists_the_filters_on_screen_and_asks_to_apply_them(docs):
    opened = DatavizTurnInput.model_validate({"native_dashboard": {
        "dashboard_id": str(DASHBOARD), "revision": 1, "page_id": str(PAGE), "view_filters": [
            {"source": "filter", "label": "City", "text": "Surabaya", "dataset_id": str(DATASET),
             "field_id": "city", "values": ["Surabaya"]},
            {"source": "selection", "label": "Order date: Mar 2026", "text": "Selected Mar 2026",
             "dataset_id": str(DATASET), "field_id": "order_date",
             "values": ["2026-03-01", "2026-04-01"]},
        ]}}).native_dashboard.model_dump(mode="json")
    text, read = await native.brief(USER, opened)
    assert read and "Filters and selections applied on screen" in text
    assert "native_dataset_query" in text
    assert '"label": "City"' in text and '"field_id": "city"' in text and "Surabaya" in text
    assert '"source": "selection"' in text and "2026-04-01" in text
    # Nothing on screen, nothing said.
    text, _ = await native.brief(USER, {"dashboard_id": str(DASHBOARD), "revision": 1})
    assert "Filters and selections" not in text


# ── NDV-AGENT-018: no dashboard open, none named ─────────────────────


@pytest.mark.asyncio
async def test_a_change_with_no_dashboard_named_points_to_the_list_and_asking(docs):
    no_dashboard = SimpleNamespace(user=USER, dataviz=None, thread_id="t1")
    outcome = await _run("native_dashboard_add_charts", {"charts": [
        {"title": "Revenue", "visual": "kpi", "measures": [{"field": "revenue"}]}]},
        no_dashboard)
    assert not outcome.ok and outcome.recoverable
    assert "list of dashboards" in outcome.error and "ask which one" in outcome.error
    assert not _leaks(outcome.error)
    assert not docs.applied and not docs.created


# ── NDV-AGENT-006: a chart change shows what the numbers will mean ───


def _two_measure_bar() -> ChartDefinition:
    return ChartDefinition.model_validate({
        "chart_id": str(CHART), "title": "Revenue by city", "dataset_id": str(DATASET),
        "dataset_revision": 1, "visual": "bar",
        "encoding": {"kind": "cartesian", "dimensions": [{"field_id": "city"}],
                     "measures": [{"field_id": "revenue", "aggregation": "sum"},
                                  {"field_id": "quantity", "aggregation": "avg"}]},
    })


async def _chart_approval(arguments: dict[str, Any]) -> str:
    tool = _tool("native_chart_update")
    return await tool.consent_preview(_call("native_chart_update", arguments), _context())


@pytest.mark.asyncio
async def test_a_type_change_that_drops_a_number_says_so_before_approval(docs):
    docs.docs[CHART] = [_two_measure_bar()]
    pie = await _chart_approval({"widget_id": str(WIDGET), "visual": "pie"})
    assert pie == ("Chart 'Revenue by city': from bar chart to pie chart. It will show total "
                   "Revenue by City instead of total Revenue and average Quantity by City. It no "
                   "longer shows average Quantity.")
    # A KPI holds one number: the approval says the change cannot be saved as asked.
    two = await _chart_approval({"widget_id": str(WIDGET), "visual": "kpi"})
    assert "cannot be saved as proposed" in two
    docs.docs[CHART] = [_chart()]
    kpi = await _chart_approval({"widget_id": str(WIDGET), "visual": "kpi"})
    assert kpi == ("Chart 'Revenue by city': from bar chart to KPI. It will show total Revenue "
                   "instead of total Revenue by City. It no longer shows the breakdown by City.")
    assert not _leaks(pie, kpi, two)
    docs.docs[CHART] = [_two_measure_bar()]
    # What was approved is what is saved.
    outcome = await _run("native_chart_update", {"widget_id": str(WIDGET), "visual": "pie"})
    assert outcome.ok
    saved = docs.docs[CHART][-1]
    assert saved.visual == "pie" and saved.encoding.measure.field_id == "revenue"
    assert saved.encoding.measure.aggregation == "sum"


@pytest.mark.asyncio
async def test_a_look_only_change_says_the_numbers_stay_and_a_new_sum_is_shown(docs):
    line = await _chart_approval({"widget_id": str(WIDGET), "visual": "line"})
    assert line == ("Chart 'Revenue by city': from bar chart to line chart. It still shows "
                    "total Revenue by City.")
    average = await _chart_approval({"widget_id": str(WIDGET), "measures": [
        {"field": "revenue", "aggregation": "avg"}]})
    assert average == ("Chart 'Revenue by city'. It will show average Revenue by City instead "
                       "of total Revenue by City.")
    renamed = await _chart_approval({"widget_id": str(WIDGET), "title": "Sales by city"})
    assert renamed == "Rename the chart 'Revenue by city' to 'Sales by city'"
    assert not docs.applied


# ── NDV-AGENT-008: Neva cannot change a formula ──────────────────────
# Decision (2026-11-17): calculations and saved metrics are written by people in
# the editor, where the expression is checked and previewed against the data.
# Neva only chooses fields and the six plain aggregations, so there is no
# formula it could change without that review.


def _keys(schema: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                found |= set(value)
            found |= _keys(value)
    elif isinstance(schema, list):
        for item in schema:
            found |= _keys(item)
    return found


def test_no_tool_accepts_a_formula_expression_or_sql():
    banned = {"formula", "expression", "sql", "calculation", "calc", "metric_expression",
              "definition", "query"}
    for tool in native.TOOLS:
        assert not _keys(tool.parameters) & banned, tool.name
    aggregations = set(native._MEASURE["properties"]["aggregation"]["enum"])
    assert aggregations == {"sum", "avg", "min", "max", "count", "count_distinct"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "arguments"), [
    ("native_chart_update", {"widget_id": str(WIDGET), "formula": "SUM(revenue)/SUM(quantity)"}),
    ("native_chart_update", {"widget_id": str(WIDGET), "measures": [
        {"field": "revenue", "aggregation": "sum", "expression": "revenue * 2"}]}),
    ("native_dashboard_add_charts", {"charts": [{"title": "Ratio", "visual": "kpi",
                                                 "measures": [{"field": "revenue",
                                                               "aggregation": "ratio"}]}]}),
])
async def test_a_formula_sent_anyway_changes_nothing(docs, name, arguments):
    outcome = await _run(name, arguments)
    assert not outcome.ok and not docs.applied and not docs.created
    assert "SUM(" not in outcome.error and "revenue * 2" not in outcome.error
    assert len(docs.docs[CHART]) == 1


@pytest.mark.asyncio
async def test_a_chart_with_a_calculation_can_only_be_changed_in_the_editor(docs):
    calc = _chart().model_dump(mode="json")
    calc["encoding"]["measures"] = [{"field_id": "revenue", "aggregation": "agg"}]
    docs.docs[CHART] = [ChartDefinition.model_validate(calc)]
    outcome = await _run("native_chart_update", {"widget_id": str(WIDGET), "visual": "line"})
    assert not outcome.ok and "only be edited in the editor" in outcome.error
    assert len(docs.docs[CHART]) == 1


def test_the_pointer_event_is_bounded_and_carries_no_arguments():
    frame = events.dashboard_activity("call-1", {
        "dashboard_id": DASHBOARD, "action": "move", "page_id": PAGE,
        "widget_ids": [uuid4() for _ in range(40)], "title": "secret", "sql": "DROP"})
    payload = json.loads(frame.split("data: ", 1)[1])
    assert set(payload) == {"tool_call_id", "dashboard_id", "action", "widget_ids", "page_id"}
    assert len(payload["widget_ids"]) == 12
    assert events.dashboard_activity("c", {"dashboard_id": DASHBOARD, "action": "drop"}) is None
    assert events.dashboard_activity("c", {"action": "move"}) is None
    # Read-only tools have no target; a change without an open dashboard has none.
    assert _tool("native_dashboard_read").target(_call("native_dashboard_read", {}),
                                                 _context()) is None
    no_dashboard = SimpleNamespace(user=USER, dataviz=None, thread_id="t1")
    assert _tool("native_dashboard_add_charts").target(
        _call("native_dashboard_add_charts", {"charts": [{"title": "K", "visual": "kpi"}]}),
        no_dashboard) is None


# ── NDV-PERF-008: a twenty-step layout plan lands as one atomic batch ─


@pytest.mark.asyncio
async def test_a_twenty_step_layout_plan_is_one_revision_with_every_position(docs):
    notes = [UUID(f"99999999-9999-4999-8999-{index:012d}") for index in range(20)]
    _with_widgets(docs, *(_note(item, f"Note {index}", 0, 4 + 2 * index)
                          for index, item in enumerate(notes)))
    start = len(docs.docs[DASHBOARD])
    # Two columns of ten, every widget moved and resized: twenty steps.
    plan = [{"widget_id": str(item), "x": (index % 2) * 6, "y": 4 + (index // 2) * 3,
             "w": 6, "h": 3} for index, item in enumerate(notes)]
    outcome = await _run("native_dashboard_arrange", {"move": plan})
    assert outcome.ok, outcome.error
    # One operation and one new revision for the whole plan.
    assert len(docs.applied) == 1 and len(docs.docs[DASHBOARD]) == start + 1
    widgets = docs.docs[DASHBOARD][-1].pages[0].widgets
    placed = {widget.widget_id: widget.layout for widget in widgets}
    for step in plan:
        layout = placed[UUID(step["widget_id"])]
        assert (layout.x, layout.y, layout.w, layout.h) == (step["x"], step["y"], step["w"],
                                                            step["h"])


@pytest.mark.asyncio
async def test_a_plan_with_one_bad_step_saves_none_of_it(docs):
    notes = [UUID(f"99999999-9999-4999-8999-{index:012d}") for index in range(20)]
    _with_widgets(docs, *(_note(item, f"Note {index}", 0, 4 + 2 * index)
                          for index, item in enumerate(notes)))
    start = len(docs.docs[DASHBOARD])
    plan = [{"widget_id": str(item), "x": 0, "y": 4 + index * 3, "w": 6, "h": 3}
            for index, item in enumerate(notes)]
    plan[19] = {**plan[19], "y": plan[18]["y"]}  # the last step overlaps the one before
    outcome = await _run("native_dashboard_arrange", {"move": plan})
    assert not outcome.ok and not docs.applied and len(docs.docs[DASHBOARD]) == start


# ── Live findings (neva-live-attempt-1): questions back and blank arguments ──


@pytest.mark.asyncio
async def test_several_dashboards_matching_a_name_settle_the_data_step(docs, monkeypatch):
    async def spaces(user):
        return [SimpleNamespace(id="personal", name="Personal Workspace")]

    rows = [{"resource_id": str(uuid4()), "title": title, "revision": 1,
             "current_publication_id": None} for title in ("Sales North", "Sales South")]

    class Two:
        async def browse(self, user, space, *, kind, draft, limit):
            return rows if draft else []

    monkeypatch.setattr(native, "_spaces", spaces)
    monkeypatch.setattr(native, "_documents", lambda: Two())
    outcome = await _run("native_dashboard_list", {"search": "sales"})
    assert outcome.ok and "ask which one" in outcome.summary
    assert [item["title"] for item in outcome.data["dashboards"]] == ["Sales North", "Sales South"]
    assert set(outcome.metadata["settles_capabilities"]) == set(native.ASKING_SETTLES)
    assert _tool("native_dashboard_list").may_settle == native.ASKING_SETTLES
    # A title that is the name exactly, or a single match, is not a question back.
    exact = await _run("native_dashboard_list", {"search": "sales north"})
    assert not (exact.metadata or {}).get("settles_capabilities")
    # The name as people say it: words in any order, "my ... dashboard" ignored.
    said = await _run("native_dashboard_list", {"search": "my Sales dashboard"})
    assert len(said.data["dashboards"]) == 2 and said.metadata["question_back"]
    north = await _run("native_dashboard_list", {"search": "the North Sales dashboard"})
    assert [item["title"] for item in north.data["dashboards"]] == ["Sales North"]
    assert not (north.metadata or {}).get("settles_capabilities")
    rows.pop()
    single = await _run("native_dashboard_list", {"search": "sales"})
    assert not (single.metadata or {}).get("settles_capabilities")


@pytest.mark.asyncio
async def test_a_removed_page_on_screen_settles_the_data_step(docs):
    _, settles = await native.brief_settling(USER, {
        "dashboard_id": str(DASHBOARD), "revision": 1, "page_id": str(uuid4())})
    assert "native_dashboard_read" in settles and set(native.ASKING_SETTLES) <= set(settles)
    _, settles = await native.brief_settling(USER, {
        "dashboard_id": str(DASHBOARD), "revision": 1, "page_id": str(PAGE)})
    assert settles == ("native_dashboard_read",)
    assert await native.brief_settling(USER, {"revision": 1}) == ("No dashboard is open.", ())


@pytest.mark.asyncio
async def test_blank_optional_arguments_mean_not_given(docs):
    # A real model sent every optional property as "" (live attempt 1).
    blanks = {"widget_id": str(WIDGET), "visual": "pie", "title": "", "x": "", "y": "",
              "size": "", "color": None, "sort": None, "orientation": "", "stack": ""}
    preview = _tool("native_chart_update").preview(_call("native_chart_update", blanks))
    assert preview == "Change a chart on the dashboard: chart type (to pie chart)"
    approval = await _chart_approval(blanks)
    assert approval.startswith("Chart 'Revenue by city': from bar chart to pie chart.")
    target = _tool("native_chart_update").target(_call("native_chart_update", blanks), _context())
    assert target is not None and target["widget_ids"] == [WIDGET]
    # An empty filter list is kept: it still clears the filters.
    assert native._given({"mode": "replace", "filters": [], "x": ""}) == {
        "mode": "replace", "filters": []}


@pytest.mark.asyncio
async def test_an_invalid_change_reads_plainly_on_the_approval_card(docs):
    approval = await _chart_approval({"widget_id": "not-a-widget", "visual": "pie"})
    assert approval.endswith("Some details of this change are missing, so it cannot be saved yet.")
    assert "widget_id" not in approval and "UUID" not in approval
    # Neva still gets which argument failed, to correct the call.
    outcome = await _run("native_chart_update", {"widget_id": "not-a-widget", "visual": "pie"})
    assert not outcome.ok and outcome.recoverable and "widget_id" in outcome.error
