"""NDV-METRIC-002: a semantic metric is computed at the chart's grain, never re-added wrongly."""

import json
from uuid import uuid4

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.viz.contracts.datasets import DatasetField
from app.modules.viz.contracts.queries import DatasetQueryRequest
from app.modules.viz.semantic_grain import MetricGrainError, plan_semantic_grain, summable

DATASET = uuid4()


def expression(sql: str) -> dict:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}


def model(*, revenue_additivity: str | None = None) -> SemanticModelIR:
    revenue = {"name": "revenue_total", "datatype": "Decimal",
               "expression": expression("SUM(sales.revenue)")}
    if revenue_additivity:
        revenue["additivity"] = revenue_additivity
    return _parsed({
        "version": "0.1.1", "name": "margins",
        "datasets": [{"name": "sales", "source": "shop.sales", "primary_key": ["id"], "fields": [
            {"name": "id", "expression": expression("id"), "kind": "dimension"},
            {"name": "region", "expression": expression("region"), "kind": "dimension"},
            {"name": "sold_on", "expression": expression("sold_on"), "kind": "dimension",
             "datatype": "Date"},
            {"name": "revenue", "expression": expression("revenue"), "datatype": "Decimal"},
            {"name": "profit", "expression": expression("profit"), "datatype": "Decimal"},
        ]}],
        "metrics": [
            {"name": "margin", "datatype": "Decimal",
             "expression": expression("SUM(sales.profit) / SUM(sales.revenue)")},
            revenue,
            {"name": "orders", "datatype": "Integer", "expression": expression("COUNT(sales.id)")},
            {"name": "buyers", "datatype": "Integer",
             "expression": expression("COUNT(DISTINCT sales.id)")},
        ],
    })


def _parsed(definition: dict) -> SemanticModelIR:
    parsed = parse_ossie(json.dumps(definition))
    assert parsed.valid, parsed.errors
    return SemanticModelIR.from_ossie(parsed.as_dict())


def field(name: str, role: str, logical: str = "string") -> DatasetField:
    return DatasetField(field_id=f"f_{name}", name=name.replace("_", " ").title(),
                        source_column=name, logical_type=logical, physical_type="varchar",
                        semantic_role=role, nullable=True)


FIELDS = (
    field("region", "dimension"), field("sold_on", "dimension", "date"),
    field("margin", "measure", "decimal"), field("revenue_total", "measure", "decimal"),
    field("orders", "measure", "integer"), field("buyers", "measure", "integer"),
)


def request(dimensions=(), measures=(("margin", "sum"),), filters=None) -> DatasetQueryRequest:
    body = {
        "revision": 1,
        "dimensions": [{"field_id": f"f_{name}", **({"date_grain": grain} if grain else {})}
                       for name, grain in dimensions],
        "measures": [{"field_id": f"f_{name}", "aggregation": aggregation}
                     for name, aggregation in measures],
    }
    if filters:
        body["filters"] = {"expression": {"type": "and", "children": [
            {"type": "predicate", "op": "eq",
             "field": {"dataset_id": str(DATASET), "dataset_revision": 1, "field_id": f"f_{name}"},
             "values": [{"type": "string", "value": value}]} for name, value in filters]}}
    return DatasetQueryRequest.model_validate(body)


@pytest.mark.parametrize("sql, expected", [
    ("SUM(sales.revenue)", True),
    ("sum( sales.revenue )", True),
    ("COUNT(sales.id)", True),
    ("SUM(sales.amount) * 2", True),
    ("COUNT(DISTINCT sales.id)", False),
    ("SUM(sales.profit) / SUM(sales.revenue)", False),
    ("AVG(sales.revenue)", False),
    ("SUM(sales.revenue) - SUM(sales.refunds)", False),
    ("MAX(sales.revenue)", False),
    ("SUM(CASE WHEN sales.x > 0 THEN 1 END) / COUNT(sales.id)", False),
])
def test_only_a_single_sum_or_count_can_be_added_up(sql, expected):
    assert summable(sql) is expected


