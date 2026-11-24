"""Paired report of two runs graded with v2 (DESIGN §8).

    cd backend && uv run python -m tests.benchmark.studio_capability.v2.report \\
        --before DIR --after DIR [--twin DIR ...] --out DIR

Per unit: v2 verdict before and after. Binary: McNemar exact over units decided
in both runs (``judge`` units are left out and listed). Judge dimension scores,
when both runs have ``judgments_v2``: mean per-unit difference with a 95%
bootstrap CI that resamples whole sessions. Twins (repeat runs of the after
build): pass^k and the flaky units, which are not counted as fixes or
regressions. Tokens per turn on the units both runs share.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tests.benchmark.studio_capability.v2 import budget, stats
from tests.benchmark.studio_capability.v2.judge import DIMENSIONS, graded_run


def judged_scores(directory: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for path in (directory / "judgments_v2").glob("*.json"):
        item = json.loads(path.read_text())
        out[str(item["id"])] = item["final"]
    return out


def paired(before: Path, after: Path, twins: list[Path] | None = None) -> dict[str, Any]:
    a, b = graded_run(before), graded_run(after)
    verdict_before = {u: x["grade"].verdict for u, x in a.items()}
    verdict_after = {u: x["grade"].verdict for u, x in b.items()}
    result: dict[str, Any] = {"before": str(before), "after": str(after)}
    flaky: set[str] = set()
    if twins:
        trials: dict[str, list[str]] = {u: [v] for u, v in verdict_after.items()}
        for twin in twins:
            for unit_id, item in graded_run(twin).items():
                trials.setdefault(unit_id, []).append(item["grade"].verdict)
        result["pass_hat_k"] = stats.pass_hat_k(trials, 1 + len(twins))
        flaky = set(result["pass_hat_k"]["flaky"])
    binary = stats.paired_binary({u: v for u, v in verdict_before.items() if u not in flaky},
                                 {u: v for u, v in verdict_after.items() if u not in flaky})
    result["binary"] = binary
    result["flaky_excluded"] = sorted(flaky)
    shared = sorted(set(a) & set(b))
    result["judge_or_human"] = sorted(u for u in shared if "judge" in
                                      {verdict_before[u], verdict_after[u]})
    jb, ja = judged_scores(before), judged_scores(after)
    for dimension in DIMENSIONS:
        diffs = {}
        for unit_id in sorted(set(jb) & set(ja)):
            x, y = jb[unit_id]["scores"].get(dimension), ja[unit_id]["scores"].get(dimension)
            if x is not None and y is not None and not (
                    jb[unit_id]["needs_human"] or ja[unit_id]["needs_human"]):
                diffs[unit_id] = float(y) - float(x)
        result.setdefault("judge_scores", {})[dimension] = stats.bootstrap_cluster_ci(diffs)
    tokens = []
    for unit_id in shared:
        x = budget.recorded_tokens(a[unit_id]["record"])
        y = budget.recorded_tokens(b[unit_id]["record"])
        if x and y:
            tokens.append((x, y))
    if tokens:
        before_sum, after_sum = sum(x for x, _ in tokens), sum(y for _, y in tokens)
        result["tokens"] = {"units": len(tokens), "before": before_sum, "after": after_sum,
                            "change": round(after_sum / before_sum - 1, 4)}
    result["units"] = [{"id": u, "before": verdict_before[u], "after": verdict_after[u],
                        "why_after": b[u]["grade"].why} for u in shared
                       if verdict_before[u] != verdict_after[u]]
    return result


def markdown(result: dict[str, Any]) -> str:
    b = result["binary"]
    lines = ["# Paired comparison (harness v2)", "",
             f"Before `{result['before']}`, after `{result['after']}`.", "",
             f"- Units shared: {b['shared']}; decided in both: {b['decided']}; left out "
             f"(judge in either run): {b['excluded']}",
             f"- Fixed (fail -> pass): {b['fixed']}; regressed (pass -> fail): {b['regressed']}; "
             f"McNemar exact p = {b['mcnemar_p']}",
             f"- Pass rate on decided units: {b['pass_before']}/{b['decided']} -> "
             f"{b['pass_after']}/{b['decided']}"]
    if "pass_hat_k" in result:
        p = result["pass_hat_k"]
        lines.append(f"- pass^{p['k']} over {p['units']} units: {p['pass_hat_k']}; flaky "
                     f"(excluded from fixed/regressed): {', '.join(p['flaky']) or '-'}")
    if "tokens" in result:
        t = result["tokens"]
        lines.append(f"- Tokens on {t['units']} shared units: {t['before']} -> {t['after']} "
                     f"({t['change']:+.1%})")
    lines += ["", "## Judge scores (after - before, cluster bootstrap 95% CI)", "",
              "| Dimension | Units | Sessions/clusters | Mean | CI |", "|---|---|---|---|---|"]
    for dimension, ci in (result.get("judge_scores") or {}).items():
        lines.append(f"| {dimension} | {ci['n']} | {ci['clusters']} | {ci['mean']} | "
                     f"{ci['ci']} |")
    lines += ["", "## Changed units", "", "| Unit | Before | After | Why (after) |",
              "|---|---|---|---|"]
    for unit in result["units"]:
        lines.append(f"| {unit['id']} | {unit['before']} | {unit['after']} | "
                     f"{unit['why_after']} |")
    lines += ["", f"Judge or human review: {', '.join(result['judge_or_human']) or '-'}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--twin", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = paired(args.before, args.after, args.twin)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "paired_v2.json").write_text(json.dumps(result, indent=1, default=str))
    (args.out / "paired_v2.md").write_text(markdown(result))
    print(markdown(result))


if __name__ == "__main__":
    main()
