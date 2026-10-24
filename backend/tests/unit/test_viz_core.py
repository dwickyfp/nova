"""The viz core: worksheet specs, SQL compilation, ShowMe, presentation model.

Split into document, query and presentation layers, these are the pure
contracts everything else (tools, routes, the editor) is built on.
"""

from __future__ import annotations

import pytest

from app.modules.viz.compiler import (
    CompileError,
    compile_worksheet,
    source_probe_sql,
    to_vega_lite,
)
from app.modules.viz.presmodel import build as build_presmodel
from app.modules.viz.showme import (
    SHOWME_BY_ID,
    classify_fields,
    default_chart_id,
    from_intent,
    rank,
    suggest,
)
from app.modules.viz.spec import (
    CategoricalFilter,
    ChartIntent,
    DashboardAction,
    DashboardSpec,
    FieldRef,
    MarksCard,
    QuantitativeFilter,
    RelativeDateFilter,
    TableCalc,
    VizSourceSql,
    WorksheetSpec,
    Zone,
    ZoneRect,
)

COLUMNS = ["region", "month", "revenue", "units"]
ROWS = [
    ["West", "2026-01-01", "100.50", 3],
    ["East", "2026-01-01", "200.25", 5],
    ["West", "2026-02-01", "110.00", 4],
    ["East", "2026-02-01", "190.75", 6],
]
SOURCE = VizSourceSql(sql="SELECT * FROM sales")


def worksheet(**overrides) -> WorksheetSpec:
    base = dict(
        title="Revenue",
        source=SOURCE,
        columns=[FieldRef(field="region")],
        rows=[
            FieldRef(field="revenue", field_type="quantitative", aggregation="sum", discrete=False)
        ],
        marks=MarksCard(type="bar"),
        field_types={"region": "nominal", "revenue": "quantitative"},
    )
    base.update(overrides)
    return WorksheetSpec(**base)


# ── Specs ──────────────────────────────────────────────────────


def test_a_worksheet_needs_a_field_on_a_shelf() -> None:
    with pytest.raises(ValueError, match="at least one field"):
        WorksheetSpec(source=SOURCE, columns=[], rows=[])


def test_dashboard_zones_reject_overlap_and_unknown_action_targets() -> None:
    with pytest.raises(ValueError, match="cannot overlap"):
        DashboardSpec(
            zones=[
                Zone(
                    zone_id="a",
                    kind="sheet",
                    artifact_id="art-1",
                    rect=ZoneRect(x=0, y=0, w=12, h=2),
                ),
                Zone(
                    zone_id="b",
                    kind="sheet",
                    artifact_id="art-2",
                    rect=ZoneRect(x=0, y=0, w=12, h=2),
                ),
            ]
        )
    with pytest.raises(ValueError, match="unknown source zone"):
        DashboardSpec(
            zones=[],
            actions=[DashboardAction(action_id="f", kind="filter", source_zone="ghost")],
        )


def test_a_sheet_zone_requires_its_artifact() -> None:
    with pytest.raises(ValueError, match="chart artifact"):
        Zone(zone_id="a", kind="sheet", rect=ZoneRect(x=0, y=0, w=12, h=2))
    with pytest.raises(ValueError, match="Only sheet zones"):
        Zone(
            zone_id="a",
            kind="spacer",
            artifact_id="art-1",
            rect=ZoneRect(x=0, y=0, w=12, h=2),
        )


def test_zone_rect_must_fit_the_grid() -> None:
    with pytest.raises(ValueError, match="24-column"):
        ZoneRect(x=20, y=0, w=8, h=1)


def test_extra_filters_replace_the_same_field() -> None:
    spec = worksheet(filters=[QuantitativeFilter(field="revenue", min=0)])
    merged = spec.with_extra_filters([QuantitativeFilter(field="revenue", min=100)])
    assert len(merged.filters) == 1
    assert merged.filters[0].min == 100
    merged = spec.with_extra_filters([CategoricalFilter(field="region", values=["West"])])
    assert len(merged.filters) == 2


