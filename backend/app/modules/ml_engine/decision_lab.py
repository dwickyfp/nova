"""Decision Lab methods on the existing ML executor, budgets, and run journal."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from time import monotonic
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from app.modules.assistant.security import session_security
from app.modules.intelligence.contracts import Contract, fingerprint
from app.modules.ml_engine.analysis import Simulation, simulate
from app.modules.ml_engine.execution.budgets import budget_for
from app.modules.ml_engine.spec import MLExecutionSpec, MLMode, MLSecurityContext, MLTask


class SimulationInput(Contract):
    action_type: Literal["inventory_transfer", "campaign_budget", "rollback", "discount", "spend"]
    baseline_units: float = Field(ge=0)
    price: float = Field(ge=0)
    unit_cost: float = Field(ge=0)
    expected_unit_change: float
    unit_change_uncertainty: float = Field(ge=0)
    action_cost: float = Field(ge=0)
    capacity: float = Field(ge=0)
    discount: float = Field(default=0, ge=0, lt=1)
    max_budget: float = Field(ge=0)


async def run_simulation(value: SimulationInput, user: dict, *, operation_id: str) -> dict:
    return await run_numerical(
        user,
        operation_id=operation_id,
        method="conditional-unit-economics-v1",
        parameters=value.model_dump(mode="json"),
        function=simulate,
        arguments=(Simulation(**value.model_dump()),),
        task=MLTask.SIMULATION,
    )


async def run_numerical(
    user: dict,
    *,
    operation_id: str,
    method: str,
    parameters: dict,
    function,
    arguments: tuple,
    task=MLTask.ANALYSIS,
) -> dict:
    from app.modules.ml_engine.service import ml_engine_service

    security = session_security(user)
    budget = budget_for(MLMode.INTERACTIVE)
    spec = MLExecutionSpec(
        task=task,
        input_sql="",
        algorithm=method,
        security=MLSecurityContext(
            username=security.principal,
            password="",
            role=security.active_role,
            security_context_version=security.security_context_version,
        ),
        parameters=parameters,
        budget=budget,
    )
    digest = fingerprint(
        {
            "parameters": spec.parameters,
            "method": spec.algorithm,
            "principal": security.principal,
            "role": security.active_role,
            "version": security.security_context_version,
            "operation": operation_id,
        }
    )
    run_id = str(uuid5(NAMESPACE_URL, digest))
    started = monotonic()
    await ml_engine_service.repository.record_run(
        run_id=run_id,
        spec=spec,
        status="RUNNING",
        fingerprint=digest,
        telemetry={"session_id": security.session_id, "operation_id": operation_id},
    )
    try:
        async with asyncio.timeout(min(120, budget.timeout_seconds)):
            result = await ml_engine_service.runtime.executor.run(function, *arguments)
    except Exception as exc:
        await ml_engine_service.repository.record_run(
            run_id=run_id,
            spec=spec,
            status="FAILED",
            fingerprint=digest,
            telemetry={"elapsed_seconds": monotonic() - started},
            error=ValueError(type(exc).__name__),
        )
        raise
    await ml_engine_service.repository.record_run(
        run_id=run_id,
        spec=spec,
        status="COMPLETE",
        fingerprint=digest,
        telemetry={
            "elapsed_seconds": monotonic() - started,
            "budget": asdict(budget),
            "method": result["method"],
            "result_fingerprint": fingerprint(result),
            "security_context_version": security.security_context_version,
            "active_role": security.active_role,
            "session_id": security.session_id,
            "operation_id": operation_id,
        },
    )
    return {**result, "run_id": run_id}
