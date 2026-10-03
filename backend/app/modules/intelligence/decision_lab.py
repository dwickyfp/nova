"""Typed numerical analysis over published, caller-authorized semantic plans."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.contracts import fingerprint
from app.modules.intelligence.engine import CycleBudget, intelligence_service
from app.modules.ml_engine.analysis import (
    Simulation,
    causal_effect,
    change_point,
    correlation,
    optimize,
    rank_drivers,
    simulate,
)
from app.modules.ml_engine.analysis_contracts import AnalysisRequest, OptimizationRequest
from app.modules.ml_engine.decision_lab import run_numerical
from app.modules.ml_engine.spec import MLExecutionSpec, MLSecurityContext, MLTask


def _optimize(values: list[dict], minimum_profit: float, maximum_cost: float) -> dict:
    estimates = []
    for index, value in enumerate(values):
        estimate = simulate(Simulation(**value))
        estimate["id"] = str(index)
        estimate["feasible"] = bool(
            estimate["feasible"]
            and estimate["cost"] <= maximum_cost
            and estimate["incremental_gross_profit"] >= minimum_profit
        )
        estimates.append(estimate)
    return {**optimize(estimates), "options": estimates, "causal_status": "unknown"}


async def optimize_options(body: OptimizationRequest, user: dict) -> dict:
    return await run_numerical(
        user,
        operation_id=body.operation_id,
        method="bounded-enumeration-v1",
        parameters=body.model_dump(mode="json"),
        function=_optimize,
        arguments=(
            [item.model_dump() for item in body.options],
            body.minimum_gross_profit,
            body.maximum_cost,
        ),
    )


def _causal_selection_error(body: AnalysisRequest, experiment: dict) -> str | None:
    plan = SemanticPlan.from_dict(body.plan)
    if (
        plan.filters
        or plan.named_filters
        or plan.time
        or plan.having
        or plan.transforms
        or plan.top_n_per_group
        or body.prior_plan is not None
    ):
        return "Causal analysis requires the complete unfiltered assignment cohort"
    unit, arm = experiment.get("unit"), experiment.get("arm")
    if unit and arm and (
        len(plan.dimensions) != 2
        or set(plan.dimensions) != {unit, arm}
        or plan.metrics != (body.value_column,)
    ):
        return "Use one outcome metric grouped by the published assignment unit and arm"
    return None


def _analyze(body: AnalysisRequest, rows: list[dict], prior: list[dict], experiment: dict) -> dict:
    if body.method == "causal_effect":
        reason = _causal_selection_error(body, experiment)
        if (
            reason
            or experiment.get("design") != "randomized"
            or experiment.get("assignment") != "independent"
            or not experiment.get("protocol_reference")
        ):
            result = causal_effect([], [], design="unsupported", independent_assignment=False)
            return {**result, **({"reason": reason} if reason else {})}
    column = body.value_column
    if not rows or any(column not in row or row[column] is None for row in rows):
        raise ValueError("The selected metric has missing observations")
    if body.method in {"forecast", "change_points", "correlation"}:
        timestamp = body.timestamp_column
        if not timestamp or any(not row.get(timestamp) for row in rows):
            raise ValueError("An explicit time alignment column is required")
        rows = sorted(rows, key=lambda row: str(row[timestamp]))
        times = [datetime.fromisoformat(str(row[timestamp])) for row in rows]
        if len(set(times)) != len(times):
            raise ValueError("Time-aligned analysis requires unique timestamps")
    values = [row[column] for row in rows]
    if body.method == "change_points":
        result = change_point(values, minimum_segment=body.minimum_segment)
        return {**result, "timestamp": str(rows[result["index"]][body.timestamp_column])}
    if body.method == "correlation":
        if not body.comparison_column or any(
            row.get(body.comparison_column) is None for row in rows
        ):
            raise ValueError("Both aligned series must be complete")
        return correlation(values, [row[body.comparison_column] for row in rows])
    if body.method == "key_drivers":
        if not body.dimension or not prior:
            raise ValueError("Key drivers require a dimension and prior comparison plan")

        def segments(table):
            result = {}
            for row in table:
                key = str(row[body.dimension])
                if key in result or row.get(column) is None:
                    raise ValueError("Driver segments must be unique and complete")
                result[key] = float(row[column])
            return result

        before, after = segments(prior), segments(rows)
        return rank_drivers(before, after, total_change=sum(after.values()) - sum(before.values()))
    if body.method == "causal_effect":
        unit, arm = experiment.get("unit"), experiment.get("arm")
        if (
            not unit
            or not arm
            or any(row.get(unit) is None or row.get(arm) not in (0, 1) for row in rows)
        ):
            raise ValueError("The published assignment unit and binary treatment arm are required")
        units = [str(row[unit]) for row in rows]
        if len(set(units)) != len(units):
            raise ValueError("Use one complete outcome per independent assignment unit")
        result = causal_effect(
            [row[column] for row in rows if row[arm] == 1],
            [row[column] for row in rows if row[arm] == 0],
            design="randomized",
            independent_assignment=True,
        )
        return {
            **result,
            "protocol_reference": experiment["protocol_reference"],
            "assignment_unit": unit,
            "design_source": "published_semantic_definition",
        }
    if body.method == "forecast":
        import pyarrow as pa

        from app.modules.ml_engine.engines.forecast import train_forecast

        output = train_forecast(
            pa.Table.from_pylist(rows),
            MLExecutionSpec(
                task=MLTask.FORECAST,
                input_sql="",
                security=MLSecurityContext(username="", password=""),
                target_column=column,
                timestamp_column=body.timestamp_column,
                horizon=body.horizon,
                frequency=body.frequency,
            ),
        )
        return {
            "method": "statsforecast-chronological-v1",
            "metrics": output.metrics,
            "results": output.results,
            "causal_status": "unknown",
        }
    raise ValueError("Unsupported numerical operation")


async def analyze(body: AnalysisRequest, user: dict) -> dict:
    service, budget = intelligence_service, CycleBudget()
    version = await service.authorize_semantic(body.semantic, user, active=True)
    context = version["definition"].get("ai_context") or {}
    experiment = (context.get("experiments") or {}).get(body.experiment, {})
    tables, evidence = [], []
    for raw in [body.plan, *([body.prior_plan] if body.prior_plan else [])]:
        try:
            plan = SemanticPlan.from_dict(raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Use a valid semantic plan") from exc
        if plan.limit is None or plan.limit < 2 or plan.limit > 1000:
            raise HTTPException(status_code=422, detail="Use a plan bounded to 2–1000 rows")
        if body.value_column not in plan.metrics:
            raise HTTPException(status_code=422, detail="Select a metric returned by the plan")
        if body.method == "causal_effect":
            reason = _causal_selection_error(body, experiment)
            if reason:
                raise HTTPException(status_code=422, detail=reason)
        if body.method == "key_drivers":
            metric = SemanticModelIR.from_ossie(version["definition"]).metric(body.value_column)
            if not metric or metric.additivity.value != "additive":
                raise HTTPException(
                    status_code=422, detail="Driver contributions require an additive metric"
                )
            if not body.prior_plan or not body.dimension or plan.dimensions != (body.dimension,):
                raise HTTPException(
                    status_code=422, detail="Use matching single-dimension comparison plans"
                )
            if plan.having or plan.top_n_per_group or plan.transforms:
                raise HTTPException(
                    status_code=422, detail="Driver analysis requires all untransformed segments"
                )
        table, source = await service.query(body.semantic, plan, user, budget)
        if len(table["rows"]) >= plan.limit:
            raise HTTPException(
                status_code=422, detail="Analysis input may be truncated; narrow its scope"
            )
        tables.append([dict(zip(table["columns"], row, strict=True)) for row in table["rows"]])
        evidence.append(source)
    try:
        result = await run_numerical(
            user,
            operation_id=body.operation_id,
            method=f"decision-lab-{body.method}-v1",
            parameters={
                "request": body.model_dump(mode="json"),
                "evidence": [item.digest for item in evidence],
                "design_digest": fingerprint(experiment),
            },
            function=_analyze,
            arguments=(body, tables[0], tables[1] if len(tables) > 1 else [], experiment),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "semantic": body.semantic, "evidence": evidence}
