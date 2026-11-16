"""Document edits preserve identity and refuse dangling or ambiguous filter state."""

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.modules.viz.contracts.commands import Operation
from app.modules.viz.contracts.dashboards import DashboardDefinition
from app.modules.viz.contracts.filters import FilterDefinition, FilterScope, RelativeWindow
from app.modules.viz.operations import apply_operation
from tests.unit.test_native_viz_contracts import (
    DATASET,
    PAGE,
    WIDGET_A,
    WIDGET_B,
    dashboard,
)


def apply(document, changes):
    return apply_operation(document, Operation.model_validate({
        "operation_id": str(uuid4()), "expected_revision": 1, "changes": changes,
    }))


def filter_definition(**changes):
    return {
        "filter_id": str(uuid4()), "title": "Region", "kind": "single_value",
        "field": {"dataset_id": DATASET, "dataset_revision": 1, "field_id": "region"},
        "scope": {"kind": "global"}, **changes,
    }


def test_dataset_registry_edits_preserve_canvas_and_other_bindings():
    document = DashboardDefinition.model_validate(dashboard())
    identity = uuid4()
    pinned = {"dataset_id": str(identity), "revision": 2}
    added = apply(document, [{"op": "datasets.replace", "datasets": [pinned]}])
    assert added.datasets[0].dataset_id == identity and added.datasets[0].revision == 2
    assert not document.datasets
    assert added.pages == document.pages and added.filters == document.filters
    changed = apply(added, [{"op": "datasets.replace", "datasets": [{**pinned, "revision": 3}]}])
    assert changed.datasets[0].revision == 3 and added.datasets[0].revision == 2
    removed = apply(changed, [{"op": "datasets.replace", "datasets": []}])
    assert removed == document
    with pytest.raises(ValueError, match="unique"):
        apply(added, [{"op": "datasets.replace", "datasets": [pinned, {**pinned, "revision": 3}]}])
    assert added.datasets[0].revision == 2


def test_page_and_widget_lifecycle_keeps_other_pages_and_source_unchanged():
    original = DashboardDefinition.model_validate(dashboard())
    new_page = str(uuid4())
    added = {"widget_id": str(uuid4()), "kind": "text", "text": "Quarterly results",
             "layout": {"x": 0, "y": 0, "w": 12, "h": 2}}
    document = apply(original, [
        {"op": "page.add", "page": {"page_id": new_page, "title": "Notes"}},
        {"op": "widget.add", "page_id": new_page, "widget": added},
        {"op": "widget.replace", "page_id": new_page, "widget": {**added, "text": "Reviewed"}},
        {"op": "page.rename", "page_id": new_page, "title": "Review"},
        {"op": "page.reorder", "page_ids": [new_page, PAGE]},
        {"op": "dashboard.rename", "title": "Quarterly sales"},
    ])
    assert document.pages[0].title == "Review"
    assert document.pages[0].widgets[0].text == "Reviewed"
    assert document.pages[1] == original.pages[0]
    document = apply(document, [
        {"op": "widget.remove", "page_id": new_page, "widget_id": added["widget_id"]},
        {"op": "page.remove", "page_id": new_page},
    ])
    assert document.pages == original.pages
    assert original.title == "Sales"


@pytest.mark.parametrize("change", [
    {"op": "page.remove", "page_id": str(uuid4())},
    {"op": "page.remove", "page_id": PAGE},
    {"op": "page.add", "page": {"page_id": PAGE, "title": "Duplicate"}},
    {"op": "page.reorder", "page_ids": [PAGE, PAGE]},
    {"op": "page.reorder", "page_ids": [str(uuid4())]},
    {"op": "widget.remove", "page_id": PAGE, "widget_id": str(uuid4())},
])
def test_invalid_edits_do_not_modify_the_original_document(change):
    document = DashboardDefinition.model_validate(dashboard())
    before = document.model_dump_json()
    with pytest.raises(ValueError):
        apply(document, [{"op": "dashboard.rename", "title": "Never committed"}, change])
    assert document.model_dump_json() == before


