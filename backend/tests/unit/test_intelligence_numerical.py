"""Independent numerical and failure-case checks for Intelligence methods."""

import math
from dataclasses import replace
from statistics import variance

import pytest

from app.modules.ml_engine.analysis import (
    Simulation,
    causal_effect,
    change_point,
    correlation,
    detect_change,
    optimize,
    outcome_dimensions,
    rank_drivers,
    simulate,
)


def test_correlated_series_does_not_claim_causation():
    result = correlation([1, 2, 3, 4], [2, 4, 6, 8])
    assert result["coefficient"] == pytest.approx(1)
    assert result["causal_status"] == "association"
    assert correlation([1, 1, 1], [2, 3, 4])["coefficient"] is None


@pytest.mark.parametrize("values", [[1], [1, math.nan], [1, math.inf], [True, 2]])
def test_invalid_numerical_inputs_fail_closed(values):
    with pytest.raises(ValueError):
        change_point(values)


def test_change_point_is_detected_at_independent_ground_truth():
    result = change_point([10.0] * 28 + [5.0] * 28)
    assert result["index"] == 28
    assert result["before"] == 10 and result["after"] == 5


@pytest.mark.parametrize("samples,expected", [(29, False), (30, True)])
def test_material_detection_requires_sample_floor(samples, expected):
    result = detect_change(
        [100, 102, 98, 100],
        65,
        sample_count=samples,
        minimum_samples=30,
        relative_threshold=0.1,
        absolute_threshold=5,
    )
    assert result["detected"] is expected
    assert result["change"] == -35


def test_baseline_noise_prevents_false_incident():
    assert not detect_change(
        [50, 150, 100, 200],
        130,
        sample_count=100,
        minimum_samples=30,
        relative_threshold=0.1,
        absolute_threshold=5,
    )["detected"]


def test_dimensional_contributions_preserve_unexplained_residual():
    result = rank_drivers(
        {"Jakarta": 100, "Bali": 50}, {"Jakarta": 60, "Bali": 60}, total_change=-35
    )
    assert result["drivers"][0]["id"] == "Jakarta"
    assert sum(row["contribution"] for row in result["drivers"]) + result["residual"] == -35
    assert result["residual"] == -5


def test_randomized_effect_and_uncertainty_match_independent_calculation():
    treated, control = list(range(10, 50)), list(range(40))
    result = causal_effect(treated, control, design="randomized", independent_assignment=True)
    margin = 1.96 * math.sqrt(variance(treated) / 40 + variance(control) / 40)
    assert result["effect"] == 10
    assert result["lower_bound"] == pytest.approx(10 - margin)
    assert result["upper_bound"] == pytest.approx(10 + margin)


@pytest.mark.parametrize(
    "design,independent,size",
    [
        ("observational", True, 50),
        ("randomized", False, 50),
        ("randomized", True, 3),
    ],
)
def test_unsupported_causal_designs_remain_insufficient(design, independent, size):
    result = causal_effect(
        [4] * size, [2] * size, design=design, independent_assignment=independent
    )
    assert result["status"] == "insufficient" and result["causal_status"] == "unknown"
    assert "effect" not in result


def test_inventory_simulation_capacity_cost_and_optimization():
    spec = Simulation(
        "inventory_transfer",
        baseline_units=100,
        price=20,
        unit_cost=10,
        expected_unit_change=40,
        unit_change_uncertainty=10,
        action_cost=50,
        capacity=130,
        max_budget=100,
    )
    result = simulate(spec)
    assert result["prediction"] == 2600
    assert result["net_benefit"] == 250
    assert result["capacity_limited"]
    assert result["interval_method"] == "user-specified-sensitivity-range"
    denied = simulate(replace(spec, action_cost=200))
    chosen = optimize([{**result, "id": "transfer"}, {**denied, "id": "expensive"}])
    assert chosen["selected_id"] == "transfer"
    assert chosen["ranked_ids"] == ["transfer"]


@pytest.mark.parametrize(
    "actual,completeness,overlap", [(None, 0, False), (120, 0.5, False), (120, 1, True)]
)
def test_missing_late_or_overlapping_outcome_never_claims_attributed_impact(
    actual, completeness, overlap
):
    result = outcome_dimensions(
        predicted=125, actual=actual, baseline=100, completeness=completeness, overlapping=overlap
    )
    assert result["attribution"] == "unknown"
    assert result["attributed_business_impact"] is None
    if completeness < 1:
        assert result["forecast_absolute_error"] is None


def test_complete_outcome_separates_forecast_accuracy_and_observed_change():
    result = outcome_dimensions(predicted=125, actual=120, baseline=100, lower=110, upper=130)
    assert result["forecast_absolute_error"] == 5
    assert result["observed_change"] == 20
    assert result["interval_covered"] is True
    assert result["attributed_business_impact"] is None


