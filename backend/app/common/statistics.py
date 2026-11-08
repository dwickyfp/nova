"""Small, dependency-free statistics for measured claims.

Every quality number Nova shows carries its uncertainty. These are the
standard estimators the ontology evaluation and the benchmarks share:

- ``wilson``: a 95% interval for a proportion, well behaved at small n and
  near 0 or 1 (unlike the normal approximation).
- ``stratified``: a proportion estimated from a stratified sample, weighted by
  stratum size, with a variance-based interval (Gao et al., PVLDB 2019 use the
  same estimator for knowledge-graph accuracy).
- ``calibration``: expected calibration error, Brier score and the reliability
  bins behind a reliability diagram (Guo et al., ICML 2017).
- ``cohen_kappa``: agreement between two raters beyond chance.
- ``mcnemar``: whether paired correct/incorrect outcomes differ (the same cases
  answered two ways), exact binomial for small discordant counts.
- ``paired_bootstrap``: an interval for a mean paired difference.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Hashable, Sequence
from statistics import mean

Z95 = 1.959963984540054


def wilson(successes: int, total: int) -> list[float] | None:
    if not total:
        return None
    p, denominator = successes / total, 1 + Z95 * Z95 / total
    center = (p + Z95 * Z95 / (2 * total)) / denominator
    radius = Z95 * math.sqrt(p * (1 - p) / total + Z95 * Z95 / (4 * total * total)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def proportion(successes: int, total: int) -> dict:
    return {
        "value": successes / total if total else None,
        "successes": successes,
        "n": total,
        "interval_95": wilson(successes, total),
    }


def stratified(strata: dict[str, tuple[int, int, int]]) -> dict:
    """Accuracy over a population from a sample per stratum.

    ``strata`` maps a stratum to ``(population, sampled, correct)``. The estimate
    weights each stratum's sample accuracy by its share of the population; the
    interval uses the stratified variance with a finite-population correction.
    Strata with nothing sampled are left out and reported.
    """
    total = sum(population for population, sampled, _ in strata.values() if sampled)
    if not total:
        return {"value": None, "n": 0, "interval_95": None, "unsampled": sorted(strata)}
    estimate = variance = 0.0
    for population, sampled, correct in strata.values():
        if not sampled:
            continue
        weight = population / total
        p = correct / sampled
        estimate += weight * p
        if sampled > 1:
            correction = max(0.0, 1 - sampled / population) if population else 0.0
            variance += weight * weight * correction * p * (1 - p) / (sampled - 1)
    radius = Z95 * math.sqrt(variance)
    return {
        "value": estimate,
        "n": sum(sampled for _, sampled, _ in strata.values()),
        "interval_95": [max(0.0, estimate - radius), min(1.0, estimate + radius)],
        "unsampled": sorted(name for name, (_, sampled, _) in strata.items() if not sampled),
    }


def calibration(pairs: Sequence[tuple[float, bool]], bins: int = 10) -> dict:
    """ECE, Brier and reliability bins for (confidence, correct) pairs."""
    usable = [(float(p), bool(y)) for p, y in pairs if 0 <= p <= 1]
    if not usable:
        return {"ece": None, "brier": None, "n": 0, "bins": []}
    rows = []
    ece = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [
            (p, y) for p, y in usable
            if low <= p < high or (index == bins - 1 and p == 1.0)
        ]
        if not members:
            continue
        confidence = mean(p for p, _ in members)
        accuracy = mean(1.0 if y else 0.0 for _, y in members)
        ece += len(members) / len(usable) * abs(accuracy - confidence)
        rows.append({
            "low": low, "high": high, "n": len(members),
            "confidence": confidence, "accuracy": accuracy,
        })
    return {
        "ece": ece,
        "brier": mean((p - (1.0 if y else 0.0)) ** 2 for p, y in usable),
        "n": len(usable),
        "bins": rows,
    }


def cohen_kappa(first: Sequence[Hashable], second: Sequence[Hashable]) -> dict:
    if len(first) != len(second):
        raise ValueError("Both raters must label the same items")
    n = len(first)
    if not n:
        return {"kappa": None, "agreement": None, "n": 0}
    agreement = sum(a == b for a, b in zip(first, second, strict=True)) / n
    left, right = Counter(first), Counter(second)
    chance = sum(left[label] * right[label] for label in set(left) | set(right)) / (n * n)
    kappa = 1.0 if chance == 1 else (agreement - chance) / (1 - chance)
    return {"kappa": kappa, "agreement": agreement, "n": n}


def mcnemar(before: Sequence[bool], after: Sequence[bool]) -> dict:
    """Paired test on the same cases; exact two-sided binomial on discordant pairs."""
    if len(before) != len(after):
        raise ValueError("Paired outcomes must cover the same cases")
    gained = sum(1 for a, b in zip(before, after, strict=True) if not a and b)
    lost = sum(1 for a, b in zip(before, after, strict=True) if a and not b)
    discordant = gained + lost
    if not discordant:
        return {"gained": 0, "lost": 0, "p_value": 1.0}
    k = min(gained, lost)
    tail = sum(math.comb(discordant, i) for i in range(k + 1)) / 2**discordant
    return {"gained": gained, "lost": lost, "p_value": min(1.0, 2 * tail)}


def paired_bootstrap(
    differences: Sequence[float], *, resamples: int = 5000, seed: int = 20261107
) -> dict:
    if not differences:
        return {"mean": None, "interval_95": None, "n": 0}
    rng = random.Random(seed)
    samples = sorted(
        mean(rng.choices(differences, k=len(differences))) for _ in range(resamples)
    )
    low, high = int(resamples * 0.025), int(resamples * 0.975) - 1
    return {
        "mean": mean(differences),
        "interval_95": [samples[low], samples[high]],
        "n": len(differences),
    }
