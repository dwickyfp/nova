"""Paired repetitions, uncertainty and explicit unavailable evaluation dimensions."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import mean, quantiles


def wilson(successes: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.959963984540054
    p, denominator = successes / total, 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0, center - radius), min(1, center + radius)]


def summarize(rows: list[dict]) -> dict:
    results = {}
    dimensions = sorted({name for row in rows for name in row.get("scores", {})})
    for dimension in dimensions:
        observations = [
            row["scores"][dimension]
            for row in rows
            if isinstance(row.get("scores", {}).get(dimension), bool)
        ]
        successes = sum(observations)
        results[dimension] = {
            "value": successes / len(observations) if observations else None,
            "scored": len(observations),
            "unavailable": len(rows) - len(observations),
            "confidence_interval_95": wilson(successes, len(observations)),
        }
    latencies = [row["latency_ms"] for row in rows if row.get("latency_ms") is not None]
    repetitions = defaultdict(list)
    for row in rows:
        repetitions[row["case_id"]].append(row.get("scores", {}).get("answer_accuracy"))
    evaluated = {
        key: values
        for key, values in repetitions.items()
        if len(values) >= 3 and all(isinstance(x, bool) for x in values)
    }
    flaky = [key for key, values in evaluated.items() if len(set(values)) > 1]
    probabilities = [
        (row.get("confidence"), row.get("scores", {}).get("answer_accuracy")) for row in rows
    ]
    calibration = [
        (p, y)
        for p, y in probabilities
        if isinstance(p, float | int) and 0 <= p <= 1 and isinstance(y, bool)
    ]
    return {
        "cases": len(rows),
        "dimensions": results,
        "consistency": 1 - len(flaky) / len(evaluated) if evaluated else None,
        "flaky_cases": flaky,
        "brier": mean((p - int(y)) ** 2 for p, y in calibration) if calibration else None,
        "latency_ms": {
            "p50": quantiles(latencies, n=100, method="inclusive")[49]
            if len(latencies) >= 2
            else (latencies[0] if latencies else None),
            "p95": quantiles(latencies, n=100, method="inclusive")[94]
            if len(latencies) >= 2
            else (latencies[0] if latencies else None),
        },
        "tokens": sum(row.get("tokens") or 0 for row in rows),
        "tokens_unavailable": sum(row.get("tokens") is None for row in rows),
        "provider_calls": sum(row.get("provider_calls") or 0 for row in rows),
        "provider_calls_unavailable": sum(row.get("provider_calls") is None for row in rows),
    }


def paired_improvement(before: list[dict], after: list[dict]) -> dict:
    def index(rows):
        output = {}
        for row in rows:
            key = (row["case_id"], row["repetition"])
            if key in output:
                raise ValueError("Duplicate paired case repetition")
            output[key] = row
        return output

    left, right = index(before), index(after)
    if left.keys() != right.keys():
        raise ValueError("Paired reports must contain exactly the same case repetitions")
    by_case = defaultdict(list)
    for key in left:
        a, b = left[key], right[key]
        if a["learning_sensitive"] != b["learning_sensitive"]:
            raise ValueError("Learning-sensitive classification changed")
        x, y = (
            a.get("scores", {}).get("answer_accuracy"),
            b.get("scores", {}).get("answer_accuracy"),
        )
        if a["learning_sensitive"] and isinstance(x, bool) and isinstance(y, bool):
            by_case[key[0]].append(int(y) - int(x))
    differences = [mean(values) for values in by_case.values()]
    if not differences:
        return {
            "absolute_change": None,
            "confidence_interval_95": None,
            "scored_cases": 0,
            "target_met": None,
        }
    rng = random.Random(20260930)
    # Resample cases, keeping each case's repetitions together.
    samples = sorted(mean(rng.choices(differences, k=len(differences))) for _ in range(5000))
    change, interval = mean(differences), [samples[125], samples[4874]]
    return {
        "absolute_change": change,
        "confidence_interval_95": interval,
        "method": "paired-case-cluster-bootstrap-v1",
        "scored_cases": len(differences),
        "target_met": change >= 0.15 and interval[0] > 0,
    }