@pytest.mark.parametrize(
    "raw,metric,method",
    [
        ({"metrics": ["order_count"]}, "order_count", "forecast"),
        ({"metrics": ["order_count"], "limit": 20}, "missing", "forecast"),
        (
            {"metrics": ["order_completeness"], "limit": 20, "dimensions": ["orders.city"]},
            "order_completeness",
            "key_drivers",
        ),
    ],
)
async def test_analysis_rejects_unbounded_or_incompatible_metric_plans_before_query(
    monkeypatch, raw, metric, method
):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.modules.intelligence import decision_lab
    from app.modules.ml_engine.analysis_contracts import AnalysisRequest
    from tests.benchmark.business_intelligence.model import starter_definition
    from tests.unit.test_intelligence_engine import REF, USER

    monkeypatch.setattr(
        decision_lab.intelligence_service,
        "authorize_semantic",
        AsyncMock(return_value={"definition": starter_definition()}),
    )
    query = AsyncMock()
    monkeypatch.setattr(decision_lab.intelligence_service, "query", query)
    with pytest.raises(HTTPException) as exc:
        await decision_lab.analyze(
            AnalysisRequest(
                operation_id="analysis-guard",
                method=method,
                semantic=REF,
                plan=raw,
                value_column=metric,
                dimension="orders.city",
                prior_plan=raw if method == "key_drivers" else None,
            ),
            USER,
        )
    assert exc.value.status_code == 422
    query.assert_not_awaited()


CAUSAL_SELECTIONS = [
    {"having": [{"metric": "outcome", "operator": ">", "value": 10}]},
    {"filters": [{"field": "assigned.arm", "operator": "=", "value": 1}]},
    {"named_filters": ["responders"]},
    {"time": {"dimension": "assigned.observed_at", "range": "last_7_days"}},
    {"transforms": [{"metric": "outcome", "kind": "percent_of_total"}]},
    {"top_n_per_group": {"n": 35, "metric": "outcome"}},
    {"dimensions": ["assigned.arm"]},
    {"dimensions": ["assigned.unit", "assigned.arm", "assigned.responded"]},
    {"metrics": ["outcome", "other_outcome"]},
]


@pytest.fixture
def randomized_cohort():
    from app.modules.ml_engine.analysis_contracts import AnalysisRequest
    from tests.unit.test_intelligence_engine import REF

    body = AnalysisRequest(
        operation_id="assignment-cohort-check",
        method="causal_effect",
        semantic=REF,
        plan={
            "metrics": ["outcome"],
            "dimensions": ["assigned.unit", "assigned.arm"],
            "limit": 100,
        },
        value_column="outcome",
        experiment="assignment",
    )
    experiment = {
        "design": "randomized",
        "assignment": "independent",
        "protocol_reference": "reviewed-assignment-protocol",
        "unit": "assigned.unit",
        "arm": "assigned.arm",
    }
    rows = [
        {"assigned.unit": f"{arm}:{i}", "assigned.arm": arm, "outcome": i + 10 * arm}
        for arm in (0, 1)
        for i in range(40)
    ]
    return body, experiment, rows


def test_complete_randomized_cohort_matches_assignment_unit_uncertainty(randomized_cohort):
    from app.modules.intelligence.decision_lab import _analyze

    body, experiment, rows = randomized_cohort
    result = _analyze(body, rows, [], experiment)
    margin = 1.96 * math.sqrt(2 * variance(range(40)) / 40)
    assert result["effect"] == 10
    assert result["lower_bound"] == pytest.approx(10 - margin)
    assert result["upper_bound"] == pytest.approx(10 + margin)
    assert result["causal_status"] == "supported_effect"
    assert result["treatment_units"] == result["control_units"] == 40


@pytest.mark.parametrize("selection", CAUSAL_SELECTIONS)
async def test_selected_cohort_never_produces_causal_evidence_or_queries(
    randomized_cohort, monkeypatch, selection
):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.modules.intelligence import decision_lab
    from tests.unit.test_intelligence_engine import USER

    body, experiment, rows = randomized_cohort
    body = body.model_copy(update={"plan": {**body.plan, **selection}})
    result = decision_lab._analyze(body, rows, [], experiment)
    assert result["status"] == "insufficient"
    assert result["causal_status"] == "unknown"
    assert "effect" not in result
    monkeypatch.setattr(
        decision_lab.intelligence_service,
        "authorize_semantic",
        AsyncMock(return_value={"definition": {"ai_context": {"experiments": {
            "assignment": experiment,
        }}}}),
    )
    query = AsyncMock()
    monkeypatch.setattr(decision_lab.intelligence_service, "query", query)
    with pytest.raises(HTTPException) as exc:
        await decision_lab.analyze(body, USER)
    assert exc.value.status_code == 422
    query.assert_not_awaited()


def test_causal_effect_rejects_comparison_cohort_and_repeated_assignment_units(randomized_cohort):
    from app.modules.intelligence.decision_lab import _analyze

    body, experiment, rows = randomized_cohort
    result = _analyze(body.model_copy(update={"prior_plan": body.plan}), rows, rows, experiment)
    assert result["causal_status"] == "unknown" and "effect" not in result
    with pytest.raises(ValueError, match="one complete outcome"):
        _analyze(body, [*rows, rows[0]], [], experiment)
