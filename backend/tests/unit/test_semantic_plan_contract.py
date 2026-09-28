"""Generated plans and verified SQL must preserve declared semantics."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic.plan_contract import semantic_plan_schema, validate_generated_plan
from app.modules.agents.semantic.planning import SemanticPlanError
from app.modules.agents.semantic.verification import verify_sql_compatibility
from tests.unit.test_semantic_intelligence import sales_model


def _plan():
    return {
        "metrics": ["total_revenue"],
        "dimensions": [],
        "filters": [],
        "named_filters": [],
        "time": None,
        "order_by": [],
        "limit": None,
        "unresolved_concepts": [],
    }


def test_generated_contract_accepts_nullable_fields_and_explicit_unresolved():
    value = _plan()
    value["unresolved_concepts"] = [
        {"text": "Enterprise", "type_hint": "literal", "material": True}
    ]
    validate_generated_plan(value)
    schema = semantic_plan_schema()
    assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.parametrize(
    "patch",
    [
        {"filters": ["region=West"]},
        {"filters": [{"field": "region", "operator": "=", "value": ["West"]}]},
        {"filters": [{"field": "region", "operator": "IN", "value": []}]},
        {"time": {"dimension": "order_date", "range": "current_month"}},
        {
            "time": {
                "dimension": "order_date",
                "range": "current_month",
                "grain": None,
                "compare": "guess",
            }
        },
        {"order_by": [{"field": "total_revenue", "direction": "ascending"}]},
        {"limit": True},
        {"limit": 1001},
        {"metrics": "total_revenue"},
        {"sql": "SELECT 1"},
        {"unresolved_concepts": [{"text": "", "type_hint": "literal", "material": True}]},
    ],
)
def test_generated_contract_rejects_silently_coercible_or_dropped_fields(patch):
    value = deepcopy(_plan())
    value.update(patch)
    with pytest.raises(SemanticPlanError):
        validate_generated_plan(value)


def test_verified_sql_allows_alias_quoting_case_and_whitespace_variations():
    verify_sql_compatibility(
        "SELECT SUM(`orders`.`amount`) AS `total` FROM `analytics`.`orders` AS orders "
        "WHERE orders.region = 'West'",
        "select sum(o.amount) as revenue from analytics.orders o where o.region='West'",
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(o.amount) FROM analytics.orders o WHERE o.region='West'",
        "SELECT SUM(o.amount) FROM analytics.orders o",
        "SELECT SUM(o.amount) FROM analytics.orders o WHERE o.region='East'",
        "SELECT SUM(o.amount) FROM payroll.orders o WHERE o.region='West'",
        "SELECT SUM(o.amount) FROM analytics.orders o WHERE o.region='West' LIMIT 1",
        "EXPLAIN SELECT SUM(o.amount) FROM analytics.orders o WHERE o.region='West'",
    ],
)
def test_verified_sql_rejects_obvious_semantic_contradictions(sql):
    expected = "SELECT SUM(o.amount) FROM analytics.orders o WHERE o.region='West'"
    with pytest.raises(SemanticPlanError):
        verify_sql_compatibility(expected, sql)


def test_verified_sql_checks_join_type_and_condition():
    expected = "SELECT SUM(o.amount) FROM orders o LEFT JOIN regions r ON o.region_id=r.id"
    for changed in (expected.replace("LEFT", "INNER"), expected.replace("r.id", "r.wrong_id")):
        with pytest.raises(SemanticPlanError):
            verify_sql_compatibility(expected, changed)
















def _verified_query_view(monkeypatch):
    from app.modules.agents.semantic.runtime import semantic_ir_to_definition
    from app.modules.intelligence.semantic_views import SemanticViewService

    service = SemanticViewService()
    view = {"id": "view-1", "name": "sales", "owner_name": "alice"}
    definition = semantic_ir_to_definition(sales_model())
    user = {"username": "alice", "encrypted_password": "encrypted"}
    insert_version = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=view))
    monkeypatch.setattr(
        service,
        "_readable_version",
        AsyncMock(return_value=(view, {"version": 1, "definition": definition})),
    )
    monkeypatch.setattr(
        "app.modules.intelligence.semantic_views.db.execute_system",
        AsyncMock(return_value={"rows": [[1]]}),
    )
    monkeypatch.setattr(service, "_insert_version", insert_version)
    monkeypatch.setattr(service, "_audit", audit)
    monkeypatch.setattr(
        service,
        "_version",
        AsyncMock(return_value={"view_id": "view-1", "version": 2}),
    )
    return service, user, insert_version, audit


async def test_verified_query_view_rejects_contradiction_before_new_version(monkeypatch):
    from app.modules.intelligence.semantic_views import SemanticViewVerifiedQueryCreate

    service, user, insert_version, audit = _verified_query_view(monkeypatch)
    body = SemanticViewVerifiedQueryCreate(
        question="Revenue",
        semantic_plan={"metrics": ["total_revenue"]},
        verified_sql="SELECT COUNT(*) FROM analytics.sales.orders",
    )
    with pytest.raises(HTTPException) as error:
        await service.add_verified_query("view-1", 1, body, user)
    assert error.value.status_code == 422
    insert_version.assert_not_awaited()
    audit.assert_not_awaited()


async def test_verified_query_view_saves_owner_and_new_version_fingerprint(monkeypatch):
    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.intelligence.semantic_views import SemanticViewVerifiedQueryCreate

    service, user, insert_version, audit = _verified_query_view(monkeypatch)
    body = SemanticViewVerifiedQueryCreate(
        question="Revenue",
        semantic_plan={"metrics": ["total_revenue"]},
        verified_sql="SELECT SUM(o.amount) AS revenue FROM analytics.sales.orders o",
    )
    result = await service.add_verified_query("view-1", 1, body, user)
    assert result == {"view_id": "view-1", "version": 2}
    view_id, version, definition, fingerprint = insert_version.await_args.args
    assert (view_id, version) == ("view-1", 2)
    verified = definition["verified_queries"]
    assert len(verified) == 1
    assert verified[0]["verified_by"] == "alice"
    assert verified[0]["question"] == "Revenue"
    assert verified[0]["verified_sql"] == body.verified_sql
    assert fingerprint == SemanticModelIR.from_ossie(definition).fingerprint
    audit.assert_awaited_once_with("ALTER", "sales", user)


async def test_legacy_verified_query_endpoint_points_to_semantic_view_draft():
    from app.modules.agents.router import create_verified_query
    from app.modules.agents.schemas import VerifiedQueryCreateRequest

    body = VerifiedQueryCreateRequest(
        question="Revenue",
        semantic_plan={"metrics": ["total_revenue"]},
        verified_sql="SELECT SUM(o.amount) FROM analytics.sales.orders o",
    )
    with pytest.raises(HTTPException) as error:
        await create_verified_query("sales", body, {"username": "alice"})
    assert error.value.status_code == 410
    assert "Semantic View draft" in error.value.detail


@pytest.mark.parametrize("supports_schema", [True, False])
async def test_generator_shares_contract_with_provider_and_runtime(supports_schema):
    from app.modules.agents.semantic.model_planner import generate_plan

    provider = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities=SimpleNamespace(
                    supports_json_schema=supports_schema,
                )
            )
        ),
        complete=AsyncMock(return_value={"content": json.dumps(_plan())}),
    )
    plan = await generate_plan(provider, sales_model(), {}, "Revenue", SimpleNamespace())
    assert plan.metrics == ("total_revenue",)
    kwargs = provider.complete.call_args.kwargs
    if supports_schema:
        assert kwargs["response_format"]["json_schema"]["strict"] is True
        assert kwargs["response_format"]["json_schema"]["schema"] == semantic_plan_schema()
    else:
        assert "response_format" not in kwargs
    assert json.loads(kwargs["messages"][1]["content"])["response_schema"] == semantic_plan_schema()
    provider.complete.return_value = {"content": '{"metrics":["total_revenue"],"sql":"SELECT 1"}'}
    with pytest.raises(SemanticPlanError, match="violates its schema"):
        await generate_plan(provider, sales_model(), {}, "Revenue", SimpleNamespace())
