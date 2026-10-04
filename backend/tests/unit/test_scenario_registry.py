"""Registered scenarios preserve unit economics and reject executable schemas."""

from typing import Literal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import Field, ValidationError

from app.modules.intelligence import scenarios
from app.modules.intelligence.contracts import Contract, fingerprint
from app.modules.ml_engine.analysis import Simulation, simulate
from app.modules.ml_engine.decision_lab import SimulationInput, run_numerical
from tests.unit.test_intelligence_decisions import program as _decision_program
from tests.unit.test_intelligence_engine import USER

decision_program = _decision_program

PARAMETERS = {
    "action_type": "inventory_transfer",
    "baseline_units": 60,
    "price": 1,
    "unit_cost": 0.2,
    "expected_unit_change": 40,
    "unit_change_uncertainty": 10,
    "action_cost": 5,
    "capacity": 120,
    "max_budget": 10,
}


def test_approved_schema_is_flat_bounded_and_defensively_copied():
    definition = scenarios.scenario_definition("unit-economics", 1)
    shared, option = definition.shared_input_schema, definition.input_schema
    assert set(shared["properties"]) | set(option["properties"]) == set(
        SimulationInput.model_fields
    )
    assert not set(shared["properties"]) & set(option["properties"])
    assert shared["additionalProperties"] is False and option["additionalProperties"] is False
    assert option["properties"]["discount"]["exclusiveMaximum"] == 1
    assert option["properties"]["action_type"]["enum"] == definition.action_types
    definition.input_schema["properties"]["discount"]["exclusiveMaximum"] = 100
    assert (
        scenarios.scenario_definition("unit-economics", 1).input_schema["properties"]["discount"][
            "exclusiveMaximum"
        ]
        == 1
    )


@pytest.mark.parametrize("kind,version", [("python", 1), ("unit-economics", 2)])
def test_unregistered_adapters_rejected(kind, version):
    with pytest.raises(HTTPException) as error:
        scenarios.normalize_scenario(kind, version, PARAMETERS)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "change", [{"formula": "eval(input)"}, {"discount": 1}, {"price": float("nan")}, {"price": -1}]
)
def test_invalid_and_executable_parameters_rejected(change):
    with pytest.raises(ValidationError):
        scenarios.normalize_scenario("unit-economics", 1, {**PARAMETERS, **change})


@pytest.mark.asyncio
async def test_generic_request_matches_legacy_numerical_results(monkeypatch):
    result = simulate(Simulation(**PARAMETERS))
    run = AsyncMock(return_value={**result, "run_id": "numerical-run"})
    monkeypatch.setattr(scenarios, "run_simulation", run)
    output = await scenarios.run_scenario(
        scenarios.ScenarioRequest(
            operation_id="scenario-1",
            parameters=PARAMETERS,
        ),
        USER,
    )
    assert all(output[name] == value for name, value in result.items())
    assert output["scenario_kind"] == "unit-economics" and output["scenario_version"] == 1
    assert output["causal_status"] == "unknown"
    assert output["assumptions"] == SimulationInput.model_validate(PARAMETERS).model_dump()
    assert run.call_args.args[1] is USER


def test_decision_generic_and_legacy_input_normalize_to_same_compatibility_digest():
    from datetime import timedelta

    from app.modules.intelligence.contracts import Window
    from app.modules.intelligence.decisions import (
        DecisionCreate,
        OptionInput,
        decision_request_digest,
    )
    from tests.unit.test_intelligence_engine import END

    legacy = OptionInput(
        id="stock",
        description="Move inventory",
        simulation=SimulationInput.model_validate(PARAMETERS),
    )
    generic = OptionInput(id="stock", description="Move inventory", parameters=PARAMETERS)
    assert generic.simulation == legacy.simulation
    common = dict(
        operation_id="decision-1",
        title="Stock",
        investigation_id="investigation",
        outcome_window=Window(start=END + timedelta(days=1), end=END + timedelta(days=2)),
    )
    assert decision_request_digest(
        DecisionCreate(**common, options=[legacy])
    ) == decision_request_digest(DecisionCreate(**common, options=[generic]))


