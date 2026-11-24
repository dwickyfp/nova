"""Statistics for paired benchmark comparisons (DESIGN §8). Standard library only.

* McNemar exact test on discordant pairs of a binary verdict per unit.
* Bootstrap 95% CI of a mean per-unit difference, resampling whole sessions
  (turns of one session are not independent).
* pass^k (tau-bench): the chance that all k trials of a unit pass.
* Cohen's kappa for judge-vs-human agreement.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import Any

DECIDED = ("pass", "fail")


def cohen_kappa(pairs: Sequence[tuple[Hashable, Hashable]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[label] * right[label] for label in set(left) | set(right)) / (n * n)
    if expected == 1:
        return 1.0 if observed == 1 else None
    return round((observed - expected) / (1 - expected), 4)


def mcnemar_exact(fixed: int, regressed: int) -> float:
    """Two-sided exact McNemar p-value (binomial test on the discordant pairs)."""
    n = fixed + regressed
    if n == 0:
        return 1.0
    k = min(fixed, regressed)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def paired_binary(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, Any]:
    """fail->pass and pass->fail over units decided in both runs (judge excluded)."""
    shared = sorted(set(before) & set(after))
    decided = [i for i in shared if before[i] in DECIDED and after[i] in DECIDED]
    fixed = [i for i in decided if before[i] == "fail" and after[i] == "pass"]
    regressed = [i for i in decided if before[i] == "pass" and after[i] == "fail"]
    return {"shared": len(shared), "decided": len(decided),
            "excluded": len(shared) - len(decided), "fixed": len(fixed),
            "regressed": len(regressed), "fixed_ids": fixed, "regressed_ids": regressed,
            "pass_before": sum(before[i] == "pass" for i in decided),
            "pass_after": sum(after[i] == "pass" for i in decided),
            "mcnemar_p": mcnemar_exact(len(fixed), len(regressed))}


def cluster_of(unit_id: str) -> str:
    """Session turns cluster by session; single questions are their own cluster."""
    return unit_id.split(".", 1)[0] if unit_id.startswith("session-") else unit_id


def bootstrap_cluster_ci(diffs: Mapping[str, float], *, resamples: int = 10_000,
                         seed: int = 20261124, level: float = 0.95,
                         cluster: Callable[[str], str] = cluster_of) -> dict[str, Any]:
    """Mean per-unit difference with a cluster-bootstrap percentile CI."""
    if not diffs:
        return {"n": 0, "clusters": 0, "mean": None, "ci": None}
    groups: dict[str, list[float]] = defaultdict(list)
    for unit_id, value in diffs.items():
        groups[cluster(unit_id)].append(float(value))
    keys = sorted(groups)
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        total, count = 0.0, 0
        for _ in keys:
            values = groups[keys[rng.randrange(len(keys))]]
            total += sum(values)
            count += len(values)
        means.append(total / count)
    means.sort()
    low = means[int((1 - level) / 2 * resamples)]
    high = means[min(resamples - 1, int((1 + level) / 2 * resamples))]
    mean = sum(diffs.values()) / len(diffs)
    return {"n": len(diffs), "clusters": len(keys), "mean": round(mean, 4),
            "ci": [round(low, 4), round(high, 4)], "level": level, "resamples": resamples}


def pass_hat_k(trials: Mapping[str, Sequence[str]], k: int) -> dict[str, Any]:
    """tau-bench pass^k: mean over units of C(c, k) / C(n, k) with c passes in n trials.

    Units with fewer than k decided trials are left out and counted; a unit
    whose verdicts disagree is reported as flaky.
    """
    scores, skipped, flaky = [], [], []
    for unit_id, verdicts in trials.items():
        decided = [v for v in verdicts if v in DECIDED]
        if len(decided) < k:
            skipped.append(unit_id)
            continue
        passes = sum(v == "pass" for v in decided)
        scores.append(math.comb(passes, k) / math.comb(len(decided), k))
        if 0 < passes < len(decided):
            flaky.append(unit_id)
    return {"k": k, "units": len(scores),
            "pass_hat_k": round(sum(scores) / len(scores), 4) if scores else None,
            "skipped": sorted(skipped), "flaky": sorted(flaky)}
