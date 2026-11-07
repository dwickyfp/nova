"""The estimators behind every measured number."""

from __future__ import annotations

import math

import pytest

from app.common.statistics import (
    calibration,
    cohen_kappa,
    mcnemar,
    paired_bootstrap,
    proportion,
    stratified,
    wilson,
)


def test_wilson_matches_the_reference_values():
    low, high = wilson(81, 263)  # the standard worked example: 0.2553-0.3662
    assert math.isclose(low, 0.2553, abs_tol=1e-4) and math.isclose(high, 0.3662, abs_tol=1e-4)
    assert wilson(0, 0) is None
    low, high = wilson(10, 10)
    assert math.isclose(high, 1.0) and math.isclose(low, 0.7225, abs_tol=1e-4)
    assert proportion(3, 4)["value"] == 0.75


def test_stratified_weights_by_population_and_reports_gaps():
    result = stratified({"entity": (100, 10, 10), "relation": (300, 30, 24), "alias": (50, 0, 0)})
    expected = (100 / 400) * 1.0 + (300 / 400) * 0.8
    assert math.isclose(result["value"], expected)
    low, high = result["interval_95"]
    assert low < expected < high
    assert result["unsampled"] == ["alias"]
    assert stratified({})["value"] is None


def test_a_constant_confidence_is_miscalibrated_by_the_gap():
    pairs = [(0.9, True)] * 6 + [(0.9, False)] * 4
    result = calibration(pairs)
    assert math.isclose(result["ece"], 0.3)
    assert result["bins"] == [
        {"low": 0.9, "high": 1.0, "n": 10, "confidence": 0.9, "accuracy": 0.6}
    ]
    assert math.isclose(result["brier"], (6 * 0.01 + 4 * 0.81) / 10)
    assert calibration([])["ece"] is None


def test_cohen_kappa():
    assert cohen_kappa(["y", "y", "n", "n"], ["y", "y", "n", "n"])["kappa"] == 1.0
    result = cohen_kappa(["y", "n", "y", "n"], ["y", "y", "n", "n"])
    assert result["kappa"] == 0.0 and result["agreement"] == 0.5
    with pytest.raises(ValueError):
        cohen_kappa(["y"], [])


def test_mcnemar_is_exact_on_discordant_pairs():
    before = [False] * 10 + [True] * 5
    after = [True] * 10 + [True] * 5
    result = mcnemar(before, after)
    assert result["gained"] == 10 and result["lost"] == 0
    assert math.isclose(result["p_value"], 2 / 2**10)
    assert mcnemar([True], [True])["p_value"] == 1.0


def test_paired_bootstrap_is_reproducible():
    first = paired_bootstrap([1, 0, 1, 1, 0, 1])
    assert first == paired_bootstrap([1, 0, 1, 1, 0, 1])
    assert first["interval_95"][0] <= first["mean"] <= first["interval_95"][1]
    assert paired_bootstrap([])["mean"] is None
