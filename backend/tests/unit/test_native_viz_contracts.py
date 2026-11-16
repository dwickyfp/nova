"""Native document boundaries, atomic layout changes and lossless conversion."""

from copy import deepcopy
from itertools import combinations
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.modules.viz.compatibility import (
    ConversionBlocked,
    dashboard_v1_to_v2,
    worksheet_v1_to_v2,
)
from app.modules.viz.contracts.charts import ChartDefinition
from app.modules.viz.contracts.commands import Grant, Operation
from app.modules.viz.contracts.common import (
    BooleanValue,
    DateValue,
    DecimalValue,
    IntegerValue,
    TimestampValue,
)
from app.modules.viz.contracts.dashboards import ChartReference, DashboardDefinition, Layout
from app.modules.viz.contracts.datasets import DatasetDefinition, Parameter
from app.modules.viz.contracts.filters import FilterTree
from app.modules.viz.operations import apply_operation
from app.modules.viz.permissions import ALL_PERMISSIONS, expand_permissions
from app.modules.viz.spec import DashboardSpec, WorksheetSpec
from scripts.generate_viz_contracts import TARGET, outputs

DASHBOARD = "00000000-0000-4000-8000-000000000001"
PAGE = "00000000-0000-4000-8000-000000000002"
WIDGET_A = "00000000-0000-4000-8000-000000000003"
WIDGET_B = "00000000-0000-4000-8000-000000000004"
CHART = "00000000-0000-4000-8000-000000000005"
DATASET = "00000000-0000-4000-8000-000000000006"


def dashboard():
    return {
        "dashboard_id": DASHBOARD, "title": "Sales", "pages": [{
            "page_id": PAGE, "title": "Page 1", "widgets": [
                {"widget_id": widget_id, "kind": "chart", "layout": layout,
                 "chart_ref": {"chart_id": CHART, "revision": 7}}
                for widget_id, layout in [
                    (WIDGET_A, {"x": 0, "y": 0, "w": 6, "h": 4}),
                    (WIDGET_B, {"x": 6, "y": 0, "w": 6, "h": 4}),
                ]
            ],
        }],
    }


def predicate(value="Jakarta"):
    return {
        "type": "predicate", "field": {
            "dataset_id": DATASET, "dataset_revision": 1, "field_id": "region",
        }, "op": "eq", "values": [{"type": "string", "value": value}],
    }


def test_schema_generation_has_no_drift():
    for name, content in outputs().items():
        assert (TARGET / name).read_text() == content


def test_empty_dataset_registry_preserves_existing_v2_receipt_digest():
    from app.modules.viz.documents import canonical

    legacy = {"schema_version": 2, "dashboard_id": DASHBOARD, "title": "Sales",
              "description": "", "columns": 12,
              "pages": [{"page_id": PAGE, "title": "Overview", "widgets": []}],
              "filters": [], "actions": []}
    assert canonical(DashboardDefinition.model_validate(legacy)) == canonical(legacy)
    explicit_empty = DashboardDefinition.model_validate({**legacy, "datasets": []})
    assert canonical(explicit_empty) == canonical(legacy)


def test_dashboard_dataset_registry_pins_revision_and_refuses_ambiguous_versions():
    payload = {**dashboard(), "datasets": [{"dataset_id": DATASET, "revision": 3}]}
    definition = DashboardDefinition.model_validate(payload)
    assert definition.model_dump(mode="json")["datasets"] == payload["datasets"]
    with pytest.raises(ValidationError, match="unique dataset IDs"):
        DashboardDefinition.model_validate({**payload, "datasets": [
            {"dataset_id": DATASET, "revision": 3}, {"dataset_id": DATASET, "revision": 4},
        ]})


def test_pinned_references_roundtrip_and_repeated_chart_instances():
    definition = DashboardDefinition.model_validate(dashboard())
    assert DashboardDefinition.model_validate_json(definition.model_dump_json()) == definition
    left, right = definition.pages[0].widgets
    assert left.widget_id != right.widget_id
    assert left.chart_ref == right.chart_ref
    assert left.chart_ref.revision == 7


