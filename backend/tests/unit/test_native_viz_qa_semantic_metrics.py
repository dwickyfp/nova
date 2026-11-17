"""Release QA: metric grain, fanout and formula safety for native Dataviz sources.

NDV-METRIC-005 (count distinct at the declared fact grain), NDV-METRIC-007
(many-to-many joins are refused) and NDV-METRIC-019 (metric formulas cannot
carry raw SQL).
"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import NovaException
from app.modules.agents.semantic.compiler import SemanticCompiler, UnsafeFanoutError
from app.modules.agents.semantic.ir import (
    SemanticDatasetIR,
    SemanticGrain,
    SemanticMetricIR,
    SemanticModelIR,
    SemanticRelationshipIR,
)
from app.modules.agents.semantic.planning import SemanticPlan, SemanticPlanError
from app.modules.viz import dataset_service as module
from app.modules.viz.aggregate import plan_aggregate
from app.modules.viz.contracts.authoring import SemanticInspect
from app.modules.viz.contracts.datasets import (
    DatasetDefinition,
    DatasetField,
    SemanticSelection,
)
from app.modules.viz.contracts.queries import DatasetQueryRequest
from app.sql_frontend.binding.models import BoundColumn
from app.sql_frontend.errors import SemanticError
from tests.unit.test_native_viz_datasets import schema_result
from tests.unit.test_native_viz_datasets import setup as setup  # noqa: F401 - fixture
from tests.unit.test_native_viz_datasets import source as source  # noqa: F401 - fixture
from tests.unit.test_semantic_intelligence import _field, sales_model

DATASET = UUID("aaaaaaaa-0000-4000-8000-000000000003")


def _with_order_count(model: SemanticModelIR) -> SemanticModelIR:
    order_count = SemanticMetricIR(name="order_count",
                                   expression="COUNT(DISTINCT orders.order_id)",
                                   base_dataset="orders")
    return replace(model, metrics=(*model.metrics, order_count))


# ── NDV-METRIC-005: count distinct with duplicate join rows ──────────────


def test_count_distinct_across_a_many_to_one_join_stays_at_the_fact_grain():
    compiled = SemanticCompiler().compile(
        _with_order_count(sales_model()),
        SemanticPlan(metrics=("order_count",), dimensions=("region",)),
    )
    assert "COUNT(DISTINCT `orders`.`order_id`) AS `order_count`" in compiled.sql
    assert "FROM `analytics`.`sales`.`orders` AS `orders`" in compiled.sql
    assert ("LEFT JOIN `analytics`.`sales`.`regions` AS `regions` "
            "ON `orders`.`region_id` = `regions`.`region_id`") in compiled.sql
    assert compiled.sql.rstrip().endswith("GROUP BY `regions`.`region`")


def test_a_join_that_duplicates_fact_rows_is_refused_not_counted():
    # order_items repeats each order once per product: grouping orders by product
    # would duplicate fact rows, so the compiler refuses instead of guessing.
    model = _with_order_count(sales_model(with_items=True))
    for metric in ("order_count", "total_revenue"):
        with pytest.raises(UnsafeFanoutError, match="fan out"):
            SemanticCompiler().compile(
                model, SemanticPlan(metrics=(metric,), dimensions=("product_name",)))


def test_native_count_distinct_aggregates_over_the_dataset_rows():
    fields = (
        DatasetField(field_id="f_order", name="Order", source_column="order_id",
                     logical_type="integer", physical_type="BIGINT",
                     semantic_role="dimension", nullable=False),
        DatasetField(field_id="f_city", name="City", source_column="city",
                     logical_type="string", physical_type="VARCHAR(32)",
                     semantic_role="dimension", nullable=True),
    )
    joined = ("SELECT o.order_id, c.city FROM orders o "
              "JOIN order_lines l ON l.order_id = o.order_id JOIN cities c ON c.id = o.city_id")
    plan = plan_aggregate(joined, fields, DatasetQueryRequest(
        revision=1, dimensions=[{"field_id": "f_city"}],
        measures=[{"field_id": "f_order", "aggregation": "count_distinct"},
                  {"field_id": "f_order", "aggregation": "count"}]),
        dataset_id=str(DATASET), reserved_parameters=set())
    assert "COUNT(DISTINCT `order_id`) AS `m0`" in plan.sql
    assert "COUNT(`order_id`) AS `m1`" in plan.sql
    assert plan.sql.startswith("SELECT `city` AS `d0`")
    assert f"FROM ({joined}\n) AS `nova_dataset`" in plan.sql
    assert [column.logical_type for column in plan.columns] == ["string", "integer", "integer"]


# ── NDV-METRIC-007: unsupported many-to-many joins ───────────────────────


def _many_to_many(cardinality: str) -> SemanticModelIR:
    base = sales_model()
    tags = SemanticDatasetIR(
        name="tags", source="analytics.sales.order_tags",
        grain=SemanticGrain(("region_id", "tag")),
        fields=(_field("tags", "region_id"), _field("tags", "tag")),
    )
    relationship = SemanticRelationshipIR(
        "orders_tags", "orders", "tags", ("region_id",), ("region_id",), cardinality)
    return replace(base, datasets=(*base.datasets, tags),
                   relationships=(*base.relationships, relationship))


@pytest.mark.parametrize("cardinality", ["many_to_many", "many-to-many", "MANY_TO_MANY", "n:n"])
def test_many_to_many_relationship_is_an_explicit_semantic_error(cardinality):
    with pytest.raises(UnsafeFanoutError) as error:
        SemanticCompiler().compile(
            _many_to_many(cardinality),
            SemanticPlan(metrics=("total_revenue",), dimensions=("tag",)),
        )
    assert "does not prove a fanout-safe cardinality" in str(error.value)


def test_metrics_not_crossing_the_many_to_many_join_still_compile():
    compiled = SemanticCompiler().compile(
        _many_to_many("many_to_many"),
        SemanticPlan(metrics=("total_revenue",), dimensions=("city",)),
    )
    assert "order_tags" not in compiled.sql


@pytest.fixture
def many_to_many_source(setup, monkeypatch):  # noqa: F811 - pytest fixture injection
    definition = {
        "name": "Orders", "version": "1.0",
        "datasets": [
            {"name": "orders", "source": "analytics.orders", "grain": {"keys": ["id"]},
             "fields": [{"name": "id", "expression": "id", "kind": "dimension"},
                        {"name": "region_id", "expression": "region_id", "kind": "dimension"},
                        {"name": "amount", "expression": "amount", "kind": "fact"}]},
            {"name": "tags", "source": "analytics.order_tags",
             "grain": {"keys": ["region_id", "tag"]},
             "fields": [{"name": "region_id", "expression": "region_id", "kind": "dimension"},
                        {"name": "tag", "expression": "tag", "kind": "dimension"}]},
        ],
        "relationships": [{"name": "orders_tags", "from": "orders", "to": "tags",
                           "from_columns": ["region_id"], "to_columns": ["region_id"],
                           "cardinality": "many_to_many"}],
        "metrics": [{"name": "total", "expression": "SUM(orders.amount)",
                     "base_dataset": "orders"}],
    }
    model = SemanticModelIR.from_ossie(definition)
    owner = AsyncMock(return_value={"definition": definition, "database_name": "analytics"})
    monkeypatch.setattr(module.semantic_view_service, "get_version_for_agent", owner)
    setup.execute.return_value = schema_result((BoundColumn("tag", "varchar(32)", 1, True),
                                                BoundColumn("total", "decimal(38,2)", 2, True)))
    request = SemanticInspect(dataset_id=uuid4(), source=SemanticSelection(
        model_id=str(uuid4()), model_revision=1, model_fingerprint=model.fingerprint,
        dimensions=("tag",), metrics=("total",)))
    return SimpleNamespace(**vars(setup), request=request, owner=owner, model=model)


async def test_a_native_semantic_dataset_over_many_to_many_is_refused_before_any_query(
    many_to_many_source,
):
    case = many_to_many_source
    with pytest.raises(NovaException) as error:
        await case.service.inspect(case.user, case.request)
    assert error.value.status_code == 422
    assert "unavailable or unsupported" in str(error.value)
    assert "orders_tags" not in str(error.value) and "fanout" not in str(error.value)
    case.owner.assert_awaited()
    case.execute.assert_not_awaited()
    # The refusal is the shared compiler's fanout gate, not a malformed model.
    with pytest.raises(UnsafeFanoutError):
        SemanticCompiler().compile(case.model, SemanticPlan(metrics=("total",),
                                                            dimensions=("tag",)))


def _definition(**extra) -> DatasetDefinition:
    return DatasetDefinition.model_validate({
        "dataset_id": str(DATASET), "title": "Orders",
        "source": {"kind": "table", "catalog": "default_catalog", "database": "sales",
                   "object_name": "orders", "physical_id": "t1"},
        "fields": [{"field_id": "order_id", "name": "Order", "source_column": "order_id",
                    "logical_type": "integer", "physical_type": "BIGINT",
                    "semantic_role": "dimension", "nullable": False}],
        "schema_fingerprint": "a" * 64,
        **extra,
    })


def test_a_native_many_to_many_relationship_has_no_execution_plan():
    document = _definition(relationships=[{
        "relationship_id": str(uuid4()), "dataset_id": str(uuid4()), "dataset_revision": 1,
        "keys": [{"local_field_id": "order_id", "foreign_field_id": "order_id"}],
        "cardinality": "many:many", "join": "inner",
    }])
    with pytest.raises(SemanticError, match="needs a semantic or parameter execution plan"):
        module.NativeDatasetService._selection(document)


# ── NDV-METRIC-019: raw SQL through a metric formula ─────────────────────


INJECTIONS = [
    "SUM(amount); DROP TABLE orders",
    "SUM(amount) -- hide the rest",
    "SUM(amount) /* comment */",
    "(SELECT SUM(amount) FROM orders)",
    "SUM(amount) + (SELECT password FROM users)",
]


@pytest.mark.parametrize("formula", INJECTIONS)
def test_a_native_dataset_metric_formula_never_becomes_sql(formula):
    document = _definition(metrics=[{
        "metric_id": str(uuid4()), "revision": 1, "name": "Revenue", "formula": formula,
        "level": "aggregate", "grain": [], "unit": "IDR", "scope": "shared",
    }])
    # Native execution has no path that turns a metric formula into SQL text.
    with pytest.raises(SemanticError):
        module.NativeDatasetService._selection(document)


@pytest.mark.parametrize("expression", [
    "total_revenue; DROP TABLE orders",
    "total_revenue -- order_count",
    "total_revenue /* order_count */",
    "(SELECT 1) + total_revenue",
    "total_revenue + order_count + users.password",
])
def test_derived_semantic_metrics_refuse_raw_sql_constructs(expression):
    model = _with_order_count(sales_model())
    derived = SemanticMetricIR(name="derived", expression=expression, base_dataset="orders",
                               dependencies=("total_revenue", "order_count"),
                               datatype="decimal")
    with pytest.raises(SemanticPlanError):
        SemanticCompiler().compile(replace(model, metrics=(*model.metrics, derived)),
                                   SemanticPlan(metrics=("derived",)))


@pytest.mark.parametrize("expression", [
    "SUM(orders.amount); DROP TABLE orders",
    "SUM(orders.amount) -- x",
    "SUM(orders.amount) /* x */",
    "(SELECT SUM(amount) FROM analytics.sales.orders)",
    "SUM(orders.amount) + @secret",
])
def test_base_semantic_metric_expressions_are_parsed_not_pasted(expression):
    model = sales_model()
    metric = replace(model.metrics[0], expression=expression)
    with pytest.raises(SemanticPlanError):
        SemanticCompiler().compile(replace(model, metrics=(metric,)),
                                   SemanticPlan(metrics=("total_revenue",)))


# ── NDV-METRIC-003: a ratio over a zero denominator ──────────────────────


@pytest.mark.parametrize(("expression", "guarded"), [
    ("total_revenue / order_count", "/ NULLIF((COUNT(DISTINCT `orders`.`order_id`)), 0)"),
    ("total_revenue / (order_count - 1)",
     "/ NULLIF(((COUNT(DISTINCT `orders`.`order_id`)) - 1), 0)"),
    ("total_revenue / 2", "/ NULLIF(2, 0)"),
])
def test_a_derived_ratio_never_divides_by_zero(expression, guarded):
    model = _with_order_count(sales_model())
    ratio = SemanticMetricIR(name="ratio", expression=expression, base_dataset="orders",
                             dependencies=("total_revenue", "order_count")
                             if "order_count" in expression else ("total_revenue",),
                             datatype="decimal")
    compiled = SemanticCompiler().compile(replace(model, metrics=(*model.metrics, ratio)),
                                          SemanticPlan(metrics=("ratio",)))
    normalised = " ".join(compiled.sql.replace("\n", " ").split())
    assert guarded in normalised, normalised
