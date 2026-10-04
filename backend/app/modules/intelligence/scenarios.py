"""Reviewed scenario adapters on the existing bounded numerical executor."""

from __future__ import annotations

import math
import re
from typing import Annotated, Any, Literal, Protocol

from fastapi import HTTPException
from pydantic import Field, ValidationError, model_validator

from app.modules.intelligence.contracts import Contract, SemanticRef, Window, fingerprint
from app.modules.ml_engine.decision_lab import SimulationInput, run_simulation

ScenarioScalar = float | int | Annotated[str, Field(max_length=2000)] | bool
_IDENTIFIER = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
_ACTION = r"^[a-z][a-z0-9_\-]{0,63}$"
_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "title", "description"}
_PROPERTY_KEYS = {
    "type",
    "title",
    "description",
    "unit",
    "enum",
    "default",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
}
_AUTHORITY_FIELDS = {"prediction", "lower_bound", "upper_bound", "run_id", "evidence_ids"}


class ScenarioDefinition(Contract):
    id: str = Field(min_length=1, max_length=128)
    scenario_kind: str = Field(pattern=r"^[a-z][a-z0-9\-]{0,63}$")
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    input_schema: dict[str, Any]
    shared_input_schema: dict[str, Any]
    action_types: list[Annotated[str, Field(pattern=_ACTION)]] = Field(min_length=1, max_length=32)
    target_metric: str | None = Field(default=None, min_length=1, max_length=128)
    target_metric_policy: Literal["named_metric", "published_metric"] = "named_metric"
    currency_required: bool = True
    constraints: list[Annotated[str, Field(pattern=_ACTION)]] = Field(max_length=32)
    simulation_adapter: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def policy_contract(self):
        if self.target_metric_policy == "named_metric" and not self.target_metric:
            raise ValueError("Named metric policy requires a target metric")
        if self.target_metric_policy == "published_metric" and self.target_metric is not None:
            raise ValueError("Published metric policy cannot name another metric")
        if len(set(self.action_types)) != len(self.action_types):
            raise ValueError("Scenario action types must be unique")
        return self


class ScenarioContext(Contract):
    """Server-derived input authority; previews have no evidence authority."""

    purpose: Literal["preview", "decision"] = "preview"
    agent_id: str | None = Field(default=None, max_length=128)
    thread_id: str | None = Field(default=None, max_length=64)
    mission_id: str | None = Field(default=None, max_length=128)
    investigation_id: str | None = Field(default=None, max_length=128)
    semantic: SemanticRef | None = None
    target_metric: str | None = Field(default=None, max_length=128)
    baseline: float | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    metric_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    outcome_window: Window | None = None
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def governed_context(self):
        if self.purpose == "decision" and (
            not self.agent_id
            or not self.investigation_id
            or not self.semantic
            or not self.target_metric
            or self.baseline is None
            or not self.outcome_window
            or not self.evidence_ids
        ):
            raise ValueError("Decision scenarios require canonical Investigation context")
        return self


class ScenarioExecution(Contract):
    scenario_kind: str = Field(min_length=1, max_length=64)
    scenario_version: int = Field(ge=1)
    action_type: str = Field(pattern=_ACTION)
    assumptions: dict[str, ScenarioScalar] = Field(max_length=32)
    prediction: float
    lower_bound: float | None = None
    upper_bound: float | None = None
    effects: dict[str, ScenarioScalar] = Field(default_factory=dict, max_length=32)
    cost: float = Field(ge=0)
    risk: Literal["low", "medium", "high"]
    feasible: bool
    method: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=100
    )
    causal_status: Literal["unknown"] = "unknown"
    incremental_gross_profit: float | None = None
    preview_details: dict[str, ScenarioScalar] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def bounded_results(self):
        if any(not _IDENTIFIER.fullmatch(name) for name in self.effects):
            raise ValueError("Scenario effect names must be bounded identifiers")
        if (self.lower_bound is not None and self.lower_bound > self.prediction) or (
            self.upper_bound is not None and self.upper_bound < self.prediction
        ):
            raise ValueError("Scenario bounds must contain the target prediction")
        if set(self.preview_details) & set(type(self).model_fields):
            raise ValueError("Preview details cannot override canonical outputs")
        return self