@pytest.mark.parametrize("extra", ["html", "content", "el", "callback", "actor", "owner_name"])
def test_unknown_widget_fields_are_refused(extra):
    payload = dashboard()
    payload["pages"][0]["widgets"][0][extra] = "untrusted"
    with pytest.raises(ValidationError):
        DashboardDefinition.model_validate(payload)


@pytest.mark.parametrize("key,value", [
    ("x", -1), ("x", 12), ("y", -1), ("y", 10001), ("w", 1), ("h", 1),
    ("x", True), ("x", "1"), ("h", 3.5), ("w", 13), ("h", 49),
])
def test_layout_rejects_invalid_geometry(key, value):
    layout = {"x": 0, "y": 0, "w": 6, "h": 4, key: value}
    with pytest.raises(ValidationError):
        Layout.model_validate(layout)


def test_layout_bounds_and_overlap_for_every_column_pair():
    for x in range(12):
        for width in range(2, 13):
            raw = {"x": x, "y": 0, "w": width, "h": 2}
            if x + width > 12:
                with pytest.raises(ValidationError):
                    Layout.model_validate(raw)
            else:
                layout = Layout.model_validate(raw)
                assert layout.overlaps(layout)
                below = Layout(x=x, y=2, w=width, h=2)
                assert not layout.overlaps(below)
    rectangles = [Layout(x=x, y=0, w=2, h=2) for x in range(11)]
    for left, right in combinations(rectangles, 2):
        assert left.overlaps(right) == right.overlaps(left) == (abs(left.x - right.x) < 2)


def test_overlap_and_duplicate_ids_across_pages_rejected():
    payload = dashboard()
    payload["pages"][0]["widgets"][1]["layout"]["x"] = 5
    with pytest.raises(ValidationError):
        DashboardDefinition.model_validate(payload)
    payload = dashboard()
    page = deepcopy(payload["pages"][0])
    page["page_id"] = str(uuid4())
    payload["pages"].append(page)
    with pytest.raises(ValidationError, match="unique across"):
        DashboardDefinition.model_validate(payload)


def test_atomic_reflow_can_swap_neighbors_without_mutating_original():
    definition = DashboardDefinition.model_validate(dashboard())
    operation = Operation.model_validate({
        "operation_id": str(uuid4()), "expected_revision": 1,
        "changes": [{"op": "layout.apply", "page_id": PAGE, "placements": [
            {"widget_id": WIDGET_A, "layout": {"x": 6, "y": 0, "w": 6, "h": 4}},
            {"widget_id": WIDGET_B, "layout": {"x": 0, "y": 0, "w": 6, "h": 4}},
        ]}],
    })
    updated = apply_operation(definition, operation)
    assert [widget.layout.x for widget in updated.pages[0].widgets] == [6, 0]
    assert [widget.layout.x for widget in definition.pages[0].widgets] == [0, 6]


def test_partial_reflow_and_forged_actor_are_refused():
    definition = DashboardDefinition.model_validate(dashboard())
    payload = {"operation_id": str(uuid4()), "expected_revision": 1, "changes": [{
        "op": "layout.apply", "page_id": PAGE, "placements": [{
            "widget_id": WIDGET_A, "layout": {"x": 0, "y": 5, "w": 6, "h": 4},
        }],
    }]}
    with pytest.raises(ValueError, match="every page widget"):
        apply_operation(definition, Operation.model_validate(payload))
    payload["actor"] = "root"
    with pytest.raises(ValidationError):
        Operation.model_validate(payload)


def test_batch_failure_preserves_original_document():
    definition = DashboardDefinition.model_validate(dashboard())
    payload = {"operation_id": str(uuid4()), "expected_revision": 1, "changes": [
        {"op": "dashboard.rename", "title": "Changed"},
        {"op": "widget.remove", "page_id": PAGE, "widget_id": str(uuid4())},
    ]}
    with pytest.raises(ValueError, match="Unknown widget"):
        apply_operation(definition, Operation.model_validate(payload))
    assert definition.title == "Sales"


