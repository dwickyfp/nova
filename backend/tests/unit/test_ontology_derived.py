"""The derived ontology layer Nova compiles from published Semantic Views."""

from __future__ import annotations

import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.ontology import representations
from app.modules.ontology import router as router_module
from app.modules.ontology.contracts import Actor
from app.modules.ontology.derived import (
    DerivedItem,
    compile_view,
    metric_components,
    role_name,
    singular,
    visible_layer,
)
from app.modules.ontology.service import OntologyService
from app.modules.ontology.stewardship import StewardshipService
from tests.benchmark.retail_field.views import VIEWS
from tests.unit._ontology import ADMIN, ANALYST, FakeCatalog, FakeRepository

ACTOR = Actor.from_user(ADMIN)


def lab_catalog(*names: str) -> FakeCatalog:
    catalog = FakeCatalog()
    catalog.unpublish("sales_sv")
    for name in names or tuple(VIEWS):
        catalog.publish(name, parse_ossie(json.dumps(VIEWS[name])).model)
    return catalog


async def compile_all(catalog: FakeCatalog, repository: FakeRepository) -> list:
    return [
        await compile_view(await catalog.published(view_id), ACTOR, repository)
        for view_id in sorted(catalog.views)
    ]


def items(repository: FakeRepository) -> dict[str, DerivedItem]:
    return {
        item.key: item for records in repository.rows.get("derived", {}).values()
        for item in records[-1:] if item.status == "ACTIVE"
    }


def relations(found: dict[str, DerivedItem], kind: str) -> set[tuple[str, str, str | None]]:
    def tail(key: str) -> str:
        return key.split(":", 1)[1].split(".")[-1]

    return {
        (tail(item.source_key or ""), tail(item.target_key or ""), item.role)
        for item in found.values()
        if item.item_type == "relation" and item.relation_type and item.relation_type.value == kind
    }


def test_naming_rules():
    assert singular("Outlets") == "Outlet"
    assert singular("Route stop items") == "Route stop item"
    assert singular("Categories") == "Category"
    assert singular("Status") == "Status"
    assert role_name("salesman_id") == "salesman"
    assert role_name("driver_id") == "driver"


@pytest.mark.parametrize(
    "expression,own,parts",
    [
        ("SUM(t.a)", "sum(t.a)", []),
        ("SUM(t.a) / NULLIF(SUM(t.b), 0)", None, ["sum(t.a)", "sum(t.b)"]),
        ("SUM(t.a - t.b * t.c)", "sum(t.a-t.b*t.c)", ["sum(t.a)", "sum(t.b*t.c)"]),
        ("AVG(t.a - t.b)", "avg(t.a-t.b)", []),
        ("COUNT(DISTINCT t.a)", "count(distinctt.a)", []),
        ("not sql at all (", None, []),
    ],
)
def test_metric_components_only_decompose_what_is_proven(expression, own, parts):
    assert metric_components(expression) == (own, parts)


async def test_three_lab_views_give_a_traversable_graph_without_human_input():
    catalog, repository = lab_catalog(), FakeRepository()
    compilations = await compile_all(catalog, repository)
    found = items(repository)

    entities = {
        item.label for item in found.values()
        if item.concept_kind and item.concept_kind.value == "BUSINESS_ENTITY"
    }
    assert {"Outlet", "Product", "Employee", "Sales order", "Route plan"} <= entities
    # One Outlet entity, proven by all three views.
    outlet = found["entity:nova_retail_field.outlets"]
    assert {e.semantic.view_id for e in outlet.evidence} == set(VIEWS)
    assert outlet.confidence == 1.0

    related = relations(found, "RELATED_TO")
    assert ("sales_orders", "employees", "salesman") in related
    assert ("route_plans", "employees", "driver") in related
    assert ("sales_orders", "outlets", None) in related

    derives = relations(found, "DERIVES")
    assert {("net_revenue", "sales_before_returns", None),
            ("net_revenue", "returned_value", None),
            ("return_rate", "returned_units", None),
            ("return_rate", "units_sold", None)} <= derives
    # COUNT(*) of routes is not COUNT(*) of stops.
    assert ("avg_distance_gap_km", "route_count", None) in derives
    assert ("avg_distance_gap_km", "stop_count", None) not in derives

    dimensions = {
        item.label for item in found.values()
        if item.concept_kind and item.concept_kind.value == "DIMENSION"
    }
    assert {"City", "District", "Product name", "Order date"} <= dimensions
    # Keys and amounts are not breakdowns.
    assert not {"Outlet id", "Salesman id", "Net amount", "Quantity"} & dimensions
    assert all(item.counts["entities"] for item in compilations)