class ScenarioRequest(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    scenario_kind: str = Field(default="unit-economics", max_length=64)
    scenario_version: int = Field(default=1, ge=1)
    parameters: dict[str, ScenarioScalar] = Field(max_length=32)


class ScenarioAdapter(Protocol):
    scenario_kind: str
    version: int
    parameter_model: type[Contract]

    def definition(self) -> ScenarioDefinition: ...

    def validate_parameters(self, parameters: dict, context: ScenarioContext) -> Contract: ...

    async def simulate(
        self, parameters: Contract, context: ScenarioContext, user: dict, *, operation_id: str
    ) -> ScenarioExecution: ...


def _scalar_matches(kind: str, value: Any) -> bool:
    if kind in {"number", "integer"}:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and (kind != "integer" or isinstance(value, int))
        )
    return isinstance(value, str) if kind == "string" else isinstance(value, bool)


def _validate_schema(schema: dict) -> None:
    if (
        set(schema) - _SCHEMA_KEYS
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or not isinstance(schema.get("properties"), dict)
        or len(schema["properties"]) > 32
    ):
        raise ValueError("Scenario schemas must be bounded flat objects")
    properties, required = schema["properties"], schema.get("required", [])
    if (
        not isinstance(required, list)
        or any(not isinstance(name, str) or name not in properties for name in required)
        or len(required) != len(set(required))
    ):
        raise ValueError("Scenario required fields do not match its schema")
    for name, field in properties.items():
        if (
            not _IDENTIFIER.fullmatch(name)
            or name in {"constructor", "prototype"}
            or name in _AUTHORITY_FIELDS
            or not isinstance(field, dict)
            or set(field) - _PROPERTY_KEYS
            or field.get("type") not in {"string", "number", "integer", "boolean"}
        ):
            raise ValueError("Scenario schemas cannot contain executable or nested fields")
        for key in ("title", "description", "unit"):
            if key in field and (not isinstance(field[key], str) or len(field[key]) > 2000):
                raise ValueError("Scenario labels must be bounded text")
        for key in (
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "minLength",
            "maxLength",
        ):
            if key in field and (
                isinstance(field[key], bool)
                or not isinstance(field[key], (int, float))
                or not math.isfinite(field[key])
            ):
                raise ValueError("Scenario bounds must be finite")
        lower, upper = (
            field.get("exclusiveMinimum", field.get("minimum")),
            field.get("exclusiveMaximum", field.get("maximum")),
        )
        if (
            lower is not None
            and upper is not None
            and (
                lower > upper
                or lower == upper
                and ("exclusiveMinimum" in field or "exclusiveMaximum" in field)
            )
        ):
            raise ValueError("Scenario bounds are inconsistent")
        for key in ("minLength", "maxLength"):
            if key in field and (
                field["type"] != "string"
                or not isinstance(field[key], int)
                or not 0 <= field[key] <= 10000
            ):
                raise ValueError("Scenario string bounds are invalid")
        if field.get("minLength", 0) > field.get("maxLength", 10000):
            raise ValueError("Scenario string bounds are inconsistent")
        values = field.get("enum")
        if values is not None and (
            not isinstance(values, list)
            or not 1 <= len(values) <= 100
            or any(not _scalar_matches(field["type"], value) for value in values)
        ):
            raise ValueError("Scenario choices must match their primitive type")
        if "default" in field and not _scalar_matches(field["type"], field["default"]):
            raise ValueError("Scenario defaults must match their primitive type")