def test_the_dataset_grain_is_exact_when_the_chart_groups_by_every_dimension():
    assert plan_semantic_grain(model(), FIELDS, request(
        dimensions=(("region", None), ("sold_on", None)))) is None


def test_a_coarser_chart_compiles_the_metric_at_its_own_grain():
    grain = plan_semantic_grain(model(), FIELDS, request(dimensions=(("region", None),)))
    assert grain is not None
    assert grain.dimensions == ("region",) and grain.metrics == ("margin",)
    assert [item.field_id for item in grain.fields] == ["f_region", "f_margin"]
    # The semantic layer computes the ratio of sums per region, not a sum of ratios.
    sql = SemanticCompiler().compile(model(), SemanticPlan(
        dimensions=grain.dimensions, metrics=grain.metrics)).sql
    assert "SUM" in sql.upper() and "sold_on" not in sql


def test_a_total_with_no_dimensions_is_one_semantic_aggregate():
    grain = plan_semantic_grain(model(), FIELDS, request(
        measures=(("margin", "sum"), ("revenue_total", "sum"))))
    assert grain is not None and grain.dimensions == ()
    assert grain.metrics == ("margin", "revenue_total")


def test_filters_on_grouped_fields_keep_the_chart_grain():
    grain = plan_semantic_grain(model(), FIELDS, request(
        dimensions=(("region", None),), filters=(("region", "West"),)))
    assert grain is not None and grain.dimensions == ("region",)


def test_a_ratio_is_refused_with_a_coarser_date_or_a_filter_on_a_field_not_shown():
    with pytest.raises(MetricGrainError, match="Margin is calculated per Region, Sold On"):
        plan_semantic_grain(model(), FIELDS, request(dimensions=(("sold_on", "month"),)))
    with pytest.raises(MetricGrainError):
        plan_semantic_grain(model(), FIELDS, request(
            dimensions=(("region", None),), filters=(("sold_on", "2024-02-29"),)))
    with pytest.raises(MetricGrainError):
        plan_semantic_grain(model(), FIELDS, request(
            dimensions=(("region", None),), measures=(("buyers", "sum"),),
            filters=(("sold_on", "2024-02-29"),)))


def test_a_plain_sum_may_still_be_added_up_where_it_cannot_be_recompiled():
    assert plan_semantic_grain(model(), FIELDS, request(
        dimensions=(("sold_on", "month"),), measures=(("revenue_total", "sum"),))) is None
    assert plan_semantic_grain(model(), FIELDS, request(
        dimensions=(("sold_on", "month"),), measures=(("orders", "sum"),))) is None
    # Averaging, or a sum the author declared non-additive, is not.
    with pytest.raises(MetricGrainError):
        plan_semantic_grain(model(), FIELDS, request(
            dimensions=(("sold_on", "month"),), measures=(("revenue_total", "avg"),)))
    with pytest.raises(MetricGrainError):
        plan_semantic_grain(model(revenue_additivity="non_additive"), FIELDS, request(
            dimensions=(("sold_on", "month"),), measures=(("revenue_total", "sum"),)))


def test_a_row_count_beside_a_metric_keeps_the_dataset_grain_rules():
    # A row count counts dataset rows, so the query is not recompiled; a ratio is refused.
    with pytest.raises(MetricGrainError):
        plan_semantic_grain(model(), FIELDS, DatasetQueryRequest.model_validate({
            "revision": 1, "dimensions": [{"field_id": "f_region"}],
            "measures": [{"field_id": "f_margin", "aggregation": "sum"},
                         {"aggregation": "count"}]}))


def test_queries_without_metrics_are_untouched():
    assert plan_semantic_grain(model(), FIELDS, request(
        dimensions=(("region", None),), measures=())) is None
