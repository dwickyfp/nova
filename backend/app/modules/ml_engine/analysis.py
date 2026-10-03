"""Bounded numerical methods for investigations and conditional decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, median, variance
from typing import Literal


def numbers(values: list[float], *, minimum: int = 2) -> list[float]:
    if not minimum <= len(values) <= 50_000:
        raise ValueError(f"Expected between {minimum} and 50000 observations")
    if any(isinstance(x, bool) or not math.isfinite(float(x)) for x in values):
        raise ValueError("Observations must be finite numbers")
    return [float(x) for x in values]


def correlation(left: list[float], right: list[float]) -> dict:
    x, y = numbers(left, minimum=3), numbers(right, minimum=3)
    if len(x) != len(y):
        raise ValueError("Correlation requires aligned observations")
    mx, my = mean(x), mean(y)
    xx = sum((v - mx) ** 2 for v in x)
    yy = sum((v - my) ** 2 for v in y)
    coefficient = (
        None
        if xx == 0 or yy == 0
        else (sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / math.sqrt(xx * yy))
    )
    return {
        "method": "pearson-v1",
        "coefficient": coefficient,
        "causal_status": "association",
        "observations": len(x),
    }


def change_point(values: list[float], *, minimum_segment: int = 7) -> dict:
    values = numbers(values, minimum=minimum_segment * 2)
    if minimum_segment < 3:
        raise ValueError("Each segment needs at least three observations")
    # Prefix sums keep the search linear instead of repeatedly slicing history.
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    count = len(values)
    candidates = range(minimum_segment, count - minimum_segment + 1)

    def score(index):
        delta = (prefix[-1] - prefix[index]) / (count - index) - prefix[index] / index
        return abs(delta) * math.sqrt(index * (count - index) / count)

    split = max(candidates, key=score)
    return {
        "method": "mean-shift-cusum-v1",
        "index": split,
        "before": prefix[split] / split,
        "after": (prefix[-1] - prefix[split]) / (count - split),
        "score": score(split),
        "causal_status": "association",
    }


def detect_change(
    baselines: list[float],
    current: float,
    *,
    sample_count: int,
    minimum_samples: int,
    relative_threshold: float,
    absolute_threshold: float,
) -> dict:
    baseline = numbers(baselines)
    numbers([current, current])
    before = median(baseline)
    delta = float(current) - before
    relative = delta / abs(before) if before else None
    deviation = median([abs(value - before) for value in baseline])
    material = abs(delta) >= absolute_threshold and abs(delta) > 0
    significant = abs(delta) >= max(3 * 1.4826 * deviation, abs(before) * relative_threshold)
    detected = sample_count >= minimum_samples and material and significant
    return {
        "detected": detected,
        "before": before,
        "after": current,
        "change": delta,
        "relative_change": relative,
        "method": "matched-weekday-median-mad-v1",
        "severity": "critical" if relative is not None and abs(relative) >= 0.25 else "warning",
        "reason": "material_change"
        if detected
        else ("insufficient_sample" if sample_count < minimum_samples else "below_threshold"),
    }


def rank_drivers(before: dict[str, float], after: dict[str, float], *, total_change: float) -> dict:
    keys = sorted(set(before) | set(after))
    if len(keys) > 1000:
        raise ValueError("Driver search exceeds the segment budget")
    contributions = []
    for key in keys:
        previous, current = float(before.get(key, 0)), float(after.get(key, 0))
        numbers([previous, current])
        contributions.append(
            {
                "id": key,
                "before": previous,
                "after": current,
                "contribution": current - previous,
                "causal_status": "arithmetic",
            }
        )
    contributions.sort(key=lambda row: (-abs(row["contribution"]), row["id"]))
    return {
        "method": "dimension-difference-v1",
        "drivers": contributions,
        "residual": total_change - math.fsum(row["contribution"] for row in contributions),
    }


def causal_effect(
    treatment: list[float], control: list[float], *, design: str, independent_assignment: bool
) -> dict:
    if design != "randomized" or not independent_assignment:
        return {
            "status": "insufficient",
            "method": "randomized-difference-in-means-v1",
            "causal_status": "unknown",
            "reason": "Verified independent random assignment required",
        }
    if len(treatment) < 30 or len(control) < 30:
        return {
            "status": "insufficient",
            "method": "randomized-difference-in-means-v1",
            "causal_status": "unknown",
            "reason": "At least 30 independent units per arm required",
        }
    treated, untreated = numbers(treatment), numbers(control)
    effect = mean(treated) - mean(untreated)
    stderr = math.sqrt(variance(treated) / len(treated) + variance(untreated) / len(untreated))
    return {
        "status": "complete",
        "method": "randomized-difference-in-means-v1",
        "effect": effect,
        "lower_bound": effect - 1.96 * stderr,
        "upper_bound": effect + 1.96 * stderr,
        "interval_level": 0.95,
        "interval_method": "normal-independent-assignment-units",
        "treatment_units": len(treated),
        "control_units": len(untreated),
        "causal_status": "supported_effect",
    }


@dataclass(frozen=True)
class Simulation:
    action_type: Literal["inventory_transfer", "campaign_budget", "rollback", "discount", "spend"]
    baseline_units: float
    price: float
    unit_cost: float
    expected_unit_change: float
    unit_change_uncertainty: float
    action_cost: float
    capacity: float
    discount: float = 0
    max_budget: float = math.inf


def simulate(spec: Simulation) -> dict:
    numbers(
        [
            spec.baseline_units,
            spec.price,
            spec.unit_cost,
            spec.expected_unit_change,
            spec.unit_change_uncertainty,
            spec.action_cost,
            spec.capacity,
            spec.discount,
        ]
    )
    if (
        min(
            spec.baseline_units,
            spec.price,
            spec.unit_cost,
            spec.unit_change_uncertainty,
            spec.action_cost,
            spec.capacity,
        )
        < 0
        or not 0 <= spec.discount < 1
    ):
        raise ValueError("Invalid simulation quantities, costs, or discount")
    if math.isnan(spec.max_budget) or spec.max_budget < 0:
        raise ValueError("Invalid budget")
    desired = max(0, spec.baseline_units + spec.expected_unit_change)
    units = min(desired, spec.capacity)
    price = spec.price * (1 - spec.discount)
    baseline_profit = spec.baseline_units * (spec.price - spec.unit_cost)
    gross_profit = units * (price - spec.unit_cost)
    lower_units = min(spec.capacity, max(0, desired - spec.unit_change_uncertainty))
    upper_units = min(spec.capacity, desired + spec.unit_change_uncertainty)
    return {
        "method": "conditional-unit-economics-v1",
        "causal_status": "unknown",
        "prediction": units * price,
        "lower_bound": lower_units * price,
        "upper_bound": upper_units * price,
        "interval_method": "user-specified-sensitivity-range",
        "incremental_gross_profit": gross_profit - baseline_profit,
        "net_benefit": gross_profit - baseline_profit - spec.action_cost,
        "cost": spec.action_cost,
        "capacity_limited": desired > spec.capacity,
        "feasible": spec.action_cost <= spec.max_budget,
        "assumption": "Demand change is an explicit scenario assumption, not a causal estimate",
    }


def optimize(options: list[dict]) -> dict:
    if not 1 <= len(options) <= 30:
        raise ValueError("Optimize requires 1 to 30 options")
    feasible = [item for item in options if item["feasible"]]
    ranked = sorted(feasible, key=lambda item: (-float(item["net_benefit"]), str(item["id"])))
    return {
        "method": "bounded-enumeration-v1",
        "objective": "incremental_gross_profit_minus_cost",
        "ranked_ids": [item["id"] for item in ranked],
        "selected_id": ranked[0]["id"] if ranked else None,
    }


def outcome_dimensions(
    *,
    predicted: float,
    actual: float | None,
    baseline: float,
    lower: float | None = None,
    upper: float | None = None,
    completeness: float = 1,
    overlapping: bool = False,
) -> dict:
    numbers([predicted, baseline, completeness])
    if not 0 <= completeness <= 1:
        raise ValueError("Completeness must be between zero and one")
    usable = actual is not None and completeness == 1
    if actual is not None:
        numbers([actual, actual])
    return {
        "forecast_absolute_error": abs(actual - predicted) if usable else None,
        "forecast_relative_error": abs(actual - predicted) / abs(actual)
        if usable and actual
        else None,
        "interval_covered": lower <= actual <= upper
        if usable and lower is not None and upper is not None
        else None,
        "expected_change": predicted - baseline,
        "observed_change": actual - baseline if usable else None,
        "attributed_business_impact": None,
        "attribution": "unknown" if overlapping or not usable else "observed_after",
        "completeness": completeness,
    }