# ── Compiler ───────────────────────────────────────────────────


def test_compile_grouped_aggregation_with_sort_and_limit() -> None:
    spec = worksheet(
        sort=[{"field": "sum(revenue)", "direction": "desc"}],
        limit=10,
    )
    compiled = compile_worksheet(spec, known_columns=COLUMNS)
    assert compiled.sql == (
        "SELECT `region`, SUM(`revenue`) AS `sum(revenue)` FROM (SELECT * FROM sales) "
        "AS nova_source GROUP BY `region` ORDER BY `sum(revenue)` DESC LIMIT 10"
    )
    assert [pill.label for pill in compiled.output_fields] == ["region", "sum(revenue)"]


def test_compile_rejects_fields_that_are_not_source_columns() -> None:
    with pytest.raises(CompileError, match="not a column"):
        compile_worksheet(worksheet(columns=[FieldRef(field="nope")]), known_columns=COLUMNS)
    with pytest.raises(CompileError, match="not a column or measure"):
        compile_worksheet(
            worksheet(sort=[{"field": "ghost", "direction": "asc"}]), known_columns=COLUMNS
        )


def test_compile_filters_cover_quantitative_categorical_and_relative_dates() -> None:
    spec = worksheet(
        filters=[
            QuantitativeFilter(field="revenue", min=10, max=900),
            CategoricalFilter(field="region", values=["West"]),
            RelativeDateFilter(field="month", range="last_n_days", n=7),
        ]
    )
    sql = compile_worksheet(spec, known_columns=COLUMNS).sql
    assert "`revenue` >= 10.0 AND `revenue` <= 900.0" in sql
    assert "(`region` IN ('West'))" in sql
    assert "`month` >= DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)" in sql


def test_compile_escapes_filter_literals_and_quotes_identifiers() -> None:
    spec = worksheet(filters=[CategoricalFilter(field="region", values=["O'Brien; DROP"])])
    sql = compile_worksheet(spec, known_columns=COLUMNS).sql
    assert "'O''Brien; DROP'" in sql
    exotic = worksheet(
        columns=[FieldRef(field="weird col`name")],
    )
    sql = compile_worksheet(exotic).sql
    assert "`weird col``name`" in sql


def test_compile_rejects_a_multi_statement_source() -> None:
    spec = worksheet(source=VizSourceSql(sql="SELECT 1; DROP TABLE x"))
    with pytest.raises(CompileError, match="exactly one"):
        compile_worksheet(spec)


def test_compile_table_calcs_become_window_functions() -> None:
    spec = worksheet(table_calcs=[TableCalc(kind="percent_of_total", field="revenue")])
    sql = compile_worksheet(spec, known_columns=COLUMNS).sql
    # The aggregate is hoisted, then the quick calc runs over the whole table.
    assert "SUM(`revenue`) AS `__nova_a0`" in sql
    assert "SUM(`__nova_a0`) OVER ()" in sql
    assert "AS `percent_of_total(sum(revenue))`" in sql


def test_compile_table_source_and_probe() -> None:
    from app.modules.viz.spec import VizSourceTable

    spec = WorksheetSpec(
        source=VizSourceTable(database="sales", table="orders"),
        columns=[FieldRef(field="region")],
        rows=[
            FieldRef(
                field="revenue", field_type="quantitative", aggregation="count", discrete=False
            )
        ],
    )
    compiled = compile_worksheet(spec, known_columns=["region", "revenue"])
    assert "FROM `sales`.`orders` AS nova_source" in compiled.sql
    assert "COUNT(`revenue`)" in compiled.sql
    assert source_probe_sql(spec).startswith("SELECT * FROM SELECT")


