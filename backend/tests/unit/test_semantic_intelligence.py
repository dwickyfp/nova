"""Semantic IR, routing, planning, compilation, and safety gates."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.agents.semantic.compiler import (
    AdditivityError,
    SemanticCompiler,
    UnsafeFanoutError,
)
from app.modules.agents.semantic.ir import (
    Additivity,
    FieldKind,
    SemanticDatasetIR,
    SemanticExampleIR,
    SemanticFieldIR,
    SemanticGrain,
    SemanticMetricIR,
    SemanticModelIR,
    SemanticNamedFilterIR,
    SemanticRelationshipIR,
)
from app.modules.agents.semantic.planning import (
    SemanticFilter,
    SemanticGraph,
    SemanticPlan,
    SemanticPlanner,
    SemanticTime,
)
from app.modules.agents.semantic.runtime import (
    LiteralResolver,
    SemanticCatalogRetriever,
    SemanticFeedback,
    SemanticModelCandidate,
    SemanticModelRouter,
    VerifiedQuery,
    VerifiedQueryRetriever,
    feedback_suggestions,
    lint_semantic_model,
    materialized_view_suggestions,
    semantic_quality,
    validate_semantic_model_ir,
)


def _field(
    dataset: str,
    name: str,
    *,
    kind: FieldKind = FieldKind.DIMENSION,
    samples: tuple[str, ...] = (),
    is_time: bool = False,
) -> SemanticFieldIR:
    return SemanticFieldIR(
        name=name,
        dataset=dataset,
        expression=name,
        kind=kind,
        sample_values=samples,
        is_time=is_time,
    )


def sales_model(*, with_items: bool = False, unknown_cardinality: bool = False) -> SemanticModelIR:
    orders = SemanticDatasetIR(
        name="orders",
        source="analytics.sales.orders",
        description="One row per order",
        grain=SemanticGrain(("order_id",)),
        fields=(
            _field("orders", "order_id"),
            _field("orders", "region_id"),
            _field("orders", "city", samples=("Jakarta", "Bandung")),
            _field("orders", "order_date", is_time=True),
            _field("orders", "amount", kind=FieldKind.FACT),
        ),
    )
    regions = SemanticDatasetIR(
        name="regions",
        source="analytics.sales.regions",
        description="One row per region",
        grain=SemanticGrain(("region_id",)),
        fields=(
            _field("regions", "region_id"),
            _field("regions", "region", samples=("West", "East")),
        ),
    )
    datasets = [orders, regions]
    relationships = [
        SemanticRelationshipIR(
            name="orders_region",
            from_dataset="orders",
            to_dataset="regions",
            from_columns=("region_id",),
            to_columns=("region_id",),
            cardinality="unknown" if unknown_cardinality else "many_to_one",
        )
    ]
    if with_items:
        datasets.append(
            SemanticDatasetIR(
                name="order_items",
                source="analytics.sales.order_items",
                description="One row per product in an order",
                grain=SemanticGrain(("order_id", "product_id")),
                fields=(
                    _field("order_items", "order_id"),
                    _field("order_items", "product_id"),
                    _field(
                        "order_items",
                        "product_name",
                        samples=("AQUA Mineral Water 600 ML PET",),
                    ),
                ),
            )
        )
        relationships.append(
            SemanticRelationshipIR(
                name="orders_items",
                from_dataset="orders",
                to_dataset="order_items",
                from_columns=("order_id",),
                to_columns=("order_id",),
                cardinality="one_to_many",
            )
        )
    return SemanticModelIR(
        name="sales",
        description="Sales revenue and orders",
        version="1",
        fingerprint="sales-v1",
        datasets=tuple(datasets),
        metrics=(
            SemanticMetricIR(
                name="total_revenue",
                expression="SUM(orders.amount)",
                base_dataset="orders",
                description="Governed completed-order revenue",
                synonyms=("revenue", "sales"),
                default_time_dimension="order_date",
            ),
        ),
        relationships=tuple(relationships),
        named_filters=(
            SemanticNamedFilterIR(
                name="completed_order",
                expression="orders.status = 'completed'",
                dataset="orders",
                synonyms=("completed orders",),
            ),
        ),
        examples=(
            SemanticExampleIR(
                question="Show sales grouped by geography",
                semantic_plan={"metrics": ["total_revenue"], "dimensions": ["region"]},
            ),
        ),
    )


def test_semantic_model_router_chooses_domain_and_reports_ties():
    sales = sales_model()
    finance = replace(
        sales,
        name="finance",
        description="Gross margin and balance",
        fingerprint="f",
        metrics=(
            replace(
                sales.metrics[0],
                name="gross_margin",
                description="Finance gross margin",
                synonyms=("gross margin",),
            ),
        ),
    )
    marketing = replace(
        sales,
        name="marketing",
        description="Campaign CAC",
        fingerprint="m",
        metrics=(
            replace(
                sales.metrics[0],
                name="campaign_cac",
                description="Campaign acquisition cost",
                synonyms=("campaign cac", "cac"),
            ),
        ),
    )
    router = SemanticModelRouter()
    assert router.route(
        "revenue by region",
        [SemanticModelCandidate("sales", sales), SemanticModelCandidate("finance", finance)],
    ).model_id == "sales"
    assert router.route(
        "campaign CAC",
        [
            SemanticModelCandidate("sales", sales),
            SemanticModelCandidate("marketing", marketing),
        ],
    ).model_id == "marketing"


def test_catalog_retrieval_is_small_and_authorization_aware():
    model = sales_model(with_items=True)
    irrelevant = tuple(
        SemanticDatasetIR(
            name=f"unused_{index}",
            source=f"analytics.other.unused_{index}",
            grain=SemanticGrain(("id",)),
            fields=(_field(f"unused_{index}", "id"),),
        )
        for index in range(60)
    )
    model = replace(model, datasets=(*model.datasets, *irrelevant))
    semantic_slice = SemanticCatalogRetriever().retrieve(
        model,
        "revenue by region",
        authorized_datasets={"orders", "regions"},
    )
    assert {item["name"] for item in semantic_slice.datasets} == {"orders", "regions"}
    assert all(not item["name"].startswith("unused_") for item in semantic_slice.datasets)
    assert len(semantic_slice.metrics) == 1


def test_planner_selects_metric_dimension_literal_and_time():
    planned = SemanticPlanner().plan(
        sales_model(), "Revenue by city in Jakarta last month top 5"
    )
    assert planned.plan is not None
    assert planned.plan.metrics == ("total_revenue",)
    assert planned.plan.dimensions == ("city",)
    assert planned.plan.filters == (SemanticFilter("city", "=", "Jakarta"),)
    assert planned.plan.time == SemanticTime(
        "order_date", grain="month", range="previous_month"
    )
    assert planned.plan.limit == 5


def test_compiler_generates_starrocks_sql_and_graph_join():
    plan = SemanticPlan(
        metrics=("total_revenue",),
        dimensions=("region",),
        filters=(SemanticFilter("city", "=", "Jakarta"),),
        time=SemanticTime("order_date", grain="month", range="previous_month"),
        limit=20,
    )
    compiled = SemanticCompiler().compile(sales_model(), plan)
    assert "LEFT JOIN `analytics`.`sales`.`regions`" in compiled.sql
    assert "DATE_TRUNC('month'" in compiled.sql
    assert "`orders`.`city` = 'Jakarta'" in compiled.sql
    assert compiled.relationship_path == ("orders_region",)


def test_compiler_builds_deterministic_previous_period_comparison():
    compiled = SemanticCompiler().compile(
        sales_model(),
        SemanticPlan(
            metrics=("total_revenue",),
            time=SemanticTime(
                "order_date",
                grain="month",
                range="current_month",
                compare="previous_period",
            ),
        ),
    )
    assert "AS `comparison_period`" in compiled.sql
    assert "'current_period' ELSE 'previous_period'" in compiled.sql
    assert "INTERVAL 1 MONTH" in compiled.sql


def test_relationship_graph_handles_multihop_preference_and_disconnected():
    base = sales_model()
    countries = SemanticDatasetIR(
        name="countries",
        source="analytics.sales.countries",
        grain=SemanticGrain(("country_id",)),
        fields=(_field("countries", "country_id"),),
    )
    rel = SemanticRelationshipIR(
        "regions_country", "regions", "countries", ("country_id",), ("country_id",), "many_to_one"
    )
    model = replace(
        base,
        datasets=(*base.datasets, countries),
        relationships=(*base.relationships, rel),
    )
    path_names = [
        item.name for item in SemanticGraph(model).path("orders", "countries").relationships
    ]
    assert path_names == [
        "orders_region",
        "regions_country",
    ]
    with pytest.raises(ValueError, match="No relationship path"):
        SemanticGraph(base).path("orders", "missing")


def test_fanout_validator_rejects_one_to_many_and_unknown_cardinality():
    with pytest.raises(UnsafeFanoutError):
        SemanticCompiler().compile(
            sales_model(with_items=True),
            SemanticPlan(metrics=("total_revenue",), dimensions=("product_name",)),
        )
    with pytest.raises(UnsafeFanoutError):
        SemanticCompiler().compile(
            sales_model(unknown_cardinality=True),
            SemanticPlan(metrics=("total_revenue",), dimensions=("region",)),
        )


def test_additivity_rules_reject_invalid_dimensions_and_time():
    base = sales_model()
    non_additive = replace(
        base.metrics[0],
        name="active_users",
        additivity=Additivity.NON_ADDITIVE,
        allowed_dimensions=("city",),
    )
    with pytest.raises(AdditivityError):
        SemanticCompiler().compile(
            replace(base, metrics=(non_additive,)),
            SemanticPlan(metrics=("active_users",), dimensions=("region",)),
        )
    inventory = replace(
        base.metrics[0],
        name="inventory_balance",
        additivity=Additivity.SEMI_ADDITIVE,
        allowed_dimensions=("city",),
    )
    with pytest.raises(AdditivityError):
        SemanticCompiler().compile(
            replace(base, metrics=(inventory,)),
            SemanticPlan(
                metrics=("inventory_balance",),
                time=SemanticTime("order_date", grain="month"),
            ),
        )


def test_literal_resolver_uses_samples_and_search_candidates():
    field = _field(
        "order_items",
        "product_name",
        samples=("AQUA Mineral Water 600 ML PET", "Tea Bottle 500 ML"),
    )
    result = LiteralResolver().resolve("Aqua botol 600ml", field)
    assert result[0].value == "AQUA Mineral Water 600 ML PET"
    assert result[0].source == "sample"


def test_verified_query_retrieval_is_version_bound():
    plan = SemanticPlan(metrics=("total_revenue",), dimensions=("region",))
    entry = VerifiedQuery(
        "v1", "sales", "sales-v1", "Revenue by region last month", plan, "SELECT 1"
    )
    retriever = VerifiedQueryRetriever()
    current = retriever.retrieve(
        "regional revenue last month", [entry], model_fingerprint="sales-v1"
    )
    stale = retriever.retrieve(
        "regional revenue last month", [entry], model_fingerprint="sales-v2"
    )
    assert current == (entry,)
    assert stale == ()


def test_semantic_validation_lint_quality_and_feedback_are_reviewable():
    model = sales_model(unknown_cardinality=True)
    assert validate_semantic_model_ir(model).valid
    codes = {item.code for item in lint_semantic_model(model)}
    assert "relationship_missing_cardinality" in codes
    quality = semantic_quality(model, verified_query_count=2)
    assert quality["verified_query_count"] == 2
    suggestions = feedback_suggestions(
        [SemanticFeedback("unresolved_literal", "Aqua bottle", "no match", "sales")]
    )
    assert suggestions[0]["requires_adoption"] is True
    workload = [
        {
            "semantic_model_id": "sales",
            "model_fingerprint": "sales-v1",
            "metrics": ["total_revenue"],
            "dimensions": ["region"],
            "time_grain": "day",
            "succeeded": True,
        }
        for _ in range(10)
    ]
    advisors = materialized_view_suggestions(workload)
    assert advisors[0]["query_count"] == 10
    assert advisors[0]["requires_adoption"] is True