def test_locked_widget_reflow_and_duplicate_add_are_rejected():
    payload = dashboard()
    payload["pages"][0]["widgets"][0]["locked"] = True
    document = DashboardDefinition.model_validate(payload)
    with pytest.raises(ValueError, match="Unlock"):
        apply(document, [{"op": "layout.apply", "page_id": PAGE, "placements": [
            {"widget_id": WIDGET_A, "layout": {"x": 6, "y": 0, "w": 6, "h": 4}},
            {"widget_id": WIDGET_B, "layout": {"x": 0, "y": 0, "w": 6, "h": 4}},
        ]}])
    with pytest.raises(ValueError, match="already exists"):
        apply(document, [{"op": "widget.add", "page_id": PAGE,
                          "widget": payload["pages"][0]["widgets"][0]}])
    missing = deepcopy(payload["pages"][0]["widgets"][0])
    missing["widget_id"] = str(uuid4())
    with pytest.raises(ValueError, match="Unknown widget"):
        apply(document, [{"op": "widget.replace", "page_id": PAGE, "widget": missing}])


def test_removing_a_widget_removes_its_own_interactions_in_the_same_revision():
    payload = dashboard()
    field = filter_definition()["field"]
    action = {"action_id": str(uuid4()), "kind": "filter", "source_widget_id": WIDGET_A,
              "target_widget_ids": [WIDGET_B], "mappings": [{"source": field, "target": field}]}
    payload["actions"] = [action]
    document = DashboardDefinition.model_validate(payload)
    remove = {"op": "widget.remove", "page_id": PAGE, "widget_id": WIDGET_A}
    # An interaction never outlives its source widget, alone or in an explicit batch.
    for changes in ([remove], [remove, {"op": "actions.replace", "actions": []}]):
        updated = apply(document, changes)
        assert [w.widget_id for w in updated.pages[0].widgets] == [UUID(WIDGET_B)]
        assert updated.actions == ()
    assert len(document.actions) == 1  # the original document is unchanged


def test_cascading_filters_require_existing_acyclic_parents():
    payload = dashboard()
    parent, child = filter_definition(), filter_definition()
    child["depends_on"] = [parent["filter_id"]]
    document = apply(DashboardDefinition.model_validate(payload), [
        {"op": "filters.replace", "filters": [parent, child]},
    ])
    assert len(document.filters) == 2
    for filters in ([child], [parent, {**child, "depends_on": [child["filter_id"]]}],
                    [{**parent, "depends_on": [child["filter_id"]]}, child]):
        with pytest.raises(ValueError):
            apply(document, [{"op": "filters.replace", "filters": filters}])


@pytest.mark.parametrize("scope", [
    {"kind": "page"}, {"kind": "global", "page_id": PAGE},
    {"kind": "global", "widget_ids": [WIDGET_A]}, {"kind": "widget_subset"},
    {"kind": "widget_subset", "widget_ids": [WIDGET_A, WIDGET_A]},
    {"kind": "chart_local", "widget_ids": [WIDGET_A, WIDGET_B]},
])
def test_ambiguous_filter_scope_is_rejected(scope):
    with pytest.raises(ValidationError):
        FilterScope.model_validate(scope)


@pytest.mark.parametrize("kind,defaults", [
    ("single_value", [{"type": "string", "value": "A"}] * 2),
    ("date_picker", [{"type": "string", "value": "2024-01-01"}]),
    ("date_range", [{"type": "date", "value": "2024-01-01"}]),
    ("number_range_slider", [{"type": "string", "value": "1"}] * 2),
    ("boolean_switch", [{"type": "integer", "value": "1"}]),
    ("text_entry", [{"type": "boolean", "value": True}]),
    ("multi_value", [{"type": "integer", "value": "1"}, {"type": "string", "value": "1"}]),
])
def test_filter_defaults_preserve_typed_control_semantics(kind, defaults):
    with pytest.raises(ValidationError):
        FilterDefinition.model_validate(filter_definition(kind=kind, defaults=defaults))


def test_parameters_have_a_distinct_binding_without_fabricating_a_field():
    parameter = {"dataset_id": DATASET, "dataset_revision": 1, "parameter_id": "customer"}
    payload = filter_definition(kind="parameter_control", parameter=parameter, field=None)
    result = FilterDefinition.model_validate(payload)
    assert result.parameter.parameter_id == "customer" and result.field is None
    with pytest.raises(ValidationError):
        FilterDefinition.model_validate({**payload, "field": filter_definition()["field"]})


def test_timezone_and_relative_filter_shape_are_validated():
    with pytest.raises(ValidationError):
        RelativeWindow(unit="day", timezone="Invalid/Timezone")
    payload = filter_definition(kind="relative_date", relative={"unit": "month"})
    assert FilterDefinition.model_validate(payload).relative.timezone == "UTC"
    with pytest.raises(ValidationError):
        FilterDefinition.model_validate({
            **payload, "defaults": [{"type": "date", "value": "2024-01-01"}],
        })