def test_same_field_predicates_remain_distinct_and_filters_are_bounded():
    first, second = predicate(), predicate("Bandung")
    first["source"], second["source"] = "global", "chart"
    tree = FilterTree.model_validate({"expression": {"type": "and", "children": [first, second]}})
    assert len(tree.expression.children) == 2
    nested = first
    for _ in range(8):
        nested = {"type": "not", "child": nested}
    with pytest.raises(ValidationError, match="8 levels"):
        FilterTree.model_validate({"expression": nested})


@pytest.mark.parametrize("op,values", [
    ("eq", []), ("in", []), ("between", [{"type": "integer", "value": "2"}]),
    ("is_null", [{"type": "string", "value": "secret"}]),
    ("like_literal", [{"type": "boolean", "value": True}]),
    ("in", [{"type": "integer", "value": "1"}, {"type": "string", "value": "1"}]),
])
def test_filter_operand_arity_and_types(op, values):
    value = predicate()
    value.update(op=op, values=values)
    with pytest.raises(ValidationError):
        FilterTree.model_validate({"expression": value})


@pytest.mark.parametrize("model,value", [
    (DateValue, "2023-02-29"), (TimestampValue, "2024-01-01T00:00:00"),
    (IntegerValue, "9223372036854775808"), (DecimalValue, "NaN"),
    (DecimalValue, "1e4"), (DecimalValue, "1" * 38 + ".2"), (BooleanValue, "false"),
])
def test_scalar_domain_is_lossless_and_finite(model, value):
    with pytest.raises(ValidationError):
        model(value=value)


def test_precise_scalar_values():
    assert IntegerValue(value="9223372036854775807").value == "9223372036854775807"
    assert DecimalValue(value="12345678901234567890.123456789").value.endswith(".123456789")
    assert DateValue(value="2024-02-29").value == "2024-02-29"
    assert TimestampValue(value="2024-02-29T12:00:00+07:00").value.endswith("+07:00")


def test_parameter_refuses_secret_defaults_and_incompatible_enum():
    with pytest.raises(ValidationError, match="Sensitive"):
        Parameter(parameter_id="token", type="string", sensitive=True,
                  defaults=({"type": "string", "value": "secret"},))
    with pytest.raises(ValidationError, match="allowed"):
        Parameter(parameter_id="region", type="enum")


@pytest.mark.parametrize("given,expected", [
    ({"view"}, {"view"}), ({"comment"}, {"view", "comment"}),
    ({"interact"}, {"view", "interact"}), ({"publish"}, set()),
    ({"edit"}, {"view", "interact", "comment", "edit"}),
    ({"edit", "publish"}, {"view", "interact", "comment", "edit", "publish"}),
    ({"manage"}, ALL_PERMISSIONS - {"export"}), ({"export"}, {"export"}),
])
def test_permission_implications_never_infer_export(given, expected):
    assert expand_permissions(given) == expected
    assert expand_permissions(given, owner=True) == ALL_PERMISSIONS


def test_publish_grant_requires_edit_and_expiry_requires_timezone():
    with pytest.raises(ValidationError):
        Grant(subject_type="role", subject_id="Sales", permissions=("publish",))
    with pytest.raises(ValidationError):
        Grant(subject_type="user", subject_id="analyst", permissions=("view",),
              expires_at="2026-01-01T00:00:00")


def test_legacy_layout_converts_exactly_and_keeps_shared_chart_reference():
    legacy = DashboardSpec.model_validate({"title": "Sales", "zones": [{
        "zone_id": "first", "kind": "sheet", "artifact_id": "old-chart",
        "rect": {"x": 12, "y": 8, "w": 12, "h": 4},
    }]})
    ref = ChartReference(chart_id=UUID(CHART), revision=3)
    converted = dashboard_v1_to_v2(legacy, dashboard_id=UUID(DASHBOARD),
                                   chart_mappings={"old-chart": ref})
    widget = converted.pages[0].widgets[0]
    assert widget.layout == Layout(x=6, y=8, w=6, h=4)
    assert widget.chart_ref == ref
    assert converted == dashboard_v1_to_v2(legacy, dashboard_id=UUID(DASHBOARD),
                                          chart_mappings={"old-chart": ref})


