"""The Studio component registry: one contract shared by the browser, server and model."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules.assistant import ui_components
from app.modules.assistant.service import PendingArtifact, _compose_output_items
from app.modules.assistant.ui_components import ComponentError

SCHEMAS = (Path(__file__).resolve().parents[3]
           / "frontend/src/features/studio/runtime/components/schemas.ts")
KPI = {"label": "Revenue", "value": 120.5, "format": "number", "aggregation": "value",
       "evidence_id": "evidence_1", "column": "revenue"}


def test_the_contract_lists_every_registry_component_and_tool_of_the_zod_source():
    # The full drift check is ``pnpm components:check`` in CI; this keeps the
    # backend from shipping a contract that lost a component the browser draws.
    source = SCHEMAS.read_text()
    block = source.split("export const COMPONENTS = {", 1)[1].split("} as const", 1)[0]
    names = set(re.findall(r"^  (\w+): \{", block, re.MULTILINE))
    contract = ui_components.contract()
    assert names == set(contract["components"]) == {"DatavizChart", "KpiTiles", "GeoMap"}
    assert set(contract["tools"]) == {"data_to_chart"}
    assert contract["version"] in source


@pytest.mark.parametrize(("name", "props"), [
    ("DatavizChart", {"title": "Revenue", "chart": {"visual": "bar"}, "draft_ref": "dr1.a.b.c"}),
    ("KpiTiles", {"items": [KPI]}),
    ("KpiTiles", {"title": "Today", "items": [{**KPI, "value": None, "format": "currency",
                                                "currency": "IDR", "decimals": 0}]}),
    ("GeoMap", {"title": "Outlets", "evidence_id": "e1", "longitude": "lng", "latitude": "lat",
                "rows": [{"lng": 106.8, "lat": -6.2}]}),
])
def test_valid_components_pass_unchanged(name, props):
    assert ui_components.component(name, props) == {
        "component": name, "version": 1, "props": props}


@pytest.mark.parametrize(("name", "props", "where"), [
    ("DatavizChart", {"title": "Revenue", "chart": {}}, "draft_ref"),
    ("DatavizChart", {"title": "R", "chart": {}, "draft_ref": "dr1.a.b.c", "rows": []}, "rows"),
    ("KpiTiles", {"items": []}, "items"),
    ("KpiTiles", {"items": [{**KPI, "value": "120"}]}, "items.0.value"),
    ("KpiTiles", {"items": [{**KPI, "aggregation": "median"}]}, "items.0.aggregation"),
    ("KpiTiles", {"items": [{**KPI, "currency": "rupiah"}]}, "items.0.currency"),
    ("KpiTiles", {"items": [KPI] * 7}, "items"),
    ("GeoMap", {"title": "O", "evidence_id": "e 1", "rows": []}, "evidence_id"),
    ("GeoMap", {"title": "O", "evidence_id": "e1", "rows": [{}] * 201}, "rows"),
])
def test_invalid_components_are_refused_with_the_field_named(name, props, where):
    with pytest.raises(ComponentError) as refused:
        ui_components.component(name, props)
    assert where in str(refused.value)


def test_unknown_components_are_refused():
    with pytest.raises(ComponentError):
        ui_components.component("Chart", {})


def test_the_model_receives_the_registry_schema_as_its_tool_definition():
    from app.modules.agents.tools.data_to_chart import DataToChartTool

    tool = DataToChartTool()
    assert tool.parameters == ui_components.tool_parameters("data_to_chart")
    assert tool.parameters["additionalProperties"] is False
    assert "kpi" in tool.parameters["properties"]["visual"]["enum"]
    ui_components.validate_tool_arguments(
        "data_to_chart", {"visual": "line", "x": "month", "y": ["revenue"],
                          "date_grain": "month"})
    for bad in ({"visual": "radar"}, {"y": []}, {"top_n": 0}, {"sql": "SELECT 1"}):
        with pytest.raises(ComponentError):
            ui_components.validate_tool_arguments("data_to_chart", bad)


def _component(name: str) -> PendingArtifact:
    return PendingArtifact("component", {"name": name, "props": {}})


def test_a_data_answer_reads_headline_kpis_charts_narrative_then_sources():
    items = _compose_output_items(
        "Revenue rose 12% in October.\n\nJakarta led, Medan fell behind.",
        [PendingArtifact("table", {"title": "Rows"}), _component("DatavizChart"),
         _component("KpiTiles"), _component("DatavizChart")],
    )
    kinds = [item.get("payload", {}).get("name") or item["kind"] for item in items]
    assert kinds == ["text", "KpiTiles", "DatavizChart", "DatavizChart", "text", "table"]
    assert items[0]["text"] == "Revenue rose 12% in October."
    assert items[4]["text"] == "Jakarta led, Medan fell behind."


def test_an_answer_with_only_tables_keeps_its_text_first():
    items = _compose_output_items("One paragraph.\n\nAnother.",
                                  [PendingArtifact("table", {"title": "Rows"})])
    assert [item["kind"] for item in items] == ["text", "table"]
    assert items[0]["text"] == "One paragraph.\n\nAnother."


TOTALS = {"columns": ["revenue", "order_count", "average_order_value"],
          "rows": [[175184000.0, 15, 11678933.333333]]}
BY_CITY = {"columns": ["city", "revenue"], "rows": [["Jakarta", 1.0], ["Medan", 2.0]]}


def _evidence(tables):
    return type("Evidence", (), {"business_tables": tables})()


def test_a_table_of_one_row_of_measures_becomes_key_figure_tiles():
    from app.modules.assistant.service import _key_figures
    from app.modules.assistant.ui_autocompose import key_figures

    program = ('root = Col([Table(@Evidence("evidence_1"), ["revenue", "order_count", '
               '"average_order_value"], "Key figures")])')
    tiles = key_figures(program, {"evidence_1": TOTALS})
    assert tiles is not None and tiles["name"] == "KpiTiles"
    items = tiles["props"]["items"]
    assert [item["column"] for item in items] == TOTALS["columns"]
    assert [item["value"] for item in items] == [175184000.0, 15.0, 11678933.333333]
    assert all(item["evidence_id"] == "evidence_1" for item in items)
    # Several rows, or a program that does more than tabulate, stay as written.
    assert key_figures(program, {"evidence_1": BY_CITY}) is None
    assert key_figures('root = Col([Text("x"), Table(@Evidence("evidence_1"))])',
                       {"evidence_1": TOTALS}) is None

    ui = PendingArtifact("ui", {"program": program, "fallback": "x"})
    [replaced] = _key_figures([ui], _evidence({"evidence_1": TOTALS}))
    assert replaced.kind == "component" and replaced.payload["name"] == "KpiTiles"


def test_charts_without_tiles_gain_the_key_figures_of_one_row_results():
    from app.modules.assistant.service import _key_figures

    chart = PendingArtifact("component", {"name": "DatavizChart", "props": {}}, "call-1")
    tables = {"evidence_1": BY_CITY, "evidence_2": TOTALS}
    composed = _key_figures([chart], _evidence(tables))
    assert [item.payload["name"] for item in composed] == ["DatavizChart", "KpiTiles"]
    # Tiles already drawn, or no one-row result, add nothing.
    tiles = PendingArtifact("component", {"name": "KpiTiles", "props": {}})
    assert _key_figures([chart, tiles], _evidence(tables)) == [chart, tiles]
    assert _key_figures([chart], _evidence({"evidence_1": BY_CITY})) == [chart]


def test_a_program_that_repeats_native_charts_is_dropped():
    from app.modules.assistant.service import _key_figures
    from app.modules.assistant.ui_autocompose import charts_only

    program = ('root = Col([a, b])\n'
               'a = Chart(@Sort(@Evidence("evidence_1"), "revenue", "desc"), "city", "revenue", '
               '"bar", "Revenue by city")\n'
               'b = Chart(@Evidence("evidence_3"), "month", "orders", "line", "Orders")')
    assert charts_only(program)
    assert not charts_only('root = Col([Chart(@Evidence("e"), "a", "b", "bar", "t"), '
                           'Slider("x", 0, 10)])')
    charts = [PendingArtifact("component", {"name": "DatavizChart", "props": {},
                                            "source": {"evidence_id": key}}, f"call-{key}")
              for key in ("evidence_1", "evidence_3")]
    ui = PendingArtifact("ui", {"program": program,
                                "provenance": {"evidence_ids": ["evidence_1", "evidence_3"]}})
    tables = {"evidence_1": BY_CITY, "evidence_3": BY_CITY}
    assert _key_figures([*charts, ui], _evidence(tables)) == charts
    # A program charting a result no native chart shows is kept.
    assert _key_figures([charts[0], ui], _evidence(tables)) == [charts[0], ui]


def test_result_columns_read_as_field_labels():
    from app.modules.assistant.ui_components import field_label

    assert field_label("sum_revenue") == "Total revenue"
    assert field_label("avg_order_value") == "Average order value"
    assert field_label("order_count") == "Order count"
    assert field_label("customer_id") == "Customer ID"
    assert field_label("orders.total_amount") == "Total amount"
    assert field_label("revenue") == "Revenue"
    assert field_label("Revenue (IDR)") == "Revenue (IDR)"