@pytest.mark.parametrize(
    "change", [{"price": "1"}, {"price": True}, {"discount": 1}, {"formula": "SELECT 1"}]
)
@pytest.mark.asyncio
async def test_invalid_registered_request_returns_422_before_executor(monkeypatch, change):
    run = AsyncMock()
    monkeypatch.setattr(scenarios, "run_simulation", run)
    body = scenarios.ScenarioRequest(
        operation_id="invalid-scenario", parameters={**PARAMETERS, **change}
    )
    with pytest.raises(HTTPException) as error:
        await scenarios.run_scenario(body, USER)
    assert error.value.status_code == 422
    assert error.value.detail == "Scenario parameters do not match the registered schema"
    run.assert_not_awaited()


class CapacityParameters(Contract):
    action_type: Literal["capacity_upgrade", "recommendation"]
    baseline: float = Field(ge=0)
    additional_capacity: float = Field(ge=0)
    action_cost: float = Field(ge=0)


def capacity_projection(baseline: float, additional: float) -> dict:
    return {"method": "capacity-v1", "prediction": baseline + additional}


class CapacityScenarioAdapter:
    """A test-only operational model with no currency or gross-profit contract."""

    scenario_kind = "capacity"
    version = 1
    parameter_model = CapacityParameters

    def definition(self):
        schema = self.parameter_model.model_json_schema()
        properties = schema["properties"]
        shared_names = {"baseline"}
        common = {"type": "object", "additionalProperties": False}
        return scenarios.ScenarioDefinition(
            id="capacity-v1",
            scenario_kind=self.scenario_kind,
            version=1,
            title="Capacity",
            description="Conditional order capacity under stated assumptions.",
            shared_input_schema={
                **common,
                "properties": {k: v for k, v in properties.items() if k in shared_names},
                "required": [k for k in schema["required"] if k in shared_names],
            },
            input_schema={
                **common,
                "properties": {k: v for k, v in properties.items() if k not in shared_names},
                "required": [k for k in schema["required"] if k not in shared_names],
            },
            action_types=["capacity_upgrade", "recommendation"],
            target_metric="orders",
            currency_required=False,
            constraints=["nonnegative_capacity"],
            simulation_adapter="capacity-v1",
        )

    def validate_parameters(self, parameters, context):
        value = CapacityParameters.model_validate(parameters, strict=True)
        if context.purpose == "decision" and value.baseline != context.baseline:
            raise HTTPException(
                status_code=422, detail="Capacity must reconcile to observed orders"
            )
        return value

    async def simulate(self, parameters, context, user, *, operation_id):
        result = await run_numerical(
            user,
            operation_id=operation_id,
            method="capacity-v1",
            parameters=parameters.model_dump(mode="json"),
            function=capacity_projection,
            arguments=(parameters.baseline, parameters.additional_capacity),
        )
        return scenarios.ScenarioExecution(
            scenario_kind="capacity",
            scenario_version=1,
            action_type=parameters.action_type,
            assumptions=parameters.model_dump(mode="json"),
            prediction=result["prediction"],
            effects={"capacity_delta": parameters.additional_capacity},
            cost=parameters.action_cost,
            risk="low",
            feasible=True,
            method="capacity-v1",
            run_id=result["run_id"],
            evidence_ids=context.evidence_ids,
        )


@pytest.fixture
def capacity_registry(monkeypatch):
    registry = scenarios.ScenarioRegistry(
        (scenarios.UnitEconomicsScenarioAdapter(), CapacityScenarioAdapter())
    )
    monkeypatch.setattr(scenarios, "scenario_registry", registry)
    return registry


