"""Semantic Views synced into Superset datasets (docs/arch-17-superset.md, Phase 5)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic.ir import (
    Additivity,
    FieldKind,
    SemanticDatasetIR,
    SemanticFieldIR,
    SemanticMetricIR,
    SemanticModelIR,
    SemanticRelationshipIR,
)
from app.modules.superset import semantic
from app.modules.superset.client import BiError


def _field(dataset, name, expression=None, kind=FieldKind.DIMENSION, **extra):
    return SemanticFieldIR(name, dataset, expression or name, kind, **extra)


def _model(metrics=None) -> SemanticModelIR:
    orders = SemanticDatasetIR(
        "orders",
        "sales.orders",
        fields=(
            _field("orders", "order_id"),
            _field("orders", "customer_id"),
            _field("orders", "ordered_at", is_time=True, description="When it was ordered"),
            _field("orders", "amount", kind=FieldKind.FACT),
            _field("orders", "net", "amount - discount", kind=FieldKind.FACT),
        ),
    )
    customers = SemanticDatasetIR(
        "customers",
        "sales.customers",
        fields=(
            _field("customers", "customer_id"),
            _field("customers", "segment"),
            _field("customers", "balance", kind=FieldKind.FACT),
        ),
    )
    stores = SemanticDatasetIR("stores", "sales.stores", fields=(_field("stores", "city"),))
    return SemanticModelIR(
        name="sales",
        description="",
        version="0.1.1",
        fingerprint="f",
        datasets=(orders, customers, stores),
        metrics=tuple(
            metrics
            or (
                SemanticMetricIR(
                    "revenue", "SUM(amount)", "orders", default_time_dimension="ordered_at"
                ),
                SemanticMetricIR("net_revenue", "SUM(net)", "orders"),
                SemanticMetricIR("discounts", "SUM(discount)", "orders"),
                SemanticMetricIR("customers_reached", "COUNT(DISTINCT customer_id)", "orders"),
                SemanticMetricIR("growth", "revenue / 2", "orders", dependencies=("revenue",)),
                SemanticMetricIR(
                    "closing_balance",
                    "SUM(balance)",
                    "customers",
                    additivity=Additivity.SEMI_ADDITIVE,
                ),
                SemanticMetricIR("store_count", "COUNT(stores.city)", "orders"),
            )
        ),  # fmt: skip
        relationships=(
            SemanticRelationshipIR(
                "order_customer",
                "orders",
                "customers",
                ("customer_id",),
                ("customer_id",),
                cardinality="many_to_one",
            ),
        ),  # fmt: skip
    )


def test_each_fact_dataset_becomes_one_dataset_with_its_metrics() -> None:
    plans = {plan.fact: plan for plan in semantic.dataset_plans(_model(), "Sales")}

    orders = plans["orders"]
    assert orders.name == "semantic_sales_orders"
    assert "FROM `sales`.`orders` AS `orders`" in orders.sql
    assert "LEFT JOIN `sales`.`customers` AS `customers`" in orders.sql
    assert "`orders`.`customer_id` = `customers`.`customer_id`" in orders.sql
    # Fields keep their semantic names; a joined name that clashes is prefixed.
    assert "AS `segment`" in orders.sql and "AS `customers_customer_id`" in orders.sql
    assert "(`orders`.`amount` - `orders`.`discount`) AS `net`" in orders.sql
    metrics = {metric["metric_name"]: metric["expression"] for metric in orders.metrics}
    assert metrics["revenue"] == "SUM(`amount`)"
    # A raw column the metric reads is exposed under a reserved name.
    assert metrics["discounts"] == "SUM(`__orders__discount`)"
    assert "`orders`.`discount` AS `__orders__discount`" in orders.sql
    assert metrics["customers_reached"] == "COUNT(DISTINCT `customer_id`)"
    # A metric over a computed field reads the same columns the field does.
    assert metrics["net_revenue"] == "SUM((`amount` - `__orders__discount`))"
    assert orders.temporal == ["ordered_at"] and orders.main_time == "ordered_at"
    assert orders.descriptions["ordered_at"] == "When it was ordered"

    left_out = " | ".join(orders.left_out)
    assert "growth: built on other metrics" in left_out
    assert "store_count: reads stores, which this dataset does not join" in left_out
    assert plans["customers"].metrics == []
    assert "semi-additive" in plans["customers"].left_out[0]


@pytest.fixture
def superset(monkeypatch):
    """A Superset that records requests, and the mapping table in memory."""
    state: dict = {"calls": [], "datasets": {}, "rows": {}, "next": 40, "refuse": None}

    class FakeBi:
        async def nova_database_id(self):
            return 1

        async def request(self, method, path, *, body=None, headers=None):
            state["calls"].append((method, path, body, headers))
            if state["refuse"]:
                raise state["refuse"]
            if method == "POST" and path == "/api/v1/dataset/":
                state["next"] += 1
                state["datasets"][state["next"]] = {
                    "columns": [
                        {"id": 1, "column_name": "ordered_at"},
                        {"id": 2, "column_name": "__orders__discount"},
                    ],
                    "metrics": [{"id": 7, "metric_name": "revenue"}],
                }
                return {"id": state["next"]}
            dataset_id = int(path.split("/")[4].split("?")[0])
            if dataset_id not in state["datasets"]:
                raise BiError("Not found", status=404)
            if method == "GET":
                return {"result": state["datasets"][dataset_id]}
            if method == "DELETE":
                state["datasets"].pop(dataset_id)
            return {}

    @asynccontextmanager
    async def for_user(user, author=True):
        state["author"] = author
        yield FakeBi()

    async def execute(sql, params=None):
        if sql.startswith("SELECT"):
            rows = [r for (view, _), r in sorted(state["rows"].items()) if view == params[0]]
            return {"rows": rows}
        if sql.startswith("INSERT"):
            view, name, bi_id, _owner, version, status, message = params
            state["rows"][(view, name)] = [name, bi_id, version, status, message, "now"]
        if sql.startswith("DELETE"):
            state["rows"].pop((params[0], params[1]), None)
        return {"affected": 1}

    monkeypatch.setattr(semantic.SupersetClient, "for_user", for_user)
    monkeypatch.setattr(semantic.db, "execute_system", execute)
    monkeypatch.setattr(semantic, "write_audit_log", AsyncMock())
    monkeypatch.setattr(semantic.settings, "SUPERSET_SERVICE_TOKEN", "service-secret")
    return state


VIEW = {
    "id": "v1",
    "name": "Sales",
    "owner_name": "alice",
    "catalog_name": "default_catalog",
    "database_name": "sales",
    "active_version": 3,
}
USER = {"username": "alice", "session_id": "s1", "active_role": "analyst"}


@pytest.mark.asyncio
async def test_publishing_creates_marked_datasets_and_a_new_version_updates_them(superset):
    synced = await semantic.sync_semantic_view(USER, VIEW, _model())

    by_name = {item["dataset"]: item for item in synced}
    assert by_name["orders"]["status"] == "SYNCED"
    assert "growth" in by_name["orders"]["message"]
    orders_id = by_name["orders"]["bi_dataset_id"]
    created = [c for c in superset["calls"] if c[0] == "POST"]
    assert (
        created[0][2]["schema"] == "sales"
        and created[0][2]["table_name"] == "semantic_sales_orders"
    )
    final = [
        c for c in superset["calls"] if c[0] == "PUT" and c[1] == f"/api/v1/dataset/{orders_id}"
    ][-1]
    body, headers = final[2], final[3]
    assert headers == {semantic.SYNC_HEADER: "service-secret"}
    assert json.loads(body["extra"])["nova_semantic_view"] == {
        "id": "v1", "name": "Sales", "version": 3, "dataset": "orders"
    }  # fmt: skip
    assert body["main_dttm_col"] == "ordered_at"
    revenue = next(m for m in body["metrics"] if m["metric_name"] == "revenue")
    assert revenue["id"] == 7  # the existing metric is updated, not duplicated
    columns = {c["column_name"]: c for c in body["columns"]}
    assert columns["ordered_at"]["is_dttm"] and not columns["__orders__discount"]["groupby"]

    # Version 4 drops the customers fact: its dataset goes, orders is updated in place.
    superset["calls"].clear()
    metrics = (SemanticMetricIR("revenue", "SUM(amount)", "orders"),)
    resynced = await semantic.sync_semantic_view(
        USER, {**VIEW, "active_version": 4}, _model(metrics)
    )
    assert [item["dataset"] for item in resynced] == ["orders"]
    assert resynced[0]["bi_dataset_id"] == orders_id and resynced[0]["version"] == 4
    assert not [c for c in superset["calls"] if c[0] == "POST"]
    assert by_name["customers"]["bi_dataset_id"] not in superset["datasets"]


@pytest.mark.asyncio
async def test_an_inactive_view_has_no_datasets(superset):
    await semantic.sync_semantic_view(USER, VIEW, _model())
    assert await semantic.sync_semantic_view(USER, VIEW, None) == []
    assert superset["datasets"] == {} and superset["author"] is False
    # Nothing synced and nothing to remove: BI is not called at all.
    superset["calls"].clear()
    assert await semantic.sync_semantic_view(USER, {**VIEW, "id": "v2"}, None) == []
    assert superset["calls"] == []


@pytest.mark.asyncio
async def test_a_refused_publisher_is_recorded_not_raised(superset):
    superset["refuse"] = BiError("Your active role cannot author BI content", status=403)

    synced = await semantic.sync_semantic_view(USER, VIEW, _model())

    assert {item["status"] for item in synced} == {"FAILED"}
    assert "cannot author" in synced[0]["message"]