def test_vega_mirror_keeps_the_worksheet_reading() -> None:
    spec = worksheet()
    mirror = to_vega_lite(spec, [{"region": "West", "sum(revenue)": 210.5}])
    assert mirror["mark"] == "bar"
    assert mirror["encoding"]["x"]["field"] == "region"
    assert mirror["encoding"]["y"]["field"] == "sum(revenue)"
    pie = worksheet(
        columns=[FieldRef(field="region")],
        rows=[
            FieldRef(field="revenue", field_type="quantitative", aggregation="sum", discrete=False)
        ],
        marks=MarksCard(
            type="pie",
            angle=FieldRef(
                field="revenue", field_type="quantitative", aggregation="sum", discrete=False
            ),
            color=FieldRef(field="region"),
        ),
    )
    mirror = to_vega_lite(pie, [{"region": "West", "sum(revenue)": 210.5}])
    assert mirror["mark"] == "arc"
    assert mirror["encoding"]["theta"]["field"] == "sum(revenue)"


# ── ShowMe ─────────────────────────────────────────────────────


def test_classify_fields_marks_numbers_as_measures_with_aggregation() -> None:
    pills = classify_fields(COLUMNS, ROWS)
    by_name = {pill.field: pill for pill in pills}
    assert by_name["revenue"].is_measure
    assert by_name["revenue"].aggregation == "sum"
    assert by_name["region"].field_type == "nominal"
    assert by_name["month"].field_type == "temporal"
    assert not by_name["region"].is_measure


def test_rank_orders_satisfied_types_first() -> None:
    pills = classify_fields(COLUMNS, ROWS)
    ranked = rank(pills)
    enabled = [item.id for item, unmet in ranked if not unmet]
    assert "bar" in enabled
    assert "line" in enabled
    disabled = {item.id: unmet for item, unmet in ranked if unmet}
    assert disabled[
        "heatmap"
    ]  # needs two dimensions and only region/month qualify — month is a date
    suggestions = suggest(pills)
    assert suggestions[0]["enabled"] is True
    # Exactly one tile is the best fit, and it is the chart Show Me would pick.
    assert [item["id"] for item in suggestions if item["recommended"]] == [default_chart_id(pills)]


def test_default_chart_id_matches_the_data_shape() -> None:
    pills = classify_fields(COLUMNS, ROWS)
    assert default_chart_id(pills) == "line"
    assert default_chart_id(classify_fields(["region", "revenue"], [["W", 1]])) == "bar"
    assert default_chart_id(classify_fields(["region"], [["W"]])) == "text_table"


def test_from_intent_named_fields_override_showme_placement() -> None:
    spec = from_intent(
        ChartIntent(chart_type="line", x="month", y="revenue"),
        COLUMNS,
        ROWS,
        source=SOURCE,
    )
    # A request that names month/revenue does not also color by region.
    assert spec.marks.color is None
    assert [pill.field for pill in spec.columns] == ["month"]
    assert [pill.label for pill in spec.rows] == ["sum(revenue)"]


def test_from_intent_auto_resolves_and_aggregation_applies() -> None:
    spec = from_intent(ChartIntent(aggregation="avg"), COLUMNS, ROWS, source=SOURCE)
    assert spec.marks.type == "line"
    assert spec.rows[0].aggregation == "avg"
    pie = from_intent(ChartIntent(chart_type="pie"), COLUMNS, ROWS, source=SOURCE)
    assert pie.marks.type == "pie"
    assert pie.marks.angle is not None and pie.marks.angle.is_measure
    assert pie.marks.color is not None and pie.marks.color.field == "region"


def test_from_intent_rejects_unknown_named_fields_by_ignoring_them() -> None:
    spec = from_intent(ChartIntent(chart_type="bar", x="ghost"), COLUMNS, ROWS, source=SOURCE)
    # "ghost" is not a column; ShowMe places from the real pills instead.
    assert "ghost" not in {pill.field for pill in spec.all_fields()}