@pytest.mark.asyncio
async def test_second_adapter_persists_without_decision_core_currency_or_profit_dependencies(
    decision_program, capacity_registry, monkeypatch
):
    from app.modules.intelligence import decisions

    service, repo, source, _, body = decision_program
    original = source._readable_version

    async def version(*args):
        view, row = await original(*args)
        row["definition"] = {"metrics": [{"name": "orders"}]}
        return view, row

    source._readable_version = version
    repo.rows[("monitors", "monitor")].value_column = "orders"
    investigation = repo.rows[("investigations", body.investigation_id)]
    baseline = repo.rows[("news", investigation.news_id)].after

    async def numerical(user, **kwargs):
        assert user is USER
        return {**kwargs["function"](*kwargs["arguments"]), "run_id": kwargs["operation_id"]}

    monkeypatch.setattr(
        __import__(__name__, fromlist=["run_numerical"]), "run_numerical", numerical
    )
    request = decisions.DecisionCreate(
        **{
            **body.model_dump(exclude={"options"}),
            "options": [
                {
                    "id": "capacity",
                    "description": "Add capacity",
                    "scenario_kind": "capacity",
                    "parameters": {
                        "action_type": "capacity_upgrade",
                        "baseline": baseline,
                        "additional_capacity": 20,
                        "action_cost": 0,
                    },
                }
            ],
        }
    )
    result = await decisions.create_decision(request, USER)
    assert result.target_metric == "orders" and result.currency is None
    option = result.options[0]
    assert option.prediction == baseline + 20
    assert option.effects == {"capacity_delta": 20}
    assert option.action_type == "capacity_upgrade" and option.incremental_gross_profit is None
    assert "incremental_gross_profit" not in option.model_dump()
    assert option.evidence_ids == [item.id for item in investigation.evidence]
    assert (await decisions.create_decision(request, USER)).id == result.id
    assert len([key for key in repo.rows if key[0] == "decisions"]) == 1
    assert (await service.lineage(result.id, USER))["decision"].options[0] == option


@pytest.mark.parametrize(
    "change",
    [
        {"action_types": ["unreviewed"]},
        {"scenario_kind": "different"},
        {"target_metric_policy": "published_metric"},
        {"target_metric": None},
        {
            "shared_input_schema": {
                "type": "object",
                "properties": {"nested": {"type": "object"}},
                "additionalProperties": False,
            }
        },
        {"input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    ],
)
def test_registry_rejects_inconsistent_reviewed_definitions(change):
    adapter = CapacityScenarioAdapter()
    original = adapter.definition()
    adapter.definition = lambda: original.model_copy(update=change)
    with pytest.raises(ValueError):
        scenarios.ScenarioRegistry((adapter,))


def test_registry_refuses_duplicate_and_mutable_registration_identity(capacity_registry):
    with pytest.raises(ValueError):
        capacity_registry.register(CapacityScenarioAdapter())
    definition = capacity_registry.definition("capacity", 1)
    definition.action_types.append("unsafe")
    assert capacity_registry.definition("capacity", 1).action_types == [
        "capacity_upgrade",
        "recommendation",
    ]


def test_integer_and_boolean_inputs_retain_types_at_request_boundary():
    class IntegerParameters(CapacityParameters):
        additional_capacity: int = Field(ge=0)
        enabled: bool = False

    adapter = CapacityScenarioAdapter()
    adapter.parameter_model = IntegerParameters
    registry = scenarios.ScenarioRegistry((adapter,))
    request = scenarios.ScenarioRequest(
        operation_id="integer-types",
        scenario_kind="capacity",
        parameters={
            "action_type": "capacity_upgrade",
            "baseline": 20,
            "additional_capacity": 2,
            "action_cost": 0,
            "enabled": False,
        },
    )
    # Use typed defaults without changing the adapter's validation responsibilities.
    adapter.validate_parameters = lambda parameters, context: IntegerParameters.model_validate(
        parameters, strict=True
    )
    value = registry.normalize("capacity", 1, request.parameters, scenarios.ScenarioContext())
    assert type(value.additional_capacity) is int and value.enabled is False
    with pytest.raises(ValidationError):
        registry.normalize(
            "capacity",
            1,
            {**request.parameters, "additional_capacity": True},
            scenarios.ScenarioContext(),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"method": "unreviewed-method"},
        {"action_type": "unreviewed-action"},
        {"prediction": float("nan")},
        {"evidence_ids": ["invented-evidence"]},
        {
            "assumptions": {
                "action_type": "capacity_upgrade",
                "baseline": 20,
                "additional_capacity": 10,
                "action_cost": True,
            }
        },
    ],
)
@pytest.mark.asyncio
async def test_registry_rejects_adapter_outputs_that_change_canonical_inputs(
    capacity_registry, monkeypatch, change
):
    parameters = {
        "action_type": "capacity_upgrade",
        "baseline": 20,
        "additional_capacity": 10,
        "action_cost": 0,
    }
    output = scenarios.ScenarioExecution(
        scenario_kind="capacity",
        scenario_version=1,
        action_type="capacity_upgrade",
        assumptions=CapacityParameters.model_validate(parameters).model_dump(mode="json"),
        prediction=30,
        cost=0,
        risk="low",
        feasible=True,
        method="capacity-v1",
        run_id="canonical-run",
    )
    monkeypatch.setattr(
        capacity_registry._adapters[("capacity", 1)],
        "simulate",
        AsyncMock(return_value=output.model_copy(update=change)),
    )
    with pytest.raises(HTTPException) as error:
        await capacity_registry.execute(
            "capacity",
            1,
            parameters,
            scenarios.ScenarioContext(),
            USER,
            operation_id="invalid-output",
        )
    assert error.value.status_code == 422