def test_legacy_odd_layout_is_not_rounded():
    legacy = DashboardSpec.model_validate({"zones": [{
        "zone_id": "first", "kind": "spacer", "rect": {"x": 1, "y": 0, "w": 5, "h": 2},
    }]})
    with pytest.raises(ConversionBlocked, match="LAYOUT_REPACK_REVIEW_REQUIRED"):
        dashboard_v1_to_v2(legacy, dashboard_id=UUID(DASHBOARD), chart_mappings={})


def test_legacy_worksheet_preserves_aggregation_and_bound_identity():
    legacy = WorksheetSpec.model_validate({
        "title": "Revenue", "source": {"kind": "table", "database": "sales", "table": "orders"},
        "columns": [{"field": "Region"}],
        "rows": [{"field": "Revenue", "aggregation": "sum", "field_type": "quantitative"}],
        "marks": {"type": "bar"}, "limit": 100,
    })
    chart = worksheet_v1_to_v2(
        legacy, chart_id=UUID(CHART), dataset_id=UUID(DATASET), dataset_revision=3,
        field_mapping={"Region": "region-v1", "Revenue": "revenue-v1"},
    )
    assert chart.dataset_revision == 3
    assert chart.encoding.measures[0].field_id == "revenue-v1"
    assert chart.encoding.measures[0].aggregation == "sum"
    assert ChartDefinition.model_validate_json(chart.model_dump_json()) == chart


@pytest.mark.parametrize("discrete,date_mode", [(True, "part"), (False, "truncate")])
def test_legacy_date_axis_keeps_part_and_continuous_timeline_semantics(discrete, date_mode):
    legacy = WorksheetSpec.model_validate({
        "title": "Monthly revenue",
        "source": {"kind": "table", "database": "sales", "table": "orders"},
        "columns": [{"field": "Date", "field_type": "temporal", "date_part": "month",
                     "discrete": discrete}],
        "rows": [{"field": "Revenue", "aggregation": "sum", "field_type": "quantitative"}],
        "marks": {"type": "line"}, "limit": 100,
    })
    chart = worksheet_v1_to_v2(
        legacy, chart_id=UUID(CHART), dataset_id=UUID(DATASET), dataset_revision=3,
        field_mapping={"Date": "date-v1", "Revenue": "revenue-v1"},
    )
    dimension = chart.encoding.dimensions[0]
    assert dimension.discrete is discrete
    assert dimension.date_grain == "month"
    assert dimension.date_mode == date_mode


def test_dataset_identity_and_unknown_grain_are_validated():
    payload = {
        "dataset_id": DATASET, "title": "Orders", "source": {
            "kind": "table", "catalog": "default_catalog", "database": "sales",
            "object_name": "orders", "physical_id": "engine-object-123",
        }, "fields": [{"field_id": "region", "name": "Region", "source_column": "region",
                       "logical_type": "string", "physical_type": "VARCHAR",
                       "semantic_role": "dimension", "nullable": False}],
        "schema_fingerprint": "a" * 64,
    }
    assert DatasetDefinition.model_validate(payload).source.physical_id == "engine-object-123"
    payload["grain"] = ["unknown"]
    with pytest.raises(ValidationError, match="unknown field"):
        DatasetDefinition.model_validate(payload)


def _table_chart(visual="table", **style):
    encoding = {"kind": "table", "dimensions": [{"field_id": "region"}],
                "measures": [{"field_id": "revenue", "aggregation": "sum"}]}
    if visual == "pivot":
        encoding["pivot_columns"] = [{"field_id": "channel"}]
    return {"schema_version": 2, "chart_id": str(uuid4()), "title": "T", "visual": visual,
            "dataset_id": str(uuid4()), "dataset_revision": 1, "encoding": encoding,
            "style": style}


@pytest.mark.parametrize("visual", ["table", "pivot"])
def test_conditional_formats_shade_measures_of_tables_and_pivots(visual):
    chart = ChartDefinition.model_validate(_table_chart(visual, conditional_formats=[
        {"field_id": "revenue", "kind": "scale"},
        {"field_id": "revenue", "kind": "rule", "operator": "lt",
         "value": {"type": "decimal", "value": "0"}, "tone": "negative"},
    ]))
    assert [item.kind for item in chart.style.conditional_formats] == ["scale", "rule"]