class ScenarioRegistry:
    """Registration is a reviewed-code operation, never a user or provider endpoint."""

    def __init__(self, adapters: tuple[ScenarioAdapter, ...] = ()):
        self._adapters: dict[tuple[str, int], ScenarioAdapter] = {}
        self._definitions: dict[tuple[str, int], ScenarioDefinition] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ScenarioAdapter) -> None:
        definition = ScenarioDefinition.model_validate(
            adapter.definition().model_dump(mode="json"), strict=True
        )
        key = (adapter.scenario_kind, adapter.version)
        if key != (definition.scenario_kind, definition.version) or key in self._adapters:
            raise ValueError("Scenario registration identity is invalid or already registered")
        if isinstance(adapter.version, bool) or any(
            value.id == definition.id for value in self._definitions.values()
        ):
            raise ValueError("Scenario definition identity must be unique")
        if (
            not issubclass(adapter.parameter_model, Contract)
            or adapter.parameter_model.model_config.get("extra") != "forbid"
        ):
            raise ValueError("Scenario parameters require a strict reviewed Contract")
        _validate_schema(definition.input_schema)
        _validate_schema(definition.shared_input_schema)
        shared, option = definition.shared_input_schema, definition.input_schema
        source = adapter.parameter_model.model_json_schema()
        properties = {**shared["properties"], **option["properties"]}
        if (
            set(shared["properties"]) & set(option["properties"])
            or not 1 <= len(properties) <= 32
            or set(properties) != set(source.get("properties", {}))
            or set(shared.get("required", []) + option.get("required", []))
            != set(source.get("required", []))
        ):
            raise ValueError("Scenario schemas must describe all typed parameters exactly once")
        for name, field in properties.items():
            structural = {
                k: v for k, v in field.items() if k not in {"title", "description", "unit"}
            }
            typed = {
                k: v
                for k, v in source["properties"][name].items()
                if k not in {"title", "description"}
            }
            if "const" in typed:
                typed["enum"] = [typed.pop("const")]
            if structural != typed:
                raise ValueError("Scenario schema differs from its typed parameter contract")
        action = properties.get("action_type", {})
        if action.get("type") != "string" or set(action.get("enum", [])) != set(
            definition.action_types
        ):
            raise ValueError("Supported actions must match the typed action choices")
        self._adapters[key] = adapter
        self._definitions[key] = definition.model_copy(deep=True)

    def definition(self, kind: str, version: int) -> ScenarioDefinition:
        value = self._definitions.get((kind, version))
        if value is None:
            raise HTTPException(status_code=422, detail="Choose a registered scenario version")
        return value.model_copy(deep=True)

    def definitions(self) -> list[ScenarioDefinition]:
        return [value.model_copy(deep=True) for value in self._definitions.values()]

    def normalize(
        self, kind: str, version: int, parameters: dict, context: ScenarioContext
    ) -> Contract:
        definition = self.definition(kind, version)
        adapter = self._adapters[(kind, version)]
        if (
            adapter.scenario_kind != kind
            or adapter.version != version
            or adapter.definition().model_dump(mode="json") != definition.model_dump(mode="json")
        ):
            raise ValueError("Scenario registration changed without a new reviewed version")
        value = adapter.parameter_model.model_validate(parameters, strict=True)
        value = adapter.validate_parameters(value.model_dump(mode="json"), context)
        if not isinstance(value, adapter.parameter_model):
            raise ValueError("Scenario validation returned another parameter contract")
        value = adapter.parameter_model.model_validate(value.model_dump(mode="json"), strict=True)
        if value.model_dump()["action_type"] not in definition.action_types:
            raise ValueError("Scenario action is not supported")
        if context.purpose == "decision":
            if (
                definition.target_metric_policy == "named_metric"
                and context.target_metric != definition.target_metric
            ):
                raise HTTPException(
                    status_code=422, detail="Scenario does not support this target metric"
                )
            if definition.currency_required and (
                not context.metric_currency or context.currency != context.metric_currency
            ):
                raise HTTPException(
                    status_code=422, detail="Scenario requires matching published metric currency"
                )
        return value

    async def execute(
        self,
        kind: str,
        version: int,
        parameters: dict,
        context: ScenarioContext,
        user: dict,
        *,
        operation_id: str,
    ) -> ScenarioExecution:
        try:
            value = self.normalize(kind, version, parameters, context)
        except (ValidationError, ValueError):
            raise HTTPException(
                status_code=422, detail="Scenario parameters do not match the registered schema"
            ) from None
        output = await self._adapters[(kind, version)].simulate(
            value, context, user, operation_id=operation_id
        )
        try:
            result = ScenarioExecution.model_validate(output.model_dump(mode="json"), strict=True)
            definition = self.definition(kind, version)
            if (
                (result.scenario_kind, result.scenario_version) != (kind, version)
                or result.action_type != value.model_dump()["action_type"]
                or result.method != definition.simulation_adapter
                or fingerprint(result.assumptions) != fingerprint(value.model_dump(mode="json"))
                or result.evidence_ids != context.evidence_ids
            ):
                raise ValueError("Scenario execution changed canonical inputs")
        except (AttributeError, ValidationError, ValueError):
            raise HTTPException(
                status_code=422, detail="Scenario execution does not match the registered contract"
            ) from None
        return result


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