def decision_context(**changes):
    from tests.unit.test_intelligence_engine import REF, WINDOW

    return scenarios.ScenarioContext(
        **{
            "purpose": "decision",
            "agent_id": "finance",
            "investigation_id": "investigation",
            "semantic": REF,
            "target_metric": "revenue",
            "baseline": 60,
            "currency": "IDR",
            "metric_currency": "IDR",
            "outcome_window": WINDOW,
            "evidence_ids": ["canonical-evidence"],
            **changes,
        }
    )


@pytest.mark.parametrize(
    "context,parameters,reason",
    [
        ({"target_metric": "orders", "metric_currency": None}, {}, "matching published currency"),
        ({"metric_currency": "USD"}, {}, "matching published currency"),
        ({}, {"baseline_units": 59}, "reconcile"),
    ],
)
@pytest.mark.asyncio
async def test_governed_target_currency_and_reconciliation_fail_before_executor(
    monkeypatch, context, parameters, reason
):
    run = AsyncMock()
    monkeypatch.setattr(scenarios, "run_simulation", run)
    with pytest.raises(HTTPException) as error:
        await scenarios.execute_scenario(
            "unit-economics",
            1,
            {**PARAMETERS, **parameters},
            decision_context(**context),
            USER,
            operation_id="guard-scenario",
        )
    assert error.value.status_code == 422 and reason in error.value.detail
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_named_metric_adapter_still_rejects_a_different_published_target(
    capacity_registry,
):
    with pytest.raises(HTTPException) as error:
        capacity_registry.normalize(
            "capacity",
            1,
            {
                "action_type": "capacity_upgrade",
                "baseline": 20,
                "additional_capacity": 10,
                "action_cost": 0,
            },
            decision_context(target_metric="revenue", baseline=20),
        )
    assert error.value.status_code == 422 and "target metric" in error.value.detail


@pytest.mark.parametrize("authority", ["prediction", "effects", "context", "run_id"])
def test_client_prediction_authority_is_rejected(authority):
    with pytest.raises(ValidationError):
        scenarios.ScenarioRequest(
            operation_id="no-client-authority", parameters=PARAMETERS, **{authority: {}}
        )
    with pytest.raises(ValidationError):
        scenarios.normalize_scenario("unit-economics", 1, {**PARAMETERS, authority: 100})


def test_legacy_option_serialization_and_policy_digest_omit_new_defaults():
    from app.modules.access_control.business_policy import BusinessPolicy, evaluate_business_policy
    from app.modules.intelligence.contracts import DecisionOption

    payload = {
        "scenario_kind": "unit-economics",
        "scenario_version": 1,
        "id": "old-option",
        "description": "Historical option",
        "action_type": "inventory_transfer",
        "assumptions": {
            k: float(v) if isinstance(v, (int, float)) else v for k, v in PARAMETERS.items()
        },
        "prediction": 100.0,
        "lower_bound": 90.0,
        "upper_bound": 110.0,
        "cost": 5.0,
        "incremental_gross_profit": 32.0,
        "risk": "medium",
        "feasible": True,
        "method": "conditional-unit-economics-v1",
        "run_id": "old-run",
        "evidence_ids": ["old-evidence"],
    }
    option = DecisionOption.model_validate(payload)
    policy, context = BusinessPolicy(), {"decision_id": "old-decision"}
    expected = fingerprint(
        {"context": context, "option": payload, "policy": policy.model_dump(mode="json")}
    )
    assert option.model_dump(mode="json") == payload
    assert evaluate_business_policy(option, policy, context).context_digest == expected