@pytest.mark.parametrize(("rule", "reason"), [
    ({"field_id": "revenue", "kind": "rule"}, "operator and a value"),
    ({"field_id": "revenue", "kind": "scale", "operator": "gt",
      "value": {"type": "decimal", "value": "1"}}, "no operator or value"),
    ({"field_id": "region", "kind": "scale"}, "must name a measure"),
])
def test_incomplete_or_misplaced_conditional_formats_are_refused(rule, reason):
    with pytest.raises(ValidationError) as refused:
        ChartDefinition.model_validate(_table_chart(conditional_formats=[rule]))
    assert reason in str(refused.value)


def test_conditional_formats_are_only_for_tables():
    bar = {**_table_chart(), "visual": "bar", "encoding": {
        "kind": "cartesian", "dimensions": [{"field_id": "region"}],
        "measures": [{"field_id": "revenue", "aggregation": "sum"}]},
        "style": {"conditional_formats": [{"field_id": "revenue", "kind": "scale"}]}}
    with pytest.raises(ValidationError) as refused:
        ChartDefinition.model_validate(bar)
    assert "tables and pivots" in str(refused.value)


def test_a_chart_without_formats_serializes_as_before():
    chart = ChartDefinition.model_validate(_table_chart())
    assert "conditional_formats" not in chart.model_dump(mode="json")["style"]


def _linked(**action):
    source, chart, page = uuid4(), uuid4(), uuid4()
    binding = {"dataset_id": str(uuid4()), "dataset_revision": 1, "field_id": "region"}
    return {
        "dashboard_id": str(uuid4()), "title": "Sales",
        "pages": [{"page_id": str(page), "title": "Overview", "widgets": [{
            "kind": "chart", "widget_id": str(source),
            "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
            "chart_ref": {"chart_id": str(chart), "revision": 1}}]}],
        "actions": [{"action_id": str(uuid4()), "kind": "navigate",
                     "source_widget_id": str(source), "run_on": "menu",
                     "mappings": [{"source": binding, "target": {**binding,
                                                                  "dataset_id": str(uuid4())}}],
                     **action}],
    }


def test_a_chart_can_link_to_another_dashboard():
    target = uuid4()
    linked = DashboardDefinition.model_validate(_linked(
        target_dashboard_id=str(target), target_workspace_id="abc", label="Regional detail"))
    assert linked.actions[0].target_dashboard_id == target
    plain = DashboardDefinition.model_validate(dashboard())
    assert "target_dashboard_id" not in str(plain.model_dump(mode="json"))


@pytest.mark.parametrize(("extra", "reason"), [
    ({"target_dashboard_id": str(uuid4())}, "workspace and at least one field mapping"),
    ({"target_dashboard_id": str(uuid4()), "target_workspace_id": "abc", "kind": "drill"},
     "navigate action"),
    ({"target_workspace_id": "abc"}, "needs a target dashboard"),
])
def test_incomplete_dashboard_links_are_refused(extra, reason):
    with pytest.raises(ValidationError) as refused:
        DashboardDefinition.model_validate(_linked(**extra))
    assert reason in str(refused.value)


def test_removing_a_linked_chart_takes_its_link_with_it():
    target = uuid4()
    linked = DashboardDefinition.model_validate(_linked(
        target_dashboard_id=str(target), target_workspace_id="abc", label="Detail"))
    page = linked.pages[0]
    widget = page.widgets[0]
    operation = Operation.model_validate({
        "operation_id": str(uuid4()), "expected_revision": 1,
        "changes": [{"op": "widget.remove", "page_id": str(page.page_id),
                     "widget_id": str(widget.widget_id)}]})
    after = apply_operation(linked, operation)
    assert after.actions == () and not after.pages[0].widgets


def test_explicit_actions_naming_a_missing_widget_are_still_refused():
    linked = _linked(target_dashboard_id=str(uuid4()), target_workspace_id="abc")
    linked["actions"][0]["source_widget_id"] = str(uuid4())
    with pytest.raises(ValidationError) as refused:
        DashboardDefinition.model_validate(linked)
    assert "Interaction source must be a chart widget" in str(refused.value)