async def test_recompiling_is_idempotent_and_a_dropped_dataset_is_retired():
    catalog, repository = lab_catalog(), FakeRepository()
    await compile_all(catalog, repository)
    again = await compile_all(catalog, repository)
    assert all(item.counts["changed"] == 0 for item in again)

    trimmed = copy.deepcopy(catalog.views["retail_sales"].definition)
    trimmed["datasets"] = [d for d in trimmed["datasets"] if d["name"] != "employees"]
    trimmed["relationships"] = [
        r for r in trimmed["relationships"] if r.get("to") != "employees"
        and "employees" not in json.dumps(r)
    ]
    catalog.publish("retail_sales", trimmed)
    await compile_view(await catalog.published("retail_sales"), ACTOR, repository)
    found = items(repository)
    # Employee is still proven by delivery_routes; the salesman role is gone.
    employee = found["entity:nova_retail_field.employees"]
    assert {e.semantic.view_id for e in employee.evidence} == {"delivery_routes"}
    assert ("sales_orders", "employees", "salesman") not in relations(found, "RELATED_TO")


async def test_the_same_metric_in_two_views_is_equivalent():
    catalog, repository = lab_catalog("outlet_stock"), FakeRepository()
    catalog.publish("outlet_stock_copy", catalog.views["outlet_stock"].definition)
    await compile_all(catalog, repository)
    equivalent = relations(items(repository), "EQUIVALENT_TO")
    assert ("sell_out_units", "sell_out_units", None) in equivalent

    catalog.unpublish("outlet_stock_copy")
    trimmed = copy.deepcopy(catalog.views["outlet_stock"].definition)
    catalog.publish("outlet_stock_copy", {**trimmed, "metrics": []})
    await compile_view(await catalog.published("outlet_stock_copy"), ACTOR, repository)
    assert not relations(items(repository), "EQUIVALENT_TO")


async def test_readers_see_only_evidence_from_views_they_can_see():
    catalog, repository = lab_catalog(), FakeRepository()
    await compile_all(catalog, repository)
    catalog.hidden["delivery_routes"] = {ANALYST["username"]}

    layer = await visible_layer(ANALYST, catalog, repository)
    shown = {item["key"]: item for item in layer["items"]}
    assert "metric:delivery_routes.avg_distance_gap_km" not in shown
    employee = shown["entity:nova_retail_field.employees"]
    assert {e["semantic"]["view_id"] for e in employee["evidence"]} == {"retail_sales"}
    assert employee["roles"] == ["salesman"]
    name = shown["dimension:nova_retail_field.employees.employee_name"]
    assert "driver" not in json.dumps(name).casefold()  # text from the hidden view
    assert "delivery_routes" not in {c["view_id"] for c in layer["compilations"]}
    for item in shown.values():
        if item["item_type"] == "relation":
            assert item["source_key"] in shown and item["target_key"] in shown

    layer = await visible_layer(ADMIN, catalog, repository)
    admin = {item["key"]: item for item in layer["items"]}
    assert admin["entity:nova_retail_field.employees"]["roles"] == ["driver", "salesman"]


async def test_publishing_a_view_compiles_it(monkeypatch):
    calls = []

    async def compile_after_publish(view_id, user):
        calls.append(view_id)

    async def refresh_view(view_id, actor, catalog=None, repository=None):
        return {"repinned": 0, "stale": 0}

    monkeypatch.setattr(representations, "refresh_view", refresh_view)
    monkeypatch.setattr(
        "app.modules.ontology.derived.compile_after_publish", compile_after_publish
    )
    await representations.refresh_after_publish("outlet_stock", ADMIN)
    assert calls == ["outlet_stock"]


@pytest.fixture
def client(monkeypatch):
    repository, catalog = FakeRepository(), lab_catalog()
    monkeypatch.setattr(router_module, "ontology_service", OntologyService(repository, catalog))
    monkeypatch.setattr(
        router_module, "stewardship_service", StewardshipService(repository, catalog)
    )
    audits = []

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr("app.modules.ontology.access.write_audit_log", audit)
    app = FastAPI()
    app.include_router(router_module.router, prefix="/ontology")
    state = {"user": ADMIN}
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    return TestClient(app), catalog, state, audits


def test_compile_and_read_routes(client):
    http, catalog, state, audits = client
    assert http.get("/ontology/derived").json()["items"] == []
    response = http.post("/ontology/derived/compile", json={})
    assert response.status_code == 200
    assert {item["view_id"] for item in response.json()["compilations"]} == set(VIEWS)
    assert audits[-1]["action"] == "DERIVE"
    layer = http.get("/ontology/derived").json()
    assert any(item["label"] == "Outlet" for item in layer["items"])

    catalog.hidden["retail_sales"] = {ANALYST["username"]}
    state["user"] = ANALYST
    refused = http.post("/ontology/derived/compile", json={"view_id": "retail_sales"})
    assert refused.status_code == 404