def test_showme_catalog_types_are_placeable() -> None:
    assert SHOWME_BY_ID["bar"].mark == "bar"
    for chart_id in (
        "text_table",
        "bar",
        "bar_stacked",
        "bar_side",
        "line",
        "area",
        "scatter",
        "pie",
        "bubble",
        "heatmap",
    ):
        spec = from_intent(ChartIntent(chart_type=chart_id), COLUMNS, ROWS, source=SOURCE)  # type: ignore[arg-type]
        assert spec.columns or spec.rows


# ── Presentation model ─────────────────────────────────────────


def test_presmodel_aggregates_raw_rows_and_orders_bars_desc() -> None:
    model = build_presmodel(worksheet(), COLUMNS, ROWS)
    assert model["rows"] == [["East", 391.0], ["West", 210.5]]
    assert model["totals"]["sum(revenue)"] == pytest.approx(601.5)
    assert model["axes"]["x"]["field"] == "region"
    assert model["axes"]["y"]["field"] == "sum(revenue)"
    assert model["legend"] == {"color": None}


def test_presmodel_passes_through_already_aggregated_input() -> None:
    model = build_presmodel(
        worksheet(),
        ["region", "sum(revenue)"],
        [["East", 391.0], ["West", 210.5]],
    )
    assert model["row_count"] == 2
    assert model["totals"]["sum(revenue)"] == pytest.approx(601.5)


def test_presmodel_applies_filters_sort_topn_and_calcs() -> None:
    spec = worksheet(
        filters=[QuantitativeFilter(field="revenue", min=150)],
        limit=1,
        table_calcs=[TableCalc(kind="percent_of_total", field="revenue")],
    )
    model = build_presmodel(spec, COLUMNS, ROWS)
    # Only East's rows clear the 150 floor, so West contributes nothing.
    assert model["rows"] == [["East", 391.0, 100.0]]
    assert [column["name"] for column in model["columns"]] == [
        "region",
        "sum(revenue)",
        "percent_of_total(sum(revenue))",
    ]


def test_presmodel_categorical_filter_and_legend_domain() -> None:
    spec = worksheet(
        marks=MarksCard(type="bar", color=FieldRef(field="region")),
        filters=[CategoricalFilter(field="region", values=["West"])],
    )
    model = build_presmodel(spec, COLUMNS, ROWS)
    assert all(row[0] == "West" for row in model["rows"])
    assert model["legend"]["color"]["kind"] == "categorical"
    assert model["legend"]["color"]["field"] == "region"


def test_presmodel_table_calcs_rank_and_running_total() -> None:
    ranked = worksheet(table_calcs=[TableCalc(kind="rank", field="revenue")])
    model = build_presmodel(ranked, COLUMNS, ROWS)
    values = {row[0]: row[2] for row in model["rows"]}
    assert values["East"] == 1
    assert values["West"] == 2
    running = worksheet(table_calcs=[TableCalc(kind="running_total", field="revenue")])
    model = build_presmodel(running, COLUMNS, ROWS)
    assert model["rows"][-1][2] == pytest.approx(601.5)


def test_presmodel_temporal_dimensions_keep_natural_order() -> None:
    spec = WorksheetSpec(
        source=SOURCE,
        columns=[FieldRef(field="month", field_type="temporal")],
        rows=[
            FieldRef(field="revenue", field_type="quantitative", aggregation="sum", discrete=False)
        ],
        marks=MarksCard(type="line"),
    )
    model = build_presmodel(spec, COLUMNS, ROWS)
    assert [row[0] for row in model["rows"]] == ["2026-01-01", "2026-02-01"]
    assert [row[1] for row in model["rows"]] == [pytest.approx(300.75), pytest.approx(300.75)]


def test_presmodel_distinct_count_measures() -> None:
    spec = WorksheetSpec(
        source=SOURCE,
        columns=[FieldRef(field="region")],
        rows=[
            FieldRef(
                field="month", field_type="temporal", aggregation="distinct_count", discrete=False
            )
        ],
        marks=MarksCard(type="bar"),
    )
    model = build_presmodel(spec, COLUMNS, ROWS)
    assert model["rows"][0][1] == 2  # two distinct months per region
