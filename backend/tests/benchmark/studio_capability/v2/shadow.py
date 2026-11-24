"""Shadow-run tooling (H5): plan the live subset, then grade it with v1 and v2 side by side.

    # 1. plan (offline): units touched by the round-2 PRs, one canary per category,
    #    twins for stochastic units, whole sessions; with a token estimate.
    cd backend && uv run python -m tests.benchmark.studio_capability.v2.shadow plan \\
        --prs PR-1,PR-2 --history /path/bench/baseline --history /path/bench/rebench \\
        --token-budget 3000000 --out /path/shadow-1
    # 2. run live (in an agreed window; the corpus linter blocks a stale corpus):
    #    run.py --ids "$(cat /path/shadow-1/ids.txt)" --token-budget 3000000 --out /path/shadow-1
    # 3. compare (offline): v1 vs v2 flip table and the paired v2 comparison.
    ... v2.shadow compare --run /path/shadow-1 --baseline /path/bench/rebench

v1 is retired after two shadow runs whose flip tables were reviewed (owner D8);
``compare`` appends each run to ``shadow_ledger.json`` beside the baseline so the
count is visible.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

from tests.benchmark.studio_capability.v2 import corpus_v3, rescore
from tests.benchmark.studio_capability.v2.budget import Estimates
from tests.benchmark.studio_capability.v2.expectation import Expectation
from tests.benchmark.studio_capability.v2.report import markdown, paired

CANARY_SEED = 20261124
#: Units each round-2 PR should flip or re-check (FIX-PLAN B1 and C2).
PR_UNITS: dict[str, list[str]] = {
    "PR-0": [],
    "PR-1": ["trend-003", "multi_step-010", "diagnosis-005", "diagnosis-006", "session-025",
             "date_relative-002", "what_if-006"],
    "PR-2": ["what_if-001", "what_if-002", "what_if-003", "multi_step-002", "session-015",
             "session-016", "session-030"],
    "PR-3": ["deep_research-001", "deep_research-002", "deep_research-003"],
    "PR-4": ["route_optimize-001", "route_optimize-002", "route_overwater-002"],
    "PR-5": ["number-026", "typo-006", "mcp-001", "mcp-002", "agent_meta-002"],
    "PR-6": ["session-015", "trend-003", "multi_step-010"],
    "PR-7": ["session-012", "session-006", "session-029", "session-005"],
    "PR-8": ["automation-001", "automation-002", "automation-004"],
}
STOCHASTIC = ("what_if", "diagnosis", "route")


def expand(ids: list[str], units: dict[str, Expectation]) -> list[str]:
    """Unit ids, with a session id expanded to every turn of that session (no twins)."""
    out: list[str] = []
    for item in ids:
        if item in units:
            out.append(item)
            continue
        out += [u.id for u in units.values() if u.session_id == item and u.unit == "session_turn"]
    return list(dict.fromkeys(out))


def canaries(units: dict[str, Expectation], latest: Path | None, exclude: set[str]) -> list[str]:
    """One unit per category that passed v2 in the latest run (fixed seed)."""
    passing: dict[str, list[str]] = {}
    if latest is not None:
        for record, g in rescore.score_run(rescore.load_turns(latest), units,
                                           corpus_v3.load_ground_truth()):
            unit = units.get(record["id"])
            if g and unit and g.verdict == "pass" and unit.unit == "single" \
                    and unit.id not in exclude:
                passing.setdefault(unit.category, []).append(unit.id)
    rng = random.Random(CANARY_SEED)
    return [rng.choice(sorted(ids)) for _, ids in sorted(passing.items())]


def plan(prs: list[str], histories: list[Path], token_budget: int | None) -> dict[str, Any]:
    units = {u.id: u for u in corpus_v3.load_units()}
    touched = expand([uid for pr in prs for uid in PR_UNITS.get(pr, [])], units)
    deferred = [u for u in touched if units[u].deferred]
    touched = [u for u in touched if not units[u].deferred]
    canary = canaries(units, histories[-1] if histories else None, set(touched))
    twins = [u for u in touched if units[u].category.startswith(STOCHASTIC)
             or units[u].category.removeprefix("session:").startswith(STOCHASTIC)]
    estimates = Estimates.from_runs(histories)
    ids = touched + canary
    cost = {u: estimates.estimate(u, units[u].category) for u in ids}
    twin_cost = sum(estimates.estimate(u, units[u].category) for u in twins)
    total = sum(cost.values()) + twin_cost
    return {"prs": prs, "touched": touched, "deferred": deferred, "canaries": canary,
            "twins": twins, "ids": ids, "estimated_tokens": total,
            "estimated_tokens_twins": twin_cost, "token_budget": token_budget,
            "fits_budget": token_budget is None or total <= token_budget,
            "planned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def compare(run: Path, baseline: Path) -> dict[str, Any]:
    units = {u.id: u for u in corpus_v3.load_units()}
    gt = corpus_v3.load_ground_truth()
    rows = rescore.rows_for(run.name, rescore.score_run(rescore.load_turns(run), units, gt), {})
    summary = rescore.summarize(rows, [run.name])
    (run / "shadow_flips.md").write_text(rescore.flips_markdown(rows, summary, [run.name]))
    result = paired(baseline, run)
    (run / "paired_v2.md").write_text(markdown(result))
    ledger_path = baseline / "shadow_ledger.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.is_file() else {"runs": []}
    ledger["runs"].append({"run": str(run), "compared_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                           "v1": summary[run.name]["v1"], "v2": summary[run.name]["v2"],
                           "flips": summary[run.name]["flips"], "reviewed": False})
    ledger["v1_retirement"] = ("eligible once two shadow runs are reviewed (owner D8)"
                               if sum(1 for r in ledger["runs"] if r["reviewed"]) < 2
                               else "eligible: two reviewed shadow runs")
    return {"summary": summary, "paired": result["binary"], "ledger": ledger,
            "ledger_path": str(ledger_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--prs", required=True, help="comma-separated, e.g. PR-1,PR-2")
    p.add_argument("--history", type=Path, action="append", default=[],
                   help="recorded runs for token estimates and canaries; latest last")
    p.add_argument("--token-budget", type=int)
    p.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("compare")
    c.add_argument("--run", type=Path, required=True)
    c.add_argument("--baseline", type=Path, required=True)
    c.add_argument("--write-ledger", action="store_true",
                   help="record this comparison in the baseline's shadow ledger")
    args = parser.parse_args()
    if args.mode == "plan":
        result = plan(args.prs.split(","), args.history, args.token_budget)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "plan.json").write_text(json.dumps(result, indent=1))
        (args.out / "ids.txt").write_text(",".join(result["ids"]))
        (args.out / "twin_ids.txt").write_text(",".join(result["twins"]))
        print(json.dumps({k: v for k, v in result.items() if k != "ids"}, indent=1))
        return
    result = compare(args.run, args.baseline)
    if args.write_ledger:
        Path(result["ledger_path"]).write_text(json.dumps(result["ledger"], indent=1))
    print(json.dumps({"v1": result["summary"][args.run.name]["v1"],
                      "v2": result["summary"][args.run.name]["v2"],
                      "paired": result["paired"]}, indent=1, default=str))


if __name__ == "__main__":
    main()
