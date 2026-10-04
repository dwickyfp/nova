"""Contextual discovery and execution share exact authorized canonical inputs."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_current_user
from app.modules.intelligence import decisions, engine_router, scenarios
from app.modules.intelligence.contracts import Scope, fingerprint
from tests.unit.test_intelligence_decisions import (
    pinned_mission,
)
from tests.unit.test_intelligence_decisions import (
    program as _program,
)
from tests.unit.test_intelligence_engine import USER
from tests.unit.test_scenario_registry import (
    CapacityScenarioAdapter,
    decision_context,
)
from tests.unit.test_scenario_registry import (
    capacity_registry as _capacity_registry,
)

program = _program
capacity_registry = _capacity_registry


def published_metric(program, metric):
    service, repo, source, _, body = program
    original = source._readable_version

    async def version(*args):
        view, row = await original(*args)
        row["definition"] = {"metrics": [metric]}
        return view, row

    source._readable_version = version
    repo.rows[("monitors", "monitor")].value_column = metric["name"]
    return service, repo, source, body


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(engine_router.router, prefix="/intelligence")
    app.dependency_overrides[get_current_user] = lambda: USER
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://nova") as client:
        yield client


@pytest.mark.asyncio
async def test_no_context_catalog_keeps_its_original_shape(client):
    response = await client.get("/intelligence/scenarios")
    assert response.status_code == 200
    assert set(response.json()) == {"items"}
    assert response.json()["items"] == [
        item.model_dump(mode="json") for item in scenarios.scenario_definitions()
    ]
    assert not {"required_currency", "required_unit", "currency_input_allowed"} & set(
        response.json()["items"][0]
    )


@pytest.mark.parametrize(
    "params",
    [
        {"investigation_id": "investigation"},
        {"investigation_revision": 1},
        {"mission_id": "mission"},
        {"investigation_id": "investigation", "investigation_revision": 0},
    ],
)
@pytest.mark.asyncio
async def test_partial_or_invalid_context_is_not_a_catalog_fallback(client, params):
    assert (await client.get("/intelligence/scenarios", params=params)).status_code == 422


@pytest.mark.asyncio
async def test_discovery_resolves_usd_and_unit_without_running_an_adapter(
    program,
    capacity_registry,
    client,
    monkeypatch,
):
    _, repo, source, body = published_metric(
        program, {"name": "revenue", "currency": "USD", "unit": "currency"}
    )
    investigation = repo.rows[("investigations", body.investigation_id)]
    run = AsyncMock()
    monkeypatch.setattr(scenarios, "run_simulation", run)
    response = await client.get(
        "/intelligence/scenarios",
        params={
            "investigation_id": investigation.id,
            "investigation_revision": investigation.revision,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert [item["scenario_kind"] for item in payload["items"]] == ["unit-economics"]
    available, excluded = payload["compatibility"]
    assert available["resolved"] == {
        "target_metric": "revenue",
        "currency": "USD",
        "unit": "currency",
        "currency_readonly": True,
        "currency_input_allowed": False,
    }
    assert "price" in available["required_inputs"]
    assert excluded["reason_codes"] == ["TARGET_METRIC_UNSUPPORTED"]
    assert all(call[1] == USER for call in source.calls)
    assert not {"scope", "semantic_plan", "session_id", "evidence_ids", "agent_id"} & set(
        available["resolved"]
    )
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_currency_is_unavailable_and_not_idr(program, client):
    _, repo, _, body = published_metric(program, {"name": "revenue", "unit": "currency"})
    investigation = repo.rows[("investigations", body.investigation_id)]
    response = await client.get(
        "/intelligence/scenarios",
        params={
            "investigation_id": investigation.id,
            "investigation_revision": investigation.revision,
        },
    )
    assert response.status_code == 200 and response.json()["items"] == []
    result = response.json()["compatibility"][0]
    assert result["reason_codes"] == ["METRIC_CURRENCY_REQUIRED"]
    assert result["resolved"]["currency"] is None
    assert "currency" in result["required_inputs"]
    with pytest.raises(HTTPException):
        await decisions.create_decision(body, USER)
    assert not any(kind == "decisions" for kind, _ in repo.rows)


@pytest.mark.asyncio
async def test_omitted_currency_executes_in_published_usd_and_mismatch_never_runs(
    program,
    monkeypatch,
):
    _, _, _, body = published_metric(program, {"name": "revenue", "currency": "USD"})
    assert body.currency is None
    decision = await decisions.create_decision(body, USER)
    assert decision.currency == "USD"
    assert (await decisions.create_decision(body, USER)).id == decision.id
    run = AsyncMock()
    monkeypatch.setattr(scenarios, "run_simulation", run)
    mismatch = body.model_copy(update={"operation_id": "mismatch-currency", "currency": "IDR"})
    with pytest.raises(HTTPException) as error:
        await decisions.create_decision(mismatch, USER)
    assert error.value.status_code == 422
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonmonetary_context_keeps_capacity_available(program, capacity_registry):
    _, repo, _, body = published_metric(program, {"name": "orders", "unit": "units"})
    investigation = repo.rows[("investigations", body.investigation_id)]
    result = await decisions.discover_scenarios(investigation.id, investigation.revision, USER)
    assert [item.scenario_kind for item in result["items"]] == ["capacity"]
    assert result["compatibility"][1].resolved.currency is None
    assert result["compatibility"][1].resolved.unit == "units"
    assert result["compatibility"][0].reason_codes == [
        "METRIC_CURRENCY_REQUIRED",
        "METRIC_UNIT_UNSUPPORTED",
    ]


@pytest.mark.parametrize("additivity", ["non_additive", "semi_additive"])
@pytest.mark.asyncio
async def test_unit_economics_never_reinterprets_nonadditive_metrics(program, additivity):
    _, repo, _, body = published_metric(
        program,
        {
            "name": "revenue",
            "currency": "USD",
            "unit": "currency",
            "additivity": additivity,
        },
    )
    investigation = repo.rows[("investigations", body.investigation_id)]
    result = await decisions.discover_scenarios(investigation.id, investigation.revision, USER)
    assert result["items"] == []
    assert result["compatibility"][0].reason_codes == ["METRIC_ADDITIVITY_UNSUPPORTED"]
    with pytest.raises(HTTPException) as error:
        await decisions.create_decision(body, USER)
    assert error.value.status_code == 422


def test_reviewed_requirements_reject_currency_unit_and_missing_evidence():
    class Reviewed(CapacityScenarioAdapter):
        def definition(self):
            return (
                super()
                .definition()
                .model_copy(
                    update={
                        "required_currency": "USD",
                        "required_unit": "units",
                        "required_evidence_types": ["query"],
                    }
                )
            )

    registry = scenarios.ScenarioRegistry((Reviewed(),))
    result = registry.compatibility(
        "capacity",
        1,
        decision_context(
            target_metric="orders",
            metric_unit="percent",
            evidence_types=[],
        ),
    )
    assert result.reason_codes == [
        "METRIC_CURRENCY_UNSUPPORTED",
        "METRIC_UNIT_UNSUPPORTED",
        "EVIDENCE_REQUIRED",
    ]
    assert result.resolved.currency == "USD" and result.resolved.unit == "units"
    result = registry.compatibility(
        "capacity",
        1,
        decision_context(
            target_metric="orders",
            currency="USD",
            metric_currency="USD",
            metric_unit="units",
            evidence_types=["query"],
        ),
    )
    assert result.compatible and result.resolved.currency_readonly


@pytest.mark.asyncio
async def test_currency_input_is_allowed_only_by_reviewed_adapter(
    program,
    monkeypatch,
):
    class InputCurrency(CapacityScenarioAdapter):
        def definition(self):
            return (
                super()
                .definition()
                .model_copy(
                    update={
                        "currency_required": True,
                        "currency_input_allowed": True,
                    }
                )
            )

    registry = scenarios.ScenarioRegistry((InputCurrency(),))
    monkeypatch.setattr(scenarios, "scenario_registry", registry)
    _, repo, _, body = published_metric(program, {"name": "orders", "unit": "units"})
    investigation = repo.rows[("investigations", body.investigation_id)]
    result = await decisions.discover_scenarios(investigation.id, investigation.revision, USER)
    assert result["compatibility"][0].compatible
    assert result["compatibility"][0].resolved.currency is None
    assert result["compatibility"][0].resolved.currency_input_allowed
    request = decisions.DecisionCreate(
        **{
            **body.model_dump(exclude={"options"}),
            "currency": "EUR",
            "options": [
                {
                    "id": "capacity",
                    "description": "Reviewed capacity option",
                    "scenario_kind": "capacity",
                    "parameters": {
                        "action_type": "recommendation",
                        "baseline": 60,
                        "additional_capacity": 0,
                        "action_cost": 0,
                    },
                }
            ],
        }
    )
    adapter = registry._adapters[("capacity", 1)]
    simulate = AsyncMock(wraps=adapter.simulate)
    monkeypatch.setattr(adapter, "simulate", simulate)
    from tests.unit import test_scenario_registry

    async def numerical(user, **kwargs):
        return {**kwargs["function"](*kwargs["arguments"]), "run_id": kwargs["operation_id"]}

    monkeypatch.setattr(test_scenario_registry, "run_numerical", numerical)
    saved = await decisions.create_decision(request, USER)
    assert saved.currency == "EUR"
    assert simulate.call_args.args[1].currency == "EUR"


@pytest.mark.asyncio
async def test_discovery_rechecks_revocation_and_exact_revisions(program, client):
    _, repo, source, _, body = program
    investigation = repo.rows[("investigations", body.investigation_id)]
    params = {
        "investigation_id": investigation.id,
        "investigation_revision": investigation.revision,
    }
    assert (await client.get("/intelligence/scenarios", params=params)).status_code == 200
    assert (
        await client.get(
            "/intelligence/scenarios",
            params={
                **params,
                "investigation_revision": investigation.revision + 1,
            },
        )
    ).status_code == 404
    source.revoked = True
    assert (await client.get("/intelligence/scenarios", params=params)).status_code == 404


@pytest.mark.asyncio
async def test_discovery_rejects_another_owner_and_stale_active_semantic(program):
    _, repo, source, _, body = program
    investigation = repo.rows[("investigations", body.investigation_id)]
    with pytest.raises(HTTPException) as denied:
        await decisions.discover_scenarios(
            investigation.id, investigation.revision, {**USER, "username": "bob"}
        )
    assert denied.value.status_code == 404
    source.active_version += 1
    with pytest.raises(HTTPException) as stale:
        await decisions.discover_scenarios(investigation.id, investigation.revision, USER)
    assert stale.value.status_code == 409


@pytest.mark.asyncio
async def test_discovery_pins_news_and_monitor_even_after_current_revisions_change(program):
    _, repo, _, _, body = program
    investigation = repo.rows[("investigations", body.investigation_id)]
    news = repo.rows[("news", investigation.news_id)]
    monitor = repo.rows[("monitors", news.monitor_id)]
    await repo.save("monitors", monitor)
    await repo.save(
        "monitors",
        monitor.model_copy(update={"value_column": "orders"}),
        expected_revision=monitor.revision,
    )
    await repo.save(
        "news", news.model_copy(update={"after": 12345}), expected_revision=news.revision
    )
    context, _, pinned_news = await decisions.resolve_scenario_context(
        investigation.id,
        USER,
        investigation_revision=investigation.revision,
    )
    assert context.target_metric == "revenue" and context.baseline == news.after
    assert pinned_news.revision == investigation.news_revision


@pytest.mark.asyncio
async def test_resumed_discovery_uses_pinned_dependencies_and_current_credentials(
    program,
    monkeypatch,
):
    from app.modules.agents import mission, router

    service, repo, source, _, body = program
    original = repo.rows[("investigations", body.investigation_id)]
    caller = {**USER, "session_id": "new-session", "security_context_version": 2}
    bound = pinned_mission(original, "investigation", Scope.from_user(caller))
    monkeypatch.setattr(mission.settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    monkeypatch.setattr(mission.mission_service, "get", AsyncMock(return_value=bound))
    monkeypatch.setattr(mission.mission_service, "_get_owner", AsyncMock(return_value=bound))
    monkeypatch.setattr(mission, "require_thread", AsyncMock())
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock())
    news = repo.rows[("news", original.news_id)]
    await repo.save(
        "news",
        news.model_copy(update={"after": 9999}),
        expected_revision=news.revision,
    )
    context, investigation, pinned_news = await decisions.resolve_scenario_context(
        original.id,
        caller,
        investigation_revision=original.revision,
        mission_id=bound.mission_id,
    )
    assert investigation.revision == original.revision
    assert pinned_news.revision == original.news_revision and context.baseline != 9999
    assert all(call[1]["session_id"] == "new-session" for call in source.calls[-2:])
    assert (
        await decisions.discover_scenarios(
            original.id,
            original.revision,
            caller,
            mission_id=bound.mission_id,
        )
    )["items"]
    with pytest.raises(HTTPException):
        await decisions.discover_scenarios(
            original.id,
            original.revision + 1,
            caller,
            mission_id=bound.mission_id,
        )
    source.revoked = True
    with pytest.raises(HTTPException):
        await decisions.discover_scenarios(
            original.id,
            original.revision,
            caller,
            mission_id=bound.mission_id,
        )


@pytest.mark.asyncio
async def test_historical_implicit_currency_hash_recovers_only_the_same_operation(program):
    service, _, _, _, body = program
    legacy = body.model_copy(update={"currency": "IDR"})
    saved = await decisions.create_decision(legacy, USER)
    assert (await decisions.create_decision(body, USER)).id == saved.id
    with pytest.raises(HTTPException) as error:
        await decisions.create_decision(body.model_copy(update={"title": "Changed inputs"}), USER)
    assert error.value.status_code == 409
    assert saved.request_digest == decisions.decision_request_digest(legacy)
    assert saved.id == fingerprint([USER["username"], USER["active_role"], body.operation_id])
    await service.authorize_record(saved, USER, decisions.CycleBudget())
