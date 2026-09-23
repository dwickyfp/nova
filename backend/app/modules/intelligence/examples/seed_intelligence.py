"""Seed visible Intelligence examples over the bundled NOVA_DEMO data.

Run after Nova migrations and docker/init-nova.sql have been applied:

    cd backend
    uv run python -m app.modules.intelligence.examples.seed_intelligence

The script is idempotent. It uses the configured StarRocks system connection,
never stores credentials, and grants demo-source read access to ACCOUNTADMIN.
Nova's bootstrap assigns that role to nova_admin. Objects are managed by the
owner or a caller with ACCOUNTADMIN active.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from copy import deepcopy
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from app.core.config import settings
from app.core.database import db
from app.core.security import encrypt_password
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.entities import EntityCreate, entity_registry
from app.modules.intelligence.feature_store import (
    FeatureGroupDefinition,
    FeatureGroupMember,
    FeatureLookup,
    FeatureViewDefinition,
    TrainingSetCreate,
    feature_store,
)
from app.modules.intelligence.search import SearchIndexCreate, search_service
from app.modules.intelligence.semantic_views import (
    SemanticViewCreate,
    SemanticViewVersionCreate,
    semantic_view_service,
)

DEMO_NAME = "nova_sales_360"
EXAMPLE = Path(__file__).resolve().parents[2] / "agents/examples/nova_sales.ossie.yaml"


def _user() -> dict:
    return {
        "username": settings.STARROCKS_ROOT_USER,
        "encrypted_password": encrypt_password(settings.STARROCKS_ROOT_PASSWORD),
        "active_role": None,
        "session_id": None,
    }


def semantic_definition(customer_id: str, order_id: str) -> dict:
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    document["name"] = DEMO_NAME
    document["description"] = "Sales, customers, and orders in Nova's sample warehouse."
    document["entities"] = {"customer": customer_id, "order": order_id}
    document["hierarchies"] = {"geography": ["customers.country", "customers.city"]}
    for dataset in document["datasets"]:
        dataset["grain"] = {"keys": dataset["primary_key"]}
        if dataset["name"] == "customers":
            for field in dataset["fields"]:
                if field["name"] in {"country", "city"}:
                    field["kind"] = "dimension"
    document["metrics"].append({
        "name": "revenue_per_order",
        "base_dataset": "orders",
        "expression": {"dialects": [{
            "dialect": "ANSI_SQL", "expression": "total_revenue / order_count",
        }]},
        "dependencies": ["total_revenue", "order_count"],
        "datatype": "Decimal",
        "description": "Revenue divided by the number of orders.",
        "additivity": "non_additive",
        "allowed_dimensions": ["orders.order_date", "customers.country", "customers.city"],
    })
    normalized = parse_ossie(yaml.safe_dump(document)).as_dict()
    plan = SemanticPlan(metrics=("total_revenue",))
    sql = SemanticCompiler().compile(SemanticModelIR.from_ossie(normalized), plan).sql
    document["verified_queries"] = [{
        "verified_query_id": "total_revenue",
        "question": "What is total revenue?",
        "semantic_plan": plan.as_dict(),
        "verified_sql": sql,
    }]
    return document


async def _wait_search(name: str, user: dict) -> dict:
    for _ in range(120):
        current = await search_service.describe(name, user)
        if current["status"] in {"ACTIVE", "FAILED"}:
            return current
        await asyncio.sleep(1)
    raise RuntimeError(f"Search index {name} did not finish building")


async def seed(embedding_alias: str | None = None) -> dict[str, str]:
    await db.init_system_pool()
    user = _user()
    try:
        for database in ("NOVA_DEMO", "NOVA_CATALOG"):
            await db.execute_system(
                f"GRANT SELECT ON ALL TABLES IN DATABASE {database} TO ROLE ACCOUNTADMIN"
            )
        await db.execute_system("GRANT ACCOUNTADMIN TO USER 'nova_admin'@'%'")

        entities = {item.name: item for item in await entity_registry.list(user)}
        if "nova_demo_customer" not in entities:
            entities["nova_demo_customer"] = await entity_registry.create(
                EntityCreate(
                    name="nova_demo_customer", description="A buyer in the demo warehouse.",
                    database="NOVA_DEMO", schema_name="NOVA_DEMO",
                    relation="NOVA_DEMO.customers", key_columns=["customer_id"],
                    tags=["demo", "commerce"],
                ), user,
            )
        if "nova_demo_order" not in entities:
            entities["nova_demo_order"] = await entity_registry.create(
                EntityCreate(
                    name="nova_demo_order", description="A purchase in the demo warehouse.",
                    database="NOVA_DEMO", schema_name="NOVA_DEMO",
                    relation="NOVA_DEMO.orders", key_columns=["order_id"],
                    tags=["demo", "commerce"],
                ), user,
            )

        definition = semantic_definition(
            entities["nova_demo_customer"].id, entities["nova_demo_order"].id,
        )
        views = {item["name"]: item for item in await semantic_view_service.list(user)}
        if DEMO_NAME not in views:
            view = await semantic_view_service.create(
                SemanticViewCreate(
                    name=DEMO_NAME, database="NOVA_DEMO", schema_name="NOVA_DEMO",
                    definition=yaml.safe_dump(definition),
                ), user,
            )
            report = await semantic_view_service.validate(view["id"], 1, user)
            if not report["valid"]:
                raise RuntimeError(f"Demo Semantic View validation failed: {report['errors']}")
            await semantic_view_service.publish(view["id"], 1, user)
            changed = deepcopy(definition)
            changed["metrics"][0]["expression"]["dialects"][0]["expression"] = (
                "SUM(orders.total_amount) + 1"
            )
            changed_ir = SemanticModelIR.from_ossie(
                parse_ossie(yaml.safe_dump(changed)).as_dict()
            )
            changed["verified_queries"][0]["verified_sql"] = SemanticCompiler().compile(
                changed_ir, SemanticPlan(metrics=("total_revenue",))
            ).sql
            second = await semantic_view_service.add_version(
                view["id"], SemanticViewVersionCreate(definition=yaml.safe_dump(changed)), user,
            )
            second_report = await semantic_view_service.validate(
                view["id"], second["version"], user
            )
            if not second_report["valid"]:
                raise RuntimeError(f"Demo draft validation failed: {second_report['errors']}")
            views[DEMO_NAME] = view

        search_name = "nova_demo_product_search"
        indexes = {item["name"]: item for item in await search_service.list(user)}
        if search_name not in indexes:
            await search_service.create(
                SearchIndexCreate(
                    name=search_name, source_relation="NOVA_CATALOG.products",
                    key_columns=["product_id"], content_columns=["product_name"],
                    filter_columns=["category", "brand"],
                ), user,
            )
            indexes[search_name] = await _wait_search(search_name, user)
            if indexes[search_name]["status"] != "ACTIVE":
                raise RuntimeError("Demo product Search Index failed to build")

        if embedding_alias:
            vector_name = "nova_demo_product_semantic_search"
            if vector_name not in indexes:
                await search_service.create(
                    SearchIndexCreate(
                        name=vector_name, source_relation="NOVA_CATALOG.products",
                        key_columns=["product_id"], content_columns=["product_name"],
                        filter_columns=["category", "brand"], model_alias=embedding_alias,
                    ), user,
                )
                built = await _wait_search(vector_name, user)
                if built["status"] != "ACTIVE":
                    raise RuntimeError("Demo semantic Search Index failed to build")

        feature_name = "nova_demo_order_features"
        feature_views = {item["name"]: item for item in await feature_store.list_views(user)}
        if feature_name not in feature_views:
            feature_views[feature_name] = await feature_store.create_view(
                FeatureViewDefinition(
                    name=feature_name, entity_id=entities["nova_demo_order"].id,
                    source_relation="NOVA_DEMO.orders", event_timestamp="order_date",
                    feature_columns=["total_amount"],
                ), user,
            )
        group_name = "nova_demo_order_feature_group"
        groups = {item["name"]: item for item in await feature_store.list_groups(user)}
        if group_name not in groups:
            await feature_store.create_group(
                FeatureGroupDefinition(
                    name=group_name, entity_id=entities["nova_demo_order"].id,
                    members=[FeatureGroupMember(
                        view_name=feature_name,
                        version=feature_views[feature_name]["active_version"],
                    )],
                ), user,
            )
        training = await db.execute_system(
            "SELECT id FROM NOVA_SYSTEM.CONFIG_FEATURE_TRAINING_SETS "
            "WHERE group_name=%s AND label_relation=%s LIMIT 1",
            [group_name, "NOVA_DEMO.orders"],
        )
        if training["rows"]:
            training_id = training["rows"][0][0]
        else:
            result = await feature_store.create_training_set(
                group_name,
                TrainingSetCreate(
                    label_relation="NOVA_DEMO.orders", entity_keys=["order_id"],
                    event_timestamp="order_date", label_columns=["status"],
                    preview_rows=3,
                ), user,
            )
            training_id = result["id"]
        first_order = await db.execute_system("SELECT order_id FROM NOVA_DEMO.orders LIMIT 1")
        if first_order["rows"]:
            await feature_store.lookup(
                group_name,
                FeatureLookup(entity_key={"order_id": first_order["rows"][0][0]}),
                user,
            )
        return {
            "entities": "nova_demo_customer, nova_demo_order",
            "semantic_view": DEMO_NAME,
            "search_index": search_name,
            "feature_view": feature_name,
            "feature_group": group_name,
            "training_set_id": training_id,
            "role": "ACCOUNTADMIN (assigned to nova_admin)",
        }
    finally:
        await db.close_system_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Nova Intelligence demo objects")
    parser.add_argument("--embedding-alias", help="Also build a semantic Search Index")
    arguments = parser.parse_args()
    print(json.dumps(asyncio.run(seed(arguments.embedding_alias)), indent=2))


if __name__ == "__main__":
    main()
