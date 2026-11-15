"""Typed presentation cannot smuggle renderer code or bind a different measure."""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.viz.bindings import validate_chart_binding, validate_widget_bindings
from app.modules.viz.contracts.charts import AxisStyle, ReferenceBand, TableCalculation
from app.modules.viz.contracts.common import NumberFormat
from app.modules.viz.contracts.dashboards import DashboardDefinition
from tests.unit.test_native_viz_bindings import chart, dataset


def decimal(value):
    return {"type": "decimal", "value": value}


def calculation(**changes):
    return {
        "calculation_id": uuid4(), "name": "Running revenue", "kind": "running_total",
        "measure": {"field_id": "amount", "aggregation": "sum"},
        "addressing": ["region"], "order": [{"field_id": "region"}], **changes,
    }


@pytest.mark.parametrize("payload", [
    {"minimum": decimal("2"), "maximum": decimal("1")},
    {"minimum": decimal("1"), "maximum": decimal("1")},
    {"scale": "log"},
    {"scale": "log", "include_zero": False, "minimum": decimal("0")},
    {"scale": "log", "include_zero": False, "maximum": decimal("-1")},
    {"minimum": decimal("NaN")}, {"scale": "custom", "callback": "code"},
])
def test_axis_refuses_invalid_domain_or_executable_scale(payload):
    with pytest.raises(ValidationError):
        AxisStyle.model_validate(payload)


def test_axis_and_band_preserve_decimal_precision_beyond_javascript_numbers():
    low, high = "9007199254740992.00000001", "9007199254740992.00000002"
    axis = AxisStyle.model_validate({"minimum": decimal(low), "maximum": decimal(high)})
    assert axis.minimum.value == low
    assert axis.maximum.value == high
    ReferenceBand.model_validate({
        "annotation_id": uuid4(), "measure": {"field_id": "amount", "aggregation": "sum"},
        "minimum": decimal(low), "maximum": decimal(high),
    })
    with pytest.raises(ValidationError):
        ReferenceBand.model_validate({
            "annotation_id": uuid4(), "measure": {"field_id": "amount", "aggregation": "sum"},
            "minimum": decimal(high), "maximum": decimal(low),
        })


@pytest.mark.parametrize("changes", [
    {"addressing": []}, {"addressing": ["region", "region"]},
    {"partition_by": ["region"]}, {"partition_by": ["date", "date"]},
    {"order": []}, {"order": [{"field_id": "date"}]},
    {"order": [{"field_id": "region"}, {"field_id": "region"}]},
    {"order": [{"field_id": "region", "aggregation": "count"}]},
    {"window_size": 3}, {"kind": "moving_average"},
    {"kind": "moving_average", "window_size": 0},
])
def test_window_semantics_are_explicit_and_unambiguous(changes):
    with pytest.raises(ValidationError):
        TableCalculation.model_validate(calculation(**changes))


def test_calculations_annotations_and_top_n_bind_encoded_measures_and_dimensions():
    source = dataset()
    annotation = {"kind": "line", "annotation_id": uuid4(),
                  "measure": {"field_id": "amount", "aggregation": "sum"},
                  "value": decimal("100.01"), "tone": "warning"}
    top = {"dimension": {"field_id": "region"},
           "measure": {"field_id": "amount", "aggregation": "sum"}}
    document = chart(source, annotations=[annotation], top_n=top,
                     table_calculations=[calculation()], tooltip={"fields": ["region"]})
    validate_chart_binding(document, source)
    for name in ("annotations", "top_n", "table_calculations"):
        raw = document.model_dump(mode="json")
        setting = raw[name][0] if isinstance(raw[name], list) else raw[name]
        setting["measure"]["aggregation"] = "avg"
        with pytest.raises(ValueError, match="encoded measure"):
            validate_chart_binding(type(document).model_validate(raw), source)
    with pytest.raises(ValueError, match="encoded dimension"):
        validate_chart_binding(chart(source, top_n={**top, "dimension": {"field_id": "date"}}),
                               source)
    with pytest.raises(ValueError, match="cover chart dimensions"):
        validate_chart_binding(chart(source, table_calculations=[calculation(
            addressing=["date"], order=[{"field_id": "date"}],
        )]), source)
    with pytest.raises(ValueError, match="absent"):
        validate_chart_binding(chart(source, tooltip={"fields": ["missing"]}), source)
    with pytest.raises(ValidationError, match="Annotation IDs"):
        chart(source, annotations=[annotation, annotation])
    with pytest.raises(ValidationError, match="calculation IDs and names"):
        chart(source, table_calculations=[calculation(), calculation()])


def test_widget_instances_keep_presentation_and_filters_separate_from_shared_chart():
    source = dataset()
    figure = chart(source)
    widget = {
        "widget_id": uuid4(), "kind": "chart", "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
        "chart_ref": {"chart_id": figure.chart_id, "revision": 7},
        "style": {"show_legend": False}, "filters": {"expression": {
            "type": "predicate", "field": {"dataset_id": source.dataset_id,
                "dataset_revision": 3, "field_id": "region"},
            "op": "eq", "values": [{"type": "string", "value": "Jakarta"}],
        }},
    }
    second = {**deepcopy(widget), "widget_id": uuid4(),
              "layout": {"x": 6, "y": 0, "w": 6, "h": 4}, "style": {"show_legend": True}}
    board = DashboardDefinition.model_validate({
        "dashboard_id": uuid4(), "title": "Regions",
        "pages": [{"page_id": uuid4(), "title": "Page 1", "widgets": [widget, second]}],
    })
    charts = {(figure.chart_id, 7): figure}
    validate_widget_bindings(board, charts)
    left, right = board.pages[0].widgets
    assert left.chart_ref == right.chart_ref
    assert left.style.show_legend is False and right.style.show_legend is True
    assert figure.style.show_legend is True
    raw = board.model_dump(mode="json")
    raw["pages"][0]["widgets"][0]["filters"]["expression"]["field"]["dataset_revision"] = 4
    with pytest.raises(ValueError, match="pinned chart dataset"):
        validate_widget_bindings(DashboardDefinition.model_validate(raw), charts)


def test_currency_format_requires_an_explicit_currency():
    with pytest.raises(ValidationError, match="explicit currency"):
        NumberFormat(kind="currency")
    assert NumberFormat(kind="currency", currency="IDR").currency == "IDR"
