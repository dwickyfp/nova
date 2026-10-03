"""Reviewed scenario definitions backed by the existing numerical executor."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import Field, ValidationError

from app.modules.intelligence.contracts import Contract
from app.modules.ml_engine.decision_lab import SimulationInput, run_simulation


class ScenarioDefinition(Contract):
    id: str
    scenario_kind: str
    version: int = Field(ge=1)
    title: str
    description: str
    input_schema: dict[str, Any]
    shared_input_schema: dict[str, Any]
    action_types: list[str]
    target_metric: Literal["revenue"]
    currency_required: bool = True
    constraints: list[str]
    simulation_adapter: str


class ScenarioRequest(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    scenario_kind: str = Field(default="unit-economics", max_length=64)
    scenario_version: int = Field(default=1, ge=1)
    parameters: dict[str, float | str | bool] = Field(max_length=32)


def _unit_schema(names: list[str]) -> dict:
    source = SimulationInput.model_json_schema()
    units = {
        "baseline_units": "units",
        "expected_unit_change": "units",
        "unit_change_uncertainty": "units",
        "capacity": "units",
        "discount": "fraction",
        "price": "currency / unit",
        "unit_cost": "currency / unit",
        "action_cost": "currency",
        "max_budget": "currency",
    }
    properties = {}
    for name in names:
        field = dict(source["properties"][name])
        field["title"] = name.replace("_", " ").capitalize()
        if name in units:
            field["unit"] = units[name]
        if name == "unit_change_uncertainty":
            field["description"] = "Assumed uncertainty in unit change; this is a scenario input."
        properties[name] = field
    return {
        "type": "object",
        "properties": properties,
        "required": [name for name in names if name in source["required"]],
        "additionalProperties": False,
    }


_SHARED = ["baseline_units", "price", "unit_cost", "capacity", "max_budget"]
_OPTION = [
    "action_type",
    "expected_unit_change",
    "unit_change_uncertainty",
    "action_cost",
    "discount",
]
_UNIT_ECONOMICS = ScenarioDefinition(
    id="unit-economics-v1",
    scenario_kind="unit-economics",
    version=1,
    title="Unit economics",
    description="Compare conditional revenue and gross profit using explicit assumptions.",
    input_schema=_unit_schema(_OPTION),
    shared_input_schema=_unit_schema(_SHARED),
    action_types=SimulationInput.model_json_schema()["properties"]["action_type"]["enum"],
    target_metric="revenue",
    constraints=["capacity", "max_budget", "nonnegative_units"],
    simulation_adapter="conditional-unit-economics-v1",
)
_DEFINITIONS = MappingProxyType({("unit-economics", 1): _UNIT_ECONOMICS})


def scenario_definition(kind: str, version: int) -> ScenarioDefinition:
    definition = _DEFINITIONS.get((kind, version))
    if definition is None:
        raise HTTPException(status_code=422, detail="Choose a registered scenario version")
    return definition.model_copy(deep=True)


def scenario_definitions() -> list[ScenarioDefinition]:
    return [definition.model_copy(deep=True) for definition in _DEFINITIONS.values()]


def normalize_scenario(kind: str, version: int, parameters: dict) -> SimulationInput:
    scenario_definition(kind, version)
    return SimulationInput.model_validate(parameters, strict=True)


async def run_scenario(body: ScenarioRequest, user: dict) -> dict:
    try:
        value = normalize_scenario(body.scenario_kind, body.scenario_version, body.parameters)
    except ValidationError:
        raise HTTPException(
            status_code=422, detail="Scenario parameters do not match the registered schema"
        ) from None
    result = await run_simulation(value, user, operation_id=body.operation_id)
    return {
        **result,
        "scenario_kind": body.scenario_kind,
        "scenario_version": body.scenario_version,
        "assumptions": value.model_dump(mode="json"),
        "causal_status": "unknown",
    }