class UnitEconomicsScenarioAdapter:
    scenario_kind = "unit-economics"
    version = 1
    parameter_model = SimulationInput

    def definition(self) -> ScenarioDefinition:
        return ScenarioDefinition(
            id="unit-economics-v1",
            scenario_kind=self.scenario_kind,
            version=self.version,
            title="Unit economics",
            description="Compare conditional revenue and gross profit using explicit assumptions.",
            shared_input_schema=_unit_schema(
                ["baseline_units", "price", "unit_cost", "capacity", "max_budget"]
            ),
            input_schema=_unit_schema(
                [
                    "action_type",
                    "expected_unit_change",
                    "unit_change_uncertainty",
                    "action_cost",
                    "discount",
                ]
            ),
            action_types=SimulationInput.model_json_schema()["properties"]["action_type"]["enum"],
            target_metric_policy="published_metric",
            constraints=["capacity", "max_budget", "nonnegative_units"],
            simulation_adapter="conditional-unit-economics-v1",
        )

    def validate_parameters(self, parameters: dict, context: ScenarioContext) -> SimulationInput:
        value = SimulationInput.model_validate(parameters, strict=True)
        if context.purpose == "decision":
            if not context.metric_currency or context.currency != context.metric_currency:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Decision unit economics require a metric with matching published currency"
                    ),
                )
            if not math.isclose(
                value.baseline_units * value.price, context.baseline, rel_tol=1e-8, abs_tol=1e-8
            ):
                raise HTTPException(
                    status_code=422, detail="Unit economics must reconcile to observed revenue"
                )
        return value

    async def simulate(
        self, parameters: Contract, context: ScenarioContext, user: dict, *, operation_id: str
    ) -> ScenarioExecution:
        parameters = SimulationInput.model_validate(parameters.model_dump(mode="json"), strict=True)
        estimate = await run_simulation(parameters, user, operation_id=operation_id)
        baseline = parameters.baseline_units * parameters.price
        return ScenarioExecution(
            scenario_kind=self.scenario_kind,
            scenario_version=self.version,
            action_type=parameters.action_type,
            assumptions=parameters.model_dump(mode="json"),
            prediction=estimate["prediction"],
            lower_bound=estimate["lower_bound"],
            upper_bound=estimate["upper_bound"],
            cost=estimate["cost"],
            effects={
                "incremental_gross_profit": estimate["incremental_gross_profit"],
                "net_benefit": estimate["net_benefit"],
                "revenue_delta": estimate["prediction"] - baseline,
            },
            incremental_gross_profit=estimate["incremental_gross_profit"],
            risk="high" if estimate["net_benefit"] < 0 else "medium",
            feasible=estimate["feasible"],
            method=estimate["method"],
            run_id=estimate["run_id"],
            evidence_ids=context.evidence_ids,
            preview_details={
                "interval_method": estimate["interval_method"],
                "assumption": estimate["assumption"],
                "net_benefit": estimate["net_benefit"],
                "capacity_limited": estimate["capacity_limited"],
            },
        )


scenario_registry = ScenarioRegistry((UnitEconomicsScenarioAdapter(),))


def scenario_definition(kind: str, version: int) -> ScenarioDefinition:
    return scenario_registry.definition(kind, version)


def scenario_definitions() -> list[ScenarioDefinition]:
    return scenario_registry.definitions()


def normalize_scenario(
    kind: str, version: int, parameters: dict, context: ScenarioContext | None = None
) -> Contract:
    return scenario_registry.normalize(kind, version, parameters, context or ScenarioContext())


async def execute_scenario(
    kind: str,
    version: int,
    parameters: dict,
    context: ScenarioContext,
    user: dict,
    *,
    operation_id: str,
) -> ScenarioExecution:
    return await scenario_registry.execute(
        kind, version, parameters, context, user, operation_id=operation_id
    )


async def run_scenario(body: ScenarioRequest, user: dict) -> dict:
    result = await execute_scenario(
        body.scenario_kind,
        body.scenario_version,
        body.parameters,
        ScenarioContext(),
        user,
        operation_id=body.operation_id,
    )
    return {
        **result.preview_details,
        **result.model_dump(mode="json", exclude={"preview_details"}, exclude_none=True),
    }
