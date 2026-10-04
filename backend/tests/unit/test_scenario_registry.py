"""Registered scenarios preserve unit economics and reject executable schemas."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.intelligence import scenarios
from app.modules.ml_engine.analysis import Simulation, simulate
from app.modules.ml_engine.decision_lab import SimulationInput
from tests.unit.test_intelligence_engine import USER

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
